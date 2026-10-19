"""Тесты API модуля hosting_app.main."""

import os
import shutil

import pytest
from fastapi.testclient import TestClient

from hosting_app import config, db, main


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def up(client, token, name, data, title=""):
    """Отправка файла на загрузку (тело запроса — сырые байты)."""
    return client.post(
        f"/api/u/{token}/upload",
        params={"name": name, "title": title},
        content=data,
    )


def stub_body():
    """Читает содержимое заглушки stub.html."""
    path = os.path.join(os.path.dirname(main.__file__), "templates", "stub.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def client(data_dir, monkeypatch):
    """Создаёт TestClient с новой базой данных."""
    monkeypatch.setattr(main, "CONN", db.init())
    return TestClient(main.app)


@pytest.fixture
def token(client):
    """Создаёт приглашение и возвращает токен."""
    return db.add_invite(main.CONN, "тест")


# ---------------------------------------------------------------------------
# 1. Базовые маршруты и заглушка
# ---------------------------------------------------------------------------

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_upload_page_valid_token(client, token):
    r = client.get(f"/u/{token}")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert '<input type="file"' in r.text


def test_stub_behavior(client, token):
    """Неизвестные адреса и отозванные токены возвращают идентичную заглушку."""
    expected = stub_body()

    # Неверный токен
    r1 = client.get("/u/wrong")
    assert r1.status_code == 200
    assert r1.text == expected

    # Корень
    r2 = client.get("/")
    assert r2.status_code == 200
    assert r2.text == expected

    # Произвольный путь
    r3 = client.get("/anything/else")
    assert r3.status_code == 200
    assert r3.text == expected

    # Отзыв приглашения
    inv = db.get_invite(main.CONN, token)
    db.revoke_invite(main.CONN, inv["id"])
    r4 = client.get(f"/u/{token}")
    assert r4.status_code == 200
    assert r4.text == expected

    # Заглушка не содержит форм, инпутов, ссылок
    assert "<form" not in expected
    assert "<input" not in expected
    assert "<a " not in expected
    assert "href" not in expected


def test_robots_txt(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text == "User-agent: *\nDisallow: /\n"


def test_x_robots_tag_header(client, token):
    """Все ответы содержат X-Robots-Tag с noindex и nofollow."""
    r1 = client.get(f"/u/{token}")
    assert "noindex" in r1.headers.get("x-robots-tag", "")
    assert "nofollow" in r1.headers.get("x-robots-tag", "")

    r2 = client.get(f"/api/u/{token}/models")
    assert "noindex" in r2.headers.get("x-robots-tag", "")
    assert "nofollow" in r2.headers.get("x-robots-tag", "")

    r3 = client.get("/")
    assert "noindex" in r3.headers.get("x-robots-tag", "")
    assert "nofollow" in r3.headers.get("x-robots-tag", "")


def test_api_wrong_token_404(client):
    r = client.get("/api/u/wrong/models")
    assert r.status_code == 404
    assert r.json() == {"error": "Не найдено"}


# ---------------------------------------------------------------------------
# 2. Успешная загрузка
# ---------------------------------------------------------------------------

def test_upload_happy_path(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 100
    r = up(client, token, "деталь 1.step", data, title="Мой корпус")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["kind"] == "step"
    assert body["title"] == "Мой корпус"

    model_id = body["id"]
    # Файл на диске
    fpath = os.path.join(config.INCOMING, model_id, "src.step")
    assert os.path.isfile(fpath)
    with open(fpath, "rb") as f:
        assert f.read() == data

    # Модель в списке
    r2 = client.get(f"/api/u/{token}/models")
    assert r2.status_code == 200
    models = r2.json()["models"]
    assert any(m["id"] == model_id for m in models)


# ---------------------------------------------------------------------------
# 3. Неподдерживаемое расширение
# ---------------------------------------------------------------------------

def test_wrong_extension(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 100
    r = up(client, token, "evil.exe", data)
    assert r.status_code == 400
    assert r.json()["error"] == "Неподдерживаемый тип файла. Допустимо: STEP, STP, PDF (3D), NWD, GLB"

    # Нет строк в БД
    assert db.count_active_models(main.CONN, db.get_invite(main.CONN, token)["id"]) == 0
    # INCOMING пуст
    if os.path.isdir(config.INCOMING):
        assert len(os.listdir(config.INCOMING)) == 0


# ---------------------------------------------------------------------------
# 4. Неправильное содержимое
# ---------------------------------------------------------------------------

def test_wrong_content(client, token):
    data = b"MZ\x90\x00" + b"\x00" * 100
    r = up(client, token, "a.step", data)
    assert r.status_code == 400
    assert r.json()["error"] == "Содержимое файла не соответствует типу"

    inv = db.get_invite(main.CONN, token)
    assert db.count_active_models(main.CONN, inv["id"]) == 0
    if os.path.isdir(config.INCOMING):
        assert len(os.listdir(config.INCOMING)) == 0


# ---------------------------------------------------------------------------
# 5. Превышение размера
# ---------------------------------------------------------------------------

def test_too_large(client, token, monkeypatch):
    monkeypatch.setitem(config.LIMIT_MB, "step", 1)
    data = b"ISO-10303-21;" + b"0" * 2_000_000
    r = up(client, token, "big.step", data)
    assert r.status_code == 413
    assert "МБ" in r.json()["error"]

    inv = db.get_invite(main.CONN, token)
    assert db.count_active_models(main.CONN, inv["id"]) == 0
    if os.path.isdir(config.INCOMING):
        assert len(os.listdir(config.INCOMING)) == 0


# ---------------------------------------------------------------------------
# 6. Квоты
# ---------------------------------------------------------------------------

def test_daily_quota(client, token, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_UPLOADS_PER_DAY", 1)
    data = b"ISO-10303-21;\n" + b"x" * 50

    r1 = up(client, token, "first.step", data)
    assert r1.status_code == 202

    r2 = up(client, token, "second.step", data)
    assert r2.status_code == 429
    assert r2.json()["error"] == "Достигнут суточный предел загрузок"

    inv = db.get_invite(main.CONN, token)
    assert db.count_active_models(main.CONN, inv["id"]) == 1


def test_model_count_quota(client, token, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_MODELS", 1)
    data = b"ISO-10303-21;\n" + b"x" * 50

    r1 = up(client, token, "first.step", data)
    assert r1.status_code == 202

    r2 = up(client, token, "second.step", data)
    assert r2.status_code == 429
    assert r2.json()["error"] == "Достигнут предел числа моделей — удалите ненужные"

    inv = db.get_invite(main.CONN, token)
    assert db.count_active_models(main.CONN, inv["id"]) == 1


# ---------------------------------------------------------------------------
# 7. Защита от переполнения диска
# ---------------------------------------------------------------------------

def test_disk_guard(client, token, monkeypatch):
    monkeypatch.setattr(main, "disk_used_pct", lambda: 99)
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data)
    assert r.status_code == 503
    assert r.json()["error"] == "Сервер временно не принимает файлы: нет места"

    inv = db.get_invite(main.CONN, token)
    assert db.count_active_models(main.CONN, inv["id"]) == 0
    if os.path.isdir(config.INCOMING):
        assert len(os.listdir(config.INCOMING)) == 0


# ---------------------------------------------------------------------------
# 8. Вредоносный заголовок
# ---------------------------------------------------------------------------

def test_hostile_title(client, token):
    hostile = "</script><img src=x onerror=alert(1)>" + "А" * 200
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data, title=hostile)
    assert r.status_code == 202
    stored_title = r.json()["title"]
    assert len(stored_title) <= 80
    # Нет управляющих символов
    for ch in stored_title:
        assert ord(ch) >= 32 or ch in "\n\t", f"Управляющий символ: {ord(ch)}"


def test_title_with_null_and_newline(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data, title="line1\nline2\x00end")
    assert r.status_code == 202
    stored_title = r.json()["title"]
    assert "\x00" not in stored_title
    assert "\n" not in stored_title


# ---------------------------------------------------------------------------
# 9. Владельчество
# ---------------------------------------------------------------------------

def test_ownership(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data)
    assert r.status_code == 202
    model_id = r.json()["id"]

    # Второе приглашение
    token2 = db.add_invite(main.CONN, "другой")

    # Попытка extend чужой модели
    r_ext = client.post(f"/api/u/{token2}/models/{model_id}/extend")
    assert r_ext.status_code == 404

    # Попытка DELETE чужой модели
    r_del = client.delete(f"/api/u/{token2}/models/{model_id}")
    assert r_del.status_code == 404

    # Владелец удаляет queued модель
    r_del2 = client.delete(f"/api/u/{token}/models/{model_id}")
    assert r_del2.status_code == 200
    assert r_del2.json() == {"ok": True}

    # Строка удалена
    assert db.get_model(main.CONN, model_id) is None
    # Каталог удалён
    assert not os.path.isdir(os.path.join(config.INCOMING, model_id))


def test_delete_running_model(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data)
    assert r.status_code == 202
    model_id = r.json()["id"]

    # Переводим в running
    claimed = db.claim_next(main.CONN)
    assert claimed is not None
    assert claimed["id"] == model_id

    r_del = client.delete(f"/api/u/{token}/models/{model_id}")
    assert r_del.status_code == 409
    assert r_del.json()["error"] == "Модель обрабатывается"


# ---------------------------------------------------------------------------
# 10. Продление
# ---------------------------------------------------------------------------

def test_extend_queued_409(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data)
    assert r.status_code == 202
    model_id = r.json()["id"]

    r_ext = client.post(f"/api/u/{token}/models/{model_id}/extend")
    assert r_ext.status_code == 409
    assert r_ext.json()["error"] == "Продление недоступно"


def test_extend_done(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data)
    assert r.status_code == 202
    model_id = r.json()["id"]

    # Завершаем модель
    db.finish_model(main.CONN, model_id, "done", size_glb=1, parts=1)

    old_row = db.get_model(main.CONN, model_id)
    old_expires = old_row["expires_at"]

    r_ext = client.post(f"/api/u/{token}/models/{model_id}/extend")
    assert r_ext.status_code == 200
    body = r_ext.json()
    assert body["ok"] is True
    new_expires = body["expires_at"]
    assert new_expires >= old_expires + config.RETENTION_DAYS * 86400


def test_extend_max_extensions(client, token):
    data = b"ISO-10303-21;\n" + b"x" * 50
    r = up(client, token, "test.step", data)
    assert r.status_code == 202
    model_id = r.json()["id"]

    db.finish_model(main.CONN, model_id, "done", size_glb=1, parts=1)

    # Продлеваем MAX_EXTENSIONS раз
    for _ in range(config.MAX_EXTENSIONS):
        r_ext = client.post(f"/api/u/{token}/models/{model_id}/extend")
        assert r_ext.status_code == 200

    # Следующее продление должно быть 409
    r_ext = client.post(f"/api/u/{token}/models/{model_id}/extend")
    assert r_ext.status_code == 409
    assert r_ext.json()["error"] == "Продление недоступно"


# ---------------------------------------------------------------------------
# 11. Отсутствие Content-Length
# ---------------------------------------------------------------------------

def test_no_content_length(client, token):
    # Чанкованная передача без Content-Length
    r = client.post(
        f"/api/u/{token}/upload",
        params={"name": "test.step", "title": ""},
        content=iter([b"ISO-10303-21;"]),
    )
    assert r.status_code == 411
    assert r.json()["error"] == "Нужен Content-Length"

    inv = db.get_invite(main.CONN, token)
    assert db.count_active_models(main.CONN, inv["id"]) == 0
    if os.path.isdir(config.INCOMING):
        assert len(os.listdir(config.INCOMING)) == 0


# ---------------------------------------------------------------------------
# 12. Заголовки ответов
# ---------------------------------------------------------------------------

def test_response_headers(client, token):
    r1 = client.get(f"/u/{token}")
    assert "no-store" in r1.headers.get("cache-control", "")
    assert r1.headers.get("referrer-policy") == "no-referrer"

    r2 = client.get(f"/api/u/{token}/models")
    assert "no-store" in r2.headers.get("cache-control", "")
    assert r2.headers.get("referrer-policy") == "no-referrer"
