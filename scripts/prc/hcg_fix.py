"""Перестройка грани с дырами: внешний контур с положительной площадью, внутренние — с отрицательной."""
from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace as MF
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.ShapeFix import ShapeFix_Face
from OCP.TopoDS import TopoDS

def area(face):
    g = GProp_GProps(); BRepGProp.SurfaceProperties_s(face, g); return g.Mass()

def fix_negative(face, outer, inners):
    if face is None or outer is None or (area(face) > 0 and BRepCheck_Analyzer(face).IsValid()):
        return face
    S = BRep_Tool.Surface_s(face); rev = lambda w: TopoDS.Wire_s(w.Reversed()); a = lambda w: area(MF(S, w, False).Face())
    mk = MF(S, outer if a(outer) > 0 else rev(outer), True)
    for iw in inners:
        mk.Add(iw if a(iw) < 0 else rev(iw))
    if not mk.IsDone():
        return face
    sf = ShapeFix_Face(mk.Face()); sf.Perform(); f2 = sf.Face()
    return f2 if area(f2) > 0 else face
