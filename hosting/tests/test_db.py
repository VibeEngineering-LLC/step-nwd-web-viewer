import hashlib
import os
import sqlite3

import pytest

from hosting_app import config, db


def test_new_token_and_hash_token():
    """Проверяем длину и уникальность токенов, а также корректность хэширования."""
    t1 = db.new_token()
    t2 = db.new_token()
    assert isinstance(t1, str)
    assert len(t1) >= 22
    assert t1 != t2

    h = db.hash_token("a")
    assert h == hashlib.sha256(b"a").hexdigest()
    assert len(h) == 64


def test_add_invite_no_plaintext(conn, invite):
    """Токен не хранится в открытом виде; поиск по токену работает, по неверному — нет."""
    token, row = invite
    cur = conn.execute("SELECT * FROM invites")
    rows = cur.fetchall()
    for r in rows:
        s = str(dict(r))
        assert token not in s

    found = db.get_invite(conn, token)
    assert found is not None
    assert found["id"] == row["id"]

    assert db.get_invite(conn, "wrong") is None
    assert db.get_invite(conn, "") is None
    assert db.get_invite(conn, None) is None


def test_revoke_invite(conn, invite):
    """После отозвания токен не работает; несуществующий id вызывает KeyError."""
    token, row = invite
    assert db.get_invite(conn, token) is not None

    db.revoke_invite(conn, row["id"])
    assert db.get_invite(conn, token) is None

    with pytest.raises(KeyError):
        db.revoke_invite(conn, 9999)


def test_revoke_invite_purge(conn, invite):
    """При purge=True удаляются все модели приглашения и возвращаются их id."""
    token, row = invite
    m1 = db.create_model(conn, row["id"], "m1", "kind", now=1000)
    m2 = db.create_model(conn, row["id"], "m2", "kind", now=1001)

    deleted = db.revoke_invite(conn, row["id"], purge=True)
    assert set(deleted) == {m1, m2}

    assert db.get_model(conn, m1) is None
    assert db.get_model(conn, m2) is None


def test_create_model_fields(conn, invite):
    """create_model возвращает id нужной длины, статус queued и корректный expires_at."""
    token, row = invite
    now = 1_000_000
    mid = db.create_model(conn, row["id"], "title", "kind", now=now)
    assert isinstance(mid, str)
    assert len(mid) >= 22

    m = db.get_model(conn, mid)
    assert m is not None
    assert m["status"] == "queued"
    assert m["expires_at"] == now + config.RETENTION_DAYS * 86400


def test_count_active_models(conn, invite):
    """Активные модели — queued/running/done с неистёкшим сроком; failed не считается."""
    token, row = invite
    now = 1000
    db.create_model(conn, row["id"], "a", "k", now=now)
    db.create_model(conn, row["id"], "b", "k", now=now)
    db.create_model(conn, row["id"], "c", "k", now=now)

    # одна done, одна failed, одна queued
    m = db.get_model(conn, db.create_model(conn, row["id"], "d", "k", now=now))
    db.finish_model(conn, m["id"], "done")
    m = db.get_model(conn, db.create_model(conn, row["id"], "e", "k", now=now))
    db.finish_model(conn, m["id"], "failed")

    # у нас: a(queued), b(queued), c(queued), d(done), e(failed)
    # active = queued + done = 4? но в задаче: одна done, одна failed, одна queued -> count 2
    # Пересоздадим чистый сценарий:
    # Удалим лишние и создадим ровно: done, failed, queued
    for mid in [x["id"] for x in db.list_models(conn, row["id"])]:
        db.delete_model(conn, mid)

    m_done = db.create_model(conn, row["id"], "done", "k", now=now)
    db.finish_model(conn, m_done, "done")
    m_failed = db.create_model(conn, row["id"], "failed", "k", now=now)
    db.finish_model(conn, m_failed, "failed")
    m_queued = db.create_model(conn, row["id"], "queued", "k", now=now)

    assert db.count_active_models(conn, row["id"], now=now) == 2

    # после истечения всех сроков — 0
    assert db.count_active_models(conn, row["id"], now=now + 31 * 86400) == 0


