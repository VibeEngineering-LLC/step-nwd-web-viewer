"""Модуль nwdscene: потоковое чтение дампа nwd_dump без json.load."""

import os
import re
import json
import array
import numpy as np

# Регулярка для быстрого извлечения path/s/f/geom из строки экземпляра
_INST_RE = re.compile(
    r'^\{"path":(-?\d+),"s":(-?\d+),"f":(-?\d+),"geom":(-?\d+),"m":\['
)


def load_scene(dump_dir):
    """Читает scene.json и возвращает объект Scene."""
    scene_path = os.path.join(dump_dir, "scene.json")
    geoms_path = os.path.join(dump_dir, "geoms.bin")

    with open(scene_path, "rb") as f:
        f.seek(0, 2)
        file_size = f.tell()

        # --- Шаг 1: найти маркер "],"systems":[" в хвосте файла ---
        marker = b'],"systems":['
        W = 16 * 1024 * 1024
        while True:
            W = min(W, file_size)
            f.seek(file_size - W)
            window = f.read(W)
            idx = window.rfind(marker)
            if idx != -1 and (W == file_size or window.rfind(b'\n', 0, idx) != -1):
                pos = file_size - W + idx
                break
            if W == file_size:
                raise ValueError("Маркер систем не найден в файле")
            W *= 2

        # --- Шаг 2: разобрать хвост (systems, geoms, overrides, stats) ---
        f.seek(pos)
        tail_bytes = f.read()
        tail = tail_bytes.decode('utf-8')
        # Склеиваем в валидный JSON
        doc_str = '{"models":[{"inst_end":[' + tail
        doc = json.loads(doc_str)
        model = doc["models"][0]

        # Проверка stats
        stats = doc.get("stats", {})
        if stats.get("models", 1) != 1:
            raise ValueError("многомодельный дамп не поддерживается")

        systems = model.get("systems", [])
        geoms = model.get("geoms", [])
        overrides = doc.get("overrides", [])

        # Заполняем массивы геометрий
        n_geoms = len(geoms)
        gtype = np.zeros(n_geoms, dtype=np.int64)
        gvo = np.full(n_geoms, -1, dtype=np.int64)
        gvn = np.zeros(n_geoms, dtype=np.int64)
        gio = np.full(n_geoms, -1, dtype=np.int64)
        gin = np.zeros(n_geoms, dtype=np.int64)

        for i, g in enumerate(geoms):
            gtype[i] = g.get("type", 0)
            gvo[i] = g.get("vo", -1)
            gvn[i] = g.get("vn", 0)
            gio[i] = g.get("io", -1)
            gin[i] = g.get("in", 0)

        # --- Шаг 3: читать голову файла построчно ---
        f.seek(0)

        # Состояние
        name = ""
        mpu = 1.0
        node_ids = array.array('i')
        node_parents = array.array('i')
        node_names = []
        intern_dict = {}
        order = array.array('i')

        # Для экземпляров
        inst_paths = array.array('i')
        inst_sys = array.array('i')
        inst_geom = array.array('i')
        inst_midx = array.array('i')
        inst_m_flat = array.array('d')
        inst_c_flat = array.array('f')
        inst_t_flat = array.array('f')

        phase = 'header'  # 'header', 'nodes', 'instances'
        line_start = 0
        current_line_start = 0

        # Прочитаем первую строку (заголовок)
        first_line = f.readline()
        if not first_line:
            raise ValueError("Пустой файл")

        # Обработка заголовка
        line_str = first_line.decode('utf-8').rstrip('\n')
        idx_nodes = line_str.find('"nodes":[')
        if idx_nodes == -1:
            raise ValueError("Не найдено начало списка узлов")

        # Извлекаем имя и mpu
        # Строка выглядит как: {"models":[{"name":"...","meters_per_unit":...,"linear_units":...,"nodes":[
        # Нам нужно разобрать часть до "nodes":[
        hdr_part = line_str[:idx_nodes + len('"nodes":[')]
        # Добавляем закрывающие скобки для валидного JSON
        hdr_json = hdr_part + ']}]}'
        try:
            hdr_doc = json.loads(hdr_json)
            hdr_model = hdr_doc["models"][0]
            name = hdr_model.get("name", "")
            mpu_val = hdr_model.get("meters_per_unit", 1.0)
            if isinstance(mpu_val, (int, float)) and mpu_val > 0:
                mpu = float(mpu_val)
            else:
                mpu = 1.0
        except json.JSONDecodeError:
            # Если не удалось разобрать, пробуем извлечь вручную
            # Но по спецификации это должно работать
            pass

        # Определяем, есть ли узлы
        # Если после "nodes":[ сразу идет "],"instances":[", значит узлов нет
        rest_after_nodes = line_str[idx_nodes + len('"nodes":['):]
        if rest_after_nodes.startswith('],"instances":['):
            phase = 'instances'
        else:
            phase = 'nodes'

        # Читаем остальные строки
        while True:
            raw_line = f.readline()
            if not raw_line:
                break

            # Проверяем, не перешли ли мы за pos
            # pos - это абсолютное смещение маркера ']'
            # Если текущая строка начинается до pos, но заканчивается после,
            # нам нужно обрезать её до pos.
            # Однако, мы читаем построчно. Если строка содержит маркер,
            # она будет обрезана.
            
            # Определяем, является ли эта строка той, что содержит pos
            # line_start - смещение начала текущей строки в файле
            # Мы не отслеживали line_start явно, но можем вычислить:
            # f.tell() после readline - это конец строки.
            # Но проще: если мы в фазе instances и строка содержит '],"systems":[',
            # то это последняя строка экземпляров.
            
            # Для корректности, давайте отслеживать смещение.
            # Но readline() не возвращает смещение.
            # Используем f.tell() до и после.
            
            # Перечитаем логику:
            # Мы уже прочитали первую строку.
            # Теперь читаем следующие.
            
            # Если мы в фазе узлов:
            if phase == 'nodes':
                line_str = raw_line.decode('utf-8').rstrip('\n')
                
                # Проверяем, является ли это последней строкой узлов
                if line_str.endswith('],"instances":['):
                    # Обрезаем хвост
                    line_str = line_str[:-len('],"instances":[')]
                    # Убираем запятую в конце, если есть
                    if line_str.endswith(','):
                        line_str = line_str[:-1]
                    
                    # Разбираем узел
                    node = json.loads(line_str)
                    nid = node.get("id", 0)
                    parent = node.get("parent", -1)
                    nname = node.get("name", "")
                    
                    node_ids.append(nid)
                    node_parents.append(parent)
                    node_names.append(intern_dict.setdefault(nname, nname))
                    order.append(nid)
                    
                    # Переходим к фазе экземпляров
                    phase = 'instances'
                    continue
                else:
                    # Обычная строка узла
                    if line_str.endswith(','):
                        line_str = line_str[:-1]
                    
                    node = json.loads(line_str)
                    nid = node.get("id", 0)
                    parent = node.get("parent", -1)
                    nname = node.get("name", "")
                    
                    node_ids.append(nid)
                    node_parents.append(parent)
                    node_names.append(intern_dict.setdefault(nname, nname))
                    order.append(nid)
                    continue

            elif phase == 'instances':
                line_str = raw_line.decode('utf-8').rstrip('\n')
                
                # Проверяем, является ли это последней строкой экземпляров
                # Она должна заканчиваться на '],"systems":[' или содержать это
                if '],"systems":[' in line_str:
                    # Обрезаем до маркера
                    idx_marker = line_str.find('],"systems":[')
                    line_str = line_str[:idx_marker]
                    # Убираем запятую в конце, если есть
                    if line_str.endswith(','):
                        line_str = line_str[:-1]
                    
                    # Разбираем экземпляр
                    _process_instance(line_str, inst_paths, inst_sys, inst_geom, 
                                     inst_midx, inst_m_flat, inst_c_flat, inst_t_flat,
                                     gtype, gvn, gin)
                    # Конец файла
                    break
                else:
                    # Обычная строка экземпляра
                    if line_str.endswith(','):
                        line_str = line_str[:-1]
                    
                    _process_instance(line_str, inst_paths, inst_sys, inst_geom, 
                                     inst_midx, inst_m_flat, inst_c_flat, inst_t_flat,
                                     gtype, gvn, gin)
                    continue

        # --- Шаг 4: собрать массивы ---
        # Узлы
        n_nodes = len(node_ids)
        if n_nodes > 0:
            max_id = max(node_ids)
            n_arr = max_id + 1
        else:
            n_arr = 0

        parent_arr = array.array('i', [-1]) * n_arr
        names_list = [""] * n_arr

        for i in range(n_nodes):
            nid = node_ids[i]
            parent_arr[nid] = node_parents[i]
            names_list[nid] = node_names[i]

        # Экземпляры
        n_inst = len(inst_paths)
        inst_path_arr = np.frombuffer(inst_paths, dtype=np.int32)
        inst_sys_arr = np.frombuffer(inst_sys, dtype=np.int32)
        inst_geom_arr = np.frombuffer(inst_geom, dtype=np.int32)
        inst_midx_arr = np.frombuffer(inst_midx, dtype=np.int32)

        n_valid = len(inst_m_flat) // 16
        if n_valid > 0:
            inst_m = np.frombuffer(inst_m_flat, dtype=np.float64).reshape(n_valid, 16)
            inst_c = np.frombuffer(inst_c_flat, dtype=np.float32).reshape(n_valid, 3)
            inst_t = np.frombuffer(inst_t_flat, dtype=np.float32)
        else:
            inst_m = np.zeros((0, 16), dtype=np.float64)
            inst_c = np.zeros((0, 3), dtype=np.float32)
            inst_t = np.zeros(0, dtype=np.float32)

        # Создаём объект Scene
        sc = Scene()
        sc.name = name
        sc.mpu = mpu
        sc.systems = systems
        sc.overrides = overrides
        sc.n_nodes = n_nodes
        sc.order = order
        sc.parent = parent_arr
        sc.names = names_list
        sc.gtype = gtype
        sc.gvo = gvo
        sc.gvn = gvn
        sc.gio = gio
        sc.gin = gin
        sc.n_inst = n_inst
        sc.inst_path = inst_path_arr
        sc.inst_sys = inst_sys_arr
        sc.inst_geom = inst_geom_arr
        sc.inst_midx = inst_midx_arr
        sc.inst_m = inst_m
        sc.inst_c = inst_c
        sc.inst_t = inst_t
        sc.stats = stats
        sc.geoms_path = geoms_path

        return sc


