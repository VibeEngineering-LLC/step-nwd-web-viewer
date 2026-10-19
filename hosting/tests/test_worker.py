"""Тесты модулей hosting_app.worker и hosting_app.convert."""

import os
import re
import time
import subprocess
import pytest

from hosting_app import config, db, convert, worker, page


def queue_model(conn, invite_row, data, kind="glb", title="Тест", name="src.glb"):
    """Создаёт модель в очереди и записывает исходный файл в INCOMING."""
    mid = db.create_model(conn, invite_row[1]["id"], title, kind)
    d = os.path.join(config.INCOMING, mid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "wb") as f:
        f.write(data)
    return mid


def test_inspect_glb_basic(data_dir, make_glb_file):
    """Проверка инспекции корректного GLB."""
    path = os.path.join(data_dir, "test.glb")
    make_glb_file(path, external_uri=False, vertices=3)
    info = convert.inspect_glb(path)
    assert info["meshes"] == 1
    assert info["vertices"] == 3
    assert info["root"] == "Корпус"
    assert info["external_uri"] is False


def test_inspect_glb_external_uri(data_dir, make_glb_file):
    """Проверка инспекции GLB с внешней ссылкой."""
    path = os.path.join(data_dir, "test_ext.glb")
    make_glb_file(path, external_uri=True, vertices=3)
    info = convert.inspect_glb(path)
    assert info["external_uri"] is True


def test_inspect_glb_bad_magic(data_dir):
    """Проверка ошибки при неверной магии GLB."""
    path = os.path.join(data_dir, "bad.glb")
    with open(path, "wb") as f:
        f.write(b"NOTGLB" + b"\x00" * 100)
    with pytest.raises(convert.ConvertError):
        convert.inspect_glb(path)


def test_check_glb_zero_vertices(data_dir, make_glb_file):
    """Проверка ошибки при нулевом числе вершин."""
    path = os.path.join(data_dir, "empty.glb")
    make_glb_file(path, external_uri=False, vertices=0)
    with pytest.raises(convert.ConvertError):
        convert.check_glb(path, max_mb=100)


def test_check_glb_too_large(data_dir, make_glb_file):
    """Проверка ошибки при превышении размера."""
    path = os.path.join(data_dir, "big.glb")
    make_glb_file(path, external_uri=False, vertices=3)
    with pytest.raises(convert.ConvertError):
        convert.check_glb(path, max_mb=0)


def test_conv_glb_external_uri(data_dir, make_glb_file):
    """Проверка ошибки при внешней ссылке в GLB."""
    src = os.path.join(data_dir, "src_ext.glb")
    make_glb_file(src, external_uri=True, vertices=3)
    work = os.path.join(data_dir, "work_ext")
    os.makedirs(work, exist_ok=True)
    with pytest.raises(convert.ConvertError):
        convert.conv_glb(src, work)


def test_conv_glb_clean(data_dir, make_glb_file):
    """Проверка успешного копирования чистого GLB."""
    src = os.path.join(data_dir, "src_clean.glb")
    make_glb_file(src, external_uri=False, vertices=3)
    work = os.path.join(data_dir, "work_clean")
    os.makedirs(work, exist_ok=True)
    convert.conv_glb(src, work)
    assert os.path.isfile(os.path.join(work, "model.glb"))


