"""Модуль работы с базой данных SQLite для приложения хостинга моделей.

Использует только стандартную библиотеку Python 3.12.
Все временные метки — целые секунды эпохи UTC.
"""

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

from . import config

# Допустимые статусы моделей
VALID_STATUSES = ("queued", "running", "done", "failed")
# Статусы, считающиеся активными
ACTIVE_STATUSES = ("queued", "running", "done")


def connect() -> sqlite3.Connection:
    """Создаёт подключение к базе данных.

    Настраивает WAL-режим, таймаут блокировки и внешние ключи.
    Создаёт родительскую директорию, если её нет.
    """
    db_path = Path(config.DB_PATH)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Инициализирует схему базы данных (идемпотентно).

    Создаёт таблицы и индексы, если они ещё не существуют.
    Возвращает подключение.
    """
    if conn is None:
        conn = connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            invite_id INTEGER NOT NULL REFERENCES invites(id),
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            extensions INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            size_glb INTEGER,
            parts INTEGER,
            error TEXT
        )
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_models_status ON models(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_models_invite_id ON models(invite_id)")

    return conn


def hash_token(token: str) -> str:
    """Возвращает SHA-256 хеш токена в hex-формате."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """Генерирует новый безопасный токен."""
    return secrets.token_urlsafe(16)


def add_invite(conn: sqlite3.Connection, label: str, now: int | None = None) -> str:
    """Создаёт новое приглашение и возвращает токен.

    Токен хранится только в виде хеша.
    """
    if now is None:
        now = int(time.time())

    token = new_token()
    token_hash = hash_token(token)

    conn.execute(
        "INSERT INTO invites (token_hash, label, created_at, revoked) VALUES (?, ?, ?, 0)",
        (token_hash, label, now),
    )
    return token


def get_invite(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """Возвращает приглашение по токену или None.

    Возвращает None, если токен пуст, не найден или приглашение отозвано.
    """
    if not token:
        return None

    token_hash = hash_token(token)
    row = conn.execute(
        "SELECT * FROM invites WHERE token_hash = ? AND revoked = 0",
        (token_hash,),
    ).fetchone()
    return row


def list_invites(conn: sqlite3.Connection) -> list[dict]:
    """Возвращает список всех приглашений с количеством моделей.

    Сортировка по id.
    """
    rows = conn.execute(
        """
        SELECT i.id, i.label, i.created_at, i.revoked,
               (SELECT COUNT(*) FROM models m WHERE m.invite_id = i.id) AS models
        FROM invites i
        ORDER BY i.id
        """
    ).fetchall()

    return [
        {
            "id": row["id"],
            "label": row["label"],
            "created_at": row["created_at"],
            "revoked": row["revoked"],
            "models": row["models"],
        }
        for row in rows
    ]


def revoke_invite(
    conn: sqlite3.Connection, invite_id: int, purge: bool = False
) -> list[str]:
    """Озывает приглашение.

    Если purge=True, удаляет все модели этого приглашения и возвращает их id.
    Иначе возвращает пустой список.
    Выбрасывает KeyError, если приглашение не существует.
    """
    invite = conn.execute("SELECT id FROM invites WHERE id = ?", (invite_id,)).fetchone()
    if invite is None:
        raise KeyError(f"Приглашение с id {invite_id} не найдено")

    conn.execute("UPDATE invites SET revoked = 1 WHERE id = ?", (invite_id,))

    deleted_ids: list[str] = []
    if purge:
        rows = conn.execute(
            "SELECT id FROM models WHERE invite_id = ?", (invite_id,)
        ).fetchall()
        deleted_ids = [row["id"] for row in rows]
        if deleted_ids:
            placeholders = ",".join("?" * len(deleted_ids))
            conn.execute(
                f"DELETE FROM models WHERE id IN ({placeholders})", deleted_ids
            )

    return deleted_ids


def create_model(
    conn: sqlite3.Connection,
    invite_id: int,
    title: str,
    kind: str,
    now: int | None = None,
    status: str = "queued",
) -> str:
    """Создаёт новую модель (по умолчанию в статусе queued).

    Статус `uploading` выставляет загрузчик на время записи файла: воркер такие модели не берёт.
    Возвращает id модели.
    """
    if now is None:
        now = int(time.time())

    model_id = secrets.token_urlsafe(16)
    expires_at = now + config.RETENTION_DAYS * 86400

    conn.execute(
        """
        INSERT INTO models (id, invite_id, title, kind, status, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (model_id, invite_id, title, kind, status, now, expires_at),
    )
    return model_id


