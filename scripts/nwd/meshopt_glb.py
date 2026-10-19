#!/usr/bin/env python3
"""
Пост-обработчик GLB: сжатие bufferViews расширением EXT_meshopt_compression.

Использует внешнюю библиотеку libmeshopt.so через ctypes.
Вход и выход — бинарный GLB. Исходные accessors/meshes/nodes не меняются.

CLI: python meshopt_glb.py IN.glb OUT.glb [--lib PATH]
Коды возврата:
  0 — успех
  3 — библиотека не найдена/не загружена
  4 — сжатие не дало выигрыша
  1 — прочие ошибки
"""

import sys
import os
import json
import struct
import ctypes
import numpy as np
from typing import Optional, Dict, List, Any, Tuple


class MeshoptUnavailable(Exception):
    """Библиотека meshopt не найдена или не может быть загружена."""
    pass


class NoGain(Exception):
    """Сжатие не дало выигрыша в размере."""
    pass


def _align4(n: int) -> int:
    """Выровнять число вверх до кратного 4."""
    return (n + 3) & ~3


def _load_meshopt_lib(lib_path: Optional[str] = None) -> ctypes.CDLL:
    """
    Загрузить libmeshopt.so.
    Порядок поиска: lib_path, env MESHOPT_LIB, /usr/local/lib/libmeshopt.so.
    """
    candidates = []
    if lib_path:
        # явно заданный путь не подменяется запасными
        candidates.append(lib_path)
    else:
        env_path = os.environ.get("MESHOPT_LIB")
        if env_path:
            candidates.append(env_path)
        candidates.append("/usr/local/lib/libmeshopt.so")

    last_err = None
    for path in candidates:
        if not os.path.isfile(path):
            last_err = f"Файл не найден: {path}"
            continue
        try:
            lib = ctypes.CDLL(path)
            # Настроить сигнатуры
            c_size_t = ctypes.c_size_t
            c_void_p = ctypes.c_void_p
            c_uint8_p = ctypes.POINTER(ctypes.c_uint8)
            c_uint32_p = ctypes.POINTER(ctypes.c_uint32)
            c_int = ctypes.c_int

            # meshopt_encodeVertexBufferBound
            fn = lib.meshopt_encodeVertexBufferBound
            fn.argtypes = [c_size_t, c_size_t]
            fn.restype = c_size_t

            # meshopt_encodeVertexBuffer
            fn = lib.meshopt_encodeVertexBuffer
            fn.argtypes = [c_uint8_p, c_size_t, c_void_p, c_size_t, c_size_t]
            fn.restype = c_size_t

            # meshopt_encodeVertexVersion
            fn = lib.meshopt_encodeVertexVersion
            fn.argtypes = [c_int]
            fn.restype = None

            # meshopt_encodeIndexBufferBound
            fn = lib.meshopt_encodeIndexBufferBound
            fn.argtypes = [c_size_t, c_size_t]
            fn.restype = c_size_t

            # meshopt_encodeIndexBuffer
            fn = lib.meshopt_encodeIndexBuffer
            fn.argtypes = [c_uint8_p, c_size_t, c_uint32_p, c_size_t]
            fn.restype = c_size_t

            # meshopt_encodeIndexVersion
            fn = lib.meshopt_encodeIndexVersion
            fn.argtypes = [c_int]
            fn.restype = None

            # Установить версию 0 (нужна для three.js r160)
            lib.meshopt_encodeVertexVersion(0)
            lib.meshopt_encodeIndexVersion(0)

            return lib
        except OSError as e:
            last_err = f"Ошибка загрузки {path}: {e}"
            continue

    raise MeshoptUnavailable(f"Не удалось загрузить libmeshopt: {last_err}")


