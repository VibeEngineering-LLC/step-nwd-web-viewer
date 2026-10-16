import sys
import math
import json
import prc_bits
import prc_parse
from hcg_prim import uvar, ivar, dvar, nbits_then_uint, entity_type

NEWLOOP = 0
ENDLOOP = 1
ISO_PLANE = 2
import os
ISO_IDX = os.environ.get("ISO_IDX", "uvar"); DEBUG = bool(os.environ.get("HCG_DEBUG"))
ISO_CYL = 3
ISO_TORUS = 4
ISO_SPHERE = 5
ISO_CONE = 6
ISO_NURBS = 7
ANA_PLANE = 8
ANA_CYL = 9
ANA_TORUS = 10
ANA_SPHERE = 11
ANA_CONE = 12
ANA_NURBS = 13
ANA_GENERIC = 14

LINE = 0
CIRCLE = 1
HERMITE = 2
ELLIPSE = 12
COMPOSITE = 13

LAST_PARTIAL = None


class BrepReader:
    def __init__(self, r, tol, nref):
        self.r = r
        self.tol = tol
        self.dtol = tol / 100.0
        self.nref = nref
        self.vertices = []
        self.curves = []
        self.faces_done = []

    def point(self):
        r = self.r
        nb = uvar(r, 6)
        if nb <= 30:
            return tuple(dvar(r, self.dtol, nb) for _ in range(3))
        else:
            return (r.read_double(), r.read_double(), r.read_double())

    def vertex(self):
        r = self.r
        if r.read_bit():
            p = self.point()
            self.vertices.append(p)
            return p
        else:
            i = uvar(r, self.nref)
            if i >= len(self.vertices):
                raise ValueError(f"vertex ref {i} >= {len(self.vertices)} at bit {r.tell()}")
            return self.vertices[i]

    def start_end(self, as_points=False):
        if as_points:
            return (self.point(), self.point())
        return (self.vertex(), self.vertex())

    def curve_body(self, ctype, as_points=False):
        r = self.r
        if ctype == LINE:
            p0, p1 = self.start_end(as_points)
            return {'t': 'line', 'p0': p0, 'p1': p1}
        elif ctype == CIRCLE:
            particular = r.read_bool()
            if particular:
                full = r.read_bool()
                p0, p1 = self.start_end(as_points)
                if full:
                    c = self.point()
                    n = self.point()
                    return {'t': 'circle_full', 'p0': p0, 'p1': p1, 'c': c, 'n': n}
                else:
                    m = self.point()
                    return {'t': 'arc3', 'p0': p0, 'p1': p1, 'm': m}
            else:
                p0, p1 = self.start_end(as_points)
                c = self.point()
                big = r.read_bool()
                return {'t': 'arc', 'p0': p0, 'p1': p1, 'c': c, 'big': big}
        elif ctype == HERMITE:
            p0, p1 = self.start_end(as_points)
            nbits = uvar(r, 4)
            npts = uvar(r, nbits)
            pbits = uvar(r, 6)
            if DEBUG:
                save = r.pos; print('    hermite nbits', nbits, 'npts', npts, 'pbits', pbits, 'bits@', r.pos, ''.join(str(r.read_bit()) for _ in range(min(1200, r.n - r.pos)))); r.pos = save
            pts = []
            for _ in range(max(npts - 2, 0)):  # stored count = npts-2 (reverse-engineered on old PRC FS12)
                if pbits > 30:
                    pts.append((r.read_double(), r.read_double(), r.read_double()))
                else:
                    pts.append(tuple(dvar(r, self.tol, pbits) for _ in range(3)))
            tbits = uvar(r, 6)
            tans = []
            for _ in range(max(npts - 0, 0)):  # stored count = npts-2 (reverse-engineered on old PRC FS12)
                if tbits > 30:
                    tans.append((r.read_double(), r.read_double(), r.read_double()))
                else:
                    tans.append(tuple(dvar(r, self.tol, tbits) for _ in range(3)))
            return {'t': 'hermite', 'p0': p0, 'p1': p1, 'pts': pts, 'tans': tans, 'pbits': pbits, 'tbits': tbits}
        elif ctype == COMPOSITE:
            p0, p1 = self.start_end(as_points)
            dim = r.read_uint()
            closed = r.read_bool()
            n = r.read_uint()
            subs = []
            for _ in range(n):
                is_curve, t = entity_type(r)
                subs.append(self.curve_body(t, as_points=True))
            return {'t': 'composite', 'p0': p0, 'p1': p1, 'subs': subs}
        else:
            raise NotImplementedError(f"curve type {ctype} at bit {r.tell()}")

    def ref_or_curve(self):
        r = self.r
        if r.read_bit():
            is_curve, t = entity_type(r)
            if not is_curve:
                raise ValueError(f"expected curve, got face type {t} at bit {r.tell()}")
            c = self.curve_body(t)
            self.curves.append(c)
            return c
        else:
            i = uvar(r, self.nref)
            if i >= len(self.curves):
                raise ValueError(f"curve ref {i} >= {len(self.curves)} at bit {r.tell()}")
            return {'t': 'ref', 'i': i, 'c': self.curves[i]}

    def loop_curves(self):
        r = self.r
        curves = []
        while True:
            if r.read_bit():
                is_curve, t = entity_type(r)
                if not is_curve and t in (NEWLOOP, ENDLOOP):
                    return curves, (t == ENDLOOP)
                if not is_curve:
                    raise ValueError(f"expected curve or loop marker, got face type {t} at bit {r.tell()}")
                c = self.curve_body(t)
                self.curves.append(c)
                curves.append(c)
                if DEBUG:
                    print('   ana curve', c['t'], {k: v for k, v in c.items() if k != 't'}, 'bit', r.tell())
            else:
                i = uvar(r, self.nref)
                if i >= len(self.curves):
                    raise ValueError(f"curve ref {i} >= {len(self.curves)} at bit {r.tell()}")
                curves.append({'t': 'ref', 'i': i, 'c': self.curves[i]})

    def ana_content(self):
        r = self.r
        trimmed = r.read_bool()
        loops = []
        if trimmed:
            last = False
            while not last:
                orient = r.read_bool()
                curves, last = self.loop_curves()
                loops.append({'orient': orient, 'curves': curves})
        return loops

    def iso_content(self):
        r = self.r
        orient = r.read_bool()
        c1 = self.ref_or_curve()
        c2 = self.ref_or_curve()
        idx = (lambda: nbits_then_uint(r)) if ISO_IDX == 'nbits' else (lambda: uvar(r, self.nref))
        third_new = r.read_bool()
        i3 = None if third_new else idx()
        fourth_new = r.read_bool()
        i4 = None if fourth_new else idx()
        common = self.vertex() if (third_new and fourth_new) else None
        ph3 = {'t': 'implicit3'} if third_new else None
        ph4 = {'t': 'implicit4'} if fourth_new else None
        for ph in (ph3, ph4):
            if ph is not None:
                self.curves.append(ph)
        if DEBUG:
            print('  iso', len(self.curves), c1['t'], c2['t'], 'i3', i3, 'i4', i4, 'common', common, 'bit', r.tell())
        return {'orient': orient, 'c1': c1, 'c2': c2, 'i3': i3, 'i4': i4, 'common': common, 'ph3': ph3, 'ph4': ph4}

    def face(self):
        r = self.r
        is_curve, t = entity_type(r)
        if is_curve:
            raise ValueError(f"expected face at bit {r.tell()}")
        d = {'type': t, 'bit': r.tell()}

        if t in (ISO_PLANE, ANA_PLANE):
            d['nx'] = r.read_double()
            d['ny'] = r.read_double()
            d['nz_pos'] = r.read_bool()

        if t == ISO_TORUS:
            d['is_major'] = r.read_bool()

        d['orient_shell'] = r.read_bool()

        if 2 <= t <= 7:
            if t == ISO_NURBS:
                raise NotImplementedError(f"iso nurbs at bit {r.tell()}")
            d['iso'] = self.iso_content()
        elif t == ANA_GENERIC:
            raise NotImplementedError(f"face type {t} at bit {r.tell()}")
        else:
            d['loops'] = self.ana_content()
        if t == ANA_NURBS:
            import hcg_nurbs
            d['nurbs'] = hcg_nurbs.read_nurbs(r, self.tol)

        if t == ANA_CYL:
            d['axis_pt'] = self.point()
            d['axis_dir'] = self.point()
        elif t == ANA_TORUS:
            d['center'] = self.point()
            d['xaxis'] = self.point()
            d['yaxis'] = self.point()
        elif t == ANA_SPHERE:
            d['center'] = self.point()
        elif t == ANA_CONE:
            d['axis_pt'] = self.point()
            d['apex'] = self.point()

        self.faces_done.append(d)
        return d

    def shell(self):
        r = self.r
        single = r.read_bool()
        n = 1 if single else nbits_then_uint(r)
        faces = [self.face() for _ in range(n)]
        for f in faces:
            f['is_iso'] = r.read_bool()
        return faces


