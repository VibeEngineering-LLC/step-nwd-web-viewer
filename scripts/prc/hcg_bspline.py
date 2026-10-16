"""Geom_BSplineSurface из сжатого NURBS (hcg_nurbs.read_nurbs); координаты * scale."""
from OCP.Geom import Geom_BSplineSurface
from OCP.TColgp import TColgp_Array2OfPnt
from OCP.TColStd import TColStd_Array1OfReal as AR, TColStd_Array1OfInteger as AI, TColStd_Array2OfReal as AR2
from OCP.gp import gp_Pnt

def _arr(vals, cls):
    a = cls(1, len(vals))
    [a.SetValue(i, v) for i, v in enumerate(vals, 1)]
    return a

def bspline_surface(n, s):
    nu, nv = n['nu'], n['nv']; poles = TColgp_Array2OfPnt(1, nu, 1, nv); w = AR2(1, nu, 1, nv)
    for i in range(nu):
        for j in range(nv):
            x, y, z = n['P'][i][j]; poles.SetValue(i + 1, j + 1, gp_Pnt(x * s, y * s, z * s))
            w.SetValue(i + 1, j + 1, (n['weights'][i * nv + j] or 1.0) if n.get('weights') else 1.0)
    args = (_arr(n['knots_u'], AR), _arr(n['knots_v'], AR), _arr(n['mult_u'], AI), _arr(n['mult_v'], AI), n['deg_u'], n['deg_v'])
    return Geom_BSplineSurface(poles, w, *args) if n.get('weights') else Geom_BSplineSurface(poles, *args)
