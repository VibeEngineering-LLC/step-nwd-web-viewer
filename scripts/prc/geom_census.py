"""Перепись секций geometry/extra_geometry: размеры, первые коды. Usage: geom_census.py <prc>"""
import sys
import prc_bits, prc_parse

sys.stdout.reconfigure(encoding="utf-8")
data = open(sys.argv[1], "rb").read()
for i, fs in enumerate(prc_parse.parse_container(data)["file_structures"]):
    secs = prc_parse.fs_sections(data, fs)
    out = [f"FS{i:2d}"] + [f"{k}={len(v)}B" for k, v in secs.items()]
    r = prc_bits.BitReader(secs["geometry"]); p = prc_parse.Parser(r)
    try:
        p.expect(306); p.content_base(False); nctx = r.read_uint()
        out.append(f"geom: n_ctx={nctx}")
        if nctx:
            t = r.read_uint(); out.append(f"ctx_type={t} next_uints={[r.read_uint() for _ in range(6)]}")
    except Exception as e:
        out.append(f"ERR {e}")
    r = prc_bits.BitReader(secs["extra_geometry"]); p = prc_parse.Parser(r)
    try:
        p.expect(307); p.content_base(False)
        for _ in range(r.read_uint()):
            for _ in range(r.read_uint()):
                st = r.read_uint(); out.append(f"body_type={st}" + (f" tol={r.read_double():g}" if st in (155, 156, 173) else ""))
    except Exception as e:
        out.append(f"extraERR {e}")
    print(" ".join(out))
