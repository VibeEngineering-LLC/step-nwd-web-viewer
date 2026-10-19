Write a complete Python 3.12 module `cli.py` (stdlib only: argparse, json, shutil, os, time, datetime; no markdown fences, no explanations, output ONLY code). Docstring, help texts and printed messages in Russian. Imported inside package `hosting_app` as `from . import config, db`. Run as `python -m hosting_app.cli ...` (the container wrapper `viewerctl` calls exactly that).

Sub-commands (argparse sub-parsers), each prints plain text to stdout and returns exit code 0, errors to stderr with exit code 2:
- `invite add LABEL` — `token = db.add_invite(conn, LABEL)`; print ONE line: `Ссылка: ` + `config.PUBLIC_URL + "/u/" + token` (if PUBLIC_URL is empty print `/u/<token>`) and a second line `Токен показывается один раз и нигде не хранится в открытом виде.`. LABEL must be non-empty and at most 60 characters.
- `invite list` — table: id, label, created date `YYYY-MM-DD`, status (`активна`/`отозвана`), number of models.
- `invite revoke ID [--purge]` — `db.revoke_invite`; with `--purge` delete the returned models' directories under `config.PAGES`, `config.INCOMING`, `config.WORK` and print how many models were removed; unknown id -> error.
- `invite export` — JSON list (ensure_ascii=False, indent 2) of `{id, label, created_at (ISO), revoked}` — no tokens or hashes.
- `models list [--invite ID]` — table: id, title, kind, status, expires date, size_glb in MB (1 decimal), invite id.
- `models delete MODEL_ID` — refuse for status `running`; remove the directories and the row; unknown id -> error.
- `stats` — print: disk used % and free GB for `config.DATA`, `db.stats` values (active invites, models by status, active models), size in MB of `config.PAGES` (sum of file sizes), queue length (`queued`).
Provide `main(argv=None)` returning the exit code and `if __name__ == "__main__": raise SystemExit(main())`. The connection is created once per call via `db.init()`.

## Типы и сигнатуры `db` (обязательны к соблюдению)
- Все временные метки в БД — `int`, секунды эпохи UTC. НЕ ISO-строки: `datetime.fromisoformat` к ним неприменим; дата для печати — `datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")`; в `invite export` поле `created_at` — ISO-строка из этого же `datetime` (`.isoformat()`).
- `db.list_invites(conn) -> list[dict]` с ключами `id, label, created_at (int), revoked (0/1), models (число моделей)` — ключ именно `models`, не `model_count`.
- `db.revoke_invite(conn, invite_id: int, purge: bool = False) -> list[str]` — список id удалённых моделей (пустой без `purge`); неизвестный id -> `KeyError` (печатать ошибку, код 2). Строки моделей удаляются самой функцией, каталоги удаляет CLI. Вызывать с `purge=args.purge`.
- `db.list_models(conn, invite_id: int) -> list[sqlite3.Row]` — `invite_id` обязателен (None не допускается). Без `--invite` CLI обходит `db.list_invites` и склеивает результаты. Элементы — `sqlite3.Row`: НЕТ `.get`, доступ `row["ключ"]`; `size_glb` бывает `NULL` (`None`) -> считать 0.
- `db.get_model(conn, model_id) -> sqlite3.Row | None`; `db.delete_model(conn, model_id) -> None`.
- `db.stats(conn) -> dict` с ключами `invites` (число неотозванных приглашений), `models_total`, `by_status` (dict статус -> число), `active` (число активных моделей). Ключей `active_invites`, `models_by_status` НЕТ.
- Аргументы `invite revoke ID` и `models list --invite ID` — `type=int`.