def mark_queued(conn: sqlite3.Connection, model_id: str) -> None:
    """Переводит модель из uploading в queued (файл полностью записан и проверен)."""
    conn.execute("UPDATE models SET status = 'queued' WHERE id = ? AND status = 'uploading'", (model_id,))


def all_model_ids(conn: sqlite3.Connection) -> set[str]:
    """Возвращает множество id всех моделей."""
    return {r["id"] for r in conn.execute("SELECT id FROM models").fetchall()}


def active_model_ids(conn: sqlite3.Connection) -> set[str]:
    """Возвращает множество id моделей в статусах uploading, queued, running (их каталоги трогать нельзя)."""
    rows = conn.execute("SELECT id FROM models WHERE status IN ('uploading', 'queued', 'running')").fetchall()
    return {r["id"] for r in rows}


def stale_uploading(conn: sqlite3.Connection, hours: int = 1, now: int | None = None) -> list[sqlite3.Row]:
    """Возвращает модели, застрявшие в uploading дольше hours часов (оборванные загрузки)."""
    if now is None:
        now = int(time.time())
    return conn.execute(
        "SELECT * FROM models WHERE status = 'uploading' AND created_at <= ?", (now - hours * 3600,)
    ).fetchall()


def get_model(conn: sqlite3.Connection, model_id: str) -> sqlite3.Row | None:
    """Возвращает модель по id или None."""
    return conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()


def list_models(conn: sqlite3.Connection, invite_id: int) -> list[sqlite3.Row]:
    """Возвращает список моделей приглашения, новые сверху."""
    return conn.execute(
        "SELECT * FROM models WHERE invite_id = ? ORDER BY created_at DESC, id DESC",
        (invite_id,),
    ).fetchall()


def count_active_models(
    conn: sqlite3.Connection, invite_id: int, now: int | None = None
) -> int:
    """Возвращает количество активных моделей приглашения.

    Активные — это модели со статусом queued/running/done и expires_at > now.
    """
    if now is None:
        now = int(time.time())

    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM models
        WHERE invite_id = ?
          AND status IN ('uploading', 'queued', 'running', 'done')
          AND expires_at > ?
        """,
        (invite_id, now),
    ).fetchone()
    return row["cnt"]


def count_uploads_since(
    conn: sqlite3.Connection,
    invite_id: int,
    seconds: int = 86400,
    now: int | None = None,
) -> int:
    """Возвращает количество моделей, созданных за последние seconds секунд."""
    if now is None:
        now = int(time.time())

    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM models
        WHERE invite_id = ?
          AND created_at > ?
        """,
        (invite_id, now - seconds),
    ).fetchone()
    return row["cnt"]


def claim_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Атомарно забирает самую старую модель в статусе queued.

    Устанавливает status='running', attempts=attempts+1.
    Возвращает обновлённую строку или None.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM models WHERE status = 'queued' ORDER BY created_at ASC, id ASC LIMIT 1"
        ).fetchone()

        if row is None:
            conn.execute("COMMIT")
            return None

        model_id = row["id"]
        new_attempts = row["attempts"] + 1

        conn.execute(
            "UPDATE models SET status = 'running', attempts = ? WHERE id = ?",
            (new_attempts, model_id),
        )

        updated = conn.execute(
            "SELECT * FROM models WHERE id = ?", (model_id,)
        ).fetchone()

        conn.execute("COMMIT")
        return updated
    except Exception:
        conn.execute("ROLLBACK")
        raise