def _parse_glb(data: bytes) -> Tuple[Dict[str, Any], bytes]:
    """
    Разобрать GLB: вернуть (json_dict, bin_chunk_bytes).
    Бросает ValueError при некорректном формате.
    """
    if len(data) < 12:
        raise ValueError("GLB слишком мал")

    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("Неверный magic GLB")
    if version != 2:
        raise ValueError("Неподдерживаемая версия GLB")
    if length != len(data):
        raise ValueError("Длина в заголовке не совпадает с фактической")

    offset = 12
    json_chunk = None
    bin_chunk = None

    while offset < len(data):
        if offset + 8 > len(data):
            break
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        if offset + chunk_len > len(data):
            break
        chunk_data = data[offset:offset + chunk_len]
        offset += chunk_len

        if chunk_type == 0x4E4F534A:  # JSON
            json_chunk = chunk_data
        elif chunk_type == 0x004E4942:  # BIN
            bin_chunk = chunk_data

    if json_chunk is None:
        raise ValueError("Нет JSON-чанка")
    if bin_chunk is None:
        raise ValueError("Нет BIN-чанка")

    json_dict = json.loads(json_chunk.decode("utf-8"))
    return json_dict, bin_chunk


def _get_accessor_component_size(component_type: int) -> int:
    """Размер компоненты в байтах."""
    sizes = {
        5120: 1,   # BYTE
        5121: 1,   # UNSIGNED_BYTE
        5122: 2,   # SHORT
        5123: 2,   # UNSIGNED_SHORT
        5125: 4,   # UNSIGNED_INT
        5126: 4,   # FLOAT
    }
    return sizes.get(component_type, 0)


def _get_accessor_element_size(accessor: Dict[str, Any]) -> int:
    """
    Размер одного элемента accessor в байтах (без учёта stride).
    = число компонент × размер компоненты.
    """
    comp_type = accessor.get("componentType", 0)
    comp_size = _get_accessor_component_size(comp_type)
    if comp_size == 0:
        return 0

    type_str = accessor.get("type", "SCALAR")
    # Число компонент по типу
    type_map = {
        "SCALAR": 1,
        "VEC2": 2,
        "VEC3": 3,
        "VEC4": 4,
        "MAT2": 4,
        "MAT3": 9,
        "MAT4": 16,
    }
    num_components = type_map.get(type_str, 1)
    return num_components * comp_size


_USAGE_CACHE: Dict[str, Any] = {}


