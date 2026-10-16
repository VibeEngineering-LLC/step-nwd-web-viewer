import sys
import math
import json
import numpy as np

from OCP.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Ax2, gp_Ax3, gp_Pln, gp_Circ, gp_Trsf
from OCP.GC import GC_MakeArcOfCircle
from OCP.Geom import Geom_BezierCurve, Geom_CylindricalSurface, Geom_SphericalSurface, Geom_ConicalSurface, Geom_ToroidalSurface, Geom_Plane
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
from OCP.BRepFill import BRepFill
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling
from OCP.GeomAbs import GeomAbs_C0
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Wire
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool, BRep_Builder
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location

import hcg_parse
import prc_parse

S = 1.0
WARN = []

def P(t):
    return gp_Pnt(t[0] * S, t[1] * S, t[2] * S)

def _dist(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

def _norm(v):
    l = math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
    if l < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0]/l, v[1]/l, v[2]/l)

def _add(v, w):
    return (v[0]+w[0], v[1]+w[1], v[2]+w[2])

def _sub(v, w):
    return (v[0]-w[0], v[1]-w[1], v[2]-w[2])

def _scale(v, s):
    return (v[0]*s, v[1]*s, v[2]*s)

def _resolve(c):
    if c is None:
        return None
    if c.get('t') == 'ref':
        return _resolve(c.get('c'))
    return c

def endpoints(c):
    c = _resolve(c)
    if c is None:
        return None, None
    t = c.get('t')
    if t == 'line':
        return c['p0'], c['p1']
    if t == 'arc':
        return c['p0'], c['p1']
    if t == 'arc3':
        return c['p0'], c['p1']
    if t == 'circle_full':
        return c['p0'], c['p1']
    if t == 'hermite':
        return c['p0'], c['p1']
    if t == 'composite':
        return c['p0'], c['p1']
    if t == 'translated':
        src = _resolve(c.get('src'))
        if src is None:
            return None, None
        sp0, sp1 = endpoints(src)
        if sp0 is None or sp1 is None:
            return None, None
        vec = c.get('vec', (0,0,0))
        return _add(sp0, vec), _add(sp1, vec)
    if t in ('implicit3', 'implicit4'):
        if 'p0' in c and 'p1' in c:
            return c['p0'], c['p1']
        if c.get('t') == 'translated':
            src = _resolve(c.get('src'))
            if src is None:
                return None, None
            sp0, sp1 = endpoints(src)
            if sp0 is None or sp1 is None:
                return None, None
            vec = c.get('vec', (0,0,0))
            return _add(sp0, vec), _add(sp1, vec)
        return None, None
    return None, None

def _make_line_edge(p0, p1):
    if _dist(p0, p1) < 1e-6:
        return None
    try:
        mk = BRepBuilderAPI_MakeEdge(P(p0), P(p1))
        if not mk.IsDone():
            return None
        return mk.Edge()
    except Exception as e:
        WARN.append(f"line edge failed: {e}")
        return None

def _make_arc_edge(c):
    try:
        p0 = c['p0']
        p1 = c['p1']
        cc = c['c']
        big = c.get('big', False)
        u = _sub(p0, cc)
        v = _sub(p1, cc)
        r = math.sqrt(u[0]*u[0] + u[1]*u[1] + u[2]*u[2])
        if r < 1e-12:
            return _make_line_edge(p0, p1)
        w = _add(u, v)
        wl = math.sqrt(w[0]*w[0] + w[1]*w[1] + w[2]*w[2])
        if wl < 1e-12 * r:
            return _make_line_edge(p0, p1)
        if big:
            mid = _sub(cc, _scale(w, r / wl))
        else:
            mid = _add(cc, _scale(w, r / wl))
        arc = GC_MakeArcOfCircle(P(p0), P(mid), P(p1))
        if not arc.IsDone():
            return _make_line_edge(p0, p1)
        curve = arc.Value()
        mk = BRepBuilderAPI_MakeEdge(curve)
        if not mk.IsDone():
            return None
        return mk.Edge()
    except Exception as e:
        WARN.append(f"arc edge failed: {e}")
        return None