def finish_model(
    conn: sqlite3.Connection,
    model_id: str,
    status: str,
    size_glb: int | None = None,
    parts: int | None = None,
    error: str | None = None,
) -> None:
    """Завершает обработку модели.

    Статус должен быть 'done' или 'failed', иначе ValueError.
    """
    if status not in ("done", "failed"):
        raise ValueError(f"Недопустимый статус завершения: {status}")

    conn.execute(
        """
        UPDATE models
        SET status = ?, size_glb = ?, parts = ?, error = ?
        WHERE id = ?
        """,
        (status, size_glb, parts, error, model_id),
    )


def reset_running(conn: sqlite3.Connection) -> int:
    """Сбрасывает модели в статусе running.

    Если attempts >= 2, переводит в failed с ошибкой.
    Иначе возвращает в queued.
    Возвращает количество обработанных моделей.
    """
    rows = conn.execute("SELECT * FROM models WHERE status = 'running'").fetchall()
    count = 0

    for row in rows:
        if row["attempts"] >= 2:
            conn.execute(
                "UPDATE models SET status = 'failed', error = ? WHERE id = ?",
                ("Сервис перезапущен во время обработки", row["id"]),
            )
        else:
            conn.execute(
                "UPDATE models SET status = 'queued' WHERE id = ?",
                (row["id"],),
            )
        count += 1

    return count


def extend_model(
    conn: sqlite3.Connection,
    model_id: str,
    invite_id: int,
    now: int | None = None,
) -> bool:
    """Продлевает срок жизни модели.

    Возвращает True, если продление выполнено, иначе False.
    Условия: модель принадлежит приглашению, статус done, extensions < MAX_EXTENSIONS.
    """
    if now is None:
        now = int(time.time())

    row = conn.execute(
        "SELECT * FROM models WHERE id = ? AND invite_id = ?",
        (model_id, invite_id),
    ).fetchone()

    if row is None:
        return False
    if row["status"] != "done":
        return False
    if row["extensions"] >= config.MAX_EXTENSIONS:
        return False

    new_expires = max(row["expires_at"], now) + config.RETENTION_DAYS * 86400
    new_extensions = row["extensions"] + 1

    conn.execute(
        "UPDATE models SET expires_at = ?, extensions = ? WHERE id = ?",
        (new_expires, new_extensions, model_id),
    )
    return True


def delete_model(conn: sqlite3.Connection, model_id: str) -> None:
    """Удаляет модель по id."""
    conn.execute("DELETE FROM models WHERE id = ?", (model_id,))


def expired_models(conn: sqlite3.Connection, now: int | None = None) -> list[sqlite3.Row]:
    """Возвращает список просроченных моделей (не в статусе running)."""
    if now is None:
        now = int(time.time())

    return conn.execute(
        "SELECT * FROM models WHERE expires_at <= ? AND status != 'running'",
        (now,),
    ).fetchall()


def stale_failed(
    conn: sqlite3.Connection, days: int = 7, now: int | None = None
) -> list[sqlite3.Row]:
    """Возвращает список неудачных моделей старше days дней."""
    if now is None:
        now = int(time.time())

    threshold = now - days * 86400
    return conn.execute(
        "SELECT * FROM models WHERE status = 'failed' AND created_at <= ?",
        (threshold,),
    ).fetchall()


def stats(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """Возвращает статистику по базе данных."""
    if now is None:
        now = int(time.time())

    invites_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM invites WHERE revoked = 0"
    ).fetchone()["cnt"]

    models_total = conn.execute("SELECT COUNT(*) AS cnt FROM models").fetchone()["cnt"]

    status_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM models GROUP BY status"
    ).fetchall()
    by_status = {row["status"]: row["cnt"] for row in status_rows}

    active_count = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM models
        WHERE status IN ('queued', 'running', 'done')
          AND expires_at > ?
        """,
        (now,),
    ).fetchone()["cnt"]

    return {
        "invites": invites_count,
        "models_total": models_total,
        "by_status": by_status,
        "active": active_count,
    }
