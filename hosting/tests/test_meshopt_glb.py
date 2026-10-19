import ctypes
import json as _jsonmod
import os
import struct
import sys
import subprocess
import numpy as np
import pytest

# Добавляем путь к скриптам в sys.path
nwd_scripts = os.environ.get('NWD_SCRIPTS', '/app/scripts/nwd')
sys.path.insert(0, nwd_scripts)

try:
    from meshopt_glb import compress_glb, MeshoptUnavailable
except ImportError:
    pytest.skip("Не удалось импортировать meshopt_glb", allow_module_level=True)

# Путь к библиотеке
meshopt_lib = os.environ.get('MESHOPT_LIB', '/usr/local/lib/libmeshopt.so')
if not os.path.exists(meshopt_lib):
    pytest.skip(f"Библиотека {meshopt_lib} не найдена", allow_module_level=True)

# Фикстура: синтетический GLB
def make_glb(path):
    # Генерация данных с детерминированным RNG
    rng = np.random.default_rng(7)
    
    # Меш A (запечённый): 2000 вершин, 40x50 ячеек
    vertices_a = np.zeros((2000, 4), dtype=np.int16)  # 3 координаты + паддинг до 8 байт
    grid_x = np.arange(40)
    grid_y = np.arange(50)
    x, y = np.meshgrid(grid_x, grid_y, indexing='ij')
    vertices_a[:, 0] = x.flatten().astype(np.int16)
    vertices_a[:, 1] = y.flatten().astype(np.int16)
    # Небольшие целые добавки
    vertices_a[:, :3] += rng.integers(-2, 3, size=(2000, 3), dtype=np.int16)

    indices_a = []
    for i in range(39):
        for j in range(49):
            idx = i * 50 + j
            # Два треугольника на ячейку
            indices_a.extend([idx, idx+1, idx+50])
            indices_a.extend([idx+1, idx+51, idx+50])
    indices_a = np.array(indices_a, dtype=np.uint32)

    # Меш B (инстансный): 300 вершин, 15x20 ячеек
    vertices_b = np.zeros((300, 4), dtype=np.int16)
    grid_x = np.arange(15)
    grid_y = np.arange(20)
    x, y = np.meshgrid(grid_x, grid_y, indexing='ij')
    vertices_b[:, 0] = x.flatten().astype(np.int16)
    vertices_b[:, 1] = y.flatten().astype(np.int16)
    vertices_b[:, :3] += rng.integers(-2, 3, size=(300, 3), dtype=np.int16)

    indices_b = []
    for i in range(14):
        for j in range(19):
            idx = i * 20 + j
            indices_b.extend([idx, idx+1, idx+20])
            indices_b.extend([idx+1, idx+21, idx+20])
    indices_b = np.array(indices_b, dtype=np.uint16)

    # Инстансы для меша B
    num_instances = 12
    translations = rng.random((num_instances, 3)).astype(np.float32)
    rotations = rng.random((num_instances, 4)).astype(np.float32)
    scales = rng.random((num_instances, 3)).astype(np.float32)

    # Сборка JSON и BIN данных
    json_data = {
        "asset": {"version": "2.0"},
        "buffers": [
            {"byteLength": 0}  # Заполнится позже
        ],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]},
            {"primitives": [{"attributes": {"POSITION": 2}, "indices": 3}]}
        ],
        "bufferViews": [
            # POSITION A (8 байт на вершину)
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": vertices_a.nbytes,
                "byteStride": 8,
                "target": 34962
            },
            # INDICES A (4 байта на индекс)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes,
                "byteLength": indices_a.nbytes,
                "target": 34963
            },
            # POSITION B (8 байт на вершину)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes + indices_a.nbytes,
                "byteLength": vertices_b.nbytes,
                "byteStride": 8,
                "target": 34962
            },
            # INDICES B (2 байта на индекс)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes + indices_a.nbytes + vertices_b.nbytes,
                "byteLength": indices_b.nbytes,
                "target": 34963
            },
            # TRANSLATION (12 байт на инстанс)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes + indices_a.nbytes + vertices_b.nbytes + indices_b.nbytes,
                "byteLength": translations.nbytes,
                "byteStride": 12,
                "target": 34962
            },
            # ROTATION (16 байт на инстанс)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes + indices_a.nbytes + vertices_b.nbytes + indices_b.nbytes + translations.nbytes,
                "byteLength": rotations.nbytes,
                "byteStride": 16,
                "target": 34962
            },
            # SCALE (12 байт на инстанс)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes + indices_a.nbytes + vertices_b.nbytes + indices_b.nbytes + translations.nbytes + rotations.nbytes,
                "byteLength": scales.nbytes,
                "byteStride": 12,
                "target": 34962
            },
            # Лишний bufferView (два accessor'а)
            {
                "buffer": 0,
                "byteOffset": vertices_a.nbytes + indices_a.nbytes + vertices_b.nbytes + indices_b.nbytes + translations.nbytes + rotations.nbytes + scales.nbytes,
                "byteLength": 16  # 4 float32
            }
        ],
        "accessors": [
            # POSITION A
            {
                "bufferView": 0,
                "componentType": 5122,  # int16
                "count": 2000,
                "type": "VEC3",
                "min": [int(vertices_a[:, k].min()) for k in range(3)],
                "max": [int(vertices_a[:, k].max()) for k in range(3)]
            },
            # INDICES A
            {
                "bufferView": 1,
                "componentType": 5125,  # uint32
                "count": len(indices_a),
                "type": "SCALAR"
            },
            # POSITION B
            {
                "bufferView": 2,
                "componentType": 5122,  # int16
                "count": 300,
                "type": "VEC3",
                "min": [int(vertices_b[:, k].min()) for k in range(3)],
                "max": [int(vertices_b[:, k].max()) for k in range(3)]
            },
            # INDICES B
            {
                "bufferView": 3,
                "componentType": 5123,  # uint16
                "count": len(indices_b),
                "type": "SCALAR"
            },
            # TRANSLATION
            {
                "bufferView": 4,
                "componentType": 5126,  # float32
                "count": num_instances,
                "type": "VEC3"
            },
            # ROTATION
            {
                "bufferView": 5,
                "componentType": 5126,  # float32
                "count": num_instances,
                "type": "VEC4"
            },
            # SCALE
            {
                "bufferView": 6,
                "componentType": 5126,  # float32
                "count": num_instances,
                "type": "VEC3"
            },
            # Лишний bufferView (два accessor'а)
            {
                "bufferView": 7,
                "componentType": 5126,  # float32
                "count": 2,
                "type": "SCALAR"
            },
            {
                "bufferView": 7,
                "byteOffset": 8,
                "componentType": 5126,  # float32
                "count": 2,
                "type": "SCALAR"
            }
        ],
        "nodes": [
            {"mesh": 0},
            {
                "mesh": 1,
                "extensions": {
                    "EXT_mesh_gpu_instancing": {
                        "attributes": {
                            "TRANSLATION": 4,
                            "ROTATION": 5,
                            "SCALE": 6
                        }
                    }
                }
            }
        ]
    }

    # Сборка BIN данных
    bin_data = b""
    bin_data += vertices_a.tobytes()
    bin_data += indices_a.tobytes()
    bin_data += vertices_b.tobytes()
    bin_data += indices_b.tobytes()
    bin_data += translations.tobytes()
    bin_data += rotations.tobytes()
    bin_data += scales.tobytes()
    bin_data += np.zeros(4, dtype=np.float32).tobytes()

    # Заполнение byteLength
    json_data["buffers"][0]["byteLength"] = len(bin_data)

    # Сохранение GLB
    import json as _json
    import struct as _struct
    jb = _json.dumps(json_data).encode('utf-8')
    jb += b' ' * (-len(jb) % 4)
    bb = bin_data + b'\x00' * (-len(bin_data) % 4)
    total = 12 + 8 + len(jb) + 8 + len(bb)
    with open(path, 'wb') as f:
        f.write(_struct.pack('<III', 0x46546C67, 2, total))
        f.write(_struct.pack('<II', len(jb), 0x4E4F534A) + jb)
        f.write(_struct.pack('<II', len(bb), 0x004E4942) + bb)

    return json_data, bin_data

