Write a complete Python 3.12 module `config.py` (no markdown fences, no explanations, output ONLY code). Docstring and comments in Russian.

Purpose: settings of the service read from environment variables, all at import time, module-level constants, no classes, no functions except a tiny helper `_int(name, default)` returning `int(os.environ.get(name, default))`.

Constants (name = env var = default), exactly these names:
- `DATA` = `DATA_DIR` = `/data`
- `SCRIPTS` = `SCRIPTS_DIR` = `/app/scripts`
- `ASSETS` = `ASSETS_DIR` = `/app/assets`
- `NWD_DUMP` = `NWD_DUMP` = `/usr/local/bin/nwd_dump`
- `PYTHON` = `PYTHON_BIN` = `python`
- `PUBLIC_URL` = `PUBLIC_URL` = `""` (base URL without trailing slash; strip a trailing `/` if present)
- `SOURCE_URL` = `SOURCE_URL` = `https://github.com/VibeEngineering-LLC/step-nwd-web-viewer`
- ints: `RETENTION_DAYS`=30, `MAX_EXTENSIONS`=3, `DISK_LIMIT_PCT`=85, `QUOTA_MODELS`=30, `QUOTA_UPLOADS_PER_DAY`=10, `TASK_TIMEOUT_S`=900, `MAX_GLB_MB`=60, `QUANTIZE_ABOVE_MB`=30 (env var names equal the constant names)
- `LIMIT_MB` — dict kind -> int megabytes: `step` 100, `pdf` 100, `nwd` 300, `glb` 60; each overridable by env `MAX_UPLOAD_MB_STEP`, `MAX_UPLOAD_MB_PDF`, `MAX_UPLOAD_MB_NWD`, `MAX_UPLOAD_MB_GLB`.
- `EXT_KIND` — dict: `.step`->`step`, `.stp`->`step`, `.pdf`->`pdf`, `.nwd`->`nwd`, `.glb`->`glb`.
- `MAGIC` — dict kind -> bytes prefix that the file must start with: `step` b"ISO-10303-21", `pdf` b"%PDF", `nwd` b"#LcUStream", `glb` b"glTF".
- paths via `os.path.join(DATA, ...)`: `INCOMING`="incoming", `WORK`="work", `PAGES`="pages", `DB_PATH`="db.sqlite".
