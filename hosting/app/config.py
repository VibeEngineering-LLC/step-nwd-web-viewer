"""
Модуль конфигурации сервиса.

Все настройки читаются из переменных окружения в момент импорта модуля.
Используются только константы верхнего уровня и одна вспомогательная функция.
"""

import os


def _int(name: str, default: int) -> int:
    """Возвращает целое число из переменной окружения или значение по умолчанию."""
    return int(os.environ.get(name, default))


# Пути к каталогам
DATA = os.environ.get("DATA_DIR", "/data")
SCRIPTS = os.environ.get("SCRIPTS_DIR", "/app/scripts")
ASSETS = os.environ.get("ASSETS_DIR", "/app/assets")

# Пути к исполняемым файлам
NWD_DUMP = os.environ.get("NWD_DUMP", "/usr/local/bin/nwd_dump")
PYTHON = os.environ.get("PYTHON_BIN", "python")

# URL
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
SOURCE_URL = os.environ.get("SOURCE_URL", "https://github.com/VibeEngineering-LLC/step-nwd-web-viewer")

# Целочисленные параметры
RETENTION_DAYS = _int("RETENTION_DAYS", 30)
MAX_EXTENSIONS = _int("MAX_EXTENSIONS", 3)
DISK_LIMIT_PCT = _int("DISK_LIMIT_PCT", 85)
QUOTA_MODELS = _int("QUOTA_MODELS", 30)
QUOTA_UPLOADS_PER_DAY = _int("QUOTA_UPLOADS_PER_DAY", 10)
TASK_TIMEOUT_S = _int("TASK_TIMEOUT_S", 900)
MAX_GLB_MB = _int("MAX_GLB_MB", 60)
QUANTIZE_ABOVE_MB = _int("QUANTIZE_ABOVE_MB", 30)

# Ограничения размера файлов в мегабайтах для каждого типа
LIMIT_MB = {
    "step": _int("MAX_UPLOAD_MB_STEP", 100),
    "pdf": _int("MAX_UPLOAD_MB_PDF", 100),
    "nwd": _int("MAX_UPLOAD_MB_NWD", 300),
    "glb": _int("MAX_UPLOAD_MB_GLB", 60),
}

# Соответствие расширений файлов типам
EXT_KIND = {
    ".step": "step",
    ".stp": "step",
    ".pdf": "pdf",
    ".nwd": "nwd",
    ".glb": "glb",
}

# Магические байты для определения типа файла
MAGIC = {
    "step": b"ISO-10303-21",
    "pdf": b"%PDF",
    "nwd": b"#LcUStream",
    "glb": b"glTF",
}

# Пути внутри каталога данных
INCOMING = os.path.join(DATA, "incoming")
WORK = os.path.join(DATA, "work")
PAGES = os.path.join(DATA, "pages")
DB_PATH = os.path.join(DATA, "db.sqlite")
