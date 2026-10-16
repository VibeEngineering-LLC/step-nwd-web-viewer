"""Перепись секций тесселяции: число тесселяций, тип первой, заголовок 173 (tolerance, origin). Usage: tess_census.py <prc>"""
import sys, struct
import prc_bits, prc_parse

sys.stdout.reconfigure(encoding="utf-8")
data = open(sys.argv[1], "rb").read()
for i, fs in enumerate(prc_parse.parse_container(data)["file_structures"]):
    buf = prc_parse.fs_sections(data, fs)["tessellation"]
    r = prc_bits.BitReader(buf); p = prc_parse.Parser(r)
    p.expect(305); p.content_base(False); n = r.read_uint(); t = r.read_uint()
    info = ""
    if t == 173:
        calc, faces = r.read_bool(), r.read_bool(); tol = r.read_double()
        org = [struct.unpack("<f", bytes(r.read_char() for _ in range(4)))[0] for _ in range(3)]
        info = f"calc={calc} faces={faces} tol={tol:g} origin={[round(x, 2) for x in org]}"
    print(f"FS{i:2d} sec={len(buf):6d}B n_tess={n} first_type={t} {info}")