def _usage_index(json_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Одноразовый индекс (по id словаря JSON): accessors по bufferView, множества id
    accessors-индексов примитивов и атрибутов (примитивов и инстансинга)."""
    if _USAGE_CACHE.get("key") == id(json_dict):
        return _USAGE_CACHE
    accessors = json_dict.get("accessors", [])
    by_bv: Dict[int, List[Dict[str, Any]]] = {}
    for acc in accessors:
        bv = acc.get("bufferView")
        if bv is not None:
            by_bv.setdefault(bv, []).append(acc)
    ind_ids = set()
    attr_ids = set()
    for mesh in json_dict.get("meshes", []):
        for prim in mesh.get("primitives", []):
            idx = prim.get("indices")
            if idx is not None and idx < len(accessors):
                ind_ids.add(id(accessors[idx]))
            for acc_idx in prim.get("attributes", {}).values():
                if acc_idx < len(accessors):
                    attr_ids.add(id(accessors[acc_idx]))
    for node in json_dict.get("nodes", []):
        attrs = node.get("extensions", {}).get("EXT_mesh_gpu_instancing", {}).get("attributes", {})
        for acc_idx in attrs.values():
            if acc_idx < len(accessors):
                attr_ids.add(id(accessors[acc_idx]))
    _USAGE_CACHE.clear()
    _USAGE_CACHE.update(key=id(json_dict), by_bv=by_bv, ind_ids=ind_ids, attr_ids=attr_ids)
    return _USAGE_CACHE


def _find_accessors_for_bufferview(json_dict: Dict[str, Any], bv_index: int) -> List[Dict[str, Any]]:
    """Найти все accessors, ссылающиеся на указанный bufferView."""
    return _usage_index(json_dict)["by_bv"].get(bv_index, [])


def _is_accessor_indices(json_dict: Dict[str, Any], accessor: Dict[str, Any]) -> bool:
    """Проверить, является ли accessor индексами примитива (по тождеству объекта)."""
    return id(accessor) in _usage_index(json_dict)["ind_ids"]


def _is_accessor_attribute(json_dict: Dict[str, Any], accessor: Dict[str, Any]) -> bool:
    """Проверить, является ли accessor атрибутом примитива или инстансинга."""
    return id(accessor) in _usage_index(json_dict)["attr_ids"]


def _determine_usage(json_dict: Dict[str, Any], bv_index: int, accessor: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    """
    Определить режим использования bufferView.
    Возвращает (mode, stride, count) или None, если не определено.
    mode: "TRIANGLES" или "ATTRIBUTES"
    """
    # Проверка: ровно один accessor, byteOffset == 0 или отсутствует, нет sparse
    accessors_for_bv = _find_accessors_for_bufferview(json_dict, bv_index)
    if len(accessors_for_bv) != 1:
        return None

    acc = accessors_for_bv[0]
    if acc.get("sparse") is not None:
        return None

    # byteOffset accessor должен быть 0 или отсутствовать
    acc_byte_offset = acc.get("byteOffset", 0)
    if acc_byte_offset != 0:
        return None

    # Определяем тип использования
    is_indices = _is_accessor_indices(json_dict, acc)
    is_attribute = _is_accessor_attribute(json_dict, acc)

    if is_indices:
        # TRIANGLES
        comp_type = acc.get("componentType", 0)
        if comp_type not in (5123, 5125):
            return None
        count = acc.get("count", 0)
        if count % 3 != 0:
            return None
        stride = _get_accessor_component_size(comp_type)  # 2 или 4
        return ("TRIANGLES", stride, count)

    elif is_attribute:
        # ATTRIBUTES
        # stride = byteStride bufferView, если задан, иначе размер элемента
        # Но нам нужен bufferView, чтобы получить byteStride
        # Возвращаем None здесь, а stride определим позже
        # Для ATTRIBUTES stride определяется из bufferView
        # Пока вернём placeholder
        return ("ATTRIBUTES", 0, 0)  # stride и count будут определены позже

    return None


def _determine_usage_full(json_dict: Dict[str, Any], bv_index: int) -> Optional[Tuple[str, int, int]]:
    """
    Полное определение режима использования с учётом bufferView.
    Возвращает (mode, stride, count) или None.
    """
    accessors_for_bv = _find_accessors_for_bufferview(json_dict, bv_index)
    if len(accessors_for_bv) != 1:
        return None

    acc = accessors_for_bv[0]
    if acc.get("sparse") is not None:
        return None

    acc_byte_offset = acc.get("byteOffset", 0)
    if acc_byte_offset != 0:
        return None

    is_indices = _is_accessor_indices(json_dict, acc)
    is_attribute = _is_accessor_attribute(json_dict, acc)

    if is_indices:
        comp_type = acc.get("componentType", 0)
        if comp_type not in (5123, 5125):
            return None
        count = acc.get("count", 0)
        if count % 3 != 0:
            return None
        stride = _get_accessor_component_size(comp_type)
        return ("TRIANGLES", stride, count)

    elif is_attribute:
        # Получаем bufferView для определения byteStride
        bufferViews = json_dict.get("bufferViews", [])
        if bv_index >= len(bufferViews):
            return None
        bv = bufferViews[bv_index]
        byte_stride = bv.get("byteStride")

        if byte_stride is not None:
            stride = byte_stride
        else:
            stride = _get_accessor_element_size(acc)

        # stride должен быть кратен 4 и <= 256
        if stride % 4 != 0 or stride > 256:
            return None

        byte_length = bv.get("byteLength", 0)
        if byte_length % stride != 0:
            return None

        count = byte_length // stride
        return ("ATTRIBUTES", stride, count)

    return None


def _encode_triangles(lib: ctypes.CDLL, indices_data: bytes, index_count: int, vertex_count: int) -> bytes:
    """Сжать индексы (TRIANGLES)."""
    c_size_t = ctypes.c_size_t
    c_uint8_p = ctypes.POINTER(ctypes.c_uint8)
    c_uint32_p = ctypes.POINTER(ctypes.c_uint32)

    # Привести к uint32
    if len(indices_data) % 2 == 0 and index_count * 2 == len(indices_data):
        # uint16
        indices_np = np.frombuffer(indices_data, dtype=np.uint16).astype(np.uint32)
    else:
        # uint32
        indices_np = np.frombuffer(indices_data, dtype=np.uint32)

    indices_np = np.ascontiguousarray(indices_np)

    bound = lib.meshopt_encodeIndexBufferBound(c_size_t(index_count), c_size_t(vertex_count))
    if bound == 0:
        raise RuntimeError("meshopt_encodeIndexBufferBound вернул 0")

    out_buf = (ctypes.c_uint8 * bound)()
    ptr = ctypes.cast(out_buf, c_uint8_p)

    n = lib.meshopt_encodeIndexBuffer(
        ptr,
        c_size_t(bound),
        ctypes.cast(indices_np.ctypes.data, c_uint32_p),
        c_size_t(index_count)
    )

    if n == 0:
        raise RuntimeError("meshopt_encodeIndexBuffer вернул 0 байт")

    return bytes(out_buf[:n])


def _encode_attributes(lib: ctypes.CDLL, data: bytes, count: int, stride: int) -> bytes:
    """Сжать атрибуты (ATTRIBUTES)."""
    c_size_t = ctypes.c_size_t
    c_uint8_p = ctypes.POINTER(ctypes.c_uint8)
    c_void_p = ctypes.c_void_p

    # Библиотека делает abort() при нарушении этих условий — отказываем исключением заранее
    if stride <= 0 or stride % 4 != 0 or stride > 256 or count <= 0 or len(data) != count * stride:
        raise ValueError(f"недопустимые параметры вершинного потока: stride={stride}, count={count}, байт={len(data)}")

    data_np = np.frombuffer(data, dtype=np.uint8)
    data_np = np.ascontiguousarray(data_np)

    bound = lib.meshopt_encodeVertexBufferBound(c_size_t(count), c_size_t(stride))
    if bound == 0:
        raise RuntimeError("meshopt_encodeVertexBufferBound вернул 0")

    out_buf = (ctypes.c_uint8 * bound)()
    ptr = ctypes.cast(out_buf, c_uint8_p)

    n = lib.meshopt_encodeVertexBuffer(
        ptr,
        c_size_t(bound),
        ctypes.cast(data_np.ctypes.data, c_void_p),
        c_size_t(count),
        c_size_t(stride)
    )

    if n == 0:
        raise RuntimeError("meshopt_encodeVertexBuffer вернул 0 байт")

    return bytes(out_buf[:n])


def compress_glb(src: str, dst: str, lib_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Сжать GLB-файл с использованием EXT_meshopt_compression.

    Args:
        src: путь к входному GLB
        dst: путь к выходному GLB
        lib_path: путь к libmeshopt.so (опционально)

    Returns:
        dict с ключами: before, after, compressed, total

    Raises:
        MeshoptUnavailable: библиотека не найдена
        NoGain: сжатие не дало выигрыша
        ValueError: некорректный GLB
    """
    # Загрузить библиотеку
    lib = _load_meshopt_lib(lib_path)

    # Прочитать входной файл
    with open(src, "rb") as f:
        src_data = f.read()

    before_size = len(src_data)

    # Разобрать GLB
    json_dict, bin_chunk = _parse_glb(src_data)

    # Проверка: extensionsUsed не содержит EXT_meshopt_compression
    ext_used = json_dict.get("extensionsUsed", [])
    if "EXT_meshopt_compression" in ext_used:
        raise ValueError("GLB уже содержит EXT_meshopt_compression")

    # Проверка: один буфер
    buffers = json_dict.get("buffers", [])
    if len(buffers) != 1:
        raise ValueError("Ожидается ровно один буфер")
    if buffers[0].get("uri") is not None:
        raise ValueError("Буфер не должен иметь uri")

    bufferViews = json_dict.get("bufferViews", [])
    accessors = json_dict.get("accessors", [])

    # Для каждого bufferView определить, сжимать ли
    # compressed_info[bv_index] = (mode, stride, count, compressed_bytes)
    compressed_info: Dict[int, Tuple[str, int, int, bytes]] = {}
    uncompressed_bvs: List[int] = []

    for bv_index, bv in enumerate(bufferViews):
        usage = _determine_usage_full(json_dict, bv_index)
        if usage is None:
            uncompressed_bvs.append(bv_index)
            continue

        mode, stride, count = usage
        byte_length = bv.get("byteLength", 0)
        byte_offset = bv.get("byteOffset", 0)

        # Извлечь данные из bin_chunk
        if byte_offset + byte_length > len(bin_chunk):
            uncompressed_bvs.append(bv_index)
            continue

        data = bin_chunk[byte_offset:byte_offset + byte_length]

        try:
            if mode == "TRIANGLES":
                # vertex_count = max(индекс) + 1
                if stride == 2:
                    indices_np = np.frombuffer(data, dtype=np.uint16)
                else:
                    indices_np = np.frombuffer(data, dtype=np.uint32)

                if len(indices_np) == 0:
                    uncompressed_bvs.append(bv_index)
                    continue

                max_idx = int(np.max(indices_np))
                vertex_count = max_idx + 1

                compressed = _encode_triangles(lib, data, count, vertex_count)
            else:  # ATTRIBUTES
                compressed = _encode_attributes(lib, data, count, stride)
        except Exception as e:
            # Если сжатие не удалось, оставляем несжатым
            uncompressed_bvs.append(bv_index)
            continue

        if len(compressed) == 0:
            uncompressed_bvs.append(bv_index)
            continue

        compressed_info[bv_index] = (mode, stride, count, compressed)

    # Если ничего не сжато — NoGain
    if not compressed_info:
        raise NoGain("Ни один bufferView не был сжат")

    # Собрать новый BIN
    # Порядок: сначала несжатые bufferViews (с выравниванием), затем сжатые
    # Но по спецификации: buffer 0 — сжатые данные, buffer 1 — fallback (несжатые)
    # Перечитаем спецификацию:
    # buffers[0] — сжатые данные (EXT_meshopt_compression)
    # buffers[1] — fallback (несжатые данные)
    #
    # Но в выходном JSON:
    # - НЕ сжатый bufferView: buffer:0, byteOffset в новом BIN
    # - Сжатый bufferView: buffer:1, extensions.EXT_meshopt_compression.buffer:0
    #
    # Это значит:
    # - buffer 0 в JSON = новый BIN (содержит и сжатые, и несжатые данные?)
    # - buffer 1 = fallback (несжатые данные)
    #
    # Перечитаем внимательно:
    # "buffers: [{"byteLength": <размер нового BIN>}, {"byteLength": <сумма raw-размеров сжатых bufferViews с выравниванием по 4>, "extensions": {"EXT_meshopt_compression": {"fallback": true}}}]"
    #
    # То есть:
    # - buffer 0: новый BIN (содержит сжатые данные + несжатые bufferViews)
    # - buffer 1: fallback (содержит несжатые данные сжатых bufferViews)
    #
    # "Каждый НЕ сжатый bufferView: копируется в новый BIN с выравниванием на 4, byteOffset обновлён, buffer:0"
    # "Каждый сжатый bufferView: buffer:1, byteOffset = смещение во fallback-буфере, byteLength = исходный несжатый размер, extensions.EXT_meshopt_compression.buffer:0, byteOffset = смещение сжатых данных в новом BIN"
    #
    # Итого:
    # Новый BIN (buffer 0): содержит сжатые данные (для сжатых BV) + несжатые данные (для несжатых BV)
    # Fallback (buffer 1): содержит несжатые данные (для сжатых BV)
    #
    # Порядок в новом BIN: сначала сжатые данные, затем несжатые? Или наоборот?
    # Спецификация не уточняет порядок, но логично: сначала сжатые, затем несжатые.
    # Или: сначала несжатые (как в исходном), затем сжатые?
    #
    # Для простоты: соберём новый BIN как:
    # 1. Сжатые данные (для каждого сжатого BV, с выравниванием)
    # 2. Несжатые данные (для каждого несжатого BV, с выравниванием)
    #
    # Fallback: несжатые данные сжатых BV (с выравниванием)

    # Собираем новый BIN
    new_bin_parts: List[bytes] = []
    new_bin_offset = 0

    # Смещения сжатых данных в новом BIN
    compressed_offsets: Dict[int, int] = {}

    # Сначала сжатые данные
    for bv_index in sorted(compressed_info.keys()):
        mode, stride, count, compressed = compressed_info[bv_index]
        # Выравнивание
        padding = _align4(new_bin_offset) - new_bin_offset
        if padding > 0:
            new_bin_parts.append(b"\x00" * padding)
            new_bin_offset += padding

        compressed_offsets[bv_index] = new_bin_offset
        new_bin_parts.append(compressed)
        new_bin_offset += len(compressed)

    # Затем несжатые данные
    uncompressed_offsets: Dict[int, int] = {}
    for bv_index in sorted(uncompressed_bvs):
        bv = bufferViews[bv_index]
        byte_length = bv.get("byteLength", 0)
        byte_offset = bv.get("byteOffset", 0)
        data = bin_chunk[byte_offset:byte_offset + byte_length]

        padding = _align4(new_bin_offset) - new_bin_offset
        if padding > 0:
            new_bin_parts.append(b"\x00" * padding)
            new_bin_offset += padding

        uncompressed_offsets[bv_index] = new_bin_offset
        new_bin_parts.append(data)
        new_bin_offset += byte_length

    new_bin = b"".join(new_bin_parts)

    # Собираем fallback (несжатые данные сжатых BV)
    fallback_parts: List[bytes] = []
    fallback_offset = 0
    fallback_offsets: Dict[int, int] = {}

    for bv_index in sorted(compressed_info.keys()):
        bv = bufferViews[bv_index]
        byte_length = bv.get("byteLength", 0)
        byte_offset = bv.get("byteOffset", 0)
        data = bin_chunk[byte_offset:byte_offset + byte_length]

        padding = _align4(fallback_offset) - fallback_offset
        if padding > 0:
            fallback_parts.append(b"\x00" * padding)
            fallback_offset += padding

        fallback_offsets[bv_index] = fallback_offset
        fallback_parts.append(data)
        fallback_offset += byte_length

    fallback = b"".join(fallback_parts)

    # Обновляем JSON
    new_json = json.loads(json.dumps(json_dict))  # Глубокая копия

    # Обновляем buffers
    new_json["buffers"] = [
        {"byteLength": len(new_bin)},
        {"byteLength": len(fallback), "extensions": {"EXT_meshopt_compression": {"fallback": True}}}
    ]

    # Обновляем bufferViews
    new_bufferViews = []
    for bv_index, bv in enumerate(bufferViews):
        new_bv = dict(bv)  # Копия

        if bv_index in compressed_info:
            mode, stride, count, compressed = compressed_info[bv_index]

            # buffer: 1 (fallback)
            new_bv["buffer"] = 1
            # byteOffset = смещение во fallback
            new_bv["byteOffset"] = fallback_offsets[bv_index]
            # byteLength = исходный несжатый размер
            new_bv["byteLength"] = bufferViews[bv_index].get("byteLength", 0)
            # byteStride сохраняется (если был)
            if "byteStride" in bufferViews[bv_index]:
                new_bv["byteStride"] = bufferViews[bv_index]["byteStride"]
            else:
                new_bv.pop("byteStride", None)
            # target сохраняется
            if "target" in bufferViews[bv_index]:
                new_bv["target"] = bufferViews[bv_index]["target"]

            # extensions
            ext = {
                "buffer": 0,
                "byteOffset": compressed_offsets[bv_index],
                "byteLength": len(compressed),
                "byteStride": stride,
                "count": count,
                "mode": mode,
                "filter": "NONE"
            }
            new_bv["extensions"] = {"EXT_meshopt_compression": ext}
        else:
            # Несжатый
            new_bv["buffer"] = 0
            new_bv["byteOffset"] = uncompressed_offsets[bv_index]
            # byteLength, byteStride, target сохраняются как есть

        new_bufferViews.append(new_bv)

    new_json["bufferViews"] = new_bufferViews

    # Обновляем extensionsUsed и extensionsRequired
    if "extensionsUsed" not in new_json:
        new_json["extensionsUsed"] = []
    if "EXT_meshopt_compression" not in new_json["extensionsUsed"]:
        new_json["extensionsUsed"].append("EXT_meshopt_compression")

    if "extensionsRequired" not in new_json:
        new_json["extensionsRequired"] = []
    if "EXT_meshopt_compression" not in new_json["extensionsRequired"]:
        new_json["extensionsRequired"].append("EXT_meshopt_compression")

    # Сериализуем JSON
    json_bytes = json.dumps(new_json, separators=(",", ":")).encode("utf-8")

    # Выравнивание JSON до 4
    json_padding = _align4(len(json_bytes)) - len(json_bytes)
    if json_padding > 0:
        json_bytes += b" " * json_padding

    # Собираем выходной GLB
    out_parts: List[bytes] = []

    # Заголовок
    total_length = 12 + 8 + len(json_bytes) + 8 + len(new_bin)
    out_parts.append(struct.pack("<III", 0x46546C67, 2, total_length))

    # JSON чанк
    out_parts.append(struct.pack("<II", len(json_bytes), 0x4E4F534A))
    out_parts.append(json_bytes)

    # BIN чанк
    out_parts.append(struct.pack("<II", len(new_bin), 0x004E4942))
    out_parts.append(new_bin)

    out_data = b"".join(out_parts)
    after_size = len(out_data)

    # Проверка выигрыша
    if after_size >= before_size:
        raise NoGain(f"Сжатие не дало выигрыша: {before_size} -> {after_size}")

    # Записать выходной файл
    with open(dst, "wb") as f:
        f.write(out_data)

    return {
        "before": before_size,
        "after": after_size,
        "compressed": len(compressed_info),
        "total": len(bufferViews)
    }


def main():
    if len(sys.argv) < 3:
        print("Использование: python meshopt_glb.py IN.glb OUT.glb [--lib PATH]", file=sys.stderr)
        sys.exit(1)

    src = sys.argv[1]
    dst = sys.argv[2]
    lib_path = None

    if "--lib" in sys.argv:
        idx = sys.argv.index("--lib")
        if idx + 1 < len(sys.argv):
            lib_path = sys.argv[idx + 1]

    try:
        result = compress_glb(src, dst, lib_path)
        print(f"meshopt: {result['before']} -> {result['after']} байт, сжато bufferViews: {result['compressed']} из {result['total']}")
        sys.exit(0)
    except MeshoptUnavailable as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(3)
    except NoGain as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