def _process_instance(line_str, inst_paths, inst_sys, inst_geom, 
                      inst_midx, inst_m_flat, inst_c_flat, inst_t_flat,
                      gtype, gvn, gin):
    """Вспомогательная функция для обработки строки экземпляра."""
    match = _INST_RE.match(line_str)
    if not match:
        # Если не совпало, пробуем json.loads, но это может быть медленно
        # По спецификации, формат должен соответствовать регулярке
        # Если не совпало, пропускаем или разбираем полностью
        try:
            inst = json.loads(line_str)
            path = inst.get("path", -1)
            s = inst.get("s", -1)
            geom = inst.get("geom", -1)
            
            inst_paths.append(path)
            inst_sys.append(s)
            inst_geom.append(geom)
            
            # Проверяем валидность геометрии
            if geom >= 0 and geom < len(gtype):
                if (gtype[geom] == 94) and (gvn[geom] > 0) and (gin[geom] > 0):
                    m = inst.get("m", [0.0]*16)
                    c = inst.get("c", [0.0]*3)
                    t = inst.get("t", 0.0)
                    
                    midx = len(inst_m_flat) // 16
                    inst_midx.append(midx)
                    
                    for val in m:
                        inst_m_flat.append(float(val))
                    for val in c:
                        inst_c_flat.append(float(val))
                    inst_t_flat.append(float(t))
                else:
                    inst_midx.append(-1)
            else:
                inst_midx.append(-1)
        except json.JSONDecodeError:
            # Пропускаем некорректную строку
            inst_paths.append(-1)
            inst_sys.append(-1)
            inst_geom.append(-1)
            inst_midx.append(-1)
        return

    path = int(match.group(1))
    s = int(match.group(2))
    geom = int(match.group(4))

    inst_paths.append(path)
    inst_sys.append(s)
    inst_geom.append(geom)
    
    # Проверяем валидность геометрии
    if geom >= 0 and geom < len(gtype):
        if (gtype[geom] == 94) and (gvn[geom] > 0) and (gin[geom] > 0):
            # Полный разбор
            inst = json.loads(line_str)
            m = inst.get("m", [0.0]*16)
            c = inst.get("c", [0.0]*3)
            t = inst.get("t", 0.0)
            
            midx = len(inst_m_flat) // 16
            inst_midx.append(midx)
            
            for val in m:
                inst_m_flat.append(float(val))
            for val in c:
                inst_c_flat.append(float(val))
            inst_t_flat.append(float(t))
        else:
            inst_midx.append(-1)
    else:
        inst_midx.append(-1)


