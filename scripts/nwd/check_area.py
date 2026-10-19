#!/usr/bin/env python3
"""check_area.py — сверка площадей дамп ↔ GLB (потоково, с поддержкой EXT_mesh_gpu_instancing)."""

import sys
import os
import re
import struct
import json
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

import nwdscene as ns


def _read_glb(path):
    """Read GLB file, return (json_chunk_dict, bin_chunk_bytes)."""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception as e:
        print(f"Error reading {path}: {e}")
        sys.exit(1)

    if len(data) < 12:
        print(f"Invalid GLB {path}: too short")
        sys.exit(1)

    magic, version, length = struct.unpack_from('<III', data, 0)
    if magic != 0x46546C67:  # 'glTF'
        print(f"Invalid GLB {path}: bad magic")
        sys.exit(1)
    if version != 2:
        print(f"Invalid GLB {path}: unsupported version {version}")
        sys.exit(1)
    if length != len(data):
        print(f"Invalid GLB {path}: length mismatch")
        sys.exit(1)

    offset = 12
    json_chunk = None
    bin_chunk = None

    while offset < len(data):
        if offset + 8 > len(data):
            break
        chunk_len, chunk_type = struct.unpack_from('<II', data, offset)
        offset += 8
        if chunk_type == 0x4E4F534A:  # 'JSON'
            json_bytes = data[offset:offset + chunk_len]
            try:
                json_chunk = json.loads(json_bytes.decode('utf-8'))
            except Exception as e:
                print(f"Invalid GLB {path}: bad JSON chunk: {e}")
                sys.exit(1)
            offset += chunk_len
        elif chunk_type == 0x004E4942:  # 'BIN\0'
            bin_chunk = data[offset:offset + chunk_len]
            offset += chunk_len
        else:
            offset += chunk_len

    if json_chunk is None:
        print(f"Invalid GLB {path}: no JSON chunk")
        sys.exit(1)

    return json_chunk, bin_chunk


