"""Rename glTF node names in a GLB (JSON chunk rewrite). Usage: glb_rename_root.py in.glb out.glb old new"""
import json, struct, sys
src, dst, old, new = sys.argv[1:5]
b = open(src, "rb").read()
jlen = struct.unpack("<I", b[12:16])[0]
j = json.loads(b[20:20 + jlen])
n = sum(1 for node in j["nodes"] if node.get("name") == old)
for node in j["nodes"]:
    if node.get("name") == old:
        node["name"] = new
js = json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
js += b" " * ((4 - len(js) % 4) % 4)
rest = b[20 + jlen:]
out = struct.pack("<I", len(js)) + b"JSON" + js + rest
open(dst, "wb").write(struct.pack("<III", 0x46546C67, 2, 12 + len(out)) + out)
print("renamed nodes:", n)