class Scene:
    """Контейнер для данных сцены."""
    pass


def valid_geoms(sc):
    """Возвращает bool-массив валидных геометрий."""
    return (sc.gtype == 94) & (sc.gvn > 0) & (sc.gin > 0)


def open_geoms(sc):
    """Открывает geoms.bin как memmap или возвращает пустой массив."""
    if os.path.exists(sc.geoms_path) and os.path.getsize(sc.geoms_path) > 0:
        return np.memmap(sc.geoms_path, dtype=np.uint8, mode='r')
    else:
        return np.zeros(0, dtype=np.uint8)


def read_geom(sc, mm, gi):
    """Читает вершины и индексы геометрии gi из memmap mm."""
    vn = sc.gvn[gi]
    in_count = sc.gin[gi]
    vo = sc.gvo[gi]
    io = sc.gio[gi]
    
    if vo < 0 or io < 0:
        raise ValueError("Некорректные смещения геометрии")
    
    verts = np.frombuffer(mm, dtype=np.float32, count=vn*3, offset=vo).reshape(vn, 3).copy()
    inds = np.frombuffer(mm, dtype=np.uint32, count=in_count, offset=io).copy()
    
    return verts, inds


def hidden_set(overrides):
    """Возвращает множество скрытых путей/узлов из overrides."""
    hidden = set()
    for ov in overrides:
        if "path" in ov:
            flags = ov.get("flags", 0)
            mv = ov.get("mv", 0)
            if mv == 1:
                mask = flags & 0xFF
                value = (flags >> 8) & 0xFF
                if (mask & 1) and (value & 1):
                    hidden.add(ov["path"])
            else:
                if flags & 1:
                    hidden.add(ov["path"])
        elif "nodes" in ov:
            flags = ov.get("flags", 0)
            mv = ov.get("mv", 0)
            if mv == 1:
                mask = flags & 0xFF
                value = (flags >> 8) & 0xFF
                if (mask & 1) and (value & 1):
                    for nid in ov["nodes"]:
                        hidden.add(nid)
            else:
                if flags & 1:
                    for nid in ov["nodes"]:
                        hidden.add(nid)
    return hidden