def _make_circle_edge(c):
    try:
        p0 = c['p0']
        cc = c['c']
        n = c.get('n', (0, 0, 1))
        r = _dist(p0, cc) * S
        if r < 1e-12:
            return None
        ax = gp_Ax2(P(cc), gp_Dir(n[0], n[1], n[2]))
        circ = gp_Circ(ax, r)
        mk = BRepBuilderAPI_MakeEdge(circ)
        if not mk.IsDone():
            return None
        return mk.Edge()
    except Exception as e:
        WARN.append(f"circle edge failed: {e}")
        return None

def _make_hermite_edges(c):
    edges = []
    try:
        p0 = c['p0']
        p1 = c['p1']
        pts = c.get('pts', [])
        tans = c.get('tans', [])
        k = len(pts)
        if len(tans) != k + 2:
            Q = [p0]
            for i in range(k):
                Q.append(_add(Q[-1], _scale(pts[i], 0.5)))
            Q.append(p1)
            for i in range(len(Q) - 1):
                e = _make_line_edge(Q[i], Q[i+1])
                if e is not None:
                    edges.append(e)
            return edges
        Q = [p0]
        for i in range(k):
            Q.append(_add(Q[-1], _scale(pts[i], 0.5)))
        Q.append(p1)
        for j in range(k + 1):
            a = _norm(tans[j])
            b = _norm(tans[j+1])
            chord = _sub(Q[j+1], Q[j])
            L = math.sqrt(chord[0]*chord[0] + chord[1]*chord[1] + chord[2]*chord[2])
            if L < 1e-12:
                continue
            if a[0] == 0 and a[1] == 0 and a[2] == 0:
                a = _norm(chord)
            if b[0] == 0 and b[1] == 0 and b[2] == 0:
                b = _norm(chord)
            c1 = _add(Q[j], _scale(a, L / 3.0))
            c2 = _sub(Q[j+1], _scale(b, L / 3.0))
            arr = TColgp_Array1OfPnt(1, 4)
            arr.SetValue(1, P(Q[j]))
            arr.SetValue(2, P(c1))
            arr.SetValue(3, P(c2))
            arr.SetValue(4, P(Q[j+1]))
            bez = Geom_BezierCurve(arr)
            mk = BRepBuilderAPI_MakeEdge(bez)
            if mk.IsDone():
                edges.append(mk.Edge())
            else:
                e = _make_line_edge(Q[j], Q[j+1])
                if e is not None:
                    edges.append(e)
    except Exception as e:
        WARN.append(f"hermite edge failed: {e}")
    return edges

def _make_composite_edges(c):
    edges = []
    try:
        subs = c.get('subs', [])
        for sc in subs:
            se = edges_of(sc)
            edges.extend(se)
    except Exception as e:
        WARN.append(f"composite edge failed: {e}")
    return edges

def _make_translated_edges(c):
    edges = []
    try:
        src = _resolve(c.get('src'))
        if src is None:
            return edges
        vec = c.get('vec', (0, 0, 0))
        se = edges_of(src)
        for e in se:
            try:
                trsf = gp_Trsf()
                trsf.SetTranslation(gp_Vec(vec[0] * S, vec[1] * S, vec[2] * S))
                from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
                mk = BRepBuilderAPI_Transform(e, trsf, False)
                if mk.IsDone():
                    edges.append(TopoDS.Edge_s(mk.Shape()))
            except Exception as ex:
                WARN.append(f"translate edge failed: {ex}")
    except Exception as e:
        WARN.append(f"translated edge failed: {e}")
    return edges

