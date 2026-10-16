import sys
import os
import json
import numpy as np
import trimesh
import prc_parse

def loc_to_matrix(loc):
    M = np.eye(4)
    if loc is None:
        return M
    kind = loc.get('kind')
    if kind == 'general':
        m = loc.get('m')
        if m is not None:
            M = np.array(m, dtype=np.float64).reshape(4, 4).T
    elif kind == 'cart':
        X = np.array(loc.get('X', (1, 0, 0)), dtype=np.float64)
        Y = np.array(loc.get('Y', (0, 1, 0)), dtype=np.float64)
        behaviour = loc.get('behaviour', 0)
        if (behaviour & 2) or (behaviour & 0x20):
            Z = np.cross(X, Y)
            M[:3, 0] = X
            M[:3, 1] = Y
            M[:3, 2] = Z
        if 'scale' in loc:
            M[:3, :3] *= loc['scale']
        if 'scale3' in loc:
            s3 = np.array(loc['scale3'], dtype=np.float64)
            M[:3, 0] *= s3[0]
            M[:3, 1] *= s3[1]
            M[:3, 2] *= s3[2]
        if 'origin' in loc:
            M[:3, 3] = np.array(loc['origin'], dtype=np.float64)
    return M

def main():
    if len(sys.argv) != 4:
        print("Usage: python prc_scene.py <file.prc> <mesh_dir> <out.glb>")
        sys.exit(1)
    
    prc_path = sys.argv[1]
    mesh_dir = sys.argv[2]
    out_glb = sys.argv[3]
    
    res = prc_parse.parse_file(prc_path)
    
    fs_list = res.get('fs', [])
    uuid_to_fs = {}
    for i, fs in enumerate(fs_list):
        uuid = fs.get('uuid')
        if uuid is not None:
            uuid_to_fs[tuple(uuid)] = i
    
    assembly_fs_index = None
    for i in range(len(fs_list) - 1, -1, -1):
        fs = fs_list[i]
        tree = fs.get('tree')
        if tree is None:
            continue
        pos = tree.get('product_occurrences', [])
        if not pos:
            continue
        has_sons = any(len(po.get('sons', [])) > 0 for po in pos)
        if has_sons:
            assembly_fs_index = i
            break
    
    if assembly_fs_index is None:
        print("Error: No assembly file structure found.")
        sys.exit(1)
    
    assembly_fs = fs_list[assembly_fs_index]
    tree = assembly_fs['tree']
    pos = tree['product_occurrences']
    
    root_idx = tree.get('index_product_occurrence', -1)
    if root_idx is not None and 0 <= root_idx < len(pos):
        root_po_index = root_idx - 1
        if root_po_index < 0:
            root_po_index = len(pos) - 1
    else:
        root_po_index = len(pos) - 1
    
    mesh_cache = {}
    geom_name_cache = {}
    
    def get_mesh(fs_index):
        if fs_index in mesh_cache:
            return mesh_cache[fs_index]
        
        npz_path = os.path.join(mesh_dir, f"fs{fs_index:02d}.npz")
        if not os.path.exists(npz_path):
            mesh_cache[fs_index] = None
            return None
        
        try:
            data = np.load(npz_path)
            V = data['V']
            F = data['F']
            mesh = trimesh.Trimesh(vertices=V * 0.001, faces=F, process=False)
            mesh_cache[fs_index] = mesh
            
            fs = fs_list[fs_index]
            t = fs.get('tree')
            if t is not None:
                p_list = t.get('product_occurrences', [])
                if p_list:
                    name = p_list[0].get('name', f"Part{fs_index}")
                else:
                    name = f"Part{fs_index}"
            else:
                name = f"Part{fs_index}"
            
            geom_name = f"{name}#fs{fs_index}"
            geom_name_cache[fs_index] = geom_name
            return mesh
        except Exception as e:
            print(f"Warning: Failed to load mesh for fs{fs_index}: {e}")
            mesh_cache[fs_index] = None
            return None
    
    scene = trimesh.Scene()
    used_names = set()
    leaf_count = 0
    missing_leaves = []
    
    def make_unique_name(name):
        if name not in used_names:
            used_names.add(name)
            return name
        i = 2
        while f"{name}_{i}" in used_names:
            i += 1
        new_name = f"{name}_{i}"
        used_names.add(new_name)
        return new_name
    
    def add_po(po_index, parent_node_name):
        nonlocal leaf_count, missing_leaves
        
        po = pos[po_index]
        name = po.get('name') or f"PO{po_index}"
        node_name = make_unique_name(name)
        
        T = loc_to_matrix(po.get('location'))
        T[:3, 3] *= 0.001
        
        proto_fs = po.get('proto_fs')
        is_leaf = False
        mesh = None
        
        if proto_fs is not None:
            proto_fs_tuple = tuple(proto_fs)
            if proto_fs_tuple in uuid_to_fs:
                fs_index = uuid_to_fs[proto_fs_tuple]
                if fs_index != assembly_fs_index:
                    is_leaf = True
                    mesh = get_mesh(fs_index)
        
        if is_leaf:
            if mesh is not None:
                geom_name = geom_name_cache.get(fs_index, f"Part{fs_index}#fs{fs_index}")
                scene.add_geometry(
                    mesh,
                    node_name=node_name,
                    geom_name=geom_name,
                    parent_node_name=parent_node_name,
                    transform=T
                )
                leaf_count += 1
            else:
                scene.graph.update(
                    frame_to=node_name,
                    frame_from=parent_node_name,
                    matrix=T
                )
                missing_leaves.append(name)
        else:
            scene.graph.update(
                frame_to=node_name,
                frame_from=parent_node_name,
                matrix=T
            )
        
        sons = po.get('sons', [])
        for s in sons:
            if 0 <= s < len(pos):
                add_po(s, node_name)
    
    add_po(root_po_index, scene.graph.base_frame)
    
    scene.export(out_glb)
    
    print(f"Leaf parts with mesh: {leaf_count}")
    if missing_leaves:
        print(f"Missing leaf parts: {', '.join(missing_leaves)}")
    else:
        print("Missing leaf parts: none")
    
    if scene.geometry:
        bounds = scene.bounds
        bounds_mm = bounds * 1000
        min_b = np.round(bounds_mm[0], 1)
        max_b = np.round(bounds_mm[1], 1)
        extents = np.round(max_b - min_b, 1)
        print(f"Bounds (mm): min={min_b.tolist()}, max={max_b.tolist()}")
        print(f"Extents (mm): {extents.tolist()}")
    else:
        print("Bounds (mm): N/A")
        print("Extents (mm): N/A")

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