# Функция чтения GLB
def read_glb(path):
    with open(path, 'rb') as f:
        data = f.read()
    
    # Разбор чанков по спецификации: длина, тип, данные
    pos = 12
    json_dict, bin_bytes = None, b""
    while pos < len(data):
        clen, ctype = struct.unpack_from("<II", data, pos)
        body = data[pos + 8:pos + 8 + clen]
        if ctype == 0x4E4F534A:
            json_dict = _jsonmod.loads(body.decode('utf-8'))
        elif ctype == 0x004E4942:
            bin_bytes = bytes(body)
        pos += 8 + clen
    assert json_dict is not None
    return json_dict, bin_bytes

# Функция декодирования bufferView
def decode_view(json, bin, bv_index):
    # Загрузка библиотеки
    lib = ctypes.CDLL(meshopt_lib)
    lib.meshopt_decodeVertexBuffer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
    lib.meshopt_decodeVertexBuffer.restype = ctypes.c_int
    lib.meshopt_decodeIndexBuffer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
    lib.meshopt_decodeIndexBuffer.restype = ctypes.c_int

    buffer_view = json["bufferViews"][bv_index]
    ext = buffer_view.get("extensions", {}).get("EXT_meshopt_compression")
    if not ext:
        raise ValueError(f"BufferView {bv_index} не сжат")

    # Получаем данные из BIN
    offset = ext["byteOffset"]
    length = ext["byteLength"]
    buf_data = (ctypes.c_ubyte * length).from_buffer_copy(bin[offset:offset + length])

    count = ext["count"]
    stride = ext.get("byteStride", 0)
    mode = ext["mode"]

    if mode == "TRIANGLES":
        dest = (ctypes.c_uint8 * (count * stride))()
        result = lib.meshopt_decodeIndexBuffer(dest, count, stride, buf_data, len(buf_data))
        if result != 0:
            raise RuntimeError("Ошибка декодирования индексов")
        return bytes(dest)
    else:  # ATTRIBUTES
        dest = (ctypes.c_uint8 * (count * stride))()
        result = lib.meshopt_decodeVertexBuffer(dest, count, stride, buf_data, len(buf_data))
        if result != 0:
            raise RuntimeError("Ошибка декодирования вершин")
        return bytes(dest)

