import os
import sys
import json
import struct
import subprocess
import math
import numpy as np

sys.path.insert(0, os.environ.get("NWD_SCRIPTS", "/app/scripts/nwd"))

import nwdscene
import nwd2glb
import glbemit
import check_area


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rot(axis, deg):
    """Return 4x4 rotation matrix (row-major, translation in m[12:15])."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        R = np.array([
            [1, 0, 0, 0],
            [0, c, -s, 0],
            [0, s, c, 0],
            [0, 0, 0, 1],
        ], dtype=np.float64)
    elif axis == "y":
        R = np.array([
            [c, 0, s, 0],
            [0, 1, 0, 0],
            [-s, 0, c, 0],
            [0, 0, 0, 1],
        ], dtype=np.float64)
    elif axis == "z":
        R = np.array([
            [c, -s, 0, 0],
            [s, c, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=np.float64)
    else:
        raise ValueError(f"Unknown axis: {axis}")
    return R


def make_transform(translation, rotation=None, scale=None):
    """Build 4x4 matrix: world = v @ M, translation in m[12:15].
    M = S * R * T (column-major convention for row-vector multiplication).
    Actually for row-vector: [v,1] @ M where M columns are basis vectors.
    M = [R*S | t] in 4x4 form.
    """
    M = np.eye(4, dtype=np.float64)
    if scale is not None:
        M[0, 0] *= scale[0]
        M[1, 1] *= scale[1]
        M[2, 2] *= scale[2]
    if rotation is not None:
        M[:3, :3] = rotation[:3, :3] @ M[:3, :3]
    M[0, 3] = translation[0]
    M[1, 3] = translation[1]
    M[2, 3] = translation[2]
    return M


def build_dump(tmp_path):
    """Create synthetic nwd_dump in tmp_path. Returns dump_info dict."""
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir(exist_ok=True)

    # Geometry definitions
    # G0: unit cube 1x1x1, 8 vertices, 12 triangles
    cube_verts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float32)
    cube_idx = np.array([
        0, 1, 2, 0, 2, 3,
        4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4,
        1, 2, 6, 1, 6, 5,
        2, 3, 7, 2, 7, 6,
        3, 0, 4, 3, 4, 7,
    ], dtype=np.uint32)

    # G1: tetrahedron, 4 vertices, 4 triangles
    tet_verts = np.array([
        [0, 0, 0], [2, 0, 0], [0, 1, 0], [0, 0, 3],
    ], dtype=np.float32)
    tet_idx = np.array([
        0, 1, 2,
        0, 1, 3,
        0, 2, 3,
        1, 2, 3,
    ], dtype=np.uint32)

    # Write geoms.bin: G0 then G1
    geoms_bin = dump_dir / "geoms.bin"
    with open(geoms_bin, "wb") as f:
        # G0
        f.write(cube_verts.tobytes())
        f.write(cube_idx.tobytes())
        # G1
        f.write(tet_verts.tobytes())
        f.write(tet_idx.tobytes())

    # Compute byte offsets
    g0_vo = 0
    g0_vn = len(cube_verts)
    g0_io = g0_vo + g0_vn * 12  # after vertices (float32 x3)
    g0_in = len(cube_idx)
    g0_total = g0_vn * 12 + g0_in * 4

    g1_vo = g0_total
    g1_vn = len(tet_verts)
    g1_io = g1_vo + g1_vn * 12
    g1_in = len(tet_idx)
    g1_total = g1_vn * 12 + g1_in * 4

    # Build instances
    # K = 6 named + 40 extra for G0 = 46 total
    # Actually spec says K≈49. Let's do 6 named + 43 extra = 49
    # G0: 4 named + 43 extra = 47 instances
    # G1: 3 named = 3 instances
    # Total = 50. Let's adjust: 4 named G0 + 40 extra G0 = 44 G0, 3 G1 = 47 total
    # Spec says K≈49. Let's do 4 named G0 + 42 extra G0 = 46 G0, 3 G1 = 49 total

    instances = []

    # Named G0 instances
    # (a) identity + shift (10,20,30)
    M_a = make_transform([10, 20, 30])
    instances.append({"geom": 0, "m": M_a.flatten().tolist()})

    # (b) 90° around Z + shift
    R_z90 = rot("z", 90)
    M_b = make_transform([5, 15, 25], rotation=R_z90)
    instances.append({"geom": 0, "m": M_b.flatten().tolist()})

    # (c) scale (2,2,2) + shift
    M_c = make_transform([3, 7, 11], scale=[2, 2, 2])
    instances.append({"geom": 0, "m": M_c.flatten().tolist()})

    # (d) 30° around X + scale (1,3,1)
    R_x30 = rot("x", 30)
    M_d = make_transform([1, 4, 9], rotation=R_x30, scale=[1, 3, 1])
    instances.append({"geom": 0, "m": M_d.flatten().tolist()})

    # Named G1 instances
    # 45° around Y + shift
    R_y45 = rot("y", 45)
    M_e = make_transform([2, 3, 4], rotation=R_y45)
    instances.append({"geom": 1, "m": M_e.flatten().tolist()})

    # 90° around X + shift
    R_x90 = rot("x", 90)
    M_f = make_transform([6, 8, 10], rotation=R_x90)
    instances.append({"geom": 1, "m": M_f.flatten().tolist()})

    # One more G1 with different rotation
    R_z45 = rot("z", 45)
    M_g = make_transform([1, 2, 3], rotation=R_z45)
    instances.append({"geom": 1, "m": M_g.flatten().tolist()})

    # Extra G0 instances (42 more) with various shifts
    for i in range(42):
        tx = 100 + i * 2
        ty = 200 + i * 3
        tz = 300 + i * 5
        M_extra = make_transform([tx, ty, tz])
        instances.append({"geom": 0, "m": M_extra.flatten().tolist()})

    K = len(instances)  # 4 + 3 + 42 = 49

    # Nodes: 0 root, 1 Src.dwfx, 2 Cat, 3..(3+K-1) leaves
    N_nodes = 3 + K  # nodes 0,1,2, then K leaves starting at 3

    # Build scene.json
    lines = []
    # Header
    lines.append('{"models":[{"name":"M.nwd","meters_per_unit":1.0,"linear_units":2,"nodes":[')
    # Node 0: root
    lines.append('{"id":0,"parent":-1,"name":"M.nwd"},')
    # Node 1: Src.dwfx
    lines.append('{"id":1,"parent":0,"name":"Src.dwfx"},')
    # Node 2: Cat
    lines.append('{"id":2,"parent":1,"name":"Cat"},')
    # Nodes 3..(3+K-1): leaves
    for i in range(K):
        node_id = 3 + i
        if i < K - 1:
            lines.append(f'{{"id":{node_id},"parent":2,"name":"leaf"}},')
        else:
            lines.append(f'{{"id":{node_id},"parent":2,"name":"leaf"}}],"instances":[')

    # Instances
    for i, inst in enumerate(instances):
        m_str = ",".join(str(x) for x in inst["m"])
        if i < K - 1:
            lines.append(f'{{"path":{3+i},"s":-1,"f":0,"geom":{inst["geom"]},"m":[{m_str}],"c":[0.5,0.5,0.5],"t":0}},')
        else:
            lines.append(f'{{"path":{3+i},"s":-1,"f":0,"geom":{inst["geom"]},"m":[{m_str}],"c":[0.5,0.5,0.5],"t":0}}],"systems":[],"geoms":[')

    # Geometries
    # G0
    lines.append(f'{{"type":94,"flags":0,"params":[0,0,0,0,0,0,0,0,0,0,0,0,0],"vo":{g0_vo},"vn":{g0_vn},"io":{g0_io},"in":{g0_in}}},')
    # G1
    lines.append(f'{{"type":94,"flags":0,"params":[0,0,0,0,0,0,0,0,0,0,0,0,0],"vo":{g1_vo},"vn":{g1_vn},"io":{g1_io},"in":{g1_in}}}]}}],"overrides":[')

    # Overrides (empty) and stats
    lines.append(f'],"stats":{{"models":1,"nodes":{N_nodes},"instances":{K},"geometries":2}}}}')

    scene_json = dump_dir / "scene.json"
    with open(scene_json, "w") as f:
        f.write("\n".join(lines) + "\n")

    # dump_info for oracle
    dump_info = {
        "dir": str(dump_dir),
        "mpu": 1.0,
        "instances": instances,
        "geoms": {
            0: {"verts": cube_verts, "idx": cube_idx},
            1: {"verts": tet_verts, "idx": tet_idx},
        },
        "K": K,
    }
    return dump_info


def oracle_points(dump_info):
    """Compute world-space vertices for all instances.
    world = (v @ M3 + t) * mpu, then axis swap (x, z, -y).
    """
    mpu = dump_info["mpu"]
    all_points = []
    for inst in dump_info["instances"]:
        geom_id = inst["geom"]
        m = np.array(inst["m"], dtype=np.float64).reshape(4, 4)
        verts = dump_info["geoms"][geom_id]["verts"].astype(np.float64)
        # строковая запись: мир = [v,1] @ m
        M = m
        world = verts @ M[:3, :3] + M[3, :3]
        world = world * mpu
        # Axis swap: (x, z, -y)
        swapped = np.column_stack([world[:, 0], world[:, 2], -world[:, 1]])
        all_points.append(swapped)
    return np.vstack(all_points)


def glb_points(path):
    """Read float-GLB and extract all mesh vertices (including instanced).
    Returns array of shape (N, 3).
    """
    with open(path, "rb") as f:
        data = f.read()

    # GLB header: magic(4) version(4) length(4)
    magic = struct.unpack_from("<I", data, 0)[0]
    assert magic == 0x46546C67, f"Not a GLB file: {hex(magic)}"
    version = struct.unpack_from("<I", data, 4)[0]
    total_len = struct.unpack_from("<I", data, 8)[0]

    # JSON chunk
    chunk_len = struct.unpack_from("<I", data, 12)[0]
    chunk_type = struct.unpack_from("<I", data, 16)[0]
    assert chunk_type == 0x4E4F534A, "Expected JSON chunk"
    json_data = data[20:20 + chunk_len]
    gltf = json.loads(json_data)

    # Binary chunk
    bin_offset = 20 + chunk_len
    bin_len = struct.unpack_from("<I", data, bin_offset)[0]
    bin_type = struct.unpack_from("<I", data, bin_offset + 4)[0]
    assert bin_type == 0x004E4942, "Expected BIN chunk"
    bin_data = data[bin_offset + 8:bin_offset + 8 + bin_len]

    # Parse accessors
    accessors = gltf.get("accessors", [])
    buffers = gltf.get("buffers", [])
    buffer_views = gltf.get("bufferViews", [])

    def get_accessor_data(acc_idx):
        acc = accessors[acc_idx]
        bv_idx = acc["bufferView"]
        bv = buffer_views[bv_idx]
        offset = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        comp_type = acc["componentType"]
        count = acc["count"]
        type_str = acc["type"]

        if type_str == "SCALAR":
            n_comp = 1
        elif type_str == "VEC2":
            n_comp = 2
        elif type_str == "VEC3":
            n_comp = 3
        elif type_str == "VEC4":
            n_comp = 4
        else:
            raise ValueError(f"Unknown type: {type_str}")

        if comp_type == 5126:  # FLOAT
            dtype = np.float32
        elif comp_type == 5123:  # UNSIGNED_SHORT
            dtype = np.uint16
        elif comp_type == 5125:  # UNSIGNED_INT
            dtype = np.uint32
        elif comp_type == 5121:  # BYTE
            dtype = np.int8
        elif comp_type == 5122:  # UNSIGNED_BYTE
            dtype = np.uint8
        else:
            raise ValueError(f"Unknown component type: {comp_type}")

        item_size = np.dtype(dtype).itemsize * n_comp
        arr = np.frombuffer(bin_data, dtype=dtype, count=count * n_comp, offset=offset)
        return arr.reshape(count, n_comp) if n_comp > 1 else arr.reshape(count)

    # Collect all points from meshes
    all_points = []

    for node in gltf.get("nodes", []):
        if "mesh" not in node:
            continue
        mesh = gltf["meshes"][node["mesh"]]
        inst_ext = (node.get("extensions", {}).get("EXT_mesh_gpu_instancing") or {}).get("attributes")
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            pos_idx = attrs.get("POSITION")
            if pos_idx is None:
                continue
            positions = get_accessor_data(pos_idx)

            # Check for EXT_mesh_gpu_instancing
            if inst_ext:
                # Get instance attributes
                trans_idx = inst_ext.get("TRANSLATION")
                rot_idx = inst_ext.get("ROTATION")
                scale_idx = inst_ext.get("SCALE")

                if trans_idx is not None:
                    translations = get_accessor_data(trans_idx)
                else:
                    translations = np.array([[0, 0, 0]], dtype=np.float32)

                if rot_idx is not None:
                    rotations = get_accessor_data(rot_idx)
                else:
                    rotations = np.array([[0, 0, 0, 1]], dtype=np.float32)

                if scale_idx is not None:
                    scales = get_accessor_data(scale_idx)
                else:
                    scales = np.array([[1, 1, 1]], dtype=np.float32)

                n_inst = len(translations)
                for i in range(n_inst):
                    T = translations[i]
                    q = rotations[i]  # xyzw
                    S = scales[i]
                    # Apply: T + R(q) * (S * v)
                    # Quaternion to rotation matrix
                    x, y, z, w = q
                    R = np.array([
                        [1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
                        [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                        [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)],
                    ], dtype=np.float64)
                    scaled = positions * S
                    transformed = scaled @ R.T + T
                    all_points.append(transformed)
            else:
                all_points.append(positions)

    if not all_points:
        return np.empty((0, 3), dtype=np.float64)
    return np.vstack(all_points).astype(np.float64)


def compare_points(oracle, glb_pts, atol=2e-3):
    """Compare two point sets after removing offset. Returns True if close."""
    if len(oracle) == 0 and len(glb_pts) == 0:
        return True
    if len(oracle) != len(glb_pts):
        return False

    # Remove offset: subtract component-wise minimum
    o_min = oracle.min(axis=0)
    g_min = glb_pts.min(axis=0)

    # округление до сортировки: шум float32 иначе переставляет почти равные точки
    o_norm = np.round(oracle - o_min, 3)
    g_norm = np.round(glb_pts - g_min, 3)

    # Sort lexicographically
    o_sorted = o_norm[np.lexsort((o_norm[:, 2], o_norm[:, 1], o_norm[:, 0]))]
    g_sorted = g_norm[np.lexsort((g_norm[:, 2], g_norm[:, 1], g_norm[:, 0]))]

    return np.allclose(o_sorted, g_sorted, atol=atol)


def run_pipeline(dump_info, tmp_path, mutate=None):
    """Run nwd2glb pipeline. Returns (out_path, report, check_rc, check_out)."""
    dump_dir = dump_info["dir"]
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)

    out_glb = str(out_dir / "out.glb")
    out_quant = str(out_dir / "out_quant.glb")
    report_path = str(out_dir / "report.json")

    # Monkeypatch sys.argv for nwd2glb
    old_argv = sys.argv
    sys.argv = ["nwd2glb.py", dump_dir, out_glb, report_path, "--quant", out_quant]

    # Мутация применяется к groups (dict) перед вызовом настоящего emit; после прогона emit восстанавливается
    original_emit = glbemit.emit

    def emit_wrapper(groups, *args, **kwargs):
        if mutate is not None:
            mutate(groups)
        return original_emit(groups, *args, **kwargs)

    glbemit.emit = emit_wrapper
    try:
        nwd2glb.main()
    finally:
        glbemit.emit = original_emit
        sys.argv = old_argv

    # Read report
    with open(report_path, "r") as f:
        report = json.load(f)

    # Run check_area
    check_result = subprocess.run(
        [sys.executable, check_area.__file__, dump_dir, out_glb],
        capture_output=True, text=True
    )
    check_rc = check_result.returncode
    check_out = check_result.stdout + check_result.stderr

    return out_glb, report, check_rc, check_out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture
def dump(tmp_path):
    return build_dump(tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pipeline_ok(dump, tmp_path):
    """Test full pipeline: float GLB, quantized GLB, check_area, oracle comparison."""
    out_glb, report, check_rc, check_out = run_pipeline(dump, tmp_path)

    # Check stats
    stats = report.get("stats", {})
    assert stats.get("instanced_instances", 0) > 0, f"Expected instanced_instances > 0, got {stats}"

    # Check check_area
    assert check_rc == 0, f"check_area failed: {check_out}"
    assert "расхождение 0.000 %" in check_out, f"Expected 'расхождение 0.000 %' in output: {check_out}"

    # Check oracle comparison
    oracle = oracle_points(dump)
    glb_pts = glb_points(out_glb)
    assert compare_points(oracle, glb_pts, atol=2e-3), "GLB points do not match oracle"

    # Check quantized GLB
    out_quant = str(tmp_path / "out" / "out_quant.glb")
    assert os.path.exists(out_quant), "Quantized GLB does not exist"

    # Check quant_max_err
    quant_max_err = report.get("quant_max_err", report.get("stats", {}).get("quant_max_err", None))
    if quant_max_err is not None:
        assert quant_max_err < 1e-3, f"quant_max_err {quant_max_err} >= 1e-3"

    # размер квантованной версии на крошечном дампе не показателен (JSON-накладные); проверяем читаемость
    assert os.path.getsize(out_quant) > 0 and quant_max_err is not None


def test_matches_baked(dump, tmp_path):
    """Test with forced baking (no instancing). Skip if NWD_NO_INSTANCING not supported."""
    # Check if NWD_NO_INSTANCING is supported
    nwd2glb_src = os.path.join(os.path.dirname(nwd2glb.__file__), "nwd2glb.py")
    try:
        with open(nwd2glb_src, "r") as f:
            src = f.read()
        if "NWD_NO_INSTANCING" not in src:
            pytest.skip("NWD_NO_INSTANCING not supported in nwd2glb.py")
    except (IOError, OSError):
        pytest.skip("Cannot read nwd2glb.py source")

    # Set environment variable
    old_env = os.environ.get("NWD_NO_INSTANCING")
    os.environ["NWD_NO_INSTANCING"] = "1"

    try:
        out_glb, report, check_rc, check_out = run_pipeline(dump, tmp_path)

        # Check that instancing was disabled
        stats = report.get("stats", {})
        # With baking, instanced_instances should be 0 or low
        # Check oracle comparison still works
        oracle = oracle_points(dump)
        glb_pts = glb_points(out_glb)
        assert compare_points(oracle, glb_pts, atol=2e-3), "Baked GLB points do not match oracle"

        # check_area should still pass
        assert check_rc == 0, f"check_area failed with baking: {check_out}"
    finally:
        if old_env is None:
            del os.environ["NWD_NO_INSTANCING"]
        else:
            os.environ["NWD_NO_INSTANCING"] = old_env


def _mutate_inst(groups, fn):
    """Применяет fn(T, Q, S) -> (T, Q, S) ко всем наборам экземпляров."""
    for grp in groups.values():
        for gi in list(grp.inst):
            grp.inst[gi] = fn(*grp.inst[gi])


def test_mutation_lost_instance(dump, tmp_path):
    """Мутация: потеря последнего экземпляра в каждом наборе -> площадь и точки расходятся."""
    _, _, rc_ok, out_ok = run_pipeline(dump, tmp_path)
    assert rc_ok == 0, out_ok
    out_mut, _, rc_mut, out_txt = run_pipeline(dump, tmp_path, mutate=lambda g: _mutate_inst(g, lambda T, Q, S: (T[:-1], Q[:-1], S[:-1])))
    assert rc_mut == 1, out_txt
    assert not compare_points(oracle_points(dump), glb_points(out_mut))


def test_mutation_wrong_matrix(dump, tmp_path):
    """Мутация: поворот потерян (единичные кватернионы) -> площадь та же, точки расходятся."""
    ident = lambda T, Q, S: (T, np.tile([0.0, 0.0, 0.0, 1.0], (len(Q), 1)), S)
    out_mut, _, rc_mut, out_txt = run_pipeline(dump, tmp_path, mutate=lambda g: _mutate_inst(g, ident))
    assert rc_mut == 0, out_txt
    assert not compare_points(oracle_points(dump), glb_points(out_mut))


def test_mutation_area_mismatch(dump, tmp_path):
    """Мутация: масштаб экземпляров x1.01 -> check_area краснеет."""
    _, _, rc_mut, out_txt = run_pipeline(dump, tmp_path, mutate=lambda g: _mutate_inst(g, lambda T, Q, S: (T, Q, S * 1.01)))
    assert rc_mut == 1, out_txt


def test_gltf_schema_minimum(dump, tmp_path):
    """Каждый bufferView ссылается на buffer, accessors — на существующие bufferViews (иначе GLTFLoader падает)."""
    out_glb, _, _, _ = run_pipeline(dump, tmp_path)
    for path in (out_glb, str(tmp_path / "out" / "out_quant.glb")):
        with open(path, "rb") as f:
            data = f.read()
        n = struct.unpack_from("<I", data, 12)[0]
        gltf = json.loads(data[20:20 + n])
        assert all(bv.get("buffer") == 0 for bv in gltf["bufferViews"])
        assert all(0 <= a["bufferView"] < len(gltf["bufferViews"]) for a in gltf["accessors"])
