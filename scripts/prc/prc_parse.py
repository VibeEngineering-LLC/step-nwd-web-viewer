import struct
import zlib
import sys
import json
from prc_bits import BitReader

TESS_3D = 172
TESS_3D_Compressed = 173
TESS_Face = 174
TESS_3D_Wire = 175
TESS_Markup = 176
MISC_Attribute = 201
MISC_CartesianTransformation = 202
MISC_GeneralTransformation = 207
RI_BrepModel = 232
RI_Curve = 233
RI_Direction = 234
RI_Plane = 235
RI_PointSet = 236
RI_PolyBrepModel = 237
RI_PolyWire = 238
RI_Set = 239
RI_CoordinateSystem = 240
ASM_ModelFile = 301
ASM_FileStructure = 302
ASM_FileStructureGlobals = 303
ASM_FileStructureTree = 304
ASM_FileStructureTessellation = 305
ASM_ProductOccurence = 310
ASM_PartDefinition = 311


def parse_container(data: bytes) -> dict:
    if data[0:3] != b'PRC':
        raise ValueError("Invalid PRC header")
    
    version_for_read = struct.unpack_from('<I', data, 3)[0]
    authoring_version = struct.unpack_from('<I', data, 7)[0]
    
    file_uuid = struct.unpack_from('<4I', data, 11)
    app_uuid = struct.unpack_from('<4I', data, 27)
    
    N = struct.unpack_from('<I', data, 43)[0]
    pos = 47
    
    file_structures = []
    for _ in range(N):
        uuid = struct.unpack_from('<4I', data, pos)
        pos += 16
        reserved = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        K = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        offsets = []
        for _ in range(K):
            offsets.append(struct.unpack_from('<I', data, pos)[0])
            pos += 4
        file_structures.append({
            'uuid': uuid,
            'offsets': offsets
        })
    
    model_file_offset = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    file_size = struct.unpack_from('<I', data, pos)[0]
    
    return {
        'version_for_read': version_for_read,
        'authoring_version': authoring_version,
        'file_structures': file_structures,
        'model_file_offset': model_file_offset,
        'file_size': file_size
    }


def inflate(data: bytes, offset: int) -> bytes:
    d = zlib.decompressobj()
    return d.decompress(data[offset:])


def fs_sections(data: bytes, fs: dict) -> dict:
    offsets = fs['offsets']
    # offsets[0] = uncompressed header; offsets[1..5] = zlib sections
    names = ['globals', 'tree', 'tessellation', 'geometry', 'extra_geometry']
    return {nm: inflate(data, off) for nm, off in zip(names, offsets[1:])}


