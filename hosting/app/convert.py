"""Модуль конвертации исходных файлов в GLB и публикации страницы просмотра."""

import os
import sys
import re
import json
import struct
import shutil
import subprocess
import signal
import tempfile

from . import config
from . import page


class ConvertError(Exception):
    """Ошибка конвертации, сообщение показывается пользователю."""
    pass


def _clean_env(cwd):
    """Возвращает очищенный словарь переменных окружения."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": cwd,
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run(cmd, cwd, timeout=None):
    """
    Запускает команду в отдельной сессии.
    Возвращает (returncode, output_text).
    """
    if timeout is None:
        timeout = config.TASK_TIMEOUT_S

    env = _clean_env(cwd)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env
    )

    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()
        raise ConvertError("Превышено время обработки")

    # добить осиротевших потомков группы: пока в группе есть живые процессы, её номер не переиспользуется
    try:
        os.killpg(proc.pid, 0)
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass

    text = out.decode("utf-8", errors="replace")
    if len(text) > 20000:
        text = text[-20000:]

    return proc.returncode, text


def inspect_glb(path):
    """
    Проверяет структуру GLB и извлекает базовую информацию.
    Возвращает словарь с ключами: nodes, meshes, vertices, root, external_uri, buffers, mesh_nodes.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        raise ConvertError("Файл GLB повреждён")

    if len(data) < 12:
        raise ConvertError("Файл GLB повреждён")

    magic = data[:4]
    if magic != b"glTF":
        raise ConvertError("Файл GLB повреждён")

    version = struct.unpack("<I", data[4:8])[0]
    if version != 2:
        raise ConvertError("Файл GLB повреждён")

    total_length = struct.unpack("<I", data[8:12])[0]
    if total_length != len(data):
        raise ConvertError("Файл GLB повреждён")

    offset = 12
    if offset + 8 > len(data):
        raise ConvertError("Файл GLB повреждён")

    chunk_length = struct.unpack("<I", data[offset:offset + 4])[0]
    chunk_type = data[offset + 4:offset + 8]

    if chunk_type != b"JSON":
        raise ConvertError("Файл GLB повреждён")

    json_start = offset + 8
    json_end = json_start + chunk_length
    if json_end > len(data):
        raise ConvertError("Файл GLB повреждён")

    json_bytes = data[json_start:json_end]
    try:
        gltf = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ConvertError("Файл GLB повреждён")

    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    buffers = gltf.get("buffers", [])
    images = gltf.get("images", [])
    accessors = gltf.get("accessors", [])
    scenes = gltf.get("scenes", [])

    vertices = 0
    for mesh in meshes:
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            pos_idx = attrs.get("POSITION")
            if pos_idx is not None and 0 <= pos_idx < len(accessors):
                acc = accessors[pos_idx]
                vertices += acc.get("count", 0)

    root = ""
    scene_idx = gltf.get("scene", 0)
    if 0 <= scene_idx < len(scenes):
        scene = scenes[scene_idx]
        scene_nodes = scene.get("nodes", [])
        if scene_nodes:
            first_node_idx = scene_nodes[0]
            if 0 <= first_node_idx < len(nodes):
                root = nodes[first_node_idx].get("name", "")

    external_uri = False
    for buf in buffers:
        uri = buf.get("uri")
        if uri and not uri.startswith("data:"):
            external_uri = True
            break
    if not external_uri:
        for img in images:
            uri = img.get("uri")
            if uri and not uri.startswith("data:"):
                external_uri = True
                break

    mesh_nodes = 0
    for node in nodes:
        if "mesh" in node:
            mesh_nodes += 1

    return {
        "nodes": len(nodes),
        "meshes": len(meshes),
        "vertices": vertices,
        "root": root,
        "external_uri": external_uri,
        "buffers": len(buffers),
        "mesh_nodes": mesh_nodes
    }


def check_glb(path, max_mb):
    """
    Проверяет GLB на наличие геометрии и размер.
    Возвращает результат inspect_glb.
    """
    info = inspect_glb(path)

    if info["meshes"] == 0 or info["vertices"] == 0:
        raise ConvertError("В модели не найдено геометрии")

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > max_mb:
        raise ConvertError(f"Страница получилась слишком тяжёлой ({size_mb:.1f} МБ, предел {max_mb} МБ)")

    return info


