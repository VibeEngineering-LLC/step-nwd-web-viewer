import sys, re, pathlib
sys.stdout.reconfigure(encoding='utf-8')
tpl, mod, out, title, sub = sys.argv[1:6]
t = pathlib.Path(tpl).read_text(encoding='utf-8'); m = pathlib.Path(mod).read_text(encoding='utf-8')
def sub1(pat, rep, s, flags=re.S):
    s2, n = re.subn(pat, lambda _: rep, s, count=1, flags=flags)
    if n != 1: raise SystemExit('pattern not found: ' + pat[:60])
    return s2
t = sub1(r'<label class="check" for="clipOn">.*?(?=<label class="check" for="xray">)', '<div id="sectionPanel"></div>\n        ', t)
t = sub1(r'function applyClip\(\) \{.*?\n\}\n', 'function applyClip() { SECTION.apply(); }\n', t)
t = sub1(r"\$\('clipOn'\)\.addEventListener.*?(?=\$\('xray'\)\.addEventListener)", '', t)
t = sub1(r'// ---- Load ----', m + '\n// ---- Load ----', t)
t = sub1(r'const tint = TINTS\.find.*?\n(\s*)o\.material = new THREE\.MeshStandardMaterial\(\{[^\n]*\}\);',
         "const src = o.material; o.geometry.computeVertexNormals();\n    o.material = new THREE.MeshStandardMaterial({ color: src.color.clone().convertSRGBToLinear(), opacity: src.opacity, transparent: src.transparent, depthWrite: !src.transparent, metalness: 0.0, roughness: 0.9, envMapIntensity: 0.45, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: (src.userData.back ? 16 : 0) + ((window.__mseq = (window.__mseq || 0) + 1) % 16), side: THREE.DoubleSide });", t)
t = sub1(r'  applyEdges\(\);\n  applyClip\(\);', '  applyEdges();\n  SECTION.init();\n  applyClip();', t)
t = sub1(r'  applyXray\(\);\n', '  applyXray();\n  SECTION.apply();\n', t)
t = sub1(r'Math\.sin\(fov / 2\)\) \* 1\.1;', 'Math.sin(fov / 2)) * 0.8;', t)
t = sub1(r'</style>', '#secBoxBlock .control{flex-wrap:wrap}#secBoxBlock .control input[type=range]{flex:1 1 40%;min-width:0;width:auto}#sectionPanel .control{flex-wrap:wrap;gap:6px}\n</style>', t)
t = sub1(r'  row\.appendChild\(eye\);\n  row\.appendChild\(nameEl\);', "  row.appendChild(eye);\n  let m0 = null; if (parts[0]) parts[0].object.traverse((o) => { if (!m0 && o.isMesh) m0 = o; });\n  if (m0 && m0.material && m0.material.color && !(opts && opts.children)) { const sw = document.createElement('span'); sw.className = 'sw'; sw.style.background = '#' + m0.material.color.getHexString(); row.appendChild(sw); }\n  row.appendChild(nameEl);", t)
t = sub1(r'</style>', '.sw{width:10px;height:10px;border-radius:3px;flex:none;border:1px solid rgba(0,0,0,.3)}\n</style>', t)
if title != '-':  # '-' оставляет плейсхолдеры: подстановку с экранированием делает вызывающий (hosting/app/page.py)
    t = t.replace('{{TITLE}}', title).replace('{{SUBTITLE}}', sub).replace('{{MODEL_NAME}}', title)
pathlib.Path(out).write_text(t, encoding='utf-8')
print('ok', len(t))