class Parser:
    def __init__(self, r: BitReader):
        self.r = r
        self.current_name = ""
        self.layer = -1
        self.style = -1
        self.behaviour = 1

    def reset(self):
        self.current_name = ""
        self.layer = -1
        self.style = -1
        self.behaviour = 1

    def expect(self, code: int):
        t = self.r.read_uint()
        if t != code:
            raise ValueError(f"expected type {code} got {t} at bit {self.r.tell()}")

    def attr_entry(self):
        if self.r.read_bool():
            return self.r.read_uint()
        else:
            return self.r.read_string()

    def attributes(self):
        n = self.r.read_uint()
        attrs = []
        for _ in range(n):
            self.expect(MISC_Attribute)
            title = self.attr_entry()
            k = self.r.read_uint()
            keys = []
            for _ in range(k):
                key_title = self.attr_entry()
                atype = self.r.read_uint()
                if atype == 1:
                    val = self.r.read_int()
                elif atype == 2:
                    val = self.r.read_double()
                elif atype == 3:
                    val = self.r.read_uint()
                elif atype == 4:
                    val = self.r.read_string()
                else:
                    val = None
                keys.append((key_title, val))
            attrs.append({'title': title, 'keys': keys})
        return attrs

    def name(self):
        if not self.r.read_bool():
            self.current_name = self.r.read_string()
        return self.current_name

    def content_base(self, eligible: bool) -> dict:
        attrs = self.attributes()
        nm = self.name()
        uid = None
        if eligible:
            cad_id = self.r.read_uint()
            cad_pid = self.r.read_uint()
            uid = self.r.read_uint()
        return {'attrs': attrs, 'name': nm, 'uid': uid}

    def graphics(self):
        if not self.r.read_bool():
            self.layer = self.r.read_uint() - 1
            self.style = self.r.read_uint() - 1
            c1 = self.r.read_char()
            c2 = self.r.read_char()
            self.behaviour = c1 | (c2 << 8)
        return (self.layer, self.style, self.behaviour)

    def content_base_graphics(self, eligible: bool) -> dict:
        d = self.content_base(eligible)
        d['graphics'] = self.graphics()
        d['v7094_bit'] = self.r.read_bit()  # v7094: extra bit after graphics (found: part def bit 65, PO, RI)
        return d

    def user_data(self):
        nbits = self.r.read_uint()
        for _ in range(nbits):
            self.r.read_bit()

    def vec3(self):
        return (self.r.read_double(), self.r.read_double(), self.r.read_double())

    def unit(self):
        from_cad = self.r.read_bool()
        u = self.r.read_double()
        return u

    def compressed_uid(self):
        return (self.r.read_uint(), self.r.read_uint(), self.r.read_uint(), self.r.read_uint())

    def cartesian_transformation(self):
        behaviour = self.r.read_char()
        result = {'behaviour': behaviour}
        if behaviour & 0x01:
            result['origin'] = self.vec3()
        if behaviour & 0x20:
            result['X'] = self.vec3()
            result['Y'] = self.vec3()
            result['Z'] = self.vec3()
        elif behaviour & 0x02:
            result['X'] = self.vec3()
            result['Y'] = self.vec3()
        if behaviour & 0x10:
            result['scale3'] = self.vec3()
        elif behaviour & 0x08:
            result['scale'] = self.r.read_double()
        if behaviour & 0x40:
            result['homog'] = [self.r.read_double() for _ in range(4)]
        return result

    def transformation3d_any(self):
        t = self.r.read_uint()
        if t == 202:
            return {'kind': 'cart', **self.cartesian_transformation()}
        elif t == 207:
            return {'kind': 'general', 'm': [self.r.read_double() for _ in range(16)]}
        else:
            raise ValueError(f"Unknown transformation type {t} at bit {self.r.tell()}")

    def markups(self):
        linked_items = self.r.read_uint()
        leaders = self.r.read_uint()
        markups = self.r.read_uint()
        annotation_entities = self.r.read_uint()
        counts = (linked_items, leaders, markups, annotation_entities)
        if any(counts):
            raise NotImplementedError(f"markups not empty at bit {self.r.tell()}: {counts}")

    def representation_item(self) -> dict:
        t = self.r.read_uint()
        content = self.content_base_graphics(True)
        idx_cs = self.r.read_uint() - 1
        idx_tess = self.r.read_uint() - 1
        
        result = {
            'type': t,
            'name': content['name'],
            'uid': content['uid'],
            'index_tessellation': idx_tess,
            'index_local_cs': idx_cs
        }

        if t == RI_BrepModel:
            has_brep = self.r.read_bit()
            if has_brep:
                context_id = self.r.read_uint()
                body_id = self.r.read_uint()
            is_closed = self.r.read_bool()
            self.user_data()
        elif t == RI_PolyBrepModel:
            is_closed = self.r.read_bool()
            self.user_data()
        elif t == RI_Curve:
            has_wire = self.r.read_bit()
            if has_wire:
                self.r.read_uint()
                self.r.read_uint()
            self.user_data()
        elif t == RI_PolyWire:
            self.user_data()
        elif t == RI_Set:
            n = self.r.read_uint()
            result['children'] = [self.representation_item() for _ in range(n)]
            self.user_data()
        elif t == RI_CoordinateSystem:
            result['transform'] = self.transformation3d_any()
            self.user_data()
        elif t == RI_PointSet:
            n = self.r.read_uint()
            result['points'] = [self.vec3() for _ in range(n)]
            self.user_data()
        elif t in (RI_Direction, RI_Plane):
            raise NotImplementedError(f"RI type {t} at bit {self.r.tell()}")
        else:
            raise NotImplementedError(f"RI type {t} at bit {self.r.tell()}")
            
        return result

    def part_definition(self) -> dict:
        self.expect(ASM_PartDefinition)
        d = self.content_base_graphics(True)
        bbox_min = self.vec3()
        bbox_max = self.vec3()
        n = self.r.read_uint()
        ris = [self.representation_item() for _ in range(n)]
        self.markups()
        n_views = self.r.read_uint()
        if n_views:
            raise NotImplementedError("views")
        self.user_data()
        return {
            'name': d['name'],
            'uid': d['uid'],
            'bbox': (bbox_min, bbox_max),
            'ris': ris
        }

    def product_occurrence(self) -> dict:
        self.expect(ASM_ProductOccurence)
        d = self.content_base_graphics(True)
        index_part = self.r.read_uint() - 1
        index_prototype = self.r.read_uint() - 1
        proto_fs = None
        if index_prototype != -1:
            proto_same = self.r.read_bool()
            if not proto_same:
                proto_fs = self.compressed_uid()
        
        index_external = self.r.read_uint() - 1
        ext_fs = None
        if index_external != -1:
            ext_same = self.r.read_bool()
            if not ext_same:
                ext_fs = self.compressed_uid()
        
        n_sons = self.r.read_uint()
        sons = [self.r.read_uint() for _ in range(n_sons)]
        
        product_behaviour = self.r.read_char()
        unit = self.unit()
        info_flags = self.r.read_char()
        load_status = self.r.read_int()
        
        has_location = self.r.read_bit()
        location = None
        if has_location:
            location = self.transformation3d_any()
        
        n_refs = self.r.read_uint()
        if n_refs:
            raise NotImplementedError("references")
        
        self.markups()
        n_views = self.r.read_uint()
        if n_views:
            raise NotImplementedError("views")
        
        has_filter = self.r.read_bit()
        if has_filter:
            raise NotImplementedError("entity filter")
        
        n_disp = self.r.read_uint()
        if n_disp:
            raise NotImplementedError("display filters")
        
        n_scene = self.r.read_uint()
        if n_scene:
            raise NotImplementedError("scene display parameters")
        
        self.user_data()
        
        return {
            'name': d['name'],
            'uid': d['uid'],
            'index_part': index_part,
            'index_prototype': index_prototype,
            'proto_fs': proto_fs,
            'index_external': index_external,
            'ext_fs': ext_fs,
            'sons': sons,
            'unit': unit,
            'location': location
        }

    def tree(self) -> dict:
        self.expect(ASM_FileStructureTree)
        self.content_base(False)
        n_parts = self.r.read_uint()
        parts = [self.part_definition() for _ in range(n_parts)]
        n_po = self.r.read_uint()
        pos = [self.product_occurrence() for _ in range(n_po)]
        
        self.expect(ASM_FileStructure)
        self.content_base(False)
        next_available_index = self.r.read_uint()
        index_product_occurrence = self.r.read_uint()
        self.user_data()
        
        return {
            'parts': parts,
            'product_occurrences': pos,
            'index_product_occurrence': index_product_occurrence
        }

    def tess_face(self) -> dict:
        self.expect(TESS_Face)
        nla = self.r.read_uint()
        line_attrs = [self.r.read_uint() - 1 for _ in range(nla)]
        start_wire = self.r.read_uint()
        nsw = self.r.read_uint()
        sizes_wire = [self.r.read_uint() for _ in range(nsw)]
        flags = self.r.read_uint()
        start_tri = self.r.read_uint()
        nst = self.r.read_uint()
        sizes_tri = [self.r.read_uint() for _ in range(nst)]
        n_tex_idx = self.r.read_uint()
        has_vc = self.r.read_bool()
        if has_vc:
            raise NotImplementedError(f"vertex colors at bit {self.r.tell()}")
        if nla:
            behaviour = self.r.read_uint()
        return {
            'flags': flags,
            'start_tri': start_tri,
            'sizes_tri': sizes_tri,
            'n_tex_idx': n_tex_idx,
            'line_attrs': line_attrs
        }

    def tess3d(self) -> dict:
        is_calculated = self.r.read_bool()
        nc = self.r.read_uint()
        coords = [self.r.read_double() for _ in range(nc)]
        has_faces = self.r.read_bool()
        has_loops = self.r.read_bool()
        must_recalc = self.r.read_bool()
        if must_recalc:
            flags = self.r.read_char()
            crease = self.r.read_double()
        nn = self.r.read_uint()
        normals = [self.r.read_double() for _ in range(nn)]
        nw = self.r.read_uint()
        wire = [self.r.read_uint() for _ in range(nw)]
        nt = self.r.read_uint()
        tri = [self.r.read_uint() for _ in range(nt)]
        nf = self.r.read_uint()
        faces = [self.tess_face() for _ in range(nf)]
        ntex = self.r.read_uint()
        tex = [self.r.read_double() for _ in range(ntex)]
        return {
            'coords': coords,
            'normals': normals,
            'tri': tri,
            'faces': faces,
            'n_wire': nw,
            'n_tex': ntex
        }

    def tessellation(self) -> list:
        self.expect(ASM_FileStructureTessellation)
        self.content_base(False)
        n = self.r.read_uint()
        out = []
        for _ in range(n):
            t = self.r.read_uint()
            if t == TESS_3D:
                out.append(self.tess3d())
            elif t in (TESS_3D_Wire, TESS_Markup, TESS_3D_Compressed):
                raise NotImplementedError(f"tess type {t} at bit {self.r.tell()}")
            else:
                raise ValueError(f"Unknown tess type {t} at bit {self.r.tell()}")
        self.user_data()
        return out


