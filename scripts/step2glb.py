"""step2glb.py: convert STEP assembly to binary glTF (.glb) for three.js.
Run with Python 3.12 and PYTHONPATH=<project>/.venv/Lib/site-packages.
build123d.export_gltf is not used because on an imported assembly it writes an empty scene (0 nodes).
Usage: python step2glb.py <in.step> <out.glb> [linear_deflection_mm=0.5] [angular_deflection_rad=0.3]
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString, TCollection_AsciiString
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDF import TDF_LabelSequence
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.RWGltf import RWGltf_CafWriter
from OCP.RWMesh import RWMesh_NameFormat
from OCP.TColStd import TColStd_IndexedDataMapOfStringString
from OCP.Message import Message_ProgressRange


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("Usage: python step2glb.py <in.step> <out.glb> [linear_deflection_mm=0.5] [angular_deflection_rad=0.3]\n")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]
    linear_deflection = 0.5
    angular_deflection = 0.3

    if len(sys.argv) > 3:
        try:
            linear_deflection = float(sys.argv[3])
        except ValueError:
            sys.stderr.write("Invalid linear_deflection_mm\n")
            sys.exit(1)

    if len(sys.argv) > 4:
        try:
            angular_deflection = float(sys.argv[4])
        except ValueError:
            sys.stderr.write("Invalid angular_deflection_rad\n")
            sys.exit(1)

    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)

    read_status = reader.ReadFile(in_path)
    if read_status != 1:
        sys.stderr.write(f"Error: failed to read STEP file: {in_path}\n")
        sys.exit(1)

    transfer_ok = reader.Transfer(doc)
    if not transfer_ok:
        sys.stderr.write(f"Error: failed to transfer STEP file: {in_path}\n")
        sys.exit(1)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    seq = TDF_LabelSequence()
    shape_tool.GetFreeShapes(seq)
    n_free = seq.Length()

    for i in range(1, n_free + 1):
        label = seq.Value(i)
        shape = shape_tool.GetShape_s(label)
        BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)

    writer = RWGltf_CafWriter(TCollection_AsciiString(out_path), True)
    writer.ChangeCoordinateSystemConverter().SetInputLengthUnit(0.001)
    # node names = part (product) names; default gives instance ids like NAUO12
    writer.SetNodeNameFormat(RWMesh_NameFormat.RWMesh_NameFormat_ProductOrInstance)
    writer.SetMeshNameFormat(RWMesh_NameFormat.RWMesh_NameFormat_ProductOrInstance)

    ok = writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange())
    if not ok:
        sys.stderr.write(f"Error: failed to write glTF file: {out_path}\n")
        sys.exit(2)

    print(f"free shapes: {n_free}, written: {out_path}")


if __name__ == "__main__":
    main()