def annotate(sc, hidden, src_re, last=False):
    """Аннотирует узлы: hid (скрытые) и src (source-узлы)."""
    n_arr = len(sc.parent)
    hid_arr = array.array('b', bytes(n_arr))
    src_arr = array.array('i', [-1]) * n_arr
    
    parent = sc.parent
    names = sc.names
    order = sc.order
    
    for nid in order:
        p = parent[nid]
        if p == -1 or p >= n_arr:
            # Корень или отсутствующий родитель
            hid_arr[nid] = 1 if nid in hidden else 0
            if last:
                src_arr[nid] = nid if src_re.search(names[nid]) else -1
            else:
                src_arr[nid] = nid if src_re.search(names[nid]) else -1
        else:
            # hid[n] = hid[parent] or (n in hidden)
            hid_arr[nid] = 1 if (hid_arr[p] or (nid in hidden)) else 0
            
            if last:
                # src[n] = n if match else src[parent]
                if src_re.search(names[nid]):
                    src_arr[nid] = nid
                else:
                    src_arr[nid] = src_arr[p]
            else:
                # src[n] = src[parent] if src[parent] != -1 else (n if match else -1)
                if src_arr[p] != -1:
                    src_arr[nid] = src_arr[p]
                else:
                    src_arr[nid] = nid if src_re.search(names[nid]) else -1
    
    hid = np.frombuffer(hid_arr, dtype=np.bool_)
    src = np.frombuffer(src_arr, dtype=np.int32)
    
    return hid, src


