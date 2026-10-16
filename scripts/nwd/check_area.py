import sys
import json
import struct
import re
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

def main():
    if len(sys.argv) != 3:
        print("Usage: python check_area.py DUMPDIR MODEL.glb")
        sys.exit(1)
    
    dumpdir = sys.argv[1]
    glb_path = sys.argv[2]
    
    # --- 1. Reference area from dump ---
    ref_areas = {}
    
    try:
        with open(f"{dumpdir}/scene.json", "r", encoding="utf-8") as f:
            scene_data = json.load(f)
    except Exception as e:
        print(f"Error reading scene.json: {e}")
        sys.exit(1)
        
    models = scene_data.get("models", [])
    if not models:
        print("No models found in scene.json")
        sys.exit(1)
        
    model = models[0]
    nodes = model.get("nodes", [])
    instances = model.get("instances", [])
    geoms = model.get("geoms", [])
    meters_per_unit = model.get("meters_per_unit", 1.0)
    
    # Build node map
    node_map = {n["id"]: n for n in nodes}
    
    # Determine hidden nodes
    hidden_nodes = set()
    overrides = scene_data.get("overrides", [])
    for ov in overrides:
        if "path" in ov and "flags" in ov:
            path = ov["path"]
            flags = ov["flags"]
            if (flags & 1) and ((flags >> 8) & 1):
                # The node at this path is hidden
                # path is a list of node ids? Or a single id? 
                # Usually path in such dumps is a list of ids from root to leaf, or just the id.
                # Or is path the id itself?
                # "the node is hidden when..." implies the node identified by path.
                # If path is a list, which node? Usually the leaf.
                # Let's check common formats. If path is [1, 2, 3], node 3 is hidden.
                # If path is an integer, that node is hidden.
                if isinstance(path, list):
                    if path:
                        hidden_nodes.add(path[-1])
                else:
                    hidden_nodes.add(path)
    
    # Load geometry data
    try:
        with open(f"{dumpdir}/geoms.bin", "rb") as f:
            geom_bin = f.read()
    except Exception as e:
        print(f"Error reading geoms.bin: {e}")
        sys.exit(1)
    
    # Regex for source name
    source_pattern = re.compile(r'\.(ifc|dwfx?)$', re.IGNORECASE)
    
    for inst in instances:
        geom_idx = inst.get("geom", -1)
        if geom_idx < 0 or geom_idx >= len(geoms):
            continue
            
        geom = geoms[geom_idx]
        if geom.get("type") != 94:
            continue
        if geom.get("in", 0) == 0:
            continue
            
        # Check visibility chain
        pid = inst.get("path", -1)
        path = []
        while pid != -1 and pid in node_map:
            path.append(pid)
            pid = node_map[pid].get("parent", -1)
        path.reverse()
        is_hidden = False
        # Traverse path (list of node ids)
        # We need to check if ANY node on the chain is hidden.
        # The chain is path -> parents -> root.
        # The 'path' in instance usually lists the nodes from root to leaf? Or leaf to root?
        # If path is [id1, id2, id3], we check id1, id2, id3.
        for nid in path:
            if nid in hidden_nodes:
                is_hidden = True
                break
        
        if is_hidden:
            continue
            
        # Determine source name
        # "Source = name of the LAST node on the chain (ordered root→leaf) whose name matches..."
        # We need the chain ordered root->leaf.
        # If 'path' is already root->leaf, we use it.
        # If 'path' is leaf->root, we reverse it.
        # Standard convention in many formats (like glTF) is that the hierarchy is defined by parent pointers.
        # If not, we might need to reconstruct. But spec says "path → parents → root", which is confusing.
        # "chain (path → parents → root)" might mean the list 'path' contains the nodes, and we go up to root?
        # Actually, if 'path' is the list of nodes in the instance, it's likely the full chain.
        
        chain_ids = path
        # Find the last node in the chain (root->leaf order) that matches the pattern
        source_name = None
        for nid in chain_ids:
            if nid not in node_map:
                continue
            name = node_map[nid].get("name", "")
            if source_pattern.search(name):
                # Remove extension
                source_name = name.rsplit('.', 1)[0]
                # "LAST node on the chain ... whose name matches"
                # Since we iterate root->leaf, the last match is the one closest to the leaf.
                # We keep updating source_name, so the final value is the last match.
                pass
        
        if source_name is None:
            continue
            
        # Calculate area
        vo = geom.get("vo", 0)
        vn = geom.get("vn", 0)
        io = geom.get("io", 0)
        in_count = geom.get("in", 0)
        
        # Read vertices
        # vn*3 float32 at byte offset vo
        v_bytes = vn * 3 * 4
        if vo + v_bytes > len(geom_bin):
            continue
        v_data = struct.unpack(f'<{vn*3}f', geom_bin[vo:vo+v_bytes])
        V = np.array(v_data, dtype=np.float32).reshape(vn, 3)
        
        # Read indices
        # in uint32 triangle indices at byte offset io
        i_bytes = in_count * 4
        if io + i_bytes > len(geom_bin):
            continue
        i_data = struct.unpack(f'<{in_count}I', geom_bin[io:io+i_bytes])
        I = np.array(i_data, dtype=np.uint32)
        
        # Transform
        m = inst.get("m", [])
        if len(m) != 16:
            continue
        M = np.array(m, dtype=np.float64).reshape(4, 4)
        
        # World vertices: W = (np.c_[V, ones] @ M)[:, :3] * meters_per_unit
        # This is a bit ambiguous. 
        # Standard: v' = M * v (column vector).
        # If M is stored row-major in memory, np.array(m).reshape(4,4) gives the matrix as seen in row-major.
        # If the matrix is "column-major" in mathematical sense (i.e., m[0..3] is first column), 
        # then storing it row-major means m[0] is M[0,0], m[1] is M[1,0], etc.
        # So np.array(m).reshape(4,4) creates a matrix where row i is m[4*i..4*i+3].
        # If the source data is column-major (col 0, col 1...), then m[0..3] is col 0.
        # So M[0,0]=m[0], M[1,0]=m[1], M[2,0]=m[2], M[3,0]=m[3].
        # This means the matrix in memory is the transpose of the standard mathematical matrix if we assume standard row-major storage of a row-major matrix.
        # Let's re-read: "row-vector times column-major matrix stored row-major equals M·v"
        # Let v be a row vector [x, y, z, 1].
        # Let A be the 4x4 matrix formed by np.array(m).reshape(4,4).
        # The operation is v @ A.
        # (v @ A)_j = sum_i v_i A_ij.
        # Standard column vector multiplication: w = M_std * v_col.
        # w_j = sum_i M_std_ji v_i.
        # If A is the transpose of M_std, then A_ij = M_std_ji.
        # Then (v @ A)_j = sum_i v_i M_std_ji = w_j.
        # So if the matrix 'm' is stored such that it represents the transpose of the standard transformation matrix, 
        # then v @ A gives the correct result.
        # The spec says "column-major matrix stored row-major".
        # A column-major matrix M_std has elements M_std[i,j] at memory offset j*4 + i.
        # If we store this in a row-major array 'm', then m[k] = M_std[k%4, k//4].
        # So m[0]=M_std[0,0], m[1]=M_std[1,0], m[2]=M_std[2,0], m[3]=M_std[3,0].
        # m[4]=M_std[0,1], ...
        # When we do np.array(m).reshape(4,4), we get A where A[i,j] = m[i*4+j] = M_std[(i*4+j)%4, (i*4+j)//4].
        # W = (np.c_[V, ones] @ np.array(m).reshape(4,4))[:, :3] * meters_per_unit
        # This is explicit. I will follow it exactly.
        
        ones = np.ones((vn, 1), dtype=np.float64)
        V_h = np.c_[V, ones]
        M_mat = np.array(m, dtype=np.float64).reshape(4, 4)
        W_h = V_h @ M_mat
        W = W_h[:, :3] * meters_per_unit
        
        # Calculate area
        # Triangles are in I. I is a flat array of indices.
        # Usually indices are grouped in triples.
        if in_count % 3 != 0:
            continue
        num_tris = in_count // 3
        I_tris = I.reshape(num_tris, 3)
        
        A = W[I_tris[:, 0]]
        B = W[I_tris[:, 1]]
        C = W[I_tris[:, 2]]
        
        cross = np.cross(B - A, C - A)
        areas = 0.5 * np.linalg.norm(cross, axis=1)
        total_area = np.sum(areas, dtype=np.float64)
        
        if source_name in ref_areas:
            ref_areas[source_name] += total_area
        else:
            ref_areas[source_name] = total_area

    # --- 2. Area from GLB ---
    glb_areas = {}
    
    try:
        with open(glb_path, "rb") as f:
            glb_data = f.read()
    except Exception as e:
        print(f"Error reading GLB: {e}")
        sys.exit(1)
        
    # Parse GLB Header
    # Magic (4), Version (4), Length (4)
    if len(glb_data) < 12:
        print("Invalid GLB file")
        sys.exit(1)
        
    magic = struct.unpack('<I', glb_data[0:4])[0]
    if magic != 0x46546C67: # 'glTF'
        print("Invalid GLB magic")
        sys.exit(1)
        
    # JSON Chunk
    # Chunk Length (4), Chunk Type (4), Chunk Data
    json_chunk_len = struct.unpack('<I', glb_data[12:16])[0]
    json_chunk_type = struct.unpack('<I', glb_data[16:20])[0]
    if json_chunk_type != 0x4E4F534A: # 'JSON'
        print("Invalid JSON chunk type")
        sys.exit(1)
        
    json_data = glb_data[20:20+json_chunk_len]
    try:
        gltf = json.loads(json_data.decode('utf-8'))
    except Exception as e:
        print(f"Error parsing GLTF JSON: {e}")
        sys.exit(1)
        
    # BIN Chunk
    # The BIN chunk starts after the JSON chunk header and data.
    # Offset = 12 (header) + 8 (json chunk header) + json_chunk_len
    bin_start_offset = 12 + 8 + json_chunk_len
    if bin_start_offset >= len(glb_data):
        bin_data = b''
    else:
        # Check if there is a BIN chunk
        # Chunk Length (4), Chunk Type (4)
        if bin_start_offset + 8 > len(glb_data):
            bin_data = b''
        else:
            bin_chunk_len = struct.unpack('<I', glb_data[bin_start_offset:bin_start_offset+4])[0]
            bin_chunk_type = struct.unpack('<I', glb_data[bin_start_offset+4:bin_start_offset+8])[0]
            if bin_chunk_type == 0x004E4942: # 'BIN\0'
                bin_data = glb_data[bin_start_offset+8 : bin_start_offset+8+bin_chunk_len]
            else:
                bin_data = b''
    
    # Accessors and BufferViews
    accessors = gltf.get("accessors", [])
    buffer_views = gltf.get("bufferViews", [])
    
    def get_accessor_data(acc_idx):
        if acc_idx < 0 or acc_idx >= len(accessors):
            return None, None
        acc = accessors[acc_idx]
        bv_idx = acc.get("bufferView", -1)
        if bv_idx < 0 or bv_idx >= len(buffer_views):
            return None, None
        bv = buffer_views[bv_idx]
        
        byte_offset = acc.get("byteOffset", 0)
        bv_byte_offset = bv.get("byteOffset", 0)
        
        component_type = acc.get("componentType")
        count = acc.get("count")
        type_str = acc.get("type")
        
        # Determine bytes per element
        if type_str == "SCALAR":
            num_components = 1
        elif type_str == "VEC2":
            num_components = 2
        elif type_str == "VEC3":
            num_components = 3
        elif type_str == "VEC4":
            num_components = 4
        elif type_str == "MAT4":
            num_components = 16
        else:
            return None, None
            
        if component_type == 5126: # FLOAT
            item_size = 4
        elif component_type == 5123: # UNSIGNED_SHORT
            item_size = 2
        elif component_type == 5125: # UNSIGNED_INT
            item_size = 4
        else:
            return None, None
            
        total_bytes = count * num_components * item_size
        start = bin_start_offset + 8 + bv_byte_offset + byte_offset
        # I sliced bin_data from glb_data. So offsets are relative to bin_data start.
        # bin_data starts at bin_start_offset + 8.
        # So relative offset in bin_data is bv_byte_offset + byte_offset.
        
        rel_start = bv_byte_offset + byte_offset
        if rel_start + total_bytes > len(bin_data):
            return None, None
            
        data_bytes = bin_data[rel_start : rel_start + total_bytes]
        
        if component_type == 5126:
            dtype = np.float32
        elif component_type == 5123:
            dtype = np.uint16
        elif component_type == 5125:
            dtype = np.uint32
        else:
            return None, None
            
        arr = np.frombuffer(data_bytes, dtype=dtype)
        if num_components > 1:
            arr = arr.reshape(count, num_components)
        else:
            arr = arr.reshape(count)
            
        return arr, type_str

    # Traverse scene
    scenes = gltf.get("scenes", [])
    if not scenes:
        pass
    else:
        scene = scenes[0]
        scene_nodes = scene.get("nodes", [])
        if not scene_nodes:
            pass
        else:
            root_node_id = scene_nodes[0]
            nodes = gltf.get("nodes", [])
            node_map_glb = {n["id"]: n for n in nodes} if "id" in nodes[0] else {i: n for i, n in enumerate(nodes)}
            
            # Find root node
            # glTF nodes don't have explicit IDs in the array index sense unless specified.
            # Usually, the index in the 'nodes' array is the ID.
            
            def get_node(idx):
                if idx < 0 or idx >= len(nodes):
                    return None
                return nodes[idx]
            
            root_node = get_node(root_node_id)
            if not root_node:
                pass
            else:
                # Children of root are source nodes
                children = root_node.get("children", [])
                for child_idx in children:
                    child_node = get_node(child_idx)
                    if not child_node:
                        continue
                    source_label = child_node.get("name", "")
                    
                    # Traverse subtree
                    stack = [child_idx]
                    while stack:
                        node_idx = stack.pop()
                        node = get_node(node_idx)
                        if not node:
                            continue
                            
                        # Add children to stack
                        for c in node.get("children", []):
                            stack.append(c)
                            
                        # Process mesh
                        mesh_idx = node.get("mesh", -1)
                        if mesh_idx >= 0:
                            meshes = gltf.get("meshes", [])
                            if mesh_idx < len(meshes):
                                mesh = meshes[mesh_idx]
                                primitives = mesh.get("primitives", [])
                                for prim in primitives:
                                    pos_idx = prim.get("attributes", {}).get("POSITION", -1)
                                    idx_accessor = prim.get("indices", -1)
                                    
                                    if pos_idx < 0:
                                        continue
                                        
                                    pos_data, pos_type = get_accessor_data(pos_idx)
                                    if pos_data is None or pos_type != "VEC3":
                                        continue
                                        
                                    if idx_accessor >= 0:
                                        idx_data, idx_type = get_accessor_data(idx_accessor)
                                        if idx_data is None:
                                            continue
                                        # idx_data is 1D array of indices
                                        if len(idx_data) % 3 != 0:
                                            continue
                                        I = idx_data
                                    else:
                                        # No indices, assume sequential triangles
                                        # This is rare for large meshes, but possible
                                        # We need to generate indices
                                        # For simplicity, if no indices, we assume the vertices are already in triangle order?
                                        # If no indices, we can't easily determine triangles without mode.
                                        # Let's skip if no indices to be safe, or assume mode 4 (triangles) and sequential.
                                        # Given the context, indices are likely present.
                                        continue
                                        
                                    V = pos_data.astype(np.float64)
                                    
                                    # Calculate area
                                    num_tris = len(I) // 3
                                    I_tris = I.reshape(num_tris, 3)
                                    
                                    A = V[I_tris[:, 0]]
                                    B = V[I_tris[:, 1]]
                                    C = V[I_tris[:, 2]]
                                    
                                    cross = np.cross(B - A, C - A)
                                    areas = 0.5 * np.linalg.norm(cross, axis=1)
                                    total_area = np.sum(areas, dtype=np.float64)
                                    
                                    if source_label in glb_areas:
                                        glb_areas[source_label] += total_area
                                    else:
                                        glb_areas[source_label] = total_area

    # --- 3. Output ---
    sources = sorted(ref_areas.keys())
    mismatch_count = 0
    
    for source in sources:
        ref = ref_areas[source]
        got = glb_areas.get(source, 0.0)
        
        if ref > 0:
            pct = abs(got - ref) / ref * 100.0
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

if __name__ == "__main__":
    main()