def _get_accessor_data(glb_json, bin_chunk, accessor_idx):
    """Read accessor data from GLB, handling byteOffset, byteStride, component types."""
    accessors = glb_json.get('accessors', [])
    if accessor_idx < 0 or accessor_idx >= len(accessors):
        return None

    acc = accessors[accessor_idx]
    bv_idx = acc.get('bufferView')
    if bv_idx is None:
        return None

    buffer_views = glb_json.get('bufferViews', [])
    if bv_idx < 0 or bv_idx >= len(buffer_views):
        return None

    bv = buffer_views[bv_idx]
    if bin_chunk is None:
        return None

    bv_offset = bv.get('byteOffset', 0)
    acc_offset = acc.get('byteOffset', 0)
    start = bv_offset + acc_offset

    comp_type = acc.get('componentType')
    count = acc.get('count', 0)
    type_str = acc.get('type', 'SCALAR')

    # Determine number of components per element
    type_map = {
        'SCALAR': 1,
        'VEC2': 2,
        'VEC3': 3,
        'VEC4': 4,
        'MAT2': 4,
        'MAT3': 9,
        'MAT4': 16,
    }
    ncomp = type_map.get(type_str, 1)

    # Component type sizes
    comp_size_map = {
        5120: 1,   # BYTE
        5121: 1,   # UNSIGNED_BYTE
        5122: 2,   # SHORT
        5123: 2,   # UNSIGNED_SHORT
        5125: 4,   # UNSIGNED_INT
        5126: 4,   # FLOAT
    }
    comp_size = comp_size_map.get(comp_type, 4)
    elem_size = ncomp * comp_size

    # Check byteStride
    byte_stride = bv.get('byteStride')
    if byte_stride is not None and byte_stride != elem_size:
        # Interleaved data
        total_bytes = start + count * byte_stride
        if total_bytes > len(bin_chunk):
            return None
        raw = np.frombuffer(bin_chunk, dtype=np.uint8, count=count * byte_stride, offset=start)
        # Reshape to (count, byte_stride) and extract
        raw = raw.reshape(count, byte_stride)
        # Extract the element portion
        elem_bytes = raw[:, :elem_size]
        # Convert based on component type
        if comp_type == 5126:  # FLOAT
            arr = elem_bytes.astype(np.float32).reshape(count, ncomp)
        elif comp_type == 5123:  # UNSIGNED_SHORT
            arr = elem_bytes.astype(np.uint16).reshape(count, ncomp)
        elif comp_type == 5125:  # UNSIGNED_INT
            arr = elem_bytes.astype(np.uint32).reshape(count, ncomp)
        elif comp_type == 5122:  # SHORT
            arr = elem_bytes.astype(np.int16).reshape(count, ncomp)
        elif comp_type == 5121:  # UNSIGNED_BYTE
            arr = elem_bytes.astype(np.uint8).reshape(count, ncomp)
        elif comp_type == 5120:  # BYTE
            arr = elem_bytes.astype(np.int8).reshape(count, ncomp)
        else:
            return None
        return arr
    else:
        total_bytes = start + count * elem_size
        if total_bytes > len(bin_chunk):
            return None
        if comp_type == 5126:  # FLOAT
            arr = np.frombuffer(bin_chunk, dtype=np.float32, count=count * ncomp, offset=start)
        elif comp_type == 5123:  # UNSIGNED_SHORT
            arr = np.frombuffer(bin_chunk, dtype=np.uint16, count=count * ncomp, offset=start)
        elif comp_type == 5125:  # UNSIGNED_INT
            arr = np.frombuffer(bin_chunk, dtype=np.uint32, count=count * ncomp, offset=start)
        elif comp_type == 5122:  # SHORT
            arr = np.frombuffer(bin_chunk, dtype=np.int16, count=count * ncomp, offset=start)
        elif comp_type == 5121:  # UNSIGNED_BYTE
            arr = np.frombuffer(bin_chunk, dtype=np.uint8, count=count * ncomp, offset=start)
        elif comp_type == 5120:  # BYTE
            arr = np.frombuffer(bin_chunk, dtype=np.int8, count=count * ncomp, offset=start)
        else:
            return None
        if ncomp == 1:
            return arr
        else:
            return arr.reshape(count, ncomp)


def _quat_to_rot_matrix(q):
    """Convert quaternion (x, y, z, w) to 3x3 rotation matrix (column-major convention).
    Returns A such that world = A @ local (column vector).
    """
    x, y, z, w = q
    # Standard rotation matrix from quaternion
    # R = [[1-2(y^2+z^2), 2(xy-wz), 2(xz+wy)],
    #      [2(xy+wz), 1-2(x^2+z^2), 2(yz-wx)],
    #      [2(xz-wy), 2(yz+wx), 1-2(x^2+y^2)]]
    # This is the matrix that rotates a column vector.
    # A = R (column-major: A @ v_local = v_world)
    A = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]
    ])
    return A