def read_brep(r):
    global LAST_PARTIAL
    base = r.read_bool()
    if base:
        p = prc_parse.Parser(r)
        p.attributes()
        p.name()
        r.read_uint()

    behavior = r.read_char()
    tol = r.read_double()
    nref = nbits_then_uint(r)
    nvref = uvar(r, nref)
    neref = uvar(r, nref)

    br = BrepReader(r, tol, nref)

    try:
        if r.read_bool():
            shells = [br.shell()]
        else:
            shells = []
            nconnex = r.read_uint()
            for _ in range(nconnex):
                nshells = r.read_uint()
                for _ in range(nshells):
                    shells.append(br.shell())

        for shell in shells:
            for face in shell:
                if r.read_bool():
                    p = prc_parse.Parser(r)
                    p.attributes()
                    p.name()
                    r.read_uint()

        return {
            'tol': tol,
            'nref': nref,
            'nvref': nvref,
            'neref': neref,
            'shells': shells,
            'nvert': len(br.vertices),
            'ncurves': len(br.curves),
            'curves': br.curves
        }
    except Exception as e:
        LAST_PARTIAL = br.faces_done
        raise RuntimeError(f"{type(e).__name__}: {e}") from e


def read_geometry(section_bytes):
    r = prc_bits.BitReader(section_bytes)
    p = prc_parse.Parser(r)
    p.expect(306)
    p.content_base(False)

    bodies = []
    ncontexts = r.read_uint()
    for _ in range(ncontexts):
        p.expect(141)
        p.content_base(False)
        r.read_char()
        r.read_double()
        r.read_double()
        if r.read_bool():
            r.read_double()
        if r.read_bool():
            scale = r.read_double()
        else:
            scale = 1.0

        nbodies = r.read_uint()
        for _ in range(nbodies):
            t = r.read_uint()
            if t != 156:
                raise NotImplementedError(f"body type {t}")
            b = read_brep(r)
            b['scale'] = scale
            bodies.append(b)

    return bodies


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python hcg_parse.py <file.prc>")
        sys.exit(1)

    sys.stdout.reconfigure(encoding='utf-8')
    filename = sys.argv[1]

    with open(filename, 'rb') as f:
        data = f.read()

    fs_list = prc_parse.parse_container(data)['file_structures']

    for i, fs in enumerate(fs_list):
        try:
            geom_bytes = prc_parse.fs_sections(data, fs)['geometry']
            bodies = read_geometry(geom_bytes)
            for b in bodies:
                nfaces = sum(len(s) for s in b['shells'])
                face_types = {}
                for s in b['shells']:
                    for f in s:
                        t = f['type']
                        face_types[t] = face_types.get(t, 0) + 1

                curve_kinds = {}
                for c in b.get('curves', []):
                    kind = c.get('t', 'unknown')
                    curve_kinds[kind] = curve_kinds.get(kind, 0) + 1

                print(f"fs {i}: faces={nfaces}, face_types={face_types}, curve_kinds={curve_kinds}, "
                      f"nvert={b['nvert']}, ncurves={b['ncurves']}, nvref={b['nvref']}, "
                      f"neref={b['neref']}, nref={b['nref']}, scale={b['scale']}")
        except Exception as e:
            print(f"fs {i}: ERROR: {e}")
            if LAST_PARTIAL is not None:
                partial_types = {}
                for f in LAST_PARTIAL:
                    t = f['type']
                    partial_types[t] = partial_types.get(t, 0) + 1
                print(f"  partial faces: {len(LAST_PARTIAL)}, types={partial_types}")
            else:
                print("  no partial faces")