def edges_of(c):
    c = _resolve(c)
    if c is None:
        return []
    t = c.get('t')
    if t == 'line':
        e = _make_line_edge(c['p0'], c['p1'])
        return [e] if e is not None else []
    if t == 'arc':
        e = _make_arc_edge(c)
        return [e] if e is not None else []
    if t == 'arc3':
        try:
            p0 = c['p0']
            p1 = c['p1']
            m = c['m']
            arc = GC_MakeArcOfCircle(P(p0), P(m), P(p1))
            if not arc.IsDone():
                return []
            curve = arc.Value()
            mk = BRepBuilderAPI_MakeEdge(curve)
            if not mk.IsDone():
                return []
            return [mk.Edge()]
        except Exception as e:
            WARN.append(f"arc3 edge failed: {e}")
            return []
    if t == 'circle_full':
        e = _make_circle_edge(c)
        return [e] if e is not None else []
    if t == 'hermite':
        return _make_hermite_edges(c)
    if t == 'composite':
        return _make_composite_edges(c)
    if t == 'translated':
        return _make_translated_edges(c)
    if t in ('implicit3', 'implicit4'):
        if c.get('t') == 'line' or 'p0' in c and 'p1' in c:
            e = _make_line_edge(c['p0'], c['p1'])
            return [e] if e is not None else []
        if c.get('t') == 'translated':
            return _make_translated_edges(c)
        return []
    return []

def chain_wire(edges):
    if not edges:
        return None
    try:
        mk = BRepBuilderAPI_MakeWire()
        for e in edges:
            mk.Add(e)
        if not mk.IsDone():
            return None
        return mk.Wire()
    except Exception as e:
        WARN.append(f"chain_wire failed: {e}")
        return None

def fill_face(wire_edges):
    try:
        mf = BRepOffsetAPI_MakeFilling()
        for e in wire_edges:
            mf.Add(e, GeomAbs_C0)
        mf.Build()
        return TopoDS.Face_s(mf.Shape())
    except Exception as e:
        WARN.append(f"fill_face failed: {e}")
        return None

def _closest_pair(p1, p2, p3, p4):
    pairs = [
        (p1, p2, 0, 1),
        (p1, p3, 0, 2),
        (p1, p4, 0, 3),
        (p2, p3, 1, 2),
        (p2, p4, 1, 3),
        (p3, p4, 2, 3),
    ]
    best = None
    best_d = float('inf')
    for a, b, ia, ib in pairs:
        d = _dist(a, b)
        if d < best_d:
            best_d = d
            best = (ia, ib)
    return best