def _compute_area_from_cofactors(nrm, L):
    """Compute area using cofactor formula.
    nrm: (T, 3) - cross products of local triangle edges
    L: (3, 3) - linear part (row-major: w = v @ L)
    Returns: scalar area contribution = 0.5 * sum_t |nrm_t @ C|
    where C has rows c_k = cross(L[(k+1)%3], L[(k+2)%3])
    """
    # C rows: c_k = cross(L[(k+1)%3], L[(k+2)%3])
    # L is (3,3), rows are L[0], L[1], L[2]
    # c_0 = cross(L[1], L[2])
    # c_1 = cross(L[2], L[0])
    # c_2 = cross(L[0], L[1])
    c0 = np.cross(L[1], L[2])
    c1 = np.cross(L[2], L[0])
    c2 = np.cross(L[0], L[1])
    C = np.array([c0, c1, c2])  # (3, 3)

    # nrm @ C: (T, 3) @ (3, 3) = (T, 3)
    # Each row is nrm_t @ C, which gives (nrm_t · c0, nrm_t · c1, nrm_t · c2)
    # Wait, the formula says |nrm_t @ C| where C is (3,3).
    # nrm_t is (1,3), C is (3,3), so nrm_t @ C is (1,3).
    # |nrm_t @ C| means the norm of this vector? Or sum of absolute values?
    # Looking at the problem: "Площадь экземпляра = 0.5 * mpu**2 * Σ_t |nrm_t @ C|"
    # This is the standard formula: area = 0.5 * |nrm| * |L| where |L| is the area scaling factor.
    # Actually, the correct formula for area transformation is:
    # area_world = 0.5 * |nrm_local| * sqrt(det(L^T L))
    # But the cofactor formula gives: area = 0.5 * sum_t |nrm_t · c_k| for k=0,1,2? No.
    # Let me re-read: "C — матрица (3,3) с строками c_k = cross(L[(k+1)%3], L[(k+2)%3])"
    # "Площадь экземпляра = 0.5 * mpu**2 * Σ_t |nrm_t @ C|"
    # I think |nrm_t @ C| means the Euclidean norm of the vector nrm_t @ C.
    # nrm_t @ C is a 1x3 vector. Its norm is sqrt((nrm_t·c0)^2 + (nrm_t·c1)^2 + (nrm_t·c2)^2).
    # This is indeed the correct formula for area scaling.
    
    # Compute nrm @ C for all triangles
    # nrm: (T, 3), C: (3, 3)
    # result: (T, 3)
    nc = nrm @ C  # (T, 3)
    # Norm of each row
    norms = np.sqrt(np.sum(nc * nc, axis=1))  # (T,)
    return 0.5 * np.sum(norms)


