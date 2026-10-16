"""3D-PDF (PRC, сжатый B-rep SolidWorks) -> GLB с деревом сборки. Usage: prc2glb.py <in.pdf> <out.glb> [workdir]
Шаги: extract_prc.py (PyMuPDF) -> hcg_build.py (OCP/cadquery-ocp) -> prc_scene.py (trimesh).
Интерпретаторы через окружение (по умолчанию текущий python): PRC_PY_FITZ, PRC_PY_OCP, PRC_PY_TRI;
PRC_OCP_SITE - доп. site-packages для OCP (в PYTHONPATH шага hcg_build)."""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_FITZ = os.environ.get("PRC_PY_FITZ", sys.executable)
PY_OCP = os.environ.get("PRC_PY_OCP", sys.executable)
PY_TRI = os.environ.get("PRC_PY_TRI", sys.executable)
OCP_SITE = os.environ.get("PRC_OCP_SITE", "")

def run(py, args, extra_env=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(extra_env or {}))
    subprocess.run([py] + args, cwd=HERE, env=env, check=True)

pdf, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
work = os.path.abspath(sys.argv[3]) if len(sys.argv) > 3 else os.path.splitext(out)[0] + "_work"
os.makedirs(os.path.join(work, "mesh"), exist_ok=True)
prc = os.path.join(work, "model.prc")
run(PY_FITZ, ["extract_prc.py", pdf, prc])
run(PY_OCP, ["hcg_build.py", prc, os.path.join(work, "mesh")], {"PYTHONPATH": OCP_SITE} if OCP_SITE else None)
run(PY_TRI, ["prc_scene.py", prc, os.path.join(work, "mesh"), out])