def parse_file(path: str) -> dict:
    with open(path, 'rb') as f:
        data = f.read()
    
    container = parse_container(data)
    fs_list = []
    
    for i, fs in enumerate(container['file_structures']):
        secs = fs_sections(data, fs)
        entry = {
            'uuid': fs['uuid'],
            'tree': None,
            'tess': None,
            'errors': []
        }
        
        try:
            p = Parser(BitReader(secs['tree']))
            entry['tree'] = p.tree()
        except Exception as e:
            entry['errors'].append(f"tree: {type(e).__name__}: {e}")
        
        try:
            p = Parser(BitReader(secs['tessellation']))
            entry['tess'] = p.tessellation()
        except Exception as e:
            entry['errors'].append(f"tessellation: {type(e).__name__}: {e}")
        
        fs_list.append(entry)
    
    return {'container': container, 'fs': fs_list}


def print_ri_info(ri, indent=0):
    prefix = "  " * indent
    print(f"{prefix}RI type: {ri['type']}, index_tessellation: {ri['index_tessellation']}")
    if 'children' in ri:
        for child in ri['children']:
            print_ri_info(child, indent + 1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    res = parse_file(sys.argv[1])
    
    for i, fs in enumerate(res['fs']):
        uuid_hex = " ".join(f"{x:08x}" for x in fs['uuid'])
        n_parts = len(fs['tree']['parts']) if fs['tree'] else 0
        n_pos = len(fs['tree']['product_occurrences']) if fs['tree'] else 0
        n_tess = len(fs['tess']) if fs['tess'] else 0
        errors_str = "; ".join(fs['errors']) if fs['errors'] else "none"
        print(f"FS {i}: uuid={uuid_hex}, parts={n_parts}, pos={n_pos}, tess={n_tess}, errors={errors_str}")
        
        if fs['tree']:
            for j, po in enumerate(fs['tree']['product_occurrences']):
                proto_fs_given = "True" if po['proto_fs'] is not None else "False"
                has_loc = "True" if po['location'] is not None else "False"
                print(f"  PO {j}: name={po['name']}, index_part={po['index_part']}, index_prototype={po['index_prototype']}, proto_fs={proto_fs_given}, sons={po['sons']}, has_location={has_loc}")
            
            for part in fs['tree']['parts']:
                print(f"  Part: name={part['name']}, bbox={part['bbox']}")
                for ri in part['ris']:
                    print_ri_info(ri, indent=2)
        
        if fs['tess']:
            for k, tess in enumerate(fs['tess']):
                n_coords = len(tess['coords']) // 3
                n_tris = len(tess['tri'])
                n_faces = len(tess['faces'])
                flags_set = set(f['flags'] for f in tess['faces'])
                print(f"  Tess {k}: coords={n_coords}, tris={n_tris}, faces={n_faces}, flags={flags_set}")