def _compute_area_batched(nrm, L, mpu=1.0):
    """Compute area in batches to avoid memory issues.
    nrm: (T, 3)
    L: (3, 3)
    mpu: scale factor
    """
    T = nrm.shape[0]
    if T == 0:
        return 0.0

    # Compute C
    c0 = np.cross(L[1], L[2])
    c1 = np.cross(L[2], L[0])
    c2 = np.cross(L[0], L[1])
    C = np.array([c0, c1, c2])  # (3, 3)

    # Process in batches to keep n*T*3 <= ~4e6
    max_elements = 4_000_000
    batch_size = max(1, max_elements // 3)  # 3 components per triangle

    total = 0.0
    for start in range(0, T, batch_size):
        end = min(start + batch_size, T)
        nrm_batch = nrm[start:end]  # (B, 3)
        nc = nrm_batch @ C  # (B, 3)
        norms = np.sqrt(np.sum(nc * nc, axis=1))  # (B,)
        total += np.sum(norms)

    return 0.5 * (mpu ** 2) * total


def compute_dump_area(dump_dir):
    """Compute reference areas from dump directory."""
    try:
        sc = ns.load_scene(dump_dir)
    except Exception as e:
        print(f"Error reading {dump_dir}: {e}")
        sys.exit(1)

    # Build hidden set
    hidden = set()
    for ov in sc.overrides:
        if 'path' in ov and 'flags' in ov:
            flags = ov['flags']
            if (flags & 1) and ((flags >> 8) & 1):
                hidden.add(ov['path'])

    src_re = re.compile(r'\.(ifc|dwfx?)$', re.I)
    hid, src = ns.annotate(sc, hidden, src_re, last=True)

    # Get valid instances
    idx = np.nonzero(sc.inst_midx >= 0)[0]
    if len(idx) == 0:
        return {}

    node = sc.inst_path[idx]
    mask = ~hid[node] & (src[node] != -1)
    idx_ok = idx[mask]

    if len(idx_ok) == 0:
        return {}

    # Group by geometry
    geoms = np.unique(sc.inst_geom[idx_ok])

    # For each geometry, collect instances and their source labels
    # Source label: name of node src[path] without last extension
    # We need to map node id -> source label
    # src[node] gives the node id of the source
    # sc.names is a list by node id

    # Build a mapping from node id to source label
    # For each valid instance, the source label is determined by src[node]
    # src[node] is the node id of the source node
    # The label is sc.names[src[node]] with last extension removed

    # Let's collect per geometry
    geom_to_instances = {}
    for i in range(len(idx_ok)):
        gi = sc.inst_geom[idx_ok[i]]
        node_id = sc.inst_path[idx_ok[i]]
        src_node_id = src[node_id]
        if src_node_id < 0 or src_node_id >= len(sc.names):
            continue
        name = sc.names[src_node_id]
        # Remove last extension
        label = name.rsplit('.', 1)[0] if '.' in name else name
        if gi not in geom_to_instances:
            geom_to_instances[gi] = []
        geom_to_instances[gi].append((idx_ok[i], label))

    # Open geometries
    try:
        mm = ns.open_geoms(sc)
    except Exception as e:
        print(f"Error reading geometries: {e}")
        sys.exit(1)

    # Compute areas per source label
    areas = {}  # label -> float64 area

    for gi, instances in geom_to_instances.items():
        # Read geometry
        try:
            verts, inds = ns.read_geom(sc, mm, gi)
        except Exception as e:
            print(f"Error reading geometry {gi}: {e}")
            sys.exit(1)

        if len(verts) == 0 or len(inds) == 0:
            continue

        verts = verts.astype(np.float64)  # (vn, 3)
        inds = inds.astype(np.int64)  # (T*3,)

        if len(inds) % 3 != 0:
            continue

        T = len(inds) // 3
        if T == 0:
            continue

        # Compute normal vectors for each triangle
        # e1 = B - A, e2 = C - A
        A = verts[inds[0::3]]  # (T, 3)
        B = verts[inds[1::3]]  # (T, 3)
        C = verts[inds[2::3]]  # (T, 3)
        e1 = B - A  # (T, 3)
        e2 = C - A  # (T, 3)
        nrm = np.cross(e1, e2)  # (T, 3)

        # For each instance, compute area contribution
        mpu = sc.mpu
        for inst_idx, label in instances:
            midx = sc.inst_midx[inst_idx]
            mat = sc.inst_m[midx].reshape(4, 4)
            L = mat[:3, :3]  # (3, 3)

            # Compute area using cofactor formula
            area = _compute_area_batched(nrm, L, mpu)
            if label not in areas:
                areas[label] = 0.0
            areas[label] += area

    return areas


def compute_glb_area(glb_path):
    """Compute areas from GLB file."""
    glb_json, bin_chunk = _read_glb(glb_path)

    # Get scenes
    scenes = glb_json.get('scenes', [])
    if not scenes:
        return {}

    # Root node
    scene = scenes[0]
    root_nodes = scene.get('nodes', [])
    if not root_nodes:
        return {}

    root_node_idx = root_nodes[0]
    nodes = glb_json.get('nodes', [])
    if root_node_idx < 0 or root_node_idx >= len(nodes):
        return {}

    root_node = nodes[root_node_idx]
    children = root_node.get('children', [])

    # Each child is a source node
    # For each child, traverse its subtree and compute area
    areas = {}  # label -> float64 area

    # Cache for accessor data
    accessor_cache = {}

    def get_accessor_data_cached(acc_idx):
        if acc_idx in accessor_cache:
            return accessor_cache[acc_idx]
        data = _get_accessor_data(glb_json, bin_chunk, acc_idx)
        accessor_cache[acc_idx] = data
        return data

    def compute_node_area(node_idx, node_transform=None):
        """Compute area for a node and its subtree. Returns total area."""
        if node_idx < 0 or node_idx >= len(nodes):
            return 0.0

        node = nodes[node_idx]
        total_area = 0.0

        # Process this node's mesh if any
        mesh_idx = node.get('mesh')
        if mesh_idx is not None:
            meshes = glb_json.get('meshes', [])
            if mesh_idx < 0 or mesh_idx >= len(meshes):
                return total_area

            mesh = meshes[mesh_idx]
            primitives = mesh.get('primitives', [])

            for prim in primitives:
                # Check for EXT_mesh_gpu_instancing
                extensions = node.get('extensions', {})
                instancing_ext = (extensions.get('EXT_mesh_gpu_instancing') or {}).get('attributes')

                if instancing_ext is not None:
                    # Instanced mesh
                    # Get POSITION and indices
                    attrs = prim.get('attributes', {})
                    pos_acc_idx = attrs.get('POSITION')
                    idx_acc_idx = prim.get('indices')

                    if pos_acc_idx is None:
                        continue

                    pos_data = get_accessor_data_cached(pos_acc_idx)
                    if pos_data is None:
                        continue

                    # Check component type
                    accessors = glb_json.get('accessors', [])
                    if pos_acc_idx < 0 or pos_acc_idx >= len(accessors):
                        continue
                    pos_acc = accessors[pos_acc_idx]
                    if pos_acc.get('componentType') != 5126:  # FLOAT
                        continue

                    if idx_acc_idx is not None:
                        idx_data = get_accessor_data_cached(idx_acc_idx)
                        if idx_data is None:
                            continue
                        # Check index type
                        if idx_acc_idx < 0 or idx_acc_idx >= len(accessors):
                            continue
                        idx_acc = accessors[idx_acc_idx]
                        idx_comp_type = idx_acc.get('componentType')
                        if idx_comp_type not in (5123, 5125):  # UNSIGNED_SHORT, UNSIGNED_INT
                            continue
                        if len(idx_data) % 3 != 0:
                            continue
                    else:
                        idx_data = None

                    # Get instancing attributes
                    trans_acc_idx = instancing_ext.get('TRANSLATION')
                    rot_acc_idx = instancing_ext.get('ROTATION')
                    scale_acc_idx = instancing_ext.get('SCALE')

                    # Defaults
                    if trans_acc_idx is not None:
                        trans_data = get_accessor_data_cached(trans_acc_idx)
                        if trans_data is None:
                            continue
                        if trans_data.shape[1] != 3:
                            continue
                    else:
                        trans_data = np.zeros((1, 3), dtype=np.float64)

                    if rot_acc_idx is not None:
                        rot_data = get_accessor_data_cached(rot_acc_idx)
                        if rot_data is None:
                            continue
                        if rot_data.shape[1] != 4:
                            continue
                    else:
                        rot_data = np.array([[0, 0, 0, 1]], dtype=np.float64)

                    if scale_acc_idx is not None:
                        scale_data = get_accessor_data_cached(scale_acc_idx)
                        if scale_data is None:
                            continue
                        if scale_data.shape[1] != 3:
                            continue
                    else:
                        scale_data = np.ones((1, 3), dtype=np.float64)

                    n_inst = min(len(trans_data), len(rot_data), len(scale_data))
                    if n_inst == 0:
                        continue

                    trans_data = trans_data[:n_inst].astype(np.float64)
                    rot_data = rot_data[:n_inst].astype(np.float64)
                    scale_data = scale_data[:n_inst].astype(np.float64)

                    # Compute local triangle normals
                    if idx_data is not None:
                        inds = idx_data.astype(np.int64)
                        T = len(inds) // 3
                        A = pos_data[inds[0::3]].astype(np.float64)
                        B = pos_data[inds[1::3]].astype(np.float64)
                        C = pos_data[inds[2::3]].astype(np.float64)
                        e1 = B - A
                        e2 = C - A
                        nrm = np.cross(e1, e2)  # (T, 3)
                    else:
                        # No indices, use all vertices as triangles (sequential)
                        vn = len(pos_data)
                        if vn < 3:
                            continue
                        T = vn // 3
                        if T == 0:
                            continue
                        A = pos_data[0::3].astype(np.float64)
                        B = pos_data[1::3].astype(np.float64)
                        C = pos_data[2::3].astype(np.float64)
                        e1 = B - A
                        e2 = C - A
                        nrm = np.cross(e1, e2)  # (T, 3)

                    # For each instance, compute area
                    for i in range(n_inst):
                        q = rot_data[i]  # (x, y, z, w)
                        s = scale_data[i]  # (3,)
                        # A_i = R(q) * diag(s)
                        R = _quat_to_rot_matrix(q)  # (3, 3)
                        A_i = R * s[np.newaxis, :]  # (3, 3), column scaling
                        # L_i = A_i^T (row-major)
                        L_i = A_i.T  # (3, 3)

                        area = _compute_area_batched(nrm, L_i, 1.0)
                        total_area += area

                else:
                    # Non-instanced mesh
                    attrs = prim.get('attributes', {})
                    pos_acc_idx = attrs.get('POSITION')
                    idx_acc_idx = prim.get('indices')

                    if pos_acc_idx is None:
                        continue

                    pos_data = get_accessor_data_cached(pos_acc_idx)
                    if pos_data is None:
                        continue

                    # Check component type
                    accessors = glb_json.get('accessors', [])
                    if pos_acc_idx < 0 or pos_acc_idx >= len(accessors):
                        continue
                    pos_acc = accessors[pos_acc_idx]
                    if pos_acc.get('componentType') != 5126:  # FLOAT
                        continue

                    if idx_acc_idx is not None:
                        idx_data = get_accessor_data_cached(idx_acc_idx)
                        if idx_data is None:
                            continue
                        if idx_acc_idx < 0 or idx_acc_idx >= len(accessors):
                            continue
                        idx_acc = accessors[idx_acc_idx]
                        idx_comp_type = idx_acc.get('componentType')
                        if idx_comp_type not in (5123, 5125):
                            continue
                        if len(idx_data) % 3 != 0:
                            continue
                    else:
                        idx_data = None

                    # Compute area in local coordinates
                    if idx_data is not None:
                        inds = idx_data.astype(np.int64)
                        T = len(inds) // 3
                        A = pos_data[inds[0::3]].astype(np.float64)
                        B = pos_data[inds[1::3]].astype(np.float64)
                        C = pos_data[inds[2::3]].astype(np.float64)
                        e1 = B - A
                        e2 = C - A
                        nrm = np.cross(e1, e2)  # (T, 3)
                        area = 0.5 * np.sum(np.sqrt(np.sum(nrm * nrm, axis=1)))
                    else:
                        vn = len(pos_data)
                        if vn < 3:
                            continue
                        T = vn // 3
                        if T == 0:
                            continue
                        A = pos_data[0::3].astype(np.float64)
                        B = pos_data[1::3].astype(np.float64)
                        C = pos_data[2::3].astype(np.float64)
                        e1 = B - A
                        e2 = C - A
                        nrm = np.cross(e1, e2)  # (T, 3)
                        area = 0.5 * np.sum(np.sqrt(np.sum(nrm * nrm, axis=1)))

                    total_area += area

        # Recurse into children
        for child_idx in node.get('children', []):
            total_area += compute_node_area(child_idx)

        return total_area

    # For each child of root, compute area and assign to source label
    for child_idx in children:
        if child_idx < 0 or child_idx >= len(nodes):
            continue
        child_node = nodes[child_idx]
        label = child_node.get('name', '')
        if not label:
            continue

        area = compute_node_area(child_idx)
        if label not in areas:
            areas[label] = 0.0
        areas[label] += area

    return areas


def main():
    if len(sys.argv) != 3:
        print("Usage: python check_area.py DUMPDIR MODEL.glb")
        sys.exit(1)

    dump_dir = sys.argv[1]
    glb_path = sys.argv[2]

    # Compute reference areas from dump
    ref_areas = compute_dump_area(dump_dir)

    # Compute areas from GLB
    got_areas = compute_glb_area(glb_path)

    # Compare
    sources = sorted(ref_areas.keys())
    mismatch_count = 0

    for source in sources:
        ref = ref_areas[source]
        got = got_areas.get(source, 0.0)

        if ref > 0:
            pct = abs(got - ref) / ref * 100
        else:
            pct = 0.0 if got == 0 else 100.0

        if pct > 0.1:
            mismatch_count += 1

        print(f"{source}: дамп {ref:.1f} м2, GLB {got:.1f} м2, расхождение {pct:.3f} %")

    print(f"источников с расхождением >0,1 %: {mismatch_count}")

    if mismatch_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