def conv_step(src, work):
    """Конвертация STEP в GLB."""
    glb_path = os.path.join(work, "model.glb")
    script = os.path.join(config.SCRIPTS, "step2glb.py")
    tolerances = ["0.5", "2.0", "5.0"]

    last_error = None
    for tol in tolerances:
        cmd = [config.PYTHON, script, src, glb_path, tol]
        rc, out = run(cmd, work)

        if rc != 0 or not os.path.exists(glb_path):
            raise ConvertError("Не удалось прочитать STEP")

        try:
            check_glb(glb_path, config.MAX_GLB_MB)
            return
        except ConvertError as e:
            last_error = e
            # Если ошибка только по размеру, пробуем следующий допуск
            if "слишком тяжёлой" in str(e):
                continue
            else:
                raise

    if last_error:
        raise last_error
    raise ConvertError("Не удалось прочитать STEP")


def conv_pdf(src, work):
    """Конвертация PDF (PRC) в GLB."""
    glb_path = os.path.join(work, "model.glb")
    script = os.path.join(config.SCRIPTS, "prc", "prc2glb.py")
    prcwork = os.path.join(work, "prcwork")

    cmd = [config.PYTHON, script, src, glb_path, prcwork]
    rc, out = run(cmd, work)

    if rc != 0:
        raise ConvertError("В PDF не найдена 3D-модель PRC или она не разобрана")

    if not os.path.exists(glb_path):
        raise ConvertError("В PDF не найдена 3D-модель PRC или она не разобрана")

    # Проверяем наличие мешей
    match = re.search(r"Leaf parts with mesh:\s*(\d+)", out)
    if not match:
        raise ConvertError("В PDF не найдена 3D-модель PRC или она не разобрана")

    n = int(match.group(1))
    if n <= 0:
        raise ConvertError("В PDF не найдена 3D-модель PRC или она не разобрана")

    if "Missing leaf parts: none" not in out:
        raise ConvertError("В PDF не найдена 3D-модель PRC или она не разобрана")


def conv_nwd(src, work):
    """Конвертация NWD в GLB."""
    glb_path = os.path.join(work, "model.glb")
    dump = os.path.join(work, "dump")
    report = os.path.join(work, "report.json")

    # Шаг 1: dump
    cmd1 = [config.NWD_DUMP, src, dump]
    rc1, _ = run(cmd1, work)
    if rc1 != 0:
        raise ConvertError("Не удалось прочитать NWD")

    # Шаг 2: nwd2glb
    script2 = os.path.join(config.SCRIPTS, "nwd", "nwd2glb.py")
    q_glb = os.path.join(work, "q.glb")
    cmd2 = [config.PYTHON, script2, dump, glb_path, report, "--quant", q_glb]
    rc2, _ = run(cmd2, work)
    if rc2 in (-9, 137):
        raise ConvertError(
            "Модель слишком большая для обработки: не хватило памяти сервера. "
            "Разбейте модель на части (по системам или этажам) и загрузите отдельно"
        )
    if rc2 != 0:
        raise ConvertError("Не удалось прочитать NWD")

    if not os.path.exists(glb_path):
        raise ConvertError("Не удалось прочитать NWD")

    # Шаг 3: check_area
    script3 = os.path.join(config.SCRIPTS, "nwd", "check_area.py")
    cmd3 = [config.PYTHON, script3, dump, glb_path]
    rc3, out3 = run(cmd3, work)

    if rc3 != 0:
        raise ConvertError("Проверка площадей модели не пройдена")

    # Ищем строку "GLB X.XX м2"
    found_area = False
    for line in out3.splitlines():
        if re.search(r"GLB [\d.]+ м2", line):
            found_area = True
            break

    if not found_area:
        raise ConvertError("Проверка площадей модели не пройдена")

    # Шаг 4: квантованная версия (собрана nwd2glb) вместо float32, если файл велик
    size_mb = os.path.getsize(glb_path) / (1024 * 1024)
    if size_mb > config.QUANTIZE_ABOVE_MB and os.path.exists(q_glb):
        os.replace(q_glb, glb_path)
    elif os.path.exists(q_glb):
        os.remove(q_glb)

    # Шаг 5: EXT_meshopt_compression для крупных файлов; нет библиотеки или выигрыша — файл остаётся как есть
    if size_mb > config.QUANTIZE_ABOVE_MB:
        mo_glb = os.path.join(work, "m.glb")
        rc5, _ = run([config.PYTHON, os.path.join(config.SCRIPTS, "nwd", "meshopt_glb.py"), glb_path, mo_glb], work)
        if rc5 == 0 and os.path.exists(mo_glb):
            os.replace(mo_glb, glb_path)
        elif os.path.exists(mo_glb):
            os.remove(mo_glb)


