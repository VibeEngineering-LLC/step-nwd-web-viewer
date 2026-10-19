import json
import os
import re

import pytest

from hosting_app import cli, config, db


def add_inv(conn, label, now=None):
    """Создаёт приглашение; db.add_invite возвращает только токен -> (токен, строка)."""
    token = db.add_invite(conn, label, now=now)
    return token, db.get_invite(conn, token)


def make_dirs(model_id):
    paths = [
        os.path.join(config.PAGES, model_id),
        os.path.join(config.INCOMING, model_id),
        os.path.join(config.WORK, model_id),
    ]
    for p in paths:
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "f.txt"), "w", encoding="utf-8") as f:
            f.write("data")


def dirs_exist(model_id):
    paths = [
        os.path.join(config.PAGES, model_id),
        os.path.join(config.INCOMING, model_id),
        os.path.join(config.WORK, model_id),
    ]
    return [os.path.isdir(p) for p in paths]


def test_invite_list_shows_label_date_and_model_count(conn, capsys):
    # Создаем приглашение с фиксированной датой
    token, row = add_inv(conn, "ссылка-А", now=1700000000)
    invite_id = row["id"]
    
    # Создаем две модели для этого приглашения
    db.create_model(conn, invite_id, "Модель 1", "kind1")
    db.create_model(conn, invite_id, "Модель 2", "kind2")
    
    # Запускаем CLI
    rc = cli.main(["invite", "list"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем наличие метки и даты
    assert "ссылка-А" in out
    assert "2023-11-14" in out
    assert "активна" in out
    
    # Проверяем количество моделей через регулярное выражение
    # Ищем строку с меткой и проверяем, что последний токен равен 2
    lines = out.splitlines()
    target_line = None
    for line in lines:
        if "ссылка-А" in line:
            target_line = line
            break
    
    assert target_line is not None, "Строка с меткой не найдена"
    
    # Разбиваем строку на токены и проверяем последний
    tokens = target_line.split()
    assert tokens[-1] == "2", f"Ожидалось 2 модели, получено: {tokens[-1]}"


def test_invite_list_marks_revoked(conn, capsys):
    # Создаем приглашение
    token, row = add_inv(conn, "отозванная-метка", now=1700000000)
    invite_id = row["id"]
    
    # Озываем приглашение
    db.revoke_invite(conn, invite_id)
    
    # Запускаем CLI
    rc = cli.main(["invite", "list"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем статус
    assert "отозвана" in out
    assert "активна" not in out


def test_invite_list_empty(conn, capsys):
    # БД пуста, приглашений нет
    rc = cli.main(["invite", "list"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем сообщение об отсутствии приглашений
    assert "Приглашений нет." in out


def test_invite_revoke_without_purge_keeps_models_and_dirs(conn, capsys):
    # Создаем приглашение
    token, row = add_inv(conn, "метка-для-отзыва", now=1700000000)
    invite_id = row["id"]
    
    # Создаем модель
    model_id = db.create_model(conn, invite_id, "Модель", "kind")
    
    # Создаем директории
    make_dirs(model_id)
    
    # Запускаем отзыв без очистки
    rc = cli.main(["invite", "revoke", str(invite_id)])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем, что приглашение удалено
    assert db.get_invite(conn, token) is None
    
    # Проверяем, что модель осталась
    model_row = db.get_model(conn, model_id)
    assert model_row is not None
    
    # Проверяем, что директории остались
    exists = dirs_exist(model_id)
    assert all(exists), f"Не все директории существуют: {exists}"


def test_invite_revoke_purge_removes_models_and_dirs(conn, capsys):
    # Создаем первое приглашение
    token1, row1 = add_inv(conn, "первое", now=1700000000)
    invite_id1 = row1["id"]
    
    # Создаем две модели для первого приглашения
    model_id1 = db.create_model(conn, invite_id1, "М1", "kind")
    model_id2 = db.create_model(conn, invite_id1, "М2", "kind")
    
    # Создаем директории для обеих моделей
    make_dirs(model_id1)
    make_dirs(model_id2)
    
    # Создаем второе приглашение (должно остаться)
    token2, row2 = add_inv(conn, "второе", now=1700000000)
    invite_id2 = row2["id"]
    
    # Создаем модель для второго приглашения
    model_id3 = db.create_model(conn, invite_id2, "М3", "kind")
    make_dirs(model_id3)
    
    # Запускаем отзыв с очисткой
    rc = cli.main(["invite", "revoke", str(invite_id1), "--purge"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем сообщение об удалении моделей
    assert "Удалено моделей: 2" in out
    
    # Проверяем, что модели первого приглашения удалены
    assert db.get_model(conn, model_id1) is None
    assert db.get_model(conn, model_id2) is None
    
    # Проверяем, что директории первого приглашения удалены
    exists1 = dirs_exist(model_id1)
    exists2 = dirs_exist(model_id2)
    assert not any(exists1), f"Директории модели 1 не удалены: {exists1}"
    assert not any(exists2), f"Директории модели 2 не удалены: {exists2}"
    
    # Проверяем, что модель второго приглашения осталась
    assert db.get_model(conn, model_id3) is not None
    
    # Проверяем, что директории второго приглашения остались
    exists3 = dirs_exist(model_id3)
    assert all(exists3), f"Директории модели 3 не существуют: {exists3}"


def test_invite_revoke_unknown_id(conn, capsys):
    # Пытаемся отозвать несуществующее приглашение
    rc = cli.main(["invite", "revoke", "999"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 2
    
    # Проверяем stderr
    assert "999" in err
    
    # Проверяем, что stdout пуст
    assert out == ""


def test_models_list_all_and_filtered(conn, capsys):
    # Создаем первое приглашение
    token1, row1 = add_inv(conn, "первое", now=1700000000)
    invite_id1 = row1["id"]
    
    # Создаем две модели для первого приглашения
    model_id1 = db.create_model(conn, invite_id1, "М1", "kind")
    model_id2 = db.create_model(conn, invite_id1, "М2", "kind")
    
    # Завершаем одну модель с размером
    db.finish_model(conn, model_id1, "done", size_glb=5 * 1024 * 1024, parts=1)
    
    # Создаем второе приглашение
    token2, row2 = add_inv(conn, "второе", now=1700000000)
    invite_id2 = row2["id"]
    
    # Создаем модель для второго приглашения
    model_id3 = db.create_model(conn, invite_id2, "М3", "kind")
    
    # Запускаем список всех моделей
    rc = cli.main(["models", "list"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем наличие всех заголовков
    assert "М1" in out
    assert "М2" in out
    assert "М3" in out
    
    # Проверяем размер завершённой модели
    assert "5.0" in out
    
    # Проверяем, что нет сообщения об отсутствии моделей
    assert "Модели не найдены." not in out
    
    # Запускаем список моделей для первого приглашения
    rc = cli.main(["models", "list", "--invite", str(invite_id1)])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем наличие моделей первого приглашения
    assert "М1" in out
    assert "М2" in out
    
    # Проверяем отсутствие модели второго приглашения
    assert "М3" not in out


def test_models_list_handles_null_size_and_empty(conn, capsys):
    # Создаем приглашение
    token, row = add_inv(conn, "метка", now=1700000000)
    invite_id = row["id"]
    
    # Создаем модель без завершения (size_glb = NULL)
    model_id = db.create_model(conn, invite_id, "Модель-NULL", "kind")
    
    # Запускаем список моделей
    rc = cli.main(["models", "list"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем наличие модели
    assert "Модель-NULL" in out
    
    # Проверяем, что нет traceback
    assert "Traceback" not in err
    
    # Теперь проверяем пустой список
    # Удаляем все модели
    db.delete_model(conn, model_id)
    
    rc = cli.main(["models", "list"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем сообщение об отсутствии моделей
    assert "Модели не найдены." in out


def test_models_delete_removes_row_and_dirs(conn, capsys):
    # Создаем приглашение
    token, row = add_inv(conn, "метка", now=1700000000)
    invite_id = row["id"]
    
    # Создаем модель и завершаем её
    model_id = db.create_model(conn, invite_id, "Модель", "kind")
    db.finish_model(conn, model_id, "done", size_glb=1024, parts=1)
    
    # Создаем директории
    make_dirs(model_id)
    
    # Создаем вторую модель (должна остаться)
    model_id2 = db.create_model(conn, invite_id, "Модель2", "kind")
    make_dirs(model_id2)
    
    # Запускаем удаление
    rc = cli.main(["models", "delete", model_id])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем, что модель удалена
    assert db.get_model(conn, model_id) is None
    
    # Проверяем, что директории удалены
    exists = dirs_exist(model_id)
    assert not any(exists), f"Директории не удалены: {exists}"
    
    # Проверяем, что ID модели в stdout
    assert model_id in out
    
    # Проверяем, что вторая модель осталась
    assert db.get_model(conn, model_id2) is not None
    
    # Проверяем, что директории второй модели остались
    exists2 = dirs_exist(model_id2)
    assert all(exists2), f"Директории модели 2 не существуют: {exists2}"


def test_models_delete_refuses_running(conn, capsys):
    # Создаем приглашение
    token, row = add_inv(conn, "метка", now=1700000000)
    invite_id = row["id"]
    
    # Создаем модель
    model_id = db.create_model(conn, invite_id, "Модель", "kind")
    
    # Создаем директории
    make_dirs(model_id)
    
    # Переводим модель в статус running
    db.claim_next(conn)
    
    # Запускаем удаление
    rc = cli.main(["models", "delete", model_id])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 2
    
    # Проверяем stderr
    assert "running" in err
    
    # Проверяем, что модель осталась
    assert db.get_model(conn, model_id) is not None
    
    # Проверяем, что директории остались
    exists = dirs_exist(model_id)
    assert all(exists), f"Директории не существуют: {exists}"


def test_models_delete_unknown_id(conn, capsys):
    # Пытаемся удалить несуществующую модель
    rc = cli.main(["models", "delete", "unknown-id-123"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 2
    
    # Проверяем stderr
    assert "unknown-id-123" in err


def test_stats_counts(conn, capsys):
    # Создаем два активных приглашения
    token1, row1 = add_inv(conn, "активное1", now=1700000000)
    invite_id1 = row1["id"]
    
    token2, row2 = add_inv(conn, "активное2", now=1700000000)
    invite_id2 = row2["id"]
    
    # Создаем одно отозванное приглашение
    token3, row3 = add_inv(conn, "отозванное", now=1700000000)
    invite_id3 = row3["id"]
    db.revoke_invite(conn, invite_id3)
    
    # Создаем модели: 2 в очереди, 1 завершённая, 1 неудачная
    model_id1 = db.create_model(conn, invite_id1, "М1", "kind")
    model_id2 = db.create_model(conn, invite_id1, "М2", "kind")
    model_id3 = db.create_model(conn, invite_id2, "М3", "kind")
    model_id4 = db.create_model(conn, invite_id2, "М4", "kind")
    
    # Завершаем одну модель
    db.finish_model(conn, model_id3, "done", size_glb=1024, parts=1)
    
    # Отмечаем одну как неудачную
    db.finish_model(conn, model_id4, "failed", error="x")
    
    # Создаем файл в каталоге страниц для проверки размера
    pages_dir = os.path.join(config.PAGES, "x")
    os.makedirs(pages_dir, exist_ok=True)
    with open(os.path.join(pages_dir, "f.bin"), "wb") as f:
        f.write(b"\x00" * (1024 * 1024))  # 1 MiB
    
    # Запускаем статистику
    rc = cli.main(["stats"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем количество активных приглашений
    assert "Активных приглашений: 2" in out
    
    # Проверяем количество активных моделей (queued + running + done)
    assert "Активных моделей: 3" in out
    
    # Проверяем количество в очереди
    assert "В очереди: 2" in out
    
    # Проверяем размер каталога страниц
    assert "Размер каталога страниц: 1.0 МБ" in out


def test_invite_export_has_iso_date_and_no_secrets(conn, capsys):
    # Создаем приглашение
    token, row = add_inv(conn, "эксп", now=1700000000)
    invite_id = row["id"]
    
    # Запускаем экспорт
    rc = cli.main(["invite", "export"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Парсим JSON
    data = json.loads(out)
    
    # Проверяем структуру
    assert isinstance(data, list)
    assert len(data) == 1
    
    item = data[0]
    assert isinstance(item, dict)
    
    # Проверяем ключи
    assert set(item.keys()) == {"id", "label", "created_at", "revoked"}
    
    # Проверяем значения
    assert item["label"] == "эксп"
    assert item["revoked"] is False
    assert item["created_at"].startswith("2023-11-14")
    
    # Проверяем, что токен и его хеш не присутствуют в stdout
    assert token not in out
    hashed = db.hash_token(token)
    assert hashed not in out


def test_invite_add_prints_link_once(conn, capsys, monkeypatch):
    # Монкипатчим PUBLIC_URL
    monkeypatch.setattr(config, "PUBLIC_URL", "https://example.test")
    
    # Запускаем добавление приглашения
    rc = cli.main(["invite", "add", "метка"])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 0
    
    # Проверяем первую строку
    lines = out.splitlines()
    assert len(lines) >= 1
    
    first_line = lines[0]
    match = re.match(r"^Ссылка: https://example\.test/u/([A-Za-z0-9_-]+)$", first_line)
    assert match is not None, f"Первая строка не соответствует формату: {first_line}"
    
    # Извлекаем токен
    token = match.group(1)
    
    # Проверяем, что приглашение создано
    invite_row = db.get_invite(conn, token)
    assert invite_row is not None
    
    # Проверяем, что добавление с пустой меткой возвращает ошибку
    rc = cli.main(["invite", "add", ""])
    out, err = capsys.readouterr()
    
    # Проверяем код возврата
    assert rc == 2
