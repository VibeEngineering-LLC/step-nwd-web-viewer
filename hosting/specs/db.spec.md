Write a complete Python 3.12 module `db.py` (stdlib only: sqlite3, hashlib, secrets, time; no markdown fences, no explanations, output ONLY code). Docstring and comments in Russian. The module is imported as `from . import config, db` (package `hosting_app`), so use `from . import config`.

All timestamps are integer epoch seconds UTC (`int(time.time())`); every function that depends on "now" takes optional keyword `now=None` (None -> current time) so tests can control time.

## Connection
`connect()` -> `sqlite3.Connection` to `config.DB_PATH`: `row_factory = sqlite3.Row`, `check_same_thread=False`, `isolation_level=None` (autocommit; explicit `BEGIN IMMEDIATE` where atomicity is needed), `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON`. Creates the parent directory if missing.
`init(conn=None)` -> creates tables if absent (idempotent) and returns the connection.

## Schema
`invites(id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL, created_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)`
`models(id TEXT PRIMARY KEY, invite_id INTEGER NOT NULL REFERENCES invites(id), title TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, extensions INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, size_glb INTEGER, parts INTEGER, error TEXT)`; status is one of `queued|running|done|failed`. Index on `models(status)` and on `models(invite_id)`.

## Functions (exact names and signatures; first parameter `conn` everywhere except the first two)
- `hash_token(token)` -> sha256 hexdigest of the UTF-8 token.
- `new_token()` -> `secrets.token_urlsafe(16)`.
- `add_invite(conn, label, now=None)` -> plain token string (stored only as hash).
- `get_invite(conn, token)` -> Row or None; None also when `revoked=1` or token is empty/None.
- `list_invites(conn)` -> list of dicts: id, label, created_at, revoked, models (count of its models), ordered by id.
- `revoke_invite(conn, invite_id, purge=False)` -> list of model ids deleted (empty list when not purge). Sets `revoked=1`; when `purge`, deletes all that invite's model rows and returns their ids (the caller removes files). Raises `KeyError` if the invite does not exist.
- `create_model(conn, invite_id, title, kind, now=None)` -> new model id `secrets.token_urlsafe(16)`; status `queued`; `expires_at = now + config.RETENTION_DAYS*86400`.
- `get_model(conn, model_id)` -> Row or None.
- `list_models(conn, invite_id)` -> list of Row, newest first.
- `count_active_models(conn, invite_id, now=None)` -> number of models of this invite with status in (`queued`,`running`,`done`) and `expires_at > now`.
- `count_uploads_since(conn, invite_id, seconds=86400, now=None)` -> number of models of this invite created within the last `seconds` (any status).
- `claim_next(conn)` -> Row or None: atomically (inside `BEGIN IMMEDIATE`) take the oldest `queued` model, set `status='running'`, `attempts=attempts+1`, return it as it is AFTER the update.
- `finish_model(conn, model_id, status, size_glb=None, parts=None, error=None)` -> sets these columns; `status` must be `done` or `failed` else `ValueError`.
- `reset_running(conn)` -> returns count: every model in `running`: if `attempts >= 2` -> `failed` with error text `Сервис перезапущен во время обработки`, else -> `queued`.
- `extend_model(conn, model_id, invite_id, now=None)` -> True if done: the model belongs to the invite, status is `done`, `extensions < config.MAX_EXTENSIONS`; then `expires_at = max(expires_at, now) + RETENTION_DAYS*86400` and `extensions += 1`; otherwise False.
- `delete_model(conn, model_id)` -> deletes the row.
- `expired_models(conn, now=None)` -> list of Row with `expires_at <= now` and status != `running`.
- `stale_failed(conn, days=7, now=None)` -> list of Row with status `failed` and `created_at <= now - days*86400`.
- Upload support: `create_model` takes an extra keyword `status="queued"` (the upload endpoint passes `"uploading"`; the worker never claims `uploading` models and `count_active_models` counts them); `mark_queued(conn, model_id)` switches `uploading` -> `queued`; `stale_uploading(conn, hours=1, now=None)` -> Rows in `uploading` created at least `hours` hours ago; `all_model_ids(conn)` -> `set` of every model id; `active_model_ids(conn)` -> `set` of ids with status `uploading`, `queued` or `running`.
- `stats(conn, now=None)` -> dict: `invites` (non-revoked count), `models_total`, `by_status` (dict status -> count), `active` (count not expired).
