"""glbemit.py — запись GLB с EXT_mesh_gpu_instancing (+ квантованная версия)."""

from __future__ import annotations

import json
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _quat_to_float32(q: np.ndarray) -> np.ndarray:
    """Q (k,4) float64 -> float32."""
    return q.astype(np.float32)


def _align4(n: int) -> int:
    """Выровнять n до кратного 4."""
    return (n + 3) & ~3


def _quantize_positions(P: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Квантование позиций float32 (n,3) -> int16 (n,3), центр c, шаг s.
    Возвращает (q_int16, c, s).
    """
    P = P.astype(np.float64)
    lo = P.min(axis=0)
    hi = P.max(axis=0)
    c = (lo + hi) / 2.0
    ext = np.max(hi - lo)
    s = ext / 65534.0 if ext > 0 else 1.0
    q = np.round((P - c) / s).astype(np.int64)
    q = np.clip(q, -32767, 32767).astype(np.int16)
    return q, c, s


def _quantize_self_check(P: np.ndarray, q: np.ndarray, c: np.ndarray, s: float) -> float:
    """
    Самопроверка: max |q*s + c - P| <= 0.5*s*1.001 + 1e-9.
    Возвращает max_err.
    """
    recon = q.astype(np.float64) * s + c
    err = np.max(np.abs(recon - P.astype(np.float64)))
    bound = 0.5 * s * 1.001 + 1e-9
    if err > bound:
        raise RuntimeError("quantization bound violated")
    return float(err)


# ---------------------------------------------------------------------------
# Внутренняя функция построения
# ---------------------------------------------------------------------------

def _build(
    groups: Dict[Tuple[str, str, Tuple[float, float, float], float, str], Any],
    geom_reader: Callable[[int], Tuple[np.ndarray, np.ndarray]],
    root_name: str,
    quant: bool,
) -> Tuple[Dict[str, Any], List[np.ndarray], Optional[float]]:
    """
    Строит gltf_dict, список numpy-массивов в порядке bufferViews,
    и quant_max_err (None если quant=False).
    """
    # --- Собираем уникальные материалы ---
    mat_keys: List[Tuple[Tuple[float, float, float], float, bool]] = []
    mat_set: set = set()
    for key in groups:
        mk = (key[2], key[3], key[4] == "")  # (color_key, trans_key, без системы: в шаблоне отодвигается от камеры)
        if mk not in mat_set:
            mat_set.add(mk)
            mat_keys.append(mk)
    mat_keys.sort()

    materials: List[Dict[str, Any]] = []
    for color_key, trans_key, back in mat_keys:
        r, g, b = color_key
        mat: Dict[str, Any] = {
            "pbrMetallicRoughness": {
                "baseColorFactor": [r, g, b, 1.0 - trans_key],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8,
            },
            "doubleSided": True,
        }
        if trans_key > 0.01:
            mat["alphaMode"] = "BLEND"
        if back:
            mat["extras"] = {"back": 1}
        materials.append(mat)

    # --- Собираем уникальные geom_id ---
    geom_ids: List[int] = []
    geom_id_set: set = set()
    for key, grp in groups.items():
        for gi in grp.inst:
            if gi not in geom_id_set:
                geom_id_set.add(gi)
                geom_ids.append(gi)
    geom_ids.sort()

    # --- Кэш геометрии: для каждого geom_id читаем и квантуем (если нужно) ---
    # geom_cache[gi] = (verts_f32, inds, quant_info)
    # quant_info = (q_int16 (n,3), c (3,), s float) или None
    geom_cache: Dict[int, Tuple[np.ndarray, np.ndarray, Optional[Tuple[np.ndarray, np.ndarray, float]]]] = {}
    quant_max_err: Optional[float] = None

    for gi in geom_ids:
        verts, inds = geom_reader(gi)
        # inds: uint32 плоский; приводим к uint16 если vn < 65536
        vn = verts.shape[0]
        if vn < 65536:
            inds = inds.astype(np.uint16)
        else:
            inds = inds.astype(np.uint32)

        if quant:
            q, c, s = _quantize_positions(verts)
            err = _quantize_self_check(verts, q, c, s)
            if quant_max_err is None or err > quant_max_err:
                quant_max_err = err
            geom_cache[gi] = (verts, inds, (q, c, s))
        else:
            geom_cache[gi] = (verts, inds, None)

    # --- Кэш аксессоров геометрии ---
    # geom_acc[gi] = (pos_acc_idx, ind_acc_idx)
    geom_acc: Dict[int, Tuple[int, int]] = {}

    # --- Собираем все данные для записи ---
    # arrays: список numpy-массивов в порядке bufferViews
    arrays: List[np.ndarray] = []
    buffer_views: List[Dict[str, Any]] = []
    accessors: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []

    # --- Собираем уникальные (source_label, category) для структуры дерева ---
    source_categories: Dict[str, Dict[str, List[Tuple[Tuple[float, float, float], float, str, Any]]]] = {}
    for key, grp in groups.items():
        source_label, category, color_key, trans_key, sysname = key
        if source_label not in source_categories:
            source_categories[source_label] = {}
        if category not in source_categories[source_label]:
            source_categories[source_label][category] = []
        source_categories[source_label][category].append((color_key, trans_key, sysname, grp))

    # --- Функция добавления bufferView и accessor ---
    def _add_buffer_view(arr: np.ndarray, target: Optional[int] = None, byte_stride: Optional[int] = None) -> int:
        """Добавляет массив в arrays и создаёт bufferView. Возвращает индекс bufferView."""
        arrays.append(arr)
        bv: Dict[str, Any] = {"buffer": 0, "byteLength": arr.nbytes}
        if target is not None:
            bv["target"] = target
        if byte_stride is not None:
            bv["byteStride"] = byte_stride
        buffer_views.append(bv)
        return len(buffer_views) - 1

    def _add_accessor(bv_idx: int, component_type: int, type_str: str, count: int,
                      min_val: Optional[List[float]] = None, max_val: Optional[List[float]] = None) -> int:
        """Добавляет accessor. Возвращает индекс accessor."""
        acc: Dict[str, Any] = {
            "bufferView": bv_idx,
            "componentType": component_type,
            "type": type_str,
            "count": count,
        }
        if min_val is not None:
            acc["min"] = min_val
        if max_val is not None:
            acc["max"] = max_val
        accessors.append(acc)
        return len(accessors) - 1

    # --- Создаём аксессоры для геометрии (один раз на geom_id) ---
    for gi in geom_ids:
        verts, inds, quant_info = geom_cache[gi]
        vn = verts.shape[0]

        if quant:
            q, c, s = quant_info
            # Квантованные позиции: (n, 4) int16, byteStride=8
            # Формируем массив (n, 4) int16: x, y, z, padding
            q_arr = np.zeros((vn, 4), dtype=np.int16)
            q_arr[:, 0] = q[:, 0]
            q_arr[:, 1] = q[:, 1]
            q_arr[:, 2] = q[:, 2]
            # q_arr[:, 3] = 0 (padding)
            bv_idx = _add_buffer_view(q_arr, target=34962, byte_stride=8)
            # min/max — целые минимум/максимум q
            q_min = [int(q[:, 0].min()), int(q[:, 1].min()), int(q[:, 2].min())]
            q_max = [int(q[:, 0].max()), int(q[:, 1].max()), int(q[:, 2].max())]
            pos_acc = _add_accessor(bv_idx, 5122, "VEC3", vn, min_val=q_min, max_val=q_max)
        else:
            # float32 позиции
            bv_idx = _add_buffer_view(verts, target=34962)
            pos_min = [float(verts[:, 0].min()), float(verts[:, 1].min()), float(verts[:, 2].min())]
            pos_max = [float(verts[:, 0].max()), float(verts[:, 1].max()), float(verts[:, 2].max())]
            pos_acc = _add_accessor(bv_idx, 5126, "VEC3", vn, min_val=pos_min, max_val=pos_max)

        # Индексы
        if inds.dtype == np.uint16:
            ind_ct = 5123
        else:
            ind_ct = 5125
        bv_idx_ind = _add_buffer_view(inds, target=34963)
        ind_acc = _add_accessor(bv_idx_ind, ind_ct, "SCALAR", inds.shape[0])

        geom_acc[gi] = (pos_acc, ind_acc)

    # --- Создаём узлы и меши ---
    # Собираем все листовые узлы по категориям
    # Для каждой группы:
    #   - если verts не пуст: один узел с мешом "запечённой части"
    #   - для каждого geom_id из inst: узел-меш с локальной геометрией и EXT_mesh_gpu_instancing

    # Считаем, есть ли инстансинг
    has_instancing = any(len(grp.inst) > 0 for grp in groups.values())

    # --- Обработка групп ---
    # Для каждой категории внутри источника:
    #   - сортируем группы по (color_key, trans_key)
    #   - для k-й группы (с 1): mesh_name = sysname if sysname else category,
    #     а если sysname пуст и групп > 1: f"{category} #{k}"
    #   - листовые узлы:
    #     * если verts не пуст: узел с мешом
    #     * для каждого geom_id: узел с мешом и инстансингом
    #   - если sysname непуст: листовые узлы -> children узла sysname -> children категории
    #   - иначе: листовые узлы -> children категории

    # --- Создаём узлы источников и категорий ---
    # Сначала создадим все узлы источников и категорий, затем заполним children

    # Индексы узлов:
    # 0: корень
    # 1..: источники
    # далее: категории
    # далее: sysname узлы (если есть)
    # далее: листовые узлы

    # --- Создаём узел корня ---
    root_node: Dict[str, Any] = {"name": root_name, "children": []}
    nodes.append(root_node)

    # --- Создаём узлы источников ---
    source_node_indices: Dict[str, int] = {}
    for source_label in sorted(source_categories.keys()):
        node: Dict[str, Any] = {"name": source_label, "children": []}
        nodes.append(node)
        source_node_indices[source_label] = len(nodes) - 1
        root_node["children"].append(len(nodes) - 1)

    # --- Создаём узлы категорий ---
    category_node_indices: Dict[Tuple[str, str], int] = {}
    for source_label in sorted(source_categories.keys()):
        for category in sorted(source_categories[source_label].keys()):
            node: Dict[str, Any] = {"name": category, "children": []}
            nodes.append(node)
            category_node_indices[(source_label, category)] = len(nodes) - 1
            source_node_indices[source_label]  # уже есть
            # Добавляем в children источника
            src_idx = source_node_indices[source_label]
            nodes[src_idx]["children"].append(len(nodes) - 1)

    # --- Обработка групп внутри категорий ---
    for source_label in sorted(source_categories.keys()):
        for category in sorted(source_categories[source_label].keys()):
            cat_groups = source_categories[source_label][category]
            # Сортируем по (color_key, trans_key)
            cat_groups.sort(key=lambda x: (x[0], x[1]))
            n_groups = len(cat_groups)
            cat_node_idx = category_node_indices[(source_label, category)]

            for k_idx, (color_key, trans_key, sysname, grp) in enumerate(cat_groups):
                k = k_idx + 1  # с 1
                # mesh_name
                if sysname:
                    mesh_name = sysname
                else:
                    if n_groups > 1:
                        mesh_name = f"{category} #{k}"
                    else:
                        mesh_name = category

                # Индекс материала
                mat_idx = mat_keys.index((color_key, trans_key, sysname == ""))

                # --- Листовые узлы ---
                leaf_nodes: List[Dict[str, Any]] = []

                # 1) Запечённая часть (verts не пуст)
                if grp.verts.shape[0] > 0:
                    # Создаём меш
                    # Аксессоры для запечённой части
                    verts_baked = grp.verts
                    inds_baked = grp.inds
                    vn_baked = verts_baked.shape[0]

                    if quant:
                        q_baked, c_baked, s_baked = _quantize_positions(verts_baked)
                        err_baked = _quantize_self_check(verts_baked, q_baked, c_baked, s_baked)
                        if quant_max_err is None or err_baked > quant_max_err:
                            quant_max_err = err_baked
                        # Квантованные позиции
                        q_arr_baked = np.zeros((vn_baked, 4), dtype=np.int16)
                        q_arr_baked[:, 0] = q_baked[:, 0]
                        q_arr_baked[:, 1] = q_baked[:, 1]
                        q_arr_baked[:, 2] = q_baked[:, 2]
                        bv_idx_baked = _add_buffer_view(q_arr_baked, target=34962, byte_stride=8)
                        q_min_baked = [int(q_baked[:, 0].min()), int(q_baked[:, 1].min()), int(q_baked[:, 2].min())]
                        q_max_baked = [int(q_baked[:, 0].max()), int(q_baked[:, 1].max()), int(q_baked[:, 2].max())]
                        pos_acc_baked = _add_accessor(bv_idx_baked, 5122, "VEC3", vn_baked, min_val=q_min_baked, max_val=q_max_baked)
                    else:
                        bv_idx_baked = _add_buffer_view(verts_baked, target=34962)
                        pos_min_baked = [float(verts_baked[:, 0].min()), float(verts_baked[:, 1].min()), float(verts_baked[:, 2].min())]
                        pos_max_baked = [float(verts_baked[:, 0].max()), float(verts_baked[:, 1].max()), float(verts_baked[:, 2].max())]
                        pos_acc_baked = _add_accessor(bv_idx_baked, 5126, "VEC3", vn_baked, min_val=pos_min_baked, max_val=pos_max_baked)

                    # Индексы
                    if inds_baked is not None:
                        if inds_baked.dtype == np.uint16:
                            ind_ct_baked = 5123
                        else:
                            ind_ct_baked = 5125
                        bv_idx_ind_baked = _add_buffer_view(inds_baked, target=34963)
                        ind_acc_baked = _add_accessor(bv_idx_ind_baked, ind_ct_baked, "SCALAR", inds_baked.shape[0])
                    else:
                        ind_acc_baked = None

                    # Меш
                    primitive: Dict[str, Any] = {
                        "attributes": {"POSITION": pos_acc_baked},
                        "material": mat_idx,
                        "mode": 4,
                    }
                    if ind_acc_baked is not None:
                        primitive["indices"] = ind_acc_baked
                    mesh: Dict[str, Any] = {"name": mesh_name, "primitives": [primitive]}
                    meshes.append(mesh)
                    mesh_idx = len(meshes) - 1

                    # Узел
                    node: Dict[str, Any] = {"name": mesh_name, "mesh": mesh_idx}
                    if quant:
                        node["scale"] = [s_baked, s_baked, s_baked]
                        node["translation"] = [float(c_baked[0]), float(c_baked[1]), float(c_baked[2])]
                    leaf_nodes.append(node)

                # 2) Инстансинг для каждого geom_id
                for gi in sorted(grp.inst.keys()):
                    T, Q, S = grp.inst[gi]
                    k_inst = T.shape[0]

                    # Аксессоры для экземпляров
                    # TRANSLATION: float32 VEC3, count=k
                    T_f32 = T.astype(np.float32)
                    bv_T = _add_buffer_view(T_f32)  # без target
                    acc_T = _add_accessor(bv_T, 5126, "VEC3", k_inst)

                    # ROTATION: float32 VEC4, count=k
                    Q_f32 = Q.astype(np.float32)
                    bv_Q = _add_buffer_view(Q_f32)  # без target
                    acc_Q = _add_accessor(bv_Q, 5126, "VEC4", k_inst)

                    # SCALE: float32 VEC3, count=k
                    S_f32 = S.astype(np.float32)
                    bv_S = _add_buffer_view(S_f32)  # без target
                    acc_S = _add_accessor(bv_S, 5126, "VEC3", k_inst)

                    # Меш с локальной геометрией
                    pos_acc_gi, ind_acc_gi = geom_acc[gi]
                    primitive: Dict[str, Any] = {
                        "attributes": {"POSITION": pos_acc_gi},
                        "indices": ind_acc_gi,
                        "material": mat_idx,
                        "mode": 4,
                    }
                    mesh: Dict[str, Any] = {"name": mesh_name, "primitives": [primitive]}
                    meshes.append(mesh)
                    mesh_idx = len(meshes) - 1

                    # Узел с EXT_mesh_gpu_instancing
                    node: Dict[str, Any] = {"name": mesh_name, "mesh": mesh_idx}

                    # Если quant: сворачиваем декодирование в атрибуты экземпляров
                    if quant:
                        # Получаем c, s для geom_id
                        _, _, quant_info_gi = geom_cache[gi]
                        c_gi, s_gi = quant_info_gi[1], quant_info_gi[2]

                        # T' = T + R(Q)·(S∘c)
                        # S' = S·s (поэлементно)
                        # Q без изменений

                        # Вычисляем R(Q)·(S∘c) для каждого экземпляра
                        # Q: (k, 4) xyzw
                        # S: (k, 3)
                        # c: (3,)
                        # S∘c: (k, 3) = S * c[None, :]
                        Sc = S * c_gi[None, :]  # (k, 3)

                        # R(Q)·v для каждого экземпляра
                        # Q: (k, 4) = (x, y, z, w)
                        # Формула: R·v = (w² - x² - y² - z²)·v + 2(x·v)·x + 2(y·v)·y + 2(z·v)·z + 2w(x×v)
                        # Или через матрицу:
                        # R = [[1-2(y²+z²), 2(xy-wz), 2(xz+wy)],
                        #      [2(xy+wz), 1-2(x²+z²), 2(yz-wx)],
                        #      [2(xz-wy), 2(yz+wx), 1-2(x²+y²)]]

                        x = Q[:, 0]
                        y = Q[:, 1]
                        z = Q[:, 2]
                        w = Q[:, 3]

                        # R·Sc для каждого экземпляра
                        # R[i] · Sc[i]
                        # R[i] = [[1-2(y²+z²), 2(xy-wz), 2(xz+wy)],
                        #         [2(xy+wz), 1-2(x²+z²), 2(yz-wx)],
                        #         [2(xz-wy), 2(yz+wx), 1-2(x²+y²)]]
                        # Sc[i] = (Sc[i,0], Sc[i,1], Sc[i,2])

                        # Вычисляем R·Sc покомпонентно
                        # R00 = 1 - 2*(y² + z²)
                        # R01 = 2*(x*y - w*z)
                        # R02 = 2*(x*z + w*y)
                        # R10 = 2*(x*y + w*z)
                        # R11 = 1 - 2*(x² + z²)
                        # R12 = 2*(y*z - w*x)
                        # R20 = 2*(x*z - w*y)
                        # R21 = 2*(y*z + w*x)
                        # R22 = 1 - 2*(x² + y²)

                        R00 = 1.0 - 2.0 * (y**2 + z**2)
                        R01 = 2.0 * (x * y - w * z)
                        R02 = 2.0 * (x * z + w * y)
                        R10 = 2.0 * (x * y + w * z)
                        R11 = 1.0 - 2.0 * (x**2 + z**2)
                        R12 = 2.0 * (y * z - w * x)
                        R20 = 2.0 * (x * z - w * y)
                        R21 = 2.0 * (y * z + w * x)
                        R22 = 1.0 - 2.0 * (x**2 + y**2)

                        # R·Sc:
                        # (R00*Sc0 + R01*Sc1 + R02*Sc2,
                        #  R10*Sc0 + R11*Sc1 + R12*Sc2,
                        #  R20*Sc0 + R21*Sc1 + R22*Sc2)
                        RSc0 = R00 * Sc[:, 0] + R01 * Sc[:, 1] + R02 * Sc[:, 2]
                        RSc1 = R10 * Sc[:, 0] + R11 * Sc[:, 1] + R12 * Sc[:, 2]
                        RSc2 = R20 * Sc[:, 0] + R21 * Sc[:, 1] + R22 * Sc[:, 2]

                        # T' = T + R·Sc
                        T_prime = T + np.column_stack([RSc0, RSc1, RSc2])
                        # S' = S * s
                        S_prime = S * s_gi

                        # Перезаписываем аксессоры для экземпляров
                        # TRANSLATION: T_prime
                        T_prime_f32 = T_prime.astype(np.float32)
                        # Заменяем массив в arrays
                        # Найдём индекс bv_T в arrays
                        # bv_T — это индекс в buffer_views, но arrays — это список массивов
                        # Мы добавили T_f32 в arrays, теперь заменяем
                        # Найдём позицию: bv_T — это индекс bufferView, но arrays — это список
                        # Мы добавили T_f32 как последний элемент arrays, но потом добавили Q_f32 и S_f32
                        # Нужно найти индекс T_f32 в arrays
                        # Проще: пересоздадим аксессоры
                        # Но мы уже создали acc_T, acc_Q, acc_S
                        # Заменим массивы в arrays
                        # Найдём индексы: bv_T, bv_Q, bv_S — это индексы в buffer_views
                        # Но arrays — это список, и мы добавили T_f32, Q_f32, S_f32 последовательно
                        # Индекс T_f32 в arrays = len(arrays) - 3 (после добавления S_f32)
                        # Но мы не знаем точно, потому что могли добавить другие массивы
                        # Лучше: пересоздадим аксессоры с новыми массивами

                        # Удалим старые аксессоры и bufferViews? Нет, проще заменить массивы
                        # Найдём индексы в arrays
                        # T_f32 был добавлен как arrays[-3] (после добавления S_f32)
                        # Но это не надёжно. Лучше пересоздать.

                        # Пересоздадим аксессоры для экземпляров
                        # Удалим старые acc_T, acc_Q, acc_S из accessors
                        # И bv_T, bv_Q, bv_S из buffer_views
                        # И T_f32, Q_f32, S_f32 из arrays

                        # Это сложно. Лучше: не создавать аксессоры заранее, а создавать их после вычисления T', S'
                        # Но мы уже создали. Пересоздадим.

                        # Удалим последние 3 аксессора
                        accessors.pop()  # acc_S
                        accessors.pop()  # acc_Q
                        accessors.pop()  # acc_T
                        # Удалим последние 3 bufferViews
                        buffer_views.pop()  # bv_S
                        buffer_views.pop()  # bv_Q
                        buffer_views.pop()  # bv_T
                        # Удалим последние 3 массива
                        arrays.pop()  # S_f32
                        arrays.pop()  # Q_f32
                        arrays.pop()  # T_f32

                        # Создаём новые аксессоры
                        T_prime_f32 = T_prime.astype(np.float32)
                        bv_T_new = _add_buffer_view(T_prime_f32)
                        acc_T_new = _add_accessor(bv_T_new, 5126, "VEC3", k_inst)

                        Q_f32_new = Q.astype(np.float32)
                        bv_Q_new = _add_buffer_view(Q_f32_new)
                        acc_Q_new = _add_accessor(bv_Q_new, 5126, "VEC4", k_inst)

                        S_prime_f32 = S_prime.astype(np.float32)
                        bv_S_new = _add_buffer_view(S_prime_f32)
                        acc_S_new = _add_accessor(bv_S_new, 5126, "VEC3", k_inst)

                        node["extensions"] = {
                            "EXT_mesh_gpu_instancing": {
                                "attributes": {
                                    "TRANSLATION": acc_T_new,
                                    "ROTATION": acc_Q_new,
                                    "SCALE": acc_S_new,
                                }
                            }
                        }
                    else:
                        node["extensions"] = {
                            "EXT_mesh_gpu_instancing": {
                                "attributes": {
                                    "TRANSLATION": acc_T,
                                    "ROTATION": acc_Q,
                                    "SCALE": acc_S,
                                }
                            }
                        }

                    leaf_nodes.append(node)

                # --- Добавляем листовые узлы ---
                if sysname:
                    # Создаём узел sysname
                    sysname_node: Dict[str, Any] = {"name": sysname, "children": []}
                    nodes.append(sysname_node)
                    sysname_node_idx = len(nodes) - 1
                    for leaf in leaf_nodes:
                        nodes.append(leaf)
                        sysname_node["children"].append(len(nodes) - 1)
                    # Добавляем sysname_node в children категории
                    nodes[cat_node_idx]["children"].append(sysname_node_idx)
                else:
                    for leaf in leaf_nodes:
                        nodes.append(leaf)
                        nodes[cat_node_idx]["children"].append(len(nodes) - 1)

    # --- Формируем gltf_dict ---
    gltf: Dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "glbemit"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": 0}],  # Заполним позже
    }

    # --- Расширения ---
    extensions_used: List[str] = []
    extensions_required: List[str] = []
    if has_instancing:
        extensions_used.append("EXT_mesh_gpu_instancing")
        extensions_required.append("EXT_mesh_gpu_instancing")
    if quant:
        extensions_used.append("KHR_mesh_quantization")
        extensions_required.append("KHR_mesh_quantization")
    if extensions_used:
        gltf["extensionsUsed"] = extensions_used
        gltf["extensionsRequired"] = extensions_required

    return gltf, arrays, quant_max_err


# ---------------------------------------------------------------------------
# Функция записи GLB
# ---------------------------------------------------------------------------

def _write_glb(path: str, gltf: Dict[str, Any], arrays: List[np.ndarray]) -> int:
    """
    Записывает GLB файл. Возвращает размер файла.
    """
    # --- Вычисляем byteOffset и byteLength для каждого bufferView ---
    # Выравнивание по 4 байта
    total_bin_size = 0
    bv_offsets: List[int] = []
    bv_lengths: List[int] = []

    for arr in arrays:
        offset = total_bin_size
        length = arr.nbytes
        bv_offsets.append(offset)
        bv_lengths.append(length)
        total_bin_size += length
        # Выравнивание до 4
        total_bin_size = _align4(total_bin_size)

    # --- Заполняем bufferViews ---
    for i, bv in enumerate(gltf["bufferViews"]):
        bv["byteOffset"] = bv_offsets[i]
        bv["byteLength"] = bv_lengths[i]

    # --- Заполняем buffers ---
    gltf["buffers"][0]["byteLength"] = total_bin_size

    # --- Сериализуем JSON ---
    json_str = json.dumps(gltf, separators=(',', ':'), ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')
    # Добить пробелами до 4
    json_padded_len = _align4(len(json_bytes))
    json_padded = json_bytes + b' ' * (json_padded_len - len(json_bytes))

    # --- BIN чанк ---
    # Данные уже выровнены, но добить нулями до 4
    bin_padded_len = _align4(total_bin_size)
    # total_bin_size уже выровнен, но на всякий случай
    bin_padding = b'\x00' * (bin_padded_len - total_bin_size)

    # --- Общая длина ---
    # Заголовок: 12 байт
    # JSON чанк: 8 (заголовок) + json_padded_len
    # BIN чанк: 8 (заголовок) + bin_padded_len
    total_size = 12 + 8 + json_padded_len + 8 + bin_padded_len

    # --- Запись ---
    with open(path, 'wb') as f:
        # Заголовок GLB
        f.write(struct.pack('<III', 0x46546C67, 2, total_size))

        # JSON чанк
        f.write(struct.pack('<II', json_padded_len, 0x4E4F534A))  # length, type (JSON)
        f.write(json_padded)

        # BIN чанк
        f.write(struct.pack('<II', bin_padded_len, 0x004E4942))  # length, type (BIN)
        # Записываем данные по каждому bufferView
        for arr in arrays:
            f.write(arr.tobytes())
            f.write(b'\x00' * (_align4(arr.nbytes) - arr.nbytes))
        # Паддинг
        if bin_padding:
            f.write(bin_padding)

    return total_size


# ---------------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------------

def emit(
    groups: Dict[Tuple[str, str, Tuple[float, float, float], float, str], Any],
    geom_reader: Callable[[int], Tuple[np.ndarray, np.ndarray]],
    root_name: str,
    out_f32: str,
    out_quant: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Записывает GLB файл(ы) с EXT_mesh_gpu_instancing.
    """
    # --- Float32 версия ---
    gltf_f32, arrays_f32, _ = _build(groups, geom_reader, root_name, quant=False)
    glb_size = _write_glb(out_f32, gltf_f32, arrays_f32)
    del arrays_f32

    # --- Квантованная версия ---
    quant_size: Optional[int] = None
    quant_max_err: Optional[float] = None
    if out_quant is not None:
        gltf_quant, arrays_quant, quant_max_err = _build(groups, geom_reader, root_name, quant=True)
        quant_size = _write_glb(out_quant, gltf_quant, arrays_quant)

    # --- Считаем меши и инстансированные узлы ---
    meshes_count = len(gltf_f32["meshes"])
    instanced_nodes = sum(
        1 for node in gltf_f32["nodes"]
        if "extensions" in node and "EXT_mesh_gpu_instancing" in node["extensions"]
    )

    return {
        "glb_size": glb_size,
        "quant_size": quant_size,
        "quant_max_err": quant_max_err,
        "meshes": meshes_count,
        "instanced_nodes": instanced_nodes,
    }