def iso_face(f, curves):
    try:
        iso = f.get('iso', {})
        c1 = _resolve(iso.get('c1'))
        c2 = _resolve(iso.get('c2'))
        i3 = iso.get('i3')
        i4 = iso.get('i4')
        common = iso.get('common')
        ph3 = iso.get('ph3')
        ph4 = iso.get('ph4')

        e1 = endpoints(c1)
        e2 = endpoints(c2)
        if e1 is None or e2 is None:
            return None
        A1, B1 = e1
        A2, B2 = e2

        pairs = [
            (A1, A2, 0, 0),
            (A1, B2, 0, 1),
            (B1, A2, 1, 0),
            (B1, B2, 1, 1),
        ]
        best = None
        best_d = float('inf')
        for a, b, ia, ib in pairs:
            d = _dist(a, b)
            if d < best_d:
                best_d = d
                best = (ia, ib)
        ia, ib = best
        if ia == 0 and ib == 0:
            B = A1
            A = B1
            C = B2
        elif ia == 0 and ib == 1:
            B = A1
            A = B1
            C = A2
        elif ia == 1 and ib == 0:
            B = B1
            A = A1
            C = B2
        else:
            B = B1
            A = A1
            C = A2

        c3 = None
        c4 = None
        if i3 is not None:
            c3 = _resolve(curves[i3])
        elif ph3 is not None:
            c3 = ph3
        if i4 is not None:
            c4 = _resolve(curves[i4])
        elif ph4 is not None:
            c4 = ph4

        if common is not None:
            D = common
        elif i3 is not None and c3 is not None and endpoints(c3)[0] is not None:
            e3 = endpoints(c3)
            if e3 is not None:
                p3a, p3b = e3
                if _dist(p3a, C) < _dist(p3b, C):
                    D = p3b
                else:
                    D = p3a
            else:
                D = _add(_sub(A, B), C)
        elif i4 is not None and c4 is not None and endpoints(c4)[0] is not None:
            e4 = endpoints(c4)
            if e4 is not None:
                p4a, p4b = e4
                if _dist(p4a, A) < _dist(p4b, A):
                    D = p4b
                else:
                    D = p4a
            else:
                D = _add(_sub(A, B), C)
        else:
            D = _add(_sub(A, B), C)

        if ph3 is not None:
            if c1 is not None and c1.get('t') == 'line':
                ph3.clear()
                ph3.update({'t': 'line', 'p0': C, 'p1': D})
            else:
                ph3.clear()
                ph3.update({'t': 'translated', 'src': c1, 'vec': _sub(C, B)})
        if ph4 is not None:
            if c2 is not None and c2.get('t') == 'line':
                ph4.clear()
                ph4.update({'t': 'line', 'p0': D, 'p1': A})
            else:
                ph4.clear()
                ph4.update({'t': 'translated', 'src': c2, 'vec': _sub(A, B)})

        c3_final = c3 if c3 is not None else ph3
        c4_final = c4 if c4 is not None else ph4

        edges = []
        for cc in [c1, c2, c3_final, c4_final]:
            if cc is not None:
                edges.extend(edges_of(cc))

        if not edges:
            return None

        ftype = f.get('type', 0)
        if ftype == 2:
            wire = chain_wire(edges)
            if wire is None:
                return None
            mk = BRepBuilderAPI_MakeFace(wire, True)
            if not mk.IsDone():
                return None
            return mk.Face()
        elif ftype == 3:
            arc_edges = []
            for cc in [c1, c2, c3_final, c4_final]:
                if cc is not None and cc.get('t') in ('arc', 'arc3'):
                    e = edges_of(cc)
                    if e:
                        arc_edges.append(e[0])
            if len(arc_edges) >= 2:
                try:
                    return BRepFill.Face_s(arc_edges[0], arc_edges[1])
                except Exception as e:
                    WARN.append(f"BRepFill.Face failed: {e}")
            return fill_face(edges)
        else:
            return fill_face(edges)
    except Exception as e:
        import traceback; WARN.append(f"iso_face failed: {e} | " + traceback.format_exc().replace(chr(10), " ")[-300:])
        return None