def conv_glb(src, work):
    """Копирование GLB с проверкой автономности."""
    info = inspect_glb(src)

    if info["external_uri"] or info["buffers"] > 1:
        raise ConvertError("GLB ссылается на внешние файлы — загрузите автономный GLB")

    glb_path = os.path.join(work, "model.glb")
    shutil.copy2(src, glb_path)


def convert(kind, src, work, page_dir, title, expires_ts):
    """
    Главная функция конвертации и публикации.
    Возвращает {"parts": int, "size_glb": int}.
    """
    os.makedirs(work, exist_ok=True)

    # Диспетчеризация по типу
    if kind == "step":
        conv_step(src, work)
    elif kind == "pdf":
        conv_pdf(src, work)
    elif kind == "nwd":
        conv_nwd(src, work)
    elif kind == "glb":
        conv_glb(src, work)
    else:
        raise ConvertError("Неизвестный тип файла")

    glb_path = os.path.join(work, "model.glb")

    # Проверка GLB
    info = check_glb(glb_path, config.MAX_GLB_MB)
    parts = info["mesh_nodes"]
    if parts < 1:
        parts = 1

    # Переименование корня
    if info["root"]:
        script_rename = os.path.join(config.SCRIPTS, "glb_rename_root.py")
        r_glb = os.path.join(work, "r.glb")
        clean_title = page.clean_title(title)
        cmd_rename = [config.PYTHON, script_rename, glb_path, r_glb, info["root"], clean_title]
        rc_rename, _ = run(cmd_rename, work)
        if rc_rename == 0 and os.path.exists(r_glb):
            os.replace(r_glb, glb_path)

    # Сборка фрагмента страницы
    if kind == "nwd":
        script_build = os.path.join(config.SCRIPTS, "nwd", "build_viewer.py")
        template = os.path.join(config.ASSETS, "viewer-template.html")
        module = os.path.join(config.ASSETS, "section_module.js")
        frag_path = os.path.join(work, "frag.html")
        cmd_build = [config.PYTHON, script_build, template, module, frag_path, "-", "-"]
        rc_build, _ = run(cmd_build, work)
        if rc_build != 0:
            raise ConvertError("Ошибка сборки страницы")
        with open(frag_path, "r", encoding="utf-8") as f:
            fragment = f.read()
    else:
        template = os.path.join(config.ASSETS, "viewer-template.html")
        with open(template, "r", encoding="utf-8") as f:
            fragment = f.read()

    # Обёртка документа
    doc = page.wrap_document(
        fragment,
        page.clean_title(title),
        expires_ts,
        fix_prc=(kind == "pdf"),
        subtitle="Просмотр модели"
    )

    # Атомарная публикация
    parent_dir = os.path.dirname(page_dir)
    if not parent_dir:
        parent_dir = "."
    os.makedirs(parent_dir, exist_ok=True)

    tmp_dir = tempfile.mkdtemp(dir=parent_dir)
    try:
        # Записываем index.html
        index_path = os.path.join(tmp_dir, "index.html")
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(doc)

        # Перемещаем model.glb
        final_glb = os.path.join(tmp_dir, "model.glb")
        shutil.move(glb_path, final_glb)

        # Права доступа
        os.chmod(tmp_dir, 0o755)
        os.chmod(index_path, 0o644)
        os.chmod(final_glb, 0o644)

        # Удаляем старую страницу, если есть
        if os.path.exists(page_dir):
            if os.path.isdir(page_dir):
                shutil.rmtree(page_dir)
            else:
                os.remove(page_dir)

        # Переименовываем
        os.rename(tmp_dir, page_dir)

    except Exception:
        # Убираем временную директорию при ошибке
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    size_glb = os.path.getsize(os.path.join(page_dir, "model.glb"))

    return {
        "parts": parts,
        "size_glb": size_glb
    }