def test_run_timeout_kills_process_group(data_dir):
    """Проверка таймаута и убийства группы процессов."""
    cwd = os.path.join(data_dir, "timeout_cwd")
    os.makedirs(cwd, exist_ok=True)

    # Считаем количество процессов sleep 30 до вызова
    try:
        before = subprocess.run(
            ["pgrep", "-f", "sleep 30"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        before_count = len([l for l in before.splitlines() if l.strip()])
    except Exception:
        before_count = 0

    start = time.monotonic()
    with pytest.raises(convert.ConvertError):
        convert.run(["sh", "-c", "sleep 30 & sleep 30"], cwd=cwd, timeout=1)
    elapsed = time.monotonic() - start

    assert elapsed < 10

    # Даём ядру время на уборку
    time.sleep(1)

    # Проверяем, что процессов sleep 30 не осталось (или не прибавилось)
    try:
        after = subprocess.run(
            ["pgrep", "-f", "sleep 30"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        after_count = len([l for l in after.splitlines() if l.strip()])
    except Exception:
        after_count = 0

    assert after_count <= before_count


def test_run_output_truncation_and_env(data_dir, monkeypatch):
    """Проверка обрезки вывода и чистоты окружения."""
    cwd = os.path.join(data_dir, "env_cwd")
    os.makedirs(cwd, exist_ok=True)

    # Устанавливаем переменные, которые не должны попасть в окружение
    monkeypatch.setenv("DATA_DIR", "/secret/data")
    monkeypatch.setenv("PUBLIC_URL", "http://secret")
    monkeypatch.setenv("MY_TOKEN", "secret_token")

    # Команда, печатающая 100000 символов
    cmd = ["python3", "-c", "print('A' * 100000)"]
    rc, out = convert.run(cmd, cwd=cwd, timeout=10)
    assert rc == 0
    assert len(out) <= 20000

    # Проверяем окружение через env
    rc2, env_out = convert.run(["env"], cwd=cwd, timeout=10)
    assert rc2 == 0
    assert "DATA_DIR" not in env_out
    assert "PUBLIC_URL" not in env_out
    # Никаких переменных с подстрокой TOKEN
    for line in env_out.splitlines():
        assert "TOKEN" not in line


def test_convert_glb_end_to_end(data_dir, conn, invite, make_glb_file):
    """Полный цикл конвертации GLB."""
    # Проверяем наличие шаблона
    template_path = os.path.join(config.ASSETS, "viewer-template.html")
    if not os.path.isfile(template_path):
        pytest.skip("Шаблон viewer-template.html не найден")

    src = os.path.join(data_dir, "e2e_src.glb")
    make_glb_file(src, external_uri=False, vertices=3)

    mid = queue_model(conn, invite, open(src, "rb").read(), kind="glb", title="Моя модель", name="src.glb")

    work = os.path.join(config.WORK, mid)
    page_dir = os.path.join(config.PAGES, mid)

    res = convert.convert("glb", src, work, page_dir, "Моя модель", int(time.time()) + 86400)

    assert res["parts"] >= 1
    assert res["size_glb"] > 0

    # Проверяем содержимое page_dir
    assert os.path.isfile(os.path.join(page_dir, "index.html"))
    assert os.path.isfile(os.path.join(page_dir, "model.glb"))
    entries = os.listdir(page_dir)
    assert set(entries) == {"index.html", "model.glb"}

    # Проверяем HTML
    with open(os.path.join(page_dir, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    assert "const SHARE" in html
    assert "noindex" in html
    assert "cdn.jsdelivr.net" not in html

    # Проверяем переименование корня
    info = convert.inspect_glb(os.path.join(page_dir, "model.glb"))
    expected_root = page.clean_title("Моя модель")
    assert info["root"] == expected_root


def test_convert_step_end_to_end(data_dir, conn, invite, make_step_cube_file):
    """Полный цикл конвертации STEP."""
    src = os.path.join(data_dir, "e2e_step.step")
    if not make_step_cube_file(src):
        pytest.skip("Не удалось создать тестовый STEP файл")

    mid = queue_model(conn, invite, open(src, "rb").read(), kind="step", title="Куб", name="src.step")

    work = os.path.join(config.WORK, mid)
    page_dir = os.path.join(config.PAGES, mid)

    res = convert.convert("step", src, work, page_dir, "Куб", int(time.time()) + 86400)

    assert res["parts"] >= 1
    assert res["size_glb"] > 0
    assert os.path.isfile(os.path.join(page_dir, "index.html"))
    assert os.path.isfile(os.path.join(page_dir, "model.glb"))


def test_worker_process_one_empty_queue(conn):
    """Проверка process_one на пустой очереди."""
    result = worker.process_one(conn)
    assert result is False


def test_worker_process_one_success(conn, invite, data_dir, make_glb_file):
    """Проверка успешной обработки модели."""
    src = os.path.join(data_dir, "worker_src.glb")
    make_glb_file(src, external_uri=False, vertices=3)
    data = open(src, "rb").read()

    mid = queue_model(conn, invite, data, kind="glb", title="Тест", name="src.glb")

    result = worker.process_one(conn)
    assert result is True

    row = db.get_model(conn, mid)
    assert row["status"] == "done"
    assert row["parts"] >= 1

    # Проверяем, что каталоги удалены
    assert not os.path.exists(os.path.join(config.INCOMING, mid))
    assert not os.path.exists(os.path.join(config.WORK, mid))

    # Проверяем, что страница создана
    assert os.path.isdir(os.path.join(config.PAGES, mid))


def test_worker_process_one_convert_error(conn, invite, data_dir, make_glb_file, monkeypatch):
    """Проверка обработки ошибки ConvertError."""
    src = os.path.join(data_dir, "worker_err.glb")
    make_glb_file(src, external_uri=False, vertices=3)
    data = open(src, "rb").read()

    mid = queue_model(conn, invite, data, kind="glb", title="Тест", name="src.glb")

    def raising_fn(*args, **kwargs):
        raise convert.ConvertError("причина")

    monkeypatch.setattr(convert, "convert", raising_fn)

    result = worker.process_one(conn)
    assert result is True

    row = db.get_model(conn, mid)
    assert row["status"] == "failed"
    assert row["error"] == "причина"

    # Проверяем, что incoming удалён
    assert not os.path.exists(os.path.join(config.INCOMING, mid))


def test_worker_process_one_internal_error(conn, invite, data_dir, make_glb_file, monkeypatch):
    """Проверка обработки внутренней ошибки."""
    src = os.path.join(data_dir, "worker_int.glb")
    make_glb_file(src, external_uri=False, vertices=3)
    data = open(src, "rb").read()

    mid = queue_model(conn, invite, data, kind="glb", title="Тест", name="src.glb")

    def raising_fn(*args, **kwargs):
        raise RuntimeError("секрет /data/x")

    monkeypatch.setattr(convert, "convert", raising_fn)

    result = worker.process_one(conn)
    assert result is True

    row = db.get_model(conn, mid)
    assert row["status"] == "failed"
    assert row["error"] == "Внутренняя ошибка обработки"

    # Проверяем, что incoming удалён
    assert not os.path.exists(os.path.join(config.INCOMING, mid))


def test_worker_process_one_missing_source(conn, invite, data_dir):
    """Проверка обработки отсутствующего исходного файла."""
    mid = db.create_model(conn, invite[1]["id"], "Тест", "glb")
    # Не создаём файл в INCOMING

    result = worker.process_one(conn)
    assert result is True

    row = db.get_model(conn, mid)
    assert row["status"] == "failed"
    assert row["error"] == "Исходный файл не найден"


def test_worker_cleanup_once(conn, invite, data_dir):
    """Проверка cleanup_once."""
    now = int(time.time())

    # 1. Expired done model
    mid_expired = db.create_model(conn, invite[1]["id"], "Expired", "glb", now=now - 100 * 86400)
    # Устанавливаем статус done и expires_at в прошлом
    conn.execute(
        "UPDATE models SET status='done', expires_at=? WHERE id=?",
        (now - 10, mid_expired)
    )
    pages_dir = os.path.join(config.PAGES, mid_expired)
    os.makedirs(pages_dir, exist_ok=True)

    # 2. Running expired model (должен остаться)
    mid_running = db.create_model(conn, invite[1]["id"], "Running", "glb", now=now - 100 * 86400)
    conn.execute(
        "UPDATE models SET status='running', expires_at=? WHERE id=?",
        (now - 10, mid_running)
    )
    pages_dir_running = os.path.join(config.PAGES, mid_running)
    os.makedirs(pages_dir_running, exist_ok=True)

    # 3. Orphan directory in PAGES (mtime 2 hours ago)
    orphan_dir = os.path.join(config.PAGES, "orphan_test")
    os.makedirs(orphan_dir, exist_ok=True)
    old_time = now - 2 * 3600
    os.utime(orphan_dir, (old_time, old_time))

    # 4. Fresh orphan directory (mtime = now)
    fresh_dir = os.path.join(config.PAGES, "fresh_test")
    os.makedirs(fresh_dir, exist_ok=True)
    os.utime(fresh_dir, (now, now))

    # 5. Stale incoming directory of queued model (должен остаться)
    mid_queued = db.create_model(conn, invite[1]["id"], "Queued", "glb", now=now)
    incoming_queued = os.path.join(config.INCOMING, mid_queued)
    os.makedirs(incoming_queued, exist_ok=True)
    old_time_q = now - 2 * 86400
    os.utime(incoming_queued, (old_time_q, old_time_q))

    # 6. Stale incoming directory of unknown id (должен быть удалён)
    unknown_incoming = os.path.join(config.INCOMING, "unknown_id_123")
    os.makedirs(unknown_incoming, exist_ok=True)
    old_time_u = now - 2 * 86400
    os.utime(unknown_incoming, (old_time_u, old_time_u))

    # 7. Failed model older than 7 days
    mid_failed = db.create_model(conn, invite[1]["id"], "Failed", "glb", now=now - 10 * 86400)
    conn.execute(
        "UPDATE models SET status='failed', error='test' WHERE id=?",
        (mid_failed,)
    )
    pages_failed = os.path.join(config.PAGES, mid_failed)
    os.makedirs(pages_failed, exist_ok=True)
    incoming_failed = os.path.join(config.INCOMING, mid_failed)
    os.makedirs(incoming_failed, exist_ok=True)
    work_failed = os.path.join(config.WORK, mid_failed)
    os.makedirs(work_failed, exist_ok=True)

    # Запускаем cleanup
    counts = worker.cleanup_once(conn, now=now)

    # Проверяем результаты
    # Expired done model удалён
    assert db.get_model(conn, mid_expired) is None
    assert not os.path.exists(pages_dir)

    # Running expired model сохранён
    assert db.get_model(conn, mid_running) is not None
    assert os.path.exists(pages_dir_running)

    # Orphan directory удалён
    assert not os.path.exists(orphan_dir)

    # Fresh orphan directory сохранён
    assert os.path.exists(fresh_dir)

    # Stale incoming of queued model сохранён
    assert os.path.exists(incoming_queued)

    # Stale incoming of unknown id удалён
    assert not os.path.exists(unknown_incoming)

    # Failed model удалён
    assert db.get_model(conn, mid_failed) is None
    assert not os.path.exists(pages_failed)
    assert not os.path.exists(incoming_failed)
    assert not os.path.exists(work_failed)

    # Проверяем счётчики
    assert counts["expired"] >= 1
    assert counts["failed_rows"] >= 1