# Тесты
def test_roundtrip(tmp_path):
    src = tmp_path / "src.glb"
    dst = tmp_path / "dst.glb"
    make_glb(src)
    verify(src, dst, compress_glb(str(src), str(dst)))


def verify(src, dst, result):
    """Все проверки результата сжатия (их же красят мутационные тесты)."""
    assert result["before"] > result["after"]
    assert result["compressed"] == 7  # POSITION и индексы двух мешей (4) + TRANSLATION, ROTATION, SCALE
    assert result["total"] == 8  # bufferViews в фикстуре
    
    # Проверка JSON
    json_dst, bin_dst = read_glb(dst)
    assert "EXT_meshopt_compression" in json_dst.get("extensionsUsed", [])
    assert "EXT_meshopt_compression" in json_dst.get("extensionsRequired", [])
    
    # Проверка bufferViews и accessors
    assert len(json_dst["bufferViews"]) == 8
    assert len(json_dst["accessors"]) == 9
    
    # Проверка buffers
    assert len(json_dst["buffers"]) == 2
    assert json_dst["buffers"][1].get("extensions", {}).get("EXT_meshopt_compression", {}).get("fallback") is True
    assert "uri" not in json_dst["buffers"][1]
    
    # Побайтное сравнение: сжатые после декодирования и несжатые — с исходными
    assert check_roundtrip(src, dst) == []
    modes = {i: bv["extensions"]["EXT_meshopt_compression"]["mode"]
             for i, bv in enumerate(json_dst["bufferViews"]) if "extensions" in bv}
    assert sorted(modes) == [0, 1, 2, 3, 4, 5, 6]  # bufferView 7 (два accessors) не сжимается
    assert [modes[i] for i in (1, 3)] == ["TRIANGLES", "TRIANGLES"]
    assert [json_dst["bufferViews"][i]["extensions"]["EXT_meshopt_compression"]["byteStride"] for i in (1, 3)] == [4, 2]

