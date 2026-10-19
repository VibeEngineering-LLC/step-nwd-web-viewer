"""Цвета воздуховодов (nwd2glb.build_palette, category_color): образцы — имена систем и категорий из боевых дампов."""
import colorsys
import json
import os
import struct
import sys

sys.path.insert(0, os.environ.get("NWD_SCRIPTS", "/app/scripts/nwd"))

import nwd2glb

SYSTEMS = ["Приток П1", "Вытяжка В2", "Рекуператор П4", "Рекуператор П12", "Рециркуляция В3.1",
           "Теплоснабжение_Зима", "Холод Приточки", "Дренаж", "Канализация 1",
           "Техн.вент. - IMA", "Техн.вент. - De Lama Item F", "Холод. Фреон_пом2031"]
AIR = [s for s in SYSTEMS if s.startswith(("Рекуператор", "Рециркуляция", "Техн.вент"))]


def old_palette(names):
    """Палитра до правки: эталон для труб (их цвета менять нельзя)."""
    out, seen = {}, {}
    for n in sorted(names):
        hue = next((h for p, h in nwd2glb.FAM if n.startswith(p)), 60)
        k = seen.get(hue, 0)
        seen[hue] = k + 1
        h = (hue + (k % 5 - 2) * 9) / 360.0
        s = 0.9 if k % 2 == 0 else 0.75
        out[n] = tuple(round(x, 3) for x in colorsys.hls_to_rgb(h, 0.42 + 0.07 * ((k // 2) % 3), s))
    return out


def hue(rgb):
    return colorsys.rgb_to_hls(*rgb)[0] * 360


def test_pipe_colors_unchanged():
    new, old = nwd2glb.build_palette(SYSTEMS), old_palette(SYSTEMS)
    pipes = [s for s in SYSTEMS if s not in AIR]
    assert [new[s] for s in pipes] == [old[s] for s in pipes]


def test_air_systems_leave_fallback_hue():
    new = nwd2glb.build_palette(SYSTEMS)
    for s in AIR:
        assert abs(hue(new[s]) - 60) > 25, (s, new[s])


def glb_materials(path):
    data = open(path, "rb").read()
    return json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])["materials"]


def test_back_flag_only_without_system(tmp_path):
    """Материал без системы помечается extras.back (в шаблоне отодвигается от камеры, z-fighting), с системой — нет."""
    from test_nwd_pipeline import build_dump, run_pipeline
    dump = build_dump(tmp_path)
    out, *_ = run_pipeline(dump, tmp_path)
    assert all(m.get("extras") == {"back": 1} for m in glb_materials(out))

    def with_system(groups):
        for k in list(groups):
            groups[k[:4] + ("Приток П1",)] = groups.pop(k)
    out2, *_ = run_pipeline(dump, tmp_path, mutate=with_system)
    assert not any("extras" in m for m in glb_materials(out2))


def test_template_fits_depth_range_each_frame():
    """Плоскости near/far подстраиваются под расстояние до модели в каждом кадре (иначе штриховка z-fighting на удалении)."""
    tpl = open(os.environ.get("ASSETS", "/app/assets") + "/viewer-template.html", encoding="utf-8").read()
    loop = tpl[tpl.index("setAnimationLoop"):]
    assert "function fitDepthRange" in tpl
    assert loop.index("fitDepthRange();") < loop.index("renderer.render(scene, camera)")


def test_viewer_offsets_depth_per_mesh(tmp_path):
    """Соседние меши получают разное смещение глубины, «задние» (без системы) — дальше всех (иначе мерцание совпадающих граней)."""
    import subprocess
    tpl = os.environ.get("ASSETS", "/app/assets") + "/viewer-template.html"
    mod = tmp_path / "m.js"; mod.write_text("x", encoding="utf-8")
    out = tmp_path / "o.html"
    subprocess.run([sys.executable, os.environ.get("NWD_SCRIPTS", "/app/scripts/nwd") + "/build_viewer.py", tpl, str(mod), str(out), "-", "-"], check=True)
    html = out.read_text(encoding="utf-8")
    assert "polygonOffset: true" in html
    assert "(src.userData.back ? 16 : 0) + ((window.__mseq = (window.__mseq || 0) + 1) % 16)" in html


def test_category_color_replaces_neutral_only():
    cov = nwd2glb.category_color("Этаж 2 · IfcCovering", (1.0, 1.0, 1.0))
    assert cov != (1.0, 1.0, 1.0) and max(cov) - min(cov) > 0.1
    assert nwd2glb.category_color("Воздухораспределители", (0.97, 0.97, 0.97)) != (0.97, 0.97, 0.97)
    assert nwd2glb.category_color("Этаж 2 · IfcCovering", (1.0, 0.0, 1.0)) == (1.0, 0.0, 1.0)
    assert nwd2glb.category_color("Этаж 02 · IfcWallStandardCase", (1.0, 1.0, 1.0)) == (1.0, 1.0, 1.0)