def test_count_uploads_since(conn, invite):
    """Считаются модели, созданные в окне seconds, независимо от статуса."""
    token, row = invite
    t = 1000
    m1 = db.create_model(conn, row["id"], "old", "k", now=t)
    m2 = db.create_model(conn, row["id"], "new", "k", now=t + 90000)
    # сделаем одну failed, чтобы проверить, что failed тоже считается
    db.finish_model(conn, m1, "failed")

    now = t + 90000 + 10
    # окно 86400: m1 создан в t, now - t = 90010 > 86400 -> не входит
    # m2 создан в t+90000, now - (t+90000) = 10 <= 86400 -> входит
    assert db.count_uploads_since(conn, row["id"], now=now) == 1


def test_claim_next(conn, invite):
    """claim_next берёт самую старую queued, переводит в running, attempts=1; дальше None."""
    token, row = invite
    m1 = db.create_model(conn, row["id"], "first", "k", now=1000)
    m2 = db.create_model(conn, row["id"], "second", "k", now=2000)

    r1 = db.claim_next(conn)
    assert r1 is not None
    assert r1["id"] == m1
    assert r1["status"] == "running"
    assert r1["attempts"] == 1

    r2 = db.claim_next(conn)
    assert r2 is not None
    assert r2["id"] == m2
    assert r2["status"] == "running"
    assert r2["attempts"] == 1

    assert db.claim_next(conn) is None


def test_reset_running(conn, invite):
    """Модели с attempts<2 возвращаются в queued, с attempts>=2 — failed с ошибкой."""
    token, row = invite
    m1 = db.create_model(conn, row["id"], "a", "k", now=1000)
    m2 = db.create_model(conn, row["id"], "b", "k", now=1001)

    # переводим в running
    db.claim_next(conn)  # m1 -> running, attempts=1
    db.claim_next(conn)  # m2 -> running, attempts=1

    # m2: ещё раз claim? нет, уже running. Увеличим attempts вручную через finish? 
    # Лучше: создадим третью и сделаем attempts=2
    m3 = db.create_model(conn, row["id"], "c", "k", now=1002)
    db.claim_next(conn)  # m3 -> running, attempts=1
    # имитируем вторую попытку: обновим attempts
    conn.execute("UPDATE models SET attempts = 2 WHERE id = ?", (m3,))

    count = db.reset_running(conn)
    assert count == 3  # m1, m2, m3

    r1 = db.get_model(conn, m1)
    assert r1["status"] == "queued"

    r2 = db.get_model(conn, m2)
    assert r2["status"] == "queued"

    r3 = db.get_model(conn, m3)
    assert r3["status"] == "failed"
    assert r3["error"] is not None and len(r3["error"]) > 0


def test_extend_model(conn, invite):
    """extend_model увеличивает срок для done-моделей до MAX_EXTENSIONS раз, дальше False."""
    token, row = invite
    now = 1000
    mid = db.create_model(conn, row["id"], "ext", "k", now=now)
    db.finish_model(conn, mid, "done")

    base = now + config.RETENTION_DAYS * 86400

    # 1-е расширение
    assert db.extend_model(conn, mid, row["id"], now=now) is True
    m = db.get_model(conn, mid)
    assert m["extensions"] == 1
    assert m["expires_at"] == max(base, now) + config.RETENTION_DAYS * 86400

    # 2-е расширение
    assert db.extend_model(conn, mid, row["id"], now=now) is True
    m = db.get_model(conn, mid)
    assert m["extensions"] == 2

    # 3-е расширение
    assert db.extend_model(conn, mid, row["id"], now=now) is True
    m = db.get_model(conn, mid)
    assert m["extensions"] == 3

    # 4-е — False (MAX_EXTENSIONS = 3)
    assert db.extend_model(conn, mid, row["id"], now=now) is False

    # чужой invite
    token2, row2 = db.add_invite(conn, "other"), None
    # add_invite возвращает токен, получим row
    row2 = db.get_invite(conn, token2)
    assert db.extend_model(conn, mid, row2["id"], now=now) is False

    # queued модель
    mq = db.create_model(conn, row["id"], "q", "k", now=now)
    assert db.extend_model(conn, mq, row["id"], now=now) is False

    # неизвестный id
    assert db.extend_model(conn, "nonexistent", row["id"], now=now) is False