def _must_break(src, dst):
    """Мутантное сжатие обязано либо упасть, либо не пройти verify."""
    try:
        verify(src, dst, compress_glb(str(src), str(dst)))
    except Exception:
        return True
    return False


def test_mutation_truncated_stream(tmp_path, monkeypatch):
    import meshopt_glb
    src, dst = tmp_path / "src.glb", tmp_path / "dst.glb"
    make_glb(src)
    original = meshopt_glb._encode_attributes
    monkeypatch.setattr(meshopt_glb, "_encode_attributes", lambda lib, data, count, stride: original(lib, data, count, stride)[:-1])
    assert _must_break(src, dst)


def test_mutation_wrong_mode(tmp_path, monkeypatch):
    import meshopt_glb
    src, dst = tmp_path / "src.glb", tmp_path / "dst.glb"
    make_glb(src)
    original = meshopt_glb._determine_usage_full

    def wrong(json_dict, bv_index):
        usage = original(json_dict, bv_index)
        return ("ATTRIBUTES",) + tuple(usage[1:]) if usage and usage[0] == "TRIANGLES" else usage

    monkeypatch.setattr(meshopt_glb, "_determine_usage_full", wrong)
    assert _must_break(src, dst)


def test_no_lib(tmp_path):
    src = tmp_path / "src.glb"
    dst = tmp_path / "dst.glb"

    json_data, bin_data = make_glb(src)

    try:
        compress_glb(str(src), str(dst), lib_path="/nonexistent/libmeshopt.so")
        assert False, "Должно было бросить исключение"
    except MeshoptUnavailable:
        pass  # Ожидается
    except Exception as e:
        assert False, f"Неправильное исключение: {e}"

    assert not os.path.exists(dst)

def test_cli_exit_codes(tmp_path):
    src, dst = tmp_path / "src.glb", tmp_path / "dst.glb"
    make_glb(src)
    script = os.path.join(nwd_scripts, "meshopt_glb.py")
    ok = subprocess.run([sys.executable, script, str(src), str(dst)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert os.path.exists(dst)
    bad = subprocess.run([sys.executable, script, str(src), str(tmp_path / "x.glb"), "--lib", "/nonexistent/libmeshopt.so"],
                         capture_output=True, text=True)
    assert bad.returncode == 3, bad.stderr


def _canon_tris(raw, width):
    """Индексы -> список треугольников, каждый повёрнут так, чтобы начинался с наименьшего индекса."""
    idx = np.frombuffer(raw, dtype=np.uint16 if width == 2 else np.uint32).reshape(-1, 3)
    out = []
    for t in idx.tolist():
        k = t.index(min(t))
        out.append(tuple(t[k:] + t[:k]))
    return out


def check_roundtrip(src_path, dst_path):
    json_src, bin_src = read_glb(src_path)
    json_dst, bin_dst = read_glb(dst_path)

    # Проверка соответствия данных
    discrepancies = []
    
    for i, bv in enumerate(json_src["bufferViews"]):
        src_bytes = bin_src[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        bv_dst = json_dst["bufferViews"][i]
        try:
            if "EXT_meshopt_compression" in bv_dst.get("extensions", {}):
                got = decode_view(json_dst, bin_dst, i)
            else:
                got = bin_dst[bv_dst.get("byteOffset", 0):bv_dst.get("byteOffset", 0) + bv_dst["byteLength"]]
        except Exception as e:
            discrepancies.append(f"BufferView {i}: ошибка декодирования: {e}")
            continue
        mode = bv_dst.get("extensions", {}).get("EXT_meshopt_compression", {}).get("mode")
        if mode == "TRIANGLES":
            # кодек индексов сохраняет порядок треугольников, но может вращать вершины внутри треугольника
            width = bv_dst["extensions"]["EXT_meshopt_compression"]["byteStride"]
            got, src_bytes = _canon_tris(got, width), _canon_tris(src_bytes, width)
        if got != src_bytes:
            discrepancies.append(f"BufferView {i} не совпадает")

    return discrepancies
