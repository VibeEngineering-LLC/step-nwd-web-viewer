import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import struct
import re
import sys
import collections
import pathlib
import numpy as np

import colorsys
FAM = [("Приток", 212), ("Вытяжка", 24), ("Местный отсос", 300), ("Воздухозабор", 150), ("Выброс", 0),
       ("Холодоснабжение", 186), ("Система VRV", 270), ("Подпор", 95), ("Строительные", 45)]
def build_palette(names):
    out, seen = {}, {}
    for n in sorted(names):
        hue = next((h for p, h in FAM if n.startswith(p)), 60)
        k = seen.get(hue, 0); seen[hue] = k + 1
        h = (hue + (k % 5 - 2) * 9) / 360.0
        s = 0.9 if k % 2 == 0 else 0.75
        l = 0.42 + 0.07 * ((k // 2) % 3)
        out[n] = tuple(round(x, 3) for x in colorsys.hls_to_rgb(h, l, s))
    return out
def main():
    if len(sys.argv) < 3:
        print("Usage: python nwd2glb.py DUMPDIR OUT.glb [REPORT.json]", file=sys.stderr)
        sys.exit(1)
    
    dump_dir = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2])
    report_path = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None

    scene_path = dump_dir / "scene.json"
    geoms_path = dump_dir / "geoms.bin"

    with open(scene_path, 'r', encoding='utf-8') as f:
        scene = json.load(f)
    
    if not scene.get("models"):
        raise ValueError("No models found in scene.json")
    
    model = scene["models"][0]
    name = model.get("name", "model")
    mpu = model.get("meters_per_unit", 1.0)
    if not isinstance(mpu, (int, float)) or mpu <= 0:
        mpu = 1.0

    nodes = model.get("nodes", [])
    instances = model.get("instances", [])
    PALETTE = build_palette(model.get("systems", []))
    geoms = model.get("geoms", [])
    overrides = scene.get("overrides", [])

    # Build node structure
    node_by_id = {n["id"]: n for n in nodes}
    parent_map = {n["id"]: n.get("parent", -1) for n in nodes}

    # Determine hidden nodes
    hidden = set()
    for ov in overrides:
        if "path" in ov:
            path = ov["path"]
            flags = ov.get("flags", 0)
            mv = ov.get("mv", 0)
            is_hidden = False
            if mv == 1:
                mask = flags & 0xFF
                value = (flags >> 8) & 0xFF
                if (mask & 1) and (value & 1):
                    is_hidden = True
            else:
                if flags & 1:
                    is_hidden = True
            if is_hidden:
                hidden.add(path)
        elif "nodes" in ov:
            ids = ov["nodes"]
            flags = ov.get("flags", 0)
            mv = ov.get("mv", 0)
            is_hidden = False
            if mv == 1:
                mask = flags & 0xFF
                value = (flags >> 8) & 0xFF
                if (mask & 1) and (value & 1):
                    is_hidden = True
            else:
                if flags & 1:
                    is_hidden = True
            if is_hidden:
                for nid in ids:
                    hidden.add(nid)

    # Load geometry data
    geom_data = {}
    if geoms_path.exists():
        with open(geoms_path, 'rb') as f:
            data = f.read()
        for g in geoms:
            gid = g.get("id", 0)
            pass
        
        # We will load on demand or pre-load all valid ones
        for i, g in enumerate(geoms):
            if g.get("type") == 94 and g.get("vn", 0) > 0 and g.get("in", 0) > 0:
                vo = g["vo"]
                vn = g["vn"]
                io = g["io"]
                in_count = g["in"]
                
                # Vertices: vn*3 float32
                v_bytes = data[vo : vo + vn*3*4]
                verts = np.frombuffer(v_bytes, dtype=np.float32).reshape(vn, 3).astype(np.float64)
                
                # Indices: in uint32
                i_bytes = data[io : io + in_count*4]
                inds = np.frombuffer(i_bytes, dtype=np.uint32)
                
                geom_data[i] = (verts, inds)

    # Process instances
    # Stats
    stats = {
        "nodes": len(nodes),
        "instances_total": len(instances),
        "visible_instances": 0,
        "skipped_hidden": 0,
        "skipped_no_geom": 0,
        "skipped_not_tri": 0,
        "skipped_empty": 0,
        "triangles_written": 0,
        "vertices_written": 0,
        "sources": 0,
        "categories": 0,
        "color_groups": 0,
        "materials": 0
    }

    # Grouping structure
    # Key: (source_label, category, color_tuple, transparency)
    # Value: { "verts": [], "inds": [], "instances": 0, "triangles": 0 }
    groups = collections.defaultdict(lambda: {"verts": [], "inds": [], "instances": 0, "triangles": 0})
    
    # Source tracking for report
    source_stats = collections.defaultdict(lambda: {"instances": 0, "triangles": 0, "categories": collections.defaultdict(lambda: {"instances": 0, "triangles": 0})})
    
    # Material tracking
    materials = {} # key: (color, trans) -> index

    # Regex for source
    src_re = re.compile(r'\.(ifc|dwfx?|nwc|dwg|rvt)$', re.IGNORECASE)
    cat_re = re.compile(r'\s*\(\d+\)$')

    # Global bounding box accumulation
    all_min = np.array([np.inf, np.inf, np.inf])
    all_max = np.array([-np.inf, -np.inf, -np.inf])

    for inst in instances:
        path = inst["path"]
        geom_idx = inst["geom"]
        
        # Check visibility
        # Chain from path to root
        chain = []
        cur = path
        while cur != -1:
            if cur not in node_by_id:
                break
            chain.append(cur)
            cur = parent_map.get(cur, -1)
        
        # Check if any node in chain is hidden
        is_hidden = False
        for nid in chain:
            if nid in hidden:
                is_hidden = True
                break
        
        if is_hidden:
            stats["skipped_hidden"] += 1
            continue

        # Check geometry
        if geom_idx == -1:
            stats["skipped_no_geom"] += 1
            continue
            
        if geom_idx not in geom_data:
            # Check if it was skipped due to type/vn/in
            g = geoms[geom_idx] if geom_idx < len(geoms) else None
            if g is None:
                stats["skipped_no_geom"] += 1
            elif g.get("type") != 94 or g.get("vn", 0) == 0 or g.get("in", 0) == 0:
                stats["skipped_not_tri"] += 1
            else:
                stats["skipped_no_geom"] += 1
            continue

        verts, inds = geom_data[geom_idx]
        
        if len(verts) == 0 or len(inds) == 0:
            stats["skipped_empty"] += 1
            continue

        # Determine Source and Category
        # Chain is currently root-to-leaf? No, we built it leaf-to-root.
        # My loop: cur=path, then parent... so chain is [path, parent, ..., root].
        # Reverse it to get root-to-leaf.
        chain_root_to_leaf = list(reversed(chain))
        
        # Find source node
        source_node_id = None
        source_name = None
        for nid in chain_root_to_leaf:
            n = node_by_id.get(nid)
            if n:
                nm = n.get("name", "")
                if src_re.search(nm):
                    source_node_id = nid
                    source_name = nm
                    break
        
        if source_node_id is None:
            source_label = "Прочее"
            # "rest of the chain starts after the root"
            # If no source found, source is "Прочее".
            # Rest = chain after source node. Since source is virtual/root?
            # This implies the "source" is effectively the root, so rest is chain[1:]?
            # Or does it mean the source is not part of the chain, so rest is the whole chain?
            # "rest of the chain starts after the root" -> chain[1:]
            rest = chain_root_to_leaf[1:] if len(chain_root_to_leaf) > 1 else []
        else:
            # Source label = name with extension removed
            source_label = re.sub(src_re, '', source_name)
            # Rest = chain after the source node
            # Find index of source_node_id in chain_root_to_leaf
            try:
                idx = chain_root_to_leaf.index(source_node_id)
                rest = chain_root_to_leaf[idx+1:]
            except ValueError:
                rest = []

        # Determine Category
        category = ""
        if source_name and source_name.lower().endswith(".ifc"):
            # category = names of rest[3] and rest[4] joined with " · "
            # missing levels are skipped; empty names skipped.
            # This phrasing is slightly ambiguous. "missing levels are skipped" usually means if index doesn't exist.
            # "empty names skipped" means if name is empty, don't include it?
            parts = []
            if len(rest) > 3:
                nm3 = node_by_id.get(rest[3], {}).get("name", "")
                if nm3:
                    parts.append(nm3)
            if len(rest) > 4:
                nm4 = node_by_id.get(rest[4], {}).get("name", "")
                if nm4:
                    parts.append(nm4)
            category = " · ".join(parts)
        else:
            # category = name of the first node in rest with a non-empty name, with trailing (digits) removed
            for nid in rest:
                nm = node_by_id.get(nid, {}).get("name", "")
                if nm:
                    category = cat_re.sub('', nm)
                    break
        
        if not category:
            category = "Без категории"

        # Geometry Baking
        m = inst.get("m", [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1])
        M = np.array(m, dtype=np.float64).reshape(4, 4).T # Column-major input -> Transpose to get row-major matrix for multiplication?
        # If m is column-major, m[0..3] is col 0.
        # Standard matrix multiplication: v' = M * v.
        # If M is stored column-major in memory, and we reshape(4,4), we get a matrix where M[i,j] is element at row i, col j?
        # numpy reshape is row-major by default.
        # If input list is [c0r0, c0r1, c0r2, c0r3, c1r0...], and we reshape(4,4), we get:
        # [[c0r0, c0r1, c0r2, c0r3],
        #  [c1r0, c1r1, c1r2, c1r3], ...]
        # This is NOT the standard matrix where M[i,j] is row i col j.
        # The spec says: "M = numpy.array(m).reshape(4,4).T"
        # Let's follow the spec exactly.
        
        # Homogeneous vertices
        V = verts
        V_h = np.hstack([V, np.ones((len(V), 1))])
        
        # World = M @ V_h
        # If M is defined as above, we do M @ V_h.T ? Or V_h @ M.T?
        # Standard: v' = M * v. In numpy, if v is column vector (4,1), M is (4,4), result is M @ v.
        # If V_h is (N, 4), we want (N, 4) result.
        # v'_i = sum_j M_ij v_j.
        # In numpy: V_h @ M.T gives (N,4) where result[i,k] = sum_j V_h[i,j] M.T[j,k] = sum_j V_h[i,j] M[k,j].
        # This is v' = M * v if M is the matrix.
        # Let's check the spec's M definition again.
        # "M = numpy.array(m).reshape(4,4).T"
        # So world = M @ V_h.T -> (4, N). Then transpose back.
        # Or simply: world = V_h @ M.T
        
        world = V_h @ M.T
        
        # Extract xyz
        world_xyz = world[:, :3]
        
        # Scale
        world_xyz = world_xyz * mpu
        
        # Z-up to Y-up: (x, y, z) -> (x, z, -y)
        # Input is (x, y, z) in world coords (Z-up).
        # Output (x, z, -y) in Y-up coords.
        new_x = world_xyz[:, 0]
        new_y = world_xyz[:, 2]
        new_z = -world_xyz[:, 1]
        
        baked_verts = np.column_stack((new_x, new_y, new_z))

        # Check determinant for winding
        # "det of the upper-left 3x3 of M"
        # M is the matrix defined in spec.
        det = np.linalg.det(M[:3, :3])
        reverse_winding = det < 0

        # Group Key
        c = inst.get("c", [1,1,1])
        t = inst.get("t", 0.0)
        sid = inst.get("s", -1)
        sysname = model["systems"][sid] if 0 <= sid < len(model.get("systems", [])) else ""
        if sysname:
            c = PALETTE[sysname]
            if "Covering" in category:
                t = 0.7  # изоляция полупрозрачная, чтобы был виден цвет воздуховода внутри
        color_key = (round(c[0], 3), round(c[1], 3), round(c[2], 3))
        trans_key = round(t, 2)

        group_key = (source_label, category, color_key, trans_key, sysname)
        
        # Update Group
        g = groups[group_key]
        base = g.get("nv", 0)
        g["nv"] = base + len(baked_verts)
        inds = inds.astype(np.int64) + base
        g["verts"].append(baked_verts)
        
        # Indices
        if reverse_winding:
            # Swap 2nd and 3rd index of every triangle
            # inds is flat array of uint32, length 3 * num_tris
            inds_swapped = inds.copy()
            inds_swapped[1::3] = inds[2::3]
            inds_swapped[2::3] = inds[1::3]
            inds = inds_swapped
            
        g["inds"].append(inds)
        g["instances"] += 1
        g["triangles"] += len(inds) // 3

        # Update Source Stats
        ss = source_stats[source_label]
        ss["instances"] += 1
        ss["triangles"] += len(inds) // 3
        cat_stat = ss["categories"][category]
        cat_stat["instances"] += 1
        cat_stat["triangles"] += len(inds) // 3

        # Update Global BBox
        min_v = baked_verts.min(axis=0)
        max_v = baked_verts.max(axis=0)
        all_min = np.minimum(all_min, min_v)
        all_max = np.maximum(all_max, max_v)

        stats["visible_instances"] += 1

    # Finalize Groups
    # Merge vertices and indices for each group
    final_groups = {}
    for key, data in groups.items():
        if not data["verts"]:
            continue
        verts = np.vstack(data["verts"])
        inds = np.concatenate(data["inds"])
        
        # Offset indices
        # We need to assign global vertex offsets later?
        # This implies each group has its own vertex buffer?
        # "Group vertices as float32, indices as uint32 (use uint16 when the group has < 65536 vertices)."
        # This suggests each group is a separate mesh/primitive with its own bufferView?
        # Or one big buffer?
        # "One material per (color, transparency)"
        # "under each category one mesh node per color group"
        # This implies each color group is a separate mesh.
        # So each group has its own vertices and indices.
        
        # Determine index type
        if len(verts) < 65536:
            inds = inds.astype(np.uint16)
        else:
            inds = inds.astype(np.uint32)
            
        verts = verts.astype(np.float32)
        
        final_groups[key] = {
            "verts": verts,
            "inds": inds,
            "instances": data["instances"],
            "triangles": data["triangles"]
        }
        
        stats["triangles_written"] += data["triangles"]
        stats["vertices_written"] += len(verts)

    # Calculate Global Offset
    # "Shift all coordinates so the global bounding-box center in X and Z is at 0 and the global minimum Y is 0"
    offset = np.zeros(3)
    if stats["vertices_written"] > 0:
        center_xz = (all_min + all_max) / 2.0
        offset[0] = -center_xz[0]
        offset[2] = -center_xz[2]
        offset[1] = -all_min[1]
        
        # Apply offset to all groups
        for key in final_groups:
            final_groups[key]["verts"] += offset

    # Update stats
    stats["sources"] = len(source_stats)
    stats["categories"] = sum(len(ss["categories"]) for ss in source_stats.values())
    stats["color_groups"] = len(final_groups)
    
    # Prepare Materials
    # Key: (color, trans)
    mat_keys = set()
    for key in final_groups:
        color = key[2]
        trans = key[3]
        mat_keys.add((color, trans))
    
    # Assign material indices
    mat_list = []
    mat_index_map = {}
    for i, mk in enumerate(sorted(mat_keys)):
        color = mk[0]
        trans = mk[1]
        mat = {
            "pbrMetallicRoughness": {
                "baseColorFactor": [color[0], color[1], color[2], 1.0 - trans],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8
            },
            "doubleSided": True
        }
        if trans > 0.01:
            mat["alphaMode"] = "BLEND"
        mat_list.append(mat)
        mat_index_map[mk] = i
    
    stats["materials"] = len(mat_list)

    # Build glTF JSON
    gltf = {
        "asset": {"version": "2.0", "generator": "nwd2glb"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": mat_list,
        "accessors": [],
        "bufferViews": [],
        "buffers": []
    }

    # We need to write the binary data first to know sizes, or build it in memory.
    # Let's build the binary buffer in memory.
    bin_data = bytearray()
    
    # Helper to add bufferView and accessor
    def add_buffer_view(data, target=None):
        # Align to 4 bytes
        while len(bin_data) % 4 != 0:
            bin_data.append(0)
        offset = len(bin_data)
        bin_data.extend(data)
        bv = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data)
        }
        if target:
            bv["target"] = target
        gltf["bufferViews"].append(bv)
        return len(gltf["bufferViews"]) - 1

    def add_accessor(bv_index, component_type, count, type_str, min_val=None, max_val=None):
        acc = {
            "bufferView": bv_index,
            "componentType": component_type,
            "count": count,
            "type": type_str
        }
        if min_val is not None:
            acc["min"] = min_val
        if max_val is not None:
            acc["max"] = max_val
        gltf["accessors"].append(acc)
        return len(gltf["accessors"]) - 1

    # 5126: ARRAY_BUFFER, 34963: ELEMENT_ARRAY_BUFFER
    TARGET_ARRAY_BUFFER = 34962
    TARGET_ELEMENT_ARRAY_BUFFER = 34963

    # Build Hierarchy
    # Scene root node name = model name without extension
    root_name = re.sub(r'\.[^.]*$', '', name)
    
    # Group final_groups by source and category
    # Key: (source, category, color, trans)
    source_cat_groups = collections.defaultdict(lambda: collections.defaultdict(list))
    for key, data in final_groups.items():
        source = key[0]
        category = key[1]
        color = key[2]
        trans = key[3]
        source_cat_groups[source][category].append((key, data))

    # Create Nodes
    # Root Node
    root_node = {"name": root_name, "children": []}
    gltf["nodes"].append(root_node)
    root_node_idx = 0
    gltf["scenes"][0]["nodes"].append(root_node_idx)

    # Iterate sources
    for source in sorted(source_cat_groups.keys()):
        # Source Node
        src_node = {"name": source, "children": []}
        gltf["nodes"].append(src_node)
        src_node_idx = len(gltf["nodes"]) - 1
        root_node["children"].append(src_node_idx)

        cats = source_cat_groups[source]
        for category in sorted(cats.keys()):
            # Category Node
            cat_node = {"name": category, "children": []}
            gltf["nodes"].append(cat_node)
            cat_node_idx = len(gltf["nodes"]) - 1
            src_node["children"].append(cat_node_idx)

            # Color Groups under this category
            groups_in_cat = cats[category]
            # Sort by color/trans for consistent naming?
            # Order? Probably consistent. Let's sort by key.
            groups_in_cat.sort(key=lambda x: (x[0][2], x[0][3]))
            
            for k, (key, data) in enumerate(groups_in_cat, 1):
                # Mesh Node
                mesh_name = key[4] if key[4] else category
                if not key[4] and len(groups_in_cat) > 1:
                    mesh_name = f"{category} #{k}"
                
                # Create Mesh
                # Vertices
                verts = data["verts"]
                inds = data["inds"]
                
                # Write Vertices
                v_bytes = verts.tobytes()
                v_bv = add_buffer_view(v_bytes, TARGET_ARRAY_BUFFER)
                
                # Accessor for Position
                min_v = verts.min(axis=0).tolist()
                max_v = verts.max(axis=0).tolist()
                pos_acc = add_accessor(v_bv, 5126, len(verts), "VEC3", min_v, max_v)
                
                # Write Indices
                i_bytes = inds.tobytes()
                i_bv = add_buffer_view(i_bytes, TARGET_ELEMENT_ARRAY_BUFFER)
                
                # Accessor for Indices
                if inds.dtype == np.uint16:
                    comp_type = 5123
                else:
                    comp_type = 5125
                ind_acc = add_accessor(i_bv, comp_type, len(inds), "SCALAR")
                
                # Material Index
                mat_idx = mat_index_map[(key[2], key[3])]
                
                # Primitive
                primitive = {
                    "attributes": {"POSITION": pos_acc},
                    "indices": ind_acc,
                    "material": mat_idx,
                    "mode": 4
                }
                
                mesh = {
                    "name": mesh_name,
                    "primitives": [primitive]
                }
                gltf["meshes"].append(mesh)
                mesh_idx = len(gltf["meshes"]) - 1
                
                # Node for Mesh
                mesh_node = {
                    "name": mesh_name,
                    "mesh": mesh_idx
                }
                gltf["nodes"].append(mesh_node)
                mesh_node_idx = len(gltf["nodes"]) - 1
                if key[4]:
                    sys_node = {"name": key[4], "children": [mesh_node_idx]}
                    gltf["nodes"].append(sys_node)
                    cat_node["children"].append(len(gltf["nodes"]) - 1)
                else:
                    cat_node["children"].append(mesh_node_idx)

    # Buffer
    gltf["buffers"] = [{"byteLength": len(bin_data)}]

    # Serialize JSON
    json_str = json.dumps(gltf, separators=(',', ':'))
    json_bytes = json_str.encode('utf-8')
    
    # Pad JSON to 4 bytes
    json_padding = (4 - (len(json_bytes) % 4)) % 4
    json_bytes += b' ' * json_padding
    
    # Pad BIN to 4 bytes
    bin_padding = (4 - (len(bin_data) % 4)) % 4
    bin_data += b'\x00' * bin_padding

    # GLB Header
    # Magic: glTF (0x46546C67)
    # Version: 2
    # Length: total
    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    
    glb = bytearray()
    glb.extend(struct.pack('<I', 0x46546C67)) # glTF
    glb.extend(struct.pack('<I', 2)) # Version
    glb.extend(struct.pack('<I', total_length)) # Length
    
    # JSON Chunk
    glb.extend(struct.pack('<I', len(json_bytes)))
    glb.extend(struct.pack('<I', 0x4E4F534A)) # JSON
    glb.extend(json_bytes)
    
    # BIN Chunk
    glb.extend(struct.pack('<I', len(bin_data)))
    glb.extend(struct.pack('<I', 0x004E4942)) # BIN
    glb.extend(bin_data)

    # Write GLB
    with open(out_path, 'wb') as f:
        f.write(glb)

    # Report
    report = {
        "stats": stats,
        "sources": [],
        "bounding_box": {
            "min": all_min.tolist() if stats["vertices_written"] > 0 else [0,0,0],
            "max": all_max.tolist() if stats["vertices_written"] > 0 else [0,0,0]
        },
        "offset": offset.tolist(),
        "glb_size": len(glb)
    }
    
    # Add source details
    for source in sorted(source_stats.keys()):
        ss = source_stats[source]
        src_entry = {
            "source": source,
            "instances": ss["instances"],
            "triangles": ss["triangles"],
            "categories": []
        }
        for cat_name in sorted(ss["categories"].keys()):
            cs = ss["categories"][cat_name]
            src_entry["categories"].append({
                "name": cat_name,
                "instances": cs["instances"],
                "triangles": cs["triangles"]
            })
        report["sources"].append(src_entry)

    # Print Report
    print(json.dumps(report, indent=2, ensure_ascii=False))
    
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