def ana_face(f):
    try:
        loops = f.get('loops', [])
        wires = []
        loop_edges = []
        for loop in loops:
            if loop is None:
                continue
            curves = loop.get('curves', [])
            edges = []
            for c in curves:
                edges.extend(edges_of(c))
            if not edges:
                continue
            loop_edges.append(edges)
            w = chain_wire(edges)
            if w is not None:
                wires.append(w)

        if not wires:
            if f.get('type') == 13 and f.get('nurbs'):
                import hcg_bspline
                return BRepBuilderAPI_MakeFace(hcg_bspline.bspline_surface(f['nurbs'], S), 1e-6).Face()
            return None

        outer_idx = 0
        max_diag = -1.0
        for i, edges in enumerate(loop_edges):
            pts = []
            for c in loops[i].get('curves', []):
                e = endpoints(c)
                if e is not None and e[0] is not None and e[1] is not None:
                    pts.append(e[0])
                    pts.append(e[1])
            if len(pts) >= 2:
                minx = min(p[0] for p in pts)
                maxx = max(p[0] for p in pts)
                miny = min(p[1] for p in pts)
                maxy = max(p[1] for p in pts)
                minz = min(p[2] for p in pts)
                maxz = max(p[2] for p in pts)
                diag = math.sqrt((maxx-minx)**2 + (maxy-miny)**2 + (maxz-minz)**2)
                if diag > max_diag:
                    max_diag = diag
                    outer_idx = i

        outer_wire = wires[outer_idx]
        inner_wires = [wires[i] for i in range(len(wires)) if i != outer_idx]

        ftype = f.get('type', 0)
        face = None

        if ftype == 8:
            try:
                nx = f.get('nx', 0.0)
                ny = f.get('ny', 0.0)
                nz_pos = f.get('nz_pos', True)
                nz_sq = max(0.0, 1.0 - nx*nx - ny*ny)
                nz = math.sqrt(nz_sq)
                if not nz_pos:
                    nz = -nz
                n = (nx, ny, nz)
                first_loop = loops[0]
                first_curve = first_loop.get('curves', [None])[0]
                e = endpoints(first_curve)
                if e is None:
                    raise ValueError("no origin")
                origin = e[0]
                pln = gp_Pln(P(origin), gp_Dir(n[0], n[1], n[2]))
                mk = BRepBuilderAPI_MakeFace(pln, outer_wire, True)
                for iw in inner_wires:
                    mk.Add(iw)
                if mk.IsDone():
                    face = mk.Face()
            except Exception as e:
                WARN.append(f"plane face failed: {e}")
        elif ftype == 9:
            try:
                axis_pt = f.get('axis_pt', (0,0,0))
                axis_dir = f.get('axis_dir', (0,0,1))
                d = _norm(axis_dir)
                o = axis_pt
                dists = []
                for loop in loops:
                    for c in loop.get('curves', []):
                        e = endpoints(c)
                        if e is not None:
                            for p in [e[0], e[1]]:
                                v = _sub(p, o)
                                cross = (v[1]*d[2] - v[2]*d[1], v[2]*d[0] - v[0]*d[2], v[0]*d[1] - v[1]*d[0])
                                dists.append(math.sqrt(cross[0]*cross[0] + cross[1]*cross[1] + cross[2]*cross[2]) * S)
                if dists:
                    radius = sum(dists) / len(dists)
                else:
                    radius = 1.0
                ax3 = gp_Ax3(P(o), gp_Dir(d[0], d[1], d[2]))
                surf = Geom_CylindricalSurface(ax3, radius)
                mk = BRepBuilderAPI_MakeFace(surf, outer_wire, True)
                for iw in inner_wires:
                    mk.Add(iw)
                if mk.IsDone():
                    face = mk.Face()
            except Exception as e:
                WARN.append(f"cylinder face failed: {e}")
        elif ftype == 11:
            try:
                center = f.get('center', (0,0,0))
                first_loop = loops[0]
                first_curve = first_loop.get('curves', [None])[0]
                e = endpoints(first_curve)
                if e is None:
                    raise ValueError("no point")
                r = _dist(center, e[0]) * S
                ax3 = gp_Ax3(P(center), gp_Dir(0, 0, 1))
                surf = Geom_SphericalSurface(ax3, r)
                mk = BRepBuilderAPI_MakeFace(surf, outer_wire, True)
                for iw in inner_wires:
                    mk.Add(iw)
                if mk.IsDone():
                    face = mk.Face()
            except Exception as e:
                WARN.append(f"sphere face failed: {e}")
        elif ftype == 13 and f.get('nurbs'):
            try:
                import hcg_bspline
                surf = hcg_bspline.bspline_surface(f['nurbs'], S)
                mk = BRepBuilderAPI_MakeFace(surf, outer_wire, True) if outer_wire is not None else BRepBuilderAPI_MakeFace(surf, 1e-6)
                for iw in inner_wires:
                    mk.Add(iw)
                if mk.IsDone():
                    face = mk.Face()
            except Exception as e:
                WARN.append(f"nurbs face failed: {e}")

        if face is not None:
            try:
                sf = ShapeFix_Face(face)
                sf.Perform()
                face = sf.Face()
            except Exception as e:
                WARN.append(f"ShapeFix_Face failed: {e}")
        else:
            face = fill_face(loop_edges[outer_idx])
        try:
            import hcg_fix
            face = hcg_fix.fix_negative(face, outer_wire, inner_wires)
        except Exception as e:
            WARN.append(f"fix_negative failed: {e}")

        return face
    except Exception as e:
        import traceback; WARN.append(f"ana_face failed: {e} | " + traceback.format_exc().replace(chr(10), " ")[-300:])
        return None

