import sys
import json
import struct
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

def main():
    if len(sys.argv) != 3:
        print("Usage: python quantize_glb.py IN.glb OUT.glb", file=sys.stderr)
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]

    try:
        with open(in_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        sys.exit(1)

    if len(data) < 12:
        print("Invalid GLB: too short", file=sys.stderr)
        sys.exit(1)

    magic, version, total_len = struct.unpack_from('<III', data, 0)
    if magic != 0x46546C67:
        print("Invalid GLB magic", file=sys.stderr)
        sys.exit(1)
    if version != 2:
        print("Unsupported GLB version", file=sys.stderr)
        sys.exit(1)

    json_len, json_type = struct.unpack_from('<II', data, 12)
    if json_type != 0x4E4F534A:
        print("Invalid JSON chunk type", file=sys.stderr)
        sys.exit(1)

    json_bytes = data[20:20+json_len]
    try:
        gltf = json.loads(json_bytes.decode('utf-8'))
    except Exception as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Find BIN chunk
    # JSON chunk ends at 20 + json_len. Next is BIN chunk header (8 bytes)
    bin_header_offset = 20 + json_len
    if bin_header_offset + 8 > len(data):
        print("Invalid GLB: missing BIN chunk", file=sys.stderr)
        sys.exit(1)
    
    bin_len, bin_type = struct.unpack_from('<II', data, bin_header_offset)
    if bin_type != 0x004E4942:
        print("Invalid BIN chunk type", file=sys.stderr)
        sys.exit(1)
        
    bin_data = data[bin_header_offset + 8 : bin_header_offset + 8 + bin_len]

    # Validate structure
    if 'buffers' not in gltf or len(gltf['buffers']) != 1:
        print("Expected exactly one buffer", file=sys.stderr)
        sys.exit(1)
        
    if 'meshes' not in gltf:
        print("No meshes found", file=sys.stderr)
        sys.exit(1)

    # Map mesh index to node index
    mesh_to_node = {}
    if 'nodes' in gltf:
        for i, node in enumerate(gltf['nodes']):
            if 'mesh' in node:
                mesh_idx = node['mesh']
                if mesh_idx in mesh_to_node:
                    print(f"Mesh {mesh_idx} referenced by multiple nodes", file=sys.stderr)
                    sys.exit(1)
                mesh_to_node[mesh_idx] = i
                # Assert no transform
                if 'translation' in node or 'rotation' in node or 'scale' in node or 'matrix' in node:
                    print(f"Node {i} referencing mesh {mesh_idx} has existing transform", file=sys.stderr)
                    sys.exit(1)

    # Process primitives
    # We need to track which bufferViews are POSITION views and replace them
    # Also need to store original positions for self-check
    
    original_positions = [] # list of (primitive_index, positions_array)
    
    # We will modify gltf in place
    # First, identify all position bufferViews and their accessors
    # Structure:
    # meshes -> primitives -> attributes -> POSITION -> accessor -> bufferView -> buffer
    
    # We need to iterate through meshes and primitives
    # For each primitive, get the POSITION accessor
    # Get the bufferView
    # Read the data from bin_data
    # Quantize
    # Create new bufferView data
    # Update accessor
    # Update node transform
    
    # We need to rebuild the BIN chunk.
    # Strategy:
    # 1. Iterate through all bufferViews in gltf['bufferViews']
    # 2. For each, determine if it's a POSITION view for a primitive.
    #    How to know? 
    #    We can pre-scan: for each mesh, for each primitive, get POSITION accessor, get bufferView index.
    #    Store mapping: bufferView_index -> (primitive_info, original_data)
    
    bv_to_primitive = {}
    primitive_data = [] # list of dicts with info for self-check
    
    for m_idx, mesh in enumerate(gltf['meshes']):
        if 'primitives' not in mesh:
            continue
        for p_idx, prim in enumerate(mesh['primitives']):
            if 'attributes' not in prim or 'POSITION' not in prim['attributes']:
                continue
            acc_idx = prim['attributes']['POSITION']
            if not 0 <= acc_idx < len(gltf['accessors']):
                print(f"Invalid accessor index {acc_idx}", file=sys.stderr)
                sys.exit(1)
            acc = gltf['accessors'][acc_idx]
            
            # Validate accessor
            if acc.get('componentType') != 5126:
                print(f"Accessor {acc_idx} not float32", file=sys.stderr)
                sys.exit(1)
            if acc.get('type') != 'VEC3':
                print(f"Accessor {acc_idx} not VEC3", file=sys.stderr)
                sys.exit(1)
            if 'bufferView' not in acc:
                print(f"Accessor {acc_idx} missing bufferView", file=sys.stderr)
                sys.exit(1)
            bv_idx = acc['bufferView']
            if not 0 <= bv_idx < len(gltf['bufferViews']):
                print(f"Invalid bufferView index {bv_idx}", file=sys.stderr)
                sys.exit(1)
            bv = gltf['bufferViews'][bv_idx]
            if bv.get('target') != 34962:
                print(f"BufferView {bv_idx} not ARRAY_BUFFER", file=sys.stderr)
                sys.exit(1)
            if 'byteStride' in bv:
                print(f"BufferView {bv_idx} has byteStride", file=sys.stderr)
                sys.exit(1)
                
            # Read data
            offset = bv.get('byteOffset', 0)
            length = bv.get('byteLength', 0)
            if offset + length > len(bin_data):
                print("BufferView out of bounds", file=sys.stderr)
                sys.exit(1)
            
            raw = bin_data[offset:offset+length]
            # Parse as float32
            # N * 3 * 4 bytes
            if length % 12 != 0:
                print("Invalid position data length", file=sys.stderr)
                sys.exit(1)
            n = length // 12
            positions = np.frombuffer(raw, dtype=np.float32).reshape(n, 3).astype(np.float64)
            
            # Store for self-check
            primitive_data.append({
                'mesh_idx': m_idx,
                'prim_idx': p_idx,
                'positions': positions,
                'bv_idx': bv_idx,
                'acc_idx': acc_idx
            })
            
            # Mark this bufferView as to be replaced
            bv_to_primitive[bv_idx] = len(primitive_data) - 1

    # Now, process each primitive to compute quantized data and update JSON
    # We need to generate new bufferView data for each position view
    # And update the JSON structure
    
    # We will create a list of new bufferView data in the order of original bufferViews
    # For non-position views, we keep original data.
    # For position views, we replace with quantized data.
    
    # First, compute quantization for all position views
    quantized_data = {} # bv_idx -> bytes
    node_updates = {} # node_idx -> (scale, translation)
    
    for pd in primitive_data:
        bv_idx = pd['bv_idx']
        positions = pd['positions']
        
        lo = positions.min(axis=0)
        hi = positions.max(axis=0)
        c = (lo + hi) / 2.0
        ext = np.max(hi - lo)
        
        if ext > 0:
            s = ext / 65534.0
        else:
            s = 1.0
            
        # q = round((P - c) / s)
        q_float = (positions - c) / s
        q = np.round(q_float).astype(np.int64)
        q = np.clip(q, -32767, 32767).astype(np.int16)
        
        # Store q in new bufferView as N elements of 8 bytes (int16 x, y, z, 0 padding)
        # Structure: [x, y, z, pad] * N
        # x, y, z are int16, pad is 2 bytes (to make 8 bytes total)
        # So we need to interleave? Or just pack?
        # "N elements of 8 bytes (int16 x, y, z, 0 padding)"
        # This implies each vertex is 8 bytes: x(2), y(2), z(2), pad(2).
        
        # Create array of shape (N, 4) where last col is 0
        # But we need to pack as int16.
        # Let's create a structured array or just pack manually.
        
        # q is (N, 3) int16
        # We want to output bytes: for each i, q[i,0], q[i,1], q[i,2], 0, 0
        
        # Method: create a new array of int16 with shape (N, 4)
        q_padded = np.zeros((q.shape[0], 4), dtype=np.int16)
        q_padded[:, 0] = q[:, 0]
        q_padded[:, 1] = q[:, 1]
        q_padded[:, 2] = q[:, 2]
        # q_padded[:, 3] is already 0
        
        # Convert to bytes
        new_bytes = q_padded.tobytes()
        
        quantized_data[bv_idx] = new_bytes
        
        # Update accessor
        acc_idx = pd['acc_idx']
        acc = gltf['accessors'][acc_idx]
        acc['componentType'] = 5122
        # type remains VEC3
        # count remains same
        # min/max
        min_q = q.min(axis=0).tolist()
        max_q = q.max(axis=0).tolist()
        acc['min'] = min_q
        acc['max'] = max_q
        
        # Update node
        # Find node for this mesh
        mesh_idx = pd['mesh_idx']
        if mesh_idx not in mesh_to_node:
            print(f"Mesh {mesh_idx} not referenced by any node", file=sys.stderr)
            sys.exit(1)
        node_idx = mesh_to_node[mesh_idx]
        node = gltf['nodes'][node_idx]
        node['scale'] = [s, s, s]
        node['translation'] = [float(c[0]), float(c[1]), float(c[2])]
        
        # Store s and c for self-check
        pd['s'] = s
        pd['c'] = c
        pd['q'] = q

    # Update extensions
    if 'extensionsUsed' in gltf:
        if 'KHR_mesh_quantization' not in gltf['extensionsUsed']:
            gltf['extensionsUsed'].append('KHR_mesh_quantization')
    else:
        gltf['extensionsUsed'] = ['KHR_mesh_quantization']
        
    if 'extensionsRequired' in gltf:
        if 'KHR_mesh_quantization' not in gltf['extensionsRequired']:
            gltf['extensionsRequired'].append('KHR_mesh_quantization')
    else:
        gltf['extensionsRequired'] = ['KHR_mesh_quantization']

    # Rebuild BIN chunk
    # Walk original bufferViews in order
    new_bin_parts = []
    current_offset = 0
    
    for bv_idx, bv in enumerate(gltf['bufferViews']):
        # Align to 4 bytes
        if current_offset % 4 != 0:
            pad = 4 - (current_offset % 4)
            new_bin_parts.append(b'\x00' * pad)
            current_offset += pad
            
        if bv_idx in quantized_data:
            data_chunk = quantized_data[bv_idx]
        else:
            # Original data
            offset = bv.get('byteOffset', 0)
            length = bv.get('byteLength', 0)
            data_chunk = bin_data[offset:offset+length]
            
        new_bin_parts.append(data_chunk)
        current_offset += len(data_chunk)
        
        # Update JSON bufferView
        bv['byteOffset'] = current_offset - len(data_chunk)
        bv['byteLength'] = len(data_chunk)
        
        # If it was a position view, update byteStride
        if bv_idx in quantized_data:
            bv['byteStride'] = 8
            # Ensure target is 34962 (it should be)
            bv['target'] = 34962

    new_bin_data = b''.join(new_bin_parts)
    
    # Pad BIN to multiple of 4
    if len(new_bin_data) % 4 != 0:
        pad = 4 - (len(new_bin_data) % 4)
        new_bin_data += b'\x00' * pad

    # Update buffer length
    gltf['buffers'][0]['byteLength'] = len(new_bin_data)

    # Serialize JSON
    json_str = json.dumps(gltf, separators=(',', ':'), ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')
    
    # Pad JSON to multiple of 4 with spaces
    if len(json_bytes) % 4 != 0:
        pad = 4 - (len(json_bytes) % 4)
        json_bytes += b' ' * pad

    # Build GLB
    glb_header = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(json_bytes) + 8 + len(new_bin_data))
    json_chunk_header = struct.pack('<II', len(json_bytes), 0x4E4F534A)
    bin_chunk_header = struct.pack('<II', len(new_bin_data), 0x004E4942)
    
    out_data = glb_header + json_chunk_header + json_bytes + bin_chunk_header + new_bin_data
    
    try:
        with open(out_path, 'wb') as f:
            f.write(out_data)
    except Exception as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        sys.exit(1)

    # Self-check
    # Re-open OUT
    with open(out_path, 'rb') as f:
        out_data_check = f.read()
        
    # Parse OUT
    magic, version, total_len = struct.unpack_from('<III', out_data_check, 0)
    json_len, json_type = struct.unpack_from('<II', out_data_check, 12)
    json_bytes_out = out_data_check[20:20+json_len]
    gltf_out = json.loads(json_bytes_out.decode('utf-8'))
    
    bin_header_offset = 20 + json_len
    bin_len, bin_type = struct.unpack_from('<II', out_data_check, bin_header_offset)
    bin_data_out = out_data_check[bin_header_offset + 8 : bin_header_offset + 8 + bin_len]
    
    max_err = 0.0
    max_err_prim = -1
    max_bound = 0.0
    
    for pd in primitive_data:
        mesh_idx = pd['mesh_idx']
        prim_idx = pd['prim_idx']
        s = pd['s']
        c = pd['c']
        
        # Get node transform from OUT
        node_idx = mesh_to_node[mesh_idx]
        node_out = gltf_out['nodes'][node_idx]
        scale_out = node_out['scale']
        trans_out = node_out['translation']
        
        # Get accessor and bufferView from OUT
        # We need to find the accessor for this primitive in OUT
        # The structure should be the same, but indices might have changed? No, we didn't add/remove meshes/primitives.
        # But bufferView indices are the same.
        # Accessor index is the same.
        
        acc_idx = pd['acc_idx']
        acc_out = gltf_out['accessors'][acc_idx]
        bv_idx = acc_out['bufferView']
        bv_out = gltf_out['bufferViews'][bv_idx]
        
        offset = bv_out['byteOffset']
        length = bv_out['byteLength']
        byte_stride = bv_out.get('byteStride', 8)
        
        # Read quantized data
        raw = bin_data_out[offset:offset+length]
        # Parse as int16 with stride 8
        # Each element is 8 bytes: x, y, z, pad
        # We can use numpy structured array or just slice
        
        # Create a view of the data as int16
        # The data is packed as [x, y, z, pad] * N
        # We want to extract x, y, z
        
        # Method: reshape to (N, 4) int16
        # But we need to ensure alignment.
        # raw is bytes.
        
        # Let's use numpy frombuffer with dtype int16
        # The total length is N * 8.
        # We can reshape to (N, 4)
        
        n = length // 8
        q_out = np.frombuffer(raw, dtype=np.int16).reshape(n, 4)
        q_out = q_out[:, :3].astype(np.float64)
        
        # Dequantize
        # P = q * scale + translation
        # scale is uniform s
        # translation is c
        
        # Note: scale_out and trans_out are lists
        s_val = scale_out[0]
        c_val = np.array(trans_out)
        
        P_out = q_out * s_val + c_val
        
        # Compare with original positions
        P_orig = pd['positions']
        
        # Max absolute error
        err = np.abs(P_out - P_orig)
        max_err_prim_val = np.max(err)
        
        bound = 0.5 * s * 1.001 + 1e-9
        
        if max_err_prim_val > max_err:
            max_err = max_err_prim_val
            max_err_prim = len(primitive_data) # Use index in list
            max_bound = bound
            
        if max_err_prim_val > bound:
            print(f"Primitive {len(primitive_data)} violates bound", file=sys.stderr)
            # Print specific index as requested: "print the primitive index"
            # The prompt says "print the primitive index and exit 1"
            # It doesn't specify which index, but likely the one that failed.
            # We already printed to stderr.
            sys.exit(1)

    # Print summary
    before_size = len(data)
    after_size = len(out_data)
    ratio = (after_size / before_size) * 100 if before_size > 0 else 0
    
    # Errors in millimeters
    max_err_mm = max_err * 1000
    max_bound_mm = max_bound * 1000
    
    print(f"размер: {before_size} -> {after_size} байт ({ratio:.1f} %), примитивов: {len(primitive_data)}, макс. ошибка: {max_err_mm:.3f} мм (допуск {max_bound_mm:.3f} мм)")
    
    sys.exit(0)

if __name__ == '__main__':
    main()
