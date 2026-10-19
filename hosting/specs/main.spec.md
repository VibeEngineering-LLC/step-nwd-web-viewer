Write a complete Python 3.12 module `main.py` (FastAPI, uvicorn is started elsewhere; stdlib + fastapi + starlette only; no markdown fences, no explanations, output ONLY code). Docstring and comments in Russian. Imported inside package `hosting_app` as `from . import config, db, page`.

Create `app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`; on startup call `db.init()`. One module-level connection `CONN = db.init()` is acceptable (autocommit mode, thread-safe flag already set); endpoints are `async def` but DB calls are short.

All JSON error bodies: `{"error": "<русский текст>"}`. Responses of HTML pages and JSON add headers `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

## Helpers
- `invite_or_404(token)` -> Row from `db.get_invite(CONN, token)`; otherwise raise `HTTPException(404)` with detail `"Не найдено"`.
- `public_model(row)` -> dict: `id`, `title`, `kind`, `status`, `error`, `created_at`, `expires_at`, `url` (`config.PUBLIC_URL + "/m/" + id + "/"`), `can_extend` (status `done` and `extensions < config.MAX_EXTENSIONS`).
- `disk_used_pct()` -> percentage of the filesystem holding `config.DATA` that is used (`shutil.disk_usage`).

## Routes
1. `GET /healthz` -> plain text `ok`.
2. `GET /u/{token}` -> after `invite_or_404`, return the file `templates/upload.html` located next to this module (`FileResponse`, media type `text/html; charset=utf-8`). For an invalid or revoked token return the STUB page instead (see below), not an error.

## Stub page (nothing about the service may be revealed to outsiders)
`STUB = open(templates/stub.html).read()` read once at import. Register an exception handler for `StarletteHTTPException` with status 404 (and for `RequestValidationError`): when the request path starts with `/api/` return JSON `{"error": "Не найдено"}` with status 404; for every other path return `HTMLResponse(STUB, status_code=200)` — an identical body and status for any unknown address, so outsiders cannot tell valid addresses from invalid ones. Add `GET /` returning the same STUB (status 200). Also `GET /robots.txt` -> plain text `User-agent: *\nDisallow: /\n`. Every response of the application gets the header `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex` (add through an HTTP middleware).
3. `GET /api/u/{token}/models` -> `{"models": [public_model...], "limits": {"max_mb": config.LIMIT_MB, "retention_days": config.RETENTION_DAYS, "quota_models": config.QUOTA_MODELS, "uploads_per_day": config.QUOTA_UPLOADS_PER_DAY}}`.
4. `POST /api/u/{token}/upload?name=<file name>&title=<title>` — the request BODY IS THE RAW FILE (not multipart). Steps in this exact order, each failure returns the stated status:
   a. `invite_or_404`.
   b. kind from the extension of `name` (lower-case, `os.path.splitext`) via `config.EXT_KIND`; unknown or missing -> 400 `"Неподдерживаемый тип файла. Допустимо: STEP, STP, PDF (3D), NWD, GLB"`. The file name is NEVER used for any path.
   c. disk: `disk_used_pct() >= config.DISK_LIMIT_PCT` -> 503 `"Сервер временно не принимает файлы: нет места"`.
   d. quotas: `db.count_active_models(...) >= config.QUOTA_MODELS` -> 429 `"Достигнут предел числа моделей — удалите ненужные"`; `db.count_uploads_since(...) >= config.QUOTA_UPLOADS_PER_DAY` -> 429 `"Достигнут суточный предел загрузок"`.
   e. header `Content-Length` must be present and an integer, otherwise 411 `"Нужен Content-Length"`; if it exceeds `config.LIMIT_MB[kind]` megabytes -> 413 `"Файл больше N МБ"`; zero -> 400. This happens BEFORE the body is read.
   f. `model_id = db.create_model(CONN, invite["id"], page.clean_title(title or stem of name), kind)`; directory `config.INCOMING/<model_id>`; stream `request.stream()` into `src<ext>` (ext = the extension chosen from the fixed set, e.g. `.step`), counting bytes; if the count exceeds the limit at any moment or differs from Content-Length at the end -> delete the directory and the row, return 413 / 400.
   g. magic bytes: the first bytes of the file must start with `config.MAGIC[kind]` (for STEP allow up to 16 leading whitespace bytes) else delete everything and return 400 `"Содержимое файла не соответствует типу"`.
   h. return 202 with `public_model(row)`.
   Any unexpected exception during streaming must clean up the directory and the row.
5. `POST /api/u/{token}/models/{model_id}/extend` -> `db.extend_model(...)`; True -> `{"ok": true, "expires_at": ...}`; False -> 409 `"Продление недоступно"`. Model must belong to the invite (otherwise 404).
6. `DELETE /api/u/{token}/models/{model_id}` -> owner check (404 otherwise); remove `config.PAGES/<id>`, `config.INCOMING/<id>`, `config.WORK/<id>` directories if present (`shutil.rmtree(..., ignore_errors=True)`) unless status is `running` (then 409 `"Модель обрабатывается"`); delete the row; `{"ok": true}`.
7. `GET /vendor/...` -> static files from directory `/app/vendor` (`StaticFiles`, mounted only if the directory exists), with header `Cache-Control: public, max-age=31536000, immutable`.

Never log tokens: do not log request paths; the application must not add its own access logging.