def annotate_categories(sc, src, ifc_ids):
    """Аннотирует категории a4, a5, first_named."""
    n_arr = len(sc.parent)
    parent = sc.parent
    names = sc.names
    order = sc.order
    
    # Вычисляем глубину
    depth = array.array('i', bytes(4 * n_arr))
    for nid in order:
        p = parent[nid]
        if p == -1 or p >= n_arr:
            depth[nid] = 0
        else:
            depth[nid] = depth[p] + 1
    
    a4 = array.array('i', [-1]) * n_arr
    a5 = array.array('i', [-1]) * n_arr
    first_named = array.array('i', [-1]) * n_arr
    
    for nid in order:
        s = src[nid]
        p = parent[nid]
        
        # a4 и a5
        if s != -1 and s < n_arr:
            d_s = depth[s]
            d_n = depth[nid]
            
            # a4: предок-или-сам на глубине d_s + 4
            if d_n == d_s + 4:
                a4[nid] = nid
            elif d_n > d_s + 4:
                if p != -1 and p < n_arr:
                    a4[nid] = a4[p]
                else:
                    a4[nid] = -1
            else:
                a4[nid] = -1
            
            # a5: предок-или-сам на глубине d_s + 5
            if d_n == d_s + 5:
                a5[nid] = nid
            elif d_n > d_s + 5:
                if p != -1 and p < n_arr:
                    a5[nid] = a5[p]
                else:
                    a5[nid] = -1
            else:
                a5[nid] = -1
        else:
            a4[nid] = -1
            a5[nid] = -1
        
        # first_named
        if s != -1 and s < n_arr:
            if nid == s:
                first_named[nid] = -1
            else:
                # Цепочка после source
                if p != -1 and p < n_arr:
                    if first_named[p] != -1:
                        first_named[nid] = first_named[p]
                    else:
                        # Проверяем, входит ли nid в цепочку
                        # Цепочка: потомки source, исключая сам source
                        # Если depth[nid] > depth[s], то nid в цепочке
                        if depth[nid] > depth[s] and names[nid]:
                            first_named[nid] = nid
                        else:
                            first_named[nid] = -1
                else:
                    first_named[nid] = -1
        else:
            # s == -1: цепочка — все узлы кроме корня
            if depth[nid] == 0:
                first_named[nid] = -1
            else:
                if p != -1 and p < n_arr:
                    if first_named[p] != -1:
                        first_named[nid] = first_named[p]
                    else:
                        if names[nid]:
                            first_named[nid] = nid
                        else:
                            first_named[nid] = -1
                else:
                    first_named[nid] = -1
    
    a4_np = np.frombuffer(a4, dtype=np.int32)
    a5_np = np.frombuffer(a5, dtype=np.int32)
    first_named_np = np.frombuffer(first_named, dtype=np.int32)
    
    return a4_np, a5_np, first_named_np
