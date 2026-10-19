#!/usr/bin/env python3
# nwd2glb.py — группировка + инстансинг NWD-сцены в GLB
import sys
import os
import re
import json
import pathlib
import array
import collections
import colorsys
import numpy as np

import nwdscene as ns
import glbemit

# ---------- палитра (скопировано дословно из старого кода) ----------
FAM = [("Приток", 212), ("Вытяжка", 24), ("Местный отсос", 300), ("Воздухозабор", 150), ("Выброс", 0),
       ("Холодоснабжение", 186), ("Система VRV", 270), ("Подпор", 95), ("Строительные", 45)]

# воздушные системы, раньше попадавшие на запасной оттенок 60 (порядок k у прочих систем не меняется)
FAM_AIR = [("Рекуператор", 170), ("Рециркуляция", 330), ("Техн.вент", 350)]


def build_palette(names):
    out, seen = {}, {}
    for n in sorted(names):
        hue = next((h for p, h in FAM if n.startswith(p)), 60)
        k = seen.get(hue, 0); seen[hue] = k + 1
        hue = next((h for p, h in FAM_AIR if n.startswith(p)), hue)
        h = (hue + (k % 5 - 2) * 9) / 360.0
        s = 0.9 if k % 2 == 0 else 0.75
        l = 0.42 + 0.07 * ((k // 2) % 3)
        out[n] = tuple(round(x, 3) for x in colorsys.hls_to_rgb(h, l, s))
    return out

# цвет по категории для экземпляров без системы, у которых в NWD нейтральный (серый/белый) цвет
CAT_COLORS = [("covering", (0.78, 0.70, 0.55)), ("изоляц", (0.78, 0.70, 0.55)),
              ("terminal", (0.55, 0.72, 0.85)), ("воздухораспред", (0.55, 0.72, 0.85)),
              ("обобщенн", (0.62, 0.62, 0.70)), ("обобщённ", (0.62, 0.62, 0.70))]


def category_color(category, c):
    """Цвет без системы: нейтральный цвет NWD заменяется цветом категории, настоящий цвет остаётся."""
    if max(c) - min(c) > 0.05:
        return c
    low = category.lower()
    return next((v for k, v in CAT_COLORS if k in low), c)


# ---------- кватернион из матрицы вращения (метод Шеппарда) ----------
def _quat_from_R(R):
    """R: (N,3,3) -> Q: (N,4) xyzw, w>=0."""
    N = R.shape[0]
    tr = np.einsum('nii->n', R)
    r00 = R[:, 0, 0]; r11 = R[:, 1, 1]; r22 = R[:, 2, 2]
    r01 = R[:, 0, 1]; r10 = R[:, 1, 0]
    r02 = R[:, 0, 2]; r20 = R[:, 2, 0]
    r12 = R[:, 1, 2]; r21 = R[:, 2, 1]

    Q = np.empty((N, 4), dtype=np.float64)
    # ветка 0: tr макс
    m0 = tr >= np.maximum(r00, np.maximum(r11, r22))
    if np.any(m0):
        s = 2.0 * np.sqrt(np.maximum(tr[m0] + 1.0, 0.0))
        Q[m0, 3] = 0.25 * s
        Q[m0, 0] = (r21[m0] - r12[m0]) / s
        Q[m0, 1] = (r02[m0] - r20[m0]) / s
        Q[m0, 2] = (r10[m0] - r01[m0]) / s
    # ветка 1: r00 макс
    m1 = (r00 > tr) & (r00 >= r11) & (r00 >= r22)
    if np.any(m1):
        s = 2.0 * np.sqrt(np.maximum(1.0 + r00[m1] - r11[m1] - r22[m1], 0.0))
        Q[m1, 0] = 0.25 * s
        Q[m1, 1] = (r01[m1] + r10[m1]) / s
        Q[m1, 2] = (r02[m1] + r20[m1]) / s
        Q[m1, 3] = (r21[m1] - r12[m1]) / s
    # ветка 2: r11 макс
    m2 = (r11 > tr) & (r11 > r00) & (r11 >= r22)
    if np.any(m2):
        s = 2.0 * np.sqrt(np.maximum(1.0 + r11[m2] - r00[m2] - r22[m2], 0.0))
        Q[m2, 1] = 0.25 * s
        Q[m2, 0] = (r01[m2] + r10[m2]) / s
        Q[m2, 2] = (r12[m2] + r21[m2]) / s
        Q[m2, 3] = (r02[m2] - r20[m2]) / s
    # ветка 3: r22 макс
    m3 = (r22 > tr) & (r22 > r00) & (r22 > r11)
    if np.any(m3):
        s = 2.0 * np.sqrt(np.maximum(1.0 + r22[m3] - r00[m3] - r11[m3], 0.0))
        Q[m3, 2] = 0.25 * s
        Q[m3, 0] = (r02[m3] + r20[m3]) / s
        Q[m3, 1] = (r12[m3] + r21[m3]) / s
        Q[m3, 3] = (r10[m3] - r01[m3]) / s

    # нормировка
    nrm = np.linalg.norm(Q, axis=1, keepdims=True)
    nrm = np.where(nrm > 0, nrm, 1.0)
    Q /= nrm
    # знак: w >= 0
    neg = Q[:, 3] < 0
    if np.any(neg):
        Q[neg] *= -1.0
    return Q

def _bake(verts, inds, A, t, nv):
    """Запекает n экземпляров: мировые вершины float32, индексы со сдвигом nv, границы."""
    n, vn = len(A), len(verts)
    world = np.einsum('ij,nkj->nik', verts.astype(np.float64), A) + t[:, None, :]
    tri = np.broadcast_to(inds.astype(np.uint32).reshape(-1, 3), (n, len(inds) // 3, 3)).copy()
    mirror = np.linalg.det(A) < 0
    if mirror.any():
        tri[mirror] = tri[mirror][:, :, [0, 2, 1]]
    tri += (nv + np.arange(n, dtype=np.uint32) * vn).astype(np.uint32)[:, None, None]
    return world.reshape(-1, 3).astype(np.float32), tri.ravel(), world.min(axis=(0, 1)), world.max(axis=(0, 1))


def _jnum(o):
    """Сериализация numpy-скаляров."""
    return o.item()


# ---------- Group ----------
class Group:
    __slots__ = ('verts', 'inds', 'inst', 'instances', 'triangles')
    def __init__(self):
        self.verts = None
        self.inds = None
        self.inst = {}
        self.instances = 0
        self.triangles = 0

# ---------- main ----------
def main():
    sys.stdout.reconfigure(encoding='utf-8')

    # --- аргументы ---
    args = sys.argv[1:]
    if len(args) < 2:
        print("Использование: python nwd2glb.py DUMPDIR OUT.glb [REPORT.json] [--quant OUTQ.glb]", file=sys.stderr)
        sys.exit(1)

    dump_dir = pathlib.Path(args[0])
    out_path = pathlib.Path(args[1])
    report_path = None
    quant_path = None

    rest = args[2:]
    i = 0
    while i < len(rest):
        if rest[i] == '--quant':
            if i + 1 >= len(rest):
                print("Ошибка: --quant требует аргумента", file=sys.stderr)
                sys.exit(1)
            quant_path = pathlib.Path(rest[i + 1])
            i += 2
        else:
            if report_path is None:
                report_path = pathlib.Path(rest[i])
            i += 1

    # --- загрузка сцены ---
    sc = ns.load_scene(dump_dir)
    if sc.n_inst == 0:
        raise ValueError("No models found in scene.json")

    # --- аннотации ---
    hidden = ns.hidden_set(sc.overrides)
    src_re = re.compile(r'\.(ifc|dwfx?|nwc|dwg|rvt)$', re.I)
    hid, src = ns.annotate(sc, hidden, src_re)
    ifc_ids = {s for s in np.unique(src[src >= 0]).tolist() if sc.names[s].lower().endswith('.ifc')}
    a4, a5, fn = ns.annotate_categories(sc, src, ifc_ids)

    # --- палитра ---
    PALETTE = build_palette(sc.systems)

    # --- статистика ---
    stats = {
        'nodes': sc.n_nodes,
        'instances_total': sc.n_inst,
        'visible_instances': 0,
        'skipped_hidden': 0,
        'skipped_no_geom': 0,
        'skipped_not_tri': 0,
        'skipped_empty': 0,
        'triangles_written': 0,
        'vertices_written': 0,
        'sources': 0,
        'categories': 0,
        'color_groups': 0,
        'materials': 0,
        'instanced_instances': 0,
        'baked_instances': 0,
        'unique_instanced_geoms': 0,
    }

    # --- маски ---
    # inst_path: (n_inst,) int, -1 если нет
    inst_path = sc.inst_path
    inst_geom = sc.inst_geom
    inst_midx = sc.inst_midx
    inst_sys = sc.inst_sys

    # скрытые: inst_path == -1 -> не скрыт
    valid_path = inst_path >= 0
    hidden_mask = np.zeros(sc.n_inst, dtype=bool)
    hidden_mask[valid_path] = hid[inst_path[valid_path]]

    stats['skipped_hidden'] = int(hidden_mask.sum())

    # среди не скрытых
    not_hidden = ~hidden_mask
    # geom == -1 -> skipped_no_geom
    geom_invalid = (inst_geom == -1) | (inst_geom >= len(sc.gtype))
    stats['skipped_no_geom'] = int((not_hidden & geom_invalid).sum())

    # геометрия не годна по valid_geoms
    valid_geoms = ns.valid_geoms(sc)
    # valid_geoms: bool по геометриям
    # для не скрытых с валидным geom: проверить valid_geoms[geom]
    geom_ok_mask = np.zeros(sc.n_inst, dtype=bool)
    valid_geom_idx = inst_geom[not_hidden & ~geom_invalid]
    if len(valid_geom_idx) > 0:
        geom_ok_mask[not_hidden & ~geom_invalid] = valid_geoms[valid_geom_idx]

    # skipped_not_tri: не скрыт, geom валиден по индексу, но не в valid_geoms
    stats['skipped_not_tri'] = int((not_hidden & ~geom_invalid & ~geom_ok_mask).sum())

    # прочие без midx -> skipped_no_geom (уже учтено выше, но уточним)
    # видимые = не скрыт & midx >= 0
    vis = not_hidden & (inst_midx >= 0)
    stats['visible_instances'] = int(vis.sum())
    stats['skipped_empty'] = 0

    # --- кэш категорий ---
    cat_cache = {}

    def get_category(node):
        s = src[node]
        key = (s, a4[node], a5[node], fn[node])
        if key in cat_cache:
            return cat_cache[key]
        if s != -1 and s in ifc_ids:
            parts = []
            for aid in (a4[node], a5[node]):
                if aid != -1 and sc.names[aid]:
                    parts.append(sc.names[aid])
            cat = " · ".join(parts)
        else:
            if fn[node] != -1:
                cat = re.sub(r'\s*\(\d+\)$', '', sc.names[fn[node]])
            else:
                cat = ""
        if not cat:
            cat = "Без категории"
        cat_cache[key] = cat
        return cat

    # --- накопление в бакеты ---
    # bucket: dict group_key -> dict geom -> array('i') midx
    bucket = collections.defaultdict(lambda: collections.defaultdict(lambda: array.array('i')))
    source_stats = collections.defaultdict(lambda: {'instances': 0, 'triangles': 0, 'categories': collections.defaultdict(lambda: {'instances': 0, 'triangles': 0})})

    vis_idx = np.nonzero(vis)[0]
    mpu = sc.mpu
    gvn = sc.gvn
    gin = sc.gin

    for i in vis_idx:
        node = inst_path[i]
        geom = inst_geom[i]
        midx = inst_midx[i]

        # source
        s = src[node]
        if s == -1:
            source_label = "Прочее"
        else:
            source_label = re.sub(src_re, '', sc.names[s])

        # category
        category = get_category(node)

        # цвет/прозрачность
        sid = inst_sys[i]
        sysname = sc.systems[sid] if 0 <= sid < len(sc.systems) else ""
        c = sc.inst_c[midx]
        t = sc.inst_t[midx]
        if sysname:
            c = PALETTE[sysname]
        else:
            c = category_color(category, c)
        if "Covering" in category:
            t = 0.4
        color_key = (round(float(c[0]), 3), round(float(c[1]), 3), round(float(c[2]), 3))
        trans_key = round(float(t), 2)

        group_key = (source_label, category, color_key, trans_key, sysname)

        # накопить
        bucket[group_key][geom].append(midx)

        # статистика по источникам
        tri_count = gin[geom] // 3
        source_stats[source_label]['instances'] += 1
        source_stats[source_label]['triangles'] += tri_count
        source_stats[source_label]['categories'][category]['instances'] += 1
        source_stats[source_label]['categories'][category]['triangles'] += tri_count

    # --- обработка групп ---
    mm = ns.open_geoms(sc)

    def read_geom(gi):
        return ns.read_geom(sc, mm, gi)

    # AABB кэш
    aabb_cache = {}

    def get_aabb(geom):
        if geom in aabb_cache:
            return aabb_cache[geom]
        verts, _ = read_geom(geom)
        if len(verts) == 0:
            aabb_cache[geom] = (np.zeros(3), np.zeros(3))
            return aabb_cache[geom]
        mn = verts.min(axis=0)
        mx = verts.max(axis=0)
        aabb_cache[geom] = (mn, mx)
        return aabb_cache[geom]

    # P: Z-up -> Y-up
    P = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)

    groups = {}
    all_min = np.full(3, np.inf)
    all_max = np.full(3, -np.inf)
    has_verts = False

    for group_key, geom_dict in bucket.items():
        g = Group()
        baked_verts_list = []
        baked_inds_list = []
        nv = 0

        for geom, midx_arr in geom_dict.items():
            midxs = np.array(midx_arr, dtype=np.int64)
            N = len(midxs)
            if N == 0:
                continue

            # матрицы
            Mx = sc.inst_m[midxs]  # (N, 16)
            mat = Mx.reshape(N, 4, 4)
            Acol = mat[:, :3, :3].transpose(0, 2, 1)  # (N, 3, 3)
            tcol = mat[:, 3, :3]  # (N, 3)

            # мир
            A = np.einsum('ij,njk->nik', P, Acol) * mpu  # (N, 3, 3)
            t = tcol @ P.T * mpu  # (N, 3)

            # разложение A = R * diag(S)
            S = np.linalg.norm(A, axis=1)  # (N, 3)
            S_safe = np.where(S > 1e-9, S, 1.0)
            R = A / S_safe[:, None, :]  # (N, 3, 3)

            # пригодность
            det_R = np.linalg.det(R)
            S_min = S.min(axis=1)
            # max|R^T R - I|
            RtR = np.einsum('nji,njk->nik', R, R)
            I = np.eye(3)
            err = np.abs(RtR - I).max(axis=(1, 2))
            good = (det_R > 0) & (S_min > 1e-9) & (err <= 1e-6)

            Ng = int(good.sum())
            Nbad = N - Ng

            # решение об инстансинге
            vn = gvn[geom]
            in_count = gin[geom]
            do_instance = (not os.environ.get('NWD_NO_INSTANCING')) and (Ng >= 2) and ((Ng - 1) * (vn * 8 + in_count * 2) > Ng * 40 + 300)

            if do_instance:
                # инстансинг
                T_inst = t[good]
                Q_inst = _quat_from_R(R[good])
                S_inst = S[good]
                g.inst[geom] = (T_inst, Q_inst, S_inst)
                g.instances += Ng
                g.triangles += Ng * (in_count // 3)
                stats['instanced_instances'] += Ng

                # AABB для инстансинговых
                mn, mx = get_aabb(geom)
                corners = np.array([
                    [mn[0], mn[1], mn[2]],
                    [mn[0], mn[1], mx[2]],
                    [mn[0], mx[1], mn[2]],
                    [mn[0], mx[1], mx[2]],
                    [mx[0], mn[1], mn[2]],
                    [mx[0], mn[1], mx[2]],
                    [mx[0], mx[1], mn[2]],
                    [mx[0], mx[1], mx[2]],
                ], dtype=np.float64)
                # world = corner @ A.T + t
                world_corners = np.einsum('ij,njk->nik', corners, A[good].transpose(0, 2, 1)) + T_inst[:, None, :]
                wmin = world_corners.min(axis=(0, 1))
                wmax = world_corners.max(axis=(0, 1))
                all_min = np.minimum(all_min, wmin)
                all_max = np.maximum(all_max, wmax)
                has_verts = True

            # запекание плохих (или всех, если не инстансим)
            bad_mask = ~good if do_instance else np.ones(N, dtype=bool)
            Nbad_actual = int(bad_mask.sum())
            if Nbad_actual > 0:
                bad_idx = np.nonzero(bad_mask)[0]
                A_bad = A[bad_idx]
                t_bad = t[bad_idx]

                verts, inds = read_geom(geom)
                if len(verts) == 0:
                    continue
                wv, wi, wmin, wmax = _bake(verts, inds, A[bad_idx], t[bad_idx], nv)
                baked_verts_list.append(wv)
                baked_inds_list.append(wi)
                nv += len(verts) * Nbad_actual
                g.instances += Nbad_actual
                g.triangles += Nbad_actual * (in_count // 3)
                stats['baked_instances'] += Nbad_actual
                all_min = np.minimum(all_min, wmin)
                all_max = np.maximum(all_max, wmax)
                has_verts = True

        # собрать Group
        if baked_verts_list:
            g.verts = np.concatenate(baked_verts_list).reshape(-1, 3).astype(np.float32)
            g.inds = np.concatenate(baked_inds_list)
            if len(g.verts) < 65536:
                g.inds = g.inds.astype(np.uint16)
            else:
                g.inds = g.inds.astype(np.uint32)
            del baked_verts_list
            del baked_inds_list
        else:
            g.verts = np.zeros((0, 3), dtype=np.float32)
            g.inds = None

        groups[group_key] = g

    # --- смещение ---
    offset = np.zeros(3, dtype=np.float64)
    if has_verts:
        center = (all_min + all_max) / 2.0
        offset = np.array([-center[0], -all_min[1], -center[2]], dtype=np.float64)
        # применить к вершинам
        for g in groups.values():
            if len(g.verts) > 0:
                g.verts = (g.verts.astype(np.float64) + offset).astype(np.float32)
            # применить к инстансам
            for geom, (T, Q, S) in g.inst.items():
                g.inst[geom] = (T + offset, Q, S)

    # --- итоговая статистика ---
    stats['triangles_written'] = sum(g.triangles for g in groups.values())
    stats['vertices_written'] = sum(len(g.verts) for g in groups.values())
    stats['sources'] = len(source_stats)
    stats['categories'] = sum(len(ss['categories']) for ss in source_stats.values())
    stats['color_groups'] = len(groups)
    materials = set()
    for gk in groups.keys():
        materials.add((gk[2], gk[3]))
    stats['materials'] = len(materials)
    stats['unique_instanced_geoms'] = sum(len(g.inst) for g in groups.values())

    # --- emit ---
    root_name = re.sub(r'\.[^.]*$', '', sc.name)
    result = glbemit.emit(groups, read_geom, root_name, str(out_path), str(quant_path) if quant_path else None)

    # --- отчёт ---
    sources_list = []
    for label in sorted(source_stats.keys()):
        ss = source_stats[label]
        cats = []
        for cat_name in sorted(ss['categories'].keys()):
            cc = ss['categories'][cat_name]
            cats.append({'name': cat_name, 'instances': cc['instances'], 'triangles': cc['triangles']})
        sources_list.append({
            'source': label,
            'instances': ss['instances'],
            'triangles': ss['triangles'],
            'categories': cats,
        })

    report = {
        'stats': stats,
        'sources': sources_list,
        'bounding_box': {
            'min': [float(all_min[0]), float(all_min[1]), float(all_min[2])] if has_verts else [0.0, 0.0, 0.0],
            'max': [float(all_max[0]), float(all_max[1]), float(all_max[2])] if has_verts else [0.0, 0.0, 0.0],
        },
        'offset': [float(offset[0]), float(offset[1]), float(offset[2])],
        'glb_size': result['glb_size'],
    }
    if result.get('quant_size') is not None:
        report['quant_size'] = result['quant_size']
    if result.get('quant_max_err') is not None:
        report['quant_max_err'] = result['quant_max_err']
    report['instanced_nodes'] = result['instanced_nodes']
    report['meshes'] = result['meshes']

    # печать
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_jnum))

    # запись в файл
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=_jnum)

    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
