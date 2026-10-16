"""Извлечь PRC-поток из 3D-PDF (PyMuPDF). Usage: extract_prc.py <pdf> <out.prc>"""
import sys
import fitz

sys.stdout.reconfigure(encoding="utf-8")
doc = fitz.open(sys.argv[1])
for x in range(1, doc.xref_length()):
    try:
        data = doc.xref_stream(x) if doc.xref_is_stream(x) else b""
    except Exception:
        data = b""
    if data[:3] == b"PRC":
        open(sys.argv[2], "wb").write(data)
        print(f"xref {x}: {len(data)} bytes, head {data[:8]!r} -> {sys.argv[2]}")
        break
else:
    sys.exit("PRC stream not found")