def build_body(body):
    global S
    S = body.get('scale', 1.0)
    curves = body.get('curves', [])
    shells = body.get('shells', [])
    faces_ok = 0
    faces_failed = {}
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)

    for shell in shells:
        for f in shell:
            ftype = f.get('type', 0)
            face = None
            try:
                if 2 <= ftype <= 7:
                    face = iso_face(f, curves)
                else:
                    face = ana_face(f)
            except Exception as e:
                WARN.append(f"face build failed: {e}")
                face = None

            if face is not None:
                if not f.get('orient_shell', True):
                    face = TopoDS.Face_s(face.Reversed())
                builder.Add(comp, face)
                faces_ok += 1
            else:
                faces_failed[ftype] = faces_failed.get(ftype, 0) + 1

    stats = {
        'faces_ok': faces_ok,
        'faces_failed': faces_failed,
        'warnings': len(WARN)
    }
    return comp, stats

def mesh_compound(comp, lin=0.5, ang=0.3):
    try:
        BRepMesh_IncrementalMesh(comp, lin, False, ang, True)
    except Exception as e:
        WARN.append(f"mesh failed: {e}")
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3), dtype=np.int64)

    V_list = []
    F_list = []
    offset = 0
    try:
        exp = TopExp_Explorer(comp, TopAbs_FACE)
        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None:
                trsf = loc.Transformation()
                nb_nodes = tri.NbNodes()
                for i in range(1, nb_nodes + 1):
                    p = tri.Node(i).Transformed(trsf)
                    V_list.append((p.X(), p.Y(), p.Z()))
                nb_tris = tri.NbTriangles()
                reversed_face = face.Orientation() == TopAbs_REVERSED
                for i in range(1, nb_tris + 1):
                    a, b, c = tri.Triangle(i).Get()
                    if reversed_face:
                        b, c = c, b
                    F_list.append((a - 1 + offset, b - 1 + offset, c - 1 + offset))
                offset += nb_nodes
            exp.Next()
    except Exception as e:
        WARN.append(f"mesh extraction failed: {e}")

    V = np.array(V_list, dtype=np.float64).reshape(-1, 3)
    F = np.array(F_list, dtype=np.int64).reshape(-1, 3)
    return V, F

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python hcg_build.py <file.prc> <out_dir>")
        sys.exit(1)

    prc_file = sys.argv[1]
    out_dir = sys.argv[2]

    with open(prc_file, 'rb') as fh:
        data = fh.read()

    container = prc_parse.parse_container(data)
    fs_list = container.get('file_structures', [])

    import os
    os.makedirs(out_dir, exist_ok=True)

    for i, fs in enumerate(fs_list):
        try:
            geom = prc_parse.fs_sections(data, fs)['geometry']
            bodies = hcg_parse.read_geometry(geom)
        except Exception as e:
            print(f"fs{i:02d}: parse error: {e}")
            continue

        for body in bodies:
            try:
                comp, stats = build_body(body)
                V, F = mesh_compound(comp)
                np.savez(os.path.join(out_dir, f"fs{i:02d}.npz"), V=V, F=F)
                if len(V) > 0:
                    bbox_min = V.min(axis=0)
                    bbox_max = V.max(axis=0)
                    bbox_size = bbox_max - bbox_min
                    bbox_str = f"{bbox_size[0]:.2f}x{bbox_size[1]:.2f}x{bbox_size[2]:.2f}"
                else:
                    bbox_str = "0.00x0.00x0.00"
                print(f"fs{i:02d}: faces_ok={stats['faces_ok']}, faces_failed={stats['faces_failed']}, triangles={len(F)}, bbox={bbox_str}mm")
            except Exception as e:
                print(f"fs{i:02d}: build/mesh error: {e}")