def test_expired_and_stale_failed(conn, invite):
    """expired_models — не running с истёкшим сроком; stale_failed — failed старше N дней."""
    token, row = invite
    now = 1000
    retention = config.RETENTION_DAYS * 86400

    # done, истёкший
    m_done = db.create_model(conn, row["id"], "d", "k", now=now)
    db.finish_model(conn, m_done, "done")
    # expires_at = now + retention; сделаем now2 > expires_at
    now2 = now + retention + 1

    # failed, истёкший
    m_failed = db.create_model(conn, row["id"], "f", "k", now=now)
    db.finish_model(conn, m_failed, "failed")

    # running, истёкший — не должен попадать
    m_run = db.create_model(conn, row["id"], "r", "k", now=now)
    db.claim_next(conn)  # -> running

    expired = db.expired_models(conn, now=now2)
    ids = {r["id"] for r in expired}
    assert m_done in ids
    assert m_failed in ids
    assert m_run not in ids

    # stale_failed: failed создан в now, now2 - now = retention+1
    # если retention >= 7*86400, то stale; иначе нет. Проверим с явным днём.
    # Создадим failed, которому точно > 7 дней
    old_failed = db.create_model(conn, row["id"], "oldf", "k", now=now)
    db.finish_model(conn, old_failed, "failed")
    now3 = now + 8 * 86400
    stale = db.stale_failed(conn, 7, now=now3)
    stale_ids = {r["id"] for r in stale}
    assert old_failed in stale_ids
    # m_failed создан в now, now3 - now = 8*86400 > 7*86400 -> тоже stale
    assert m_failed in stale_ids


def test_finish_model_invalid_and_stats(conn, invite):
    """finish_model с недопустимым статусом — ValueError; stats корректен."""
    token, row = invite
    mid = db.create_model(conn, row["id"], "s", "k", now=1000)

    with pytest.raises(ValueError):
        db.finish_model(conn, mid, "queued")

    # подготовим набор: 1 done, 1 failed, 1 queued
    m1 = db.create_model(conn, row["id"], "a", "k", now=1000)
    db.finish_model(conn, m1, "done")
    m2 = db.create_model(conn, row["id"], "b", "k", now=1000)
    db.finish_model(conn, m2, "failed")
    m3 = db.create_model(conn, row["id"], "c", "k", now=1000)

    s = db.stats(conn, now=1000)
    assert s["models_total"] == 4  # mid + m1 + m2 + m3
    assert s["by_status"]["done"] == 1
    assert s["by_status"]["failed"] == 1
    assert s["by_status"]["queued"] == 2


def test_claim_next_concurrency(data_dir):
    """Два соединения не должны вернуть одну и ту же модель дважды."""
    c1 = db.init()
    c2 = db.connect()
    try:
        token = db.add_invite(c1, "conc")
        row = db.get_invite(c1, token)
        for i in range(5):
            db.create_model(c1, row["id"], f"m{i}", "k", now=1000 + i)

        claimed = []
        for _ in range(5):
            r1 = db.claim_next(c1)
            if r1 is not None:
                claimed.append(r1["id"])
            r2 = db.claim_next(c2)
            if r2 is not None:
                claimed.append(r2["id"])

        assert len(claimed) == 5
        assert len(set(claimed)) == 5
    finally:
        c1.close()
        c2.close()
