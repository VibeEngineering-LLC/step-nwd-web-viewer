import os
import sys
import struct
import tempfile
import importlib.util
from pathlib import Path

# Определяем корень репозитория и каталог приложения
# Каталог тестов: текущий каталог (где лежит conftest.py)
TESTS_DIR = Path(__file__).resolve().parent
# Каталог приложения: ../app относительно каталога тестов
APP_DIR = TESTS_DIR.parent / "app"
# Корень репозитория: родитель каталога, содержащего "hosting" (т.е. TESTS_DIR.parent)
# Согласно ТЗ: "repo root is the parent of the `hosting` directory"
# Если структура <root>/hosting/<tests_dir>/conftest.py, то root = TESTS_DIR.parent
# Но в ТЗ сказано: "The code under test is the package `hosting_app` located in the directory `../app` relative to the tests directory"
# И: "repo root is the parent of the `hosting` directory"
# Допустим, структура: <root>/hosting/tests/conftest.py и <root>/hosting/app/__init__.py
# Тогда TESTS_DIR = <root>/hosting/tests
# APP_DIR = <root>/hosting/app
# REPO_ROOT = <root> (родитель "hosting")
REPO_ROOT = TESTS_DIR.parent.parent

# Настройка переменных окружения перед импортом приложения
# Если /app/assets не существует (не в контейнере), устанавливаем пути относительно REPO_ROOT
if not os.path.exists("/app/assets"):
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
    os.environ.setdefault("ASSETS_DIR", str(REPO_ROOT / "assets"))
    os.environ.setdefault("SCRIPTS_DIR", str(REPO_ROOT / "scripts"))
else:
    # В контейнере пути /app/... уже корректны, но setdefault безопасен
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
    # Не переопределяем ASSETS_DIR и SCRIPTS_DIR, если они уже заданы или по умолчанию /app/...
    # ТЗ: "in the container the defaults `/app/...` are right"
    # Если переменные не заданы, они могут быть пустыми или отсутствовать.
    # Но ТЗ говорит: "only when `/app/assets` does not exist" для установки REPO_ROOT путей.
    # Значит, если /app/assets существует, мы НЕ устанавливаем REPO_ROOT пути.
    # Однако DATA_DIR всегда нужно установить, если не задан.
    pass

# Попытка импорта hosting_app
try:
    import hosting_app
except ImportError:
    # Загружаем из файла, если импорт не удался
    init_file = APP_DIR / "__init__.py"
    if not init_file.exists():
        raise ImportError(f"Не удалось найти {init_file}")
    
    spec = importlib.util.spec_from_file_location(
        "hosting_app",
        str(init_file),
        submodule_search_locations=[str(APP_DIR)]
    )
    if spec is None:
        raise ImportError(f"Не удалось создать спецификацию для {init_file}")
    
    module = importlib.util.module_from_spec(spec)
    sys.modules["hosting_app"] = module
    spec.loader.exec_module(module)
    hosting_app = module

import pytest
import hosting_app.config as config
import hosting_app.db as db


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """
    Фикстура, создающая временную директорию для данных и настраивающая конфиг.
    """
    data_path = str(tmp_path / "data")
    os.makedirs(data_path, exist_ok=True)
    
    # Сохраняем оригинальные значения для восстановления (monkeypatch делает это автоматически для setattr)
    # Но для dict используем setitem, который тоже откатывается monkeypatch'ем
    
    # Устанавливаем пути
    monkeypatch.setattr(config, "DATA", data_path)
    monkeypatch.setattr(config, "INCOMING", os.path.join(data_path, "incoming"))
    monkeypatch.setattr(config, "WORK", os.path.join(data_path, "work"))
    monkeypatch.setattr(config, "PAGES", os.path.join(data_path, "pages"))
    monkeypatch.setattr(config, "DB_PATH", os.path.join(data_path, "db.sqlite3"))
    
    # Создаем директории
    os.makedirs(config.INCOMING, exist_ok=True)
    os.makedirs(config.WORK, exist_ok=True)
    os.makedirs(config.PAGES, exist_ok=True)
    
    # Защита лимитов: monkeypatch вернёт значения после теста, если тест их изменит
    monkeypatch.setattr(config, "LIMIT_MB", dict(config.LIMIT_MB))
    monkeypatch.setattr(config, "QUOTA_UPLOADS_PER_DAY", config.QUOTA_UPLOADS_PER_DAY)
    monkeypatch.setattr(config, "QUOTA_MODELS", config.QUOTA_MODELS)

    return data_path


@pytest.fixture
def conn(data_dir):
    """
    Фикстура, создающая соединение с БД.
    """
    connection = db.init()
    yield connection
    connection.close()


@pytest.fixture
def invite(conn):
    """
    Фикстура, создающая приглашение.
    """
    token = db.add_invite(conn, "тест")
    invite_row = db.get_invite(conn, token)
    return token, invite_row


def make_glb(path, external_uri=False, vertices=3):
    """
    Создает минимальный валидный GLB 2.0 файл.
    """
    # JSON часть
    json_data = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "nodes": [{"name": "Корпус", "mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "mode": 4
                    }
                ]
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,  # FLOAT
                "count": vertices,
                "type": "VEC3"
            }
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": vertices * 12
            }
        ],
        "buffers": [
            {
                "byteLength": vertices * 12
            }
        ]
    }
    
    if external_uri:
        json_data["buffers"][0]["uri"] = "https://evil.example/x.bin"
    
    import json
    json_bytes = json.dumps(json_data, ensure_ascii=False).encode('utf-8')
    
    # Выравнивание JSON до 4 байт (пробелами)
    json_padding = (4 - (len(json_bytes) % 4)) % 4
    json_bytes += b' ' * json_padding
    
    # BIN часть
    bin_bytes = b'\x00' * (vertices * 12)
    # Выравнивание BIN до 4 байт (нулями)
    bin_padding = (4 - (len(bin_bytes) % 4)) % 4
    bin_bytes += b'\x00' * bin_padding
    
    # Заголовок GLB
    # Magic: glTF (4 bytes)
    # Version: 2 (4 bytes, little-endian)
    # Length: total length (4 bytes, little-endian)
    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    
    header = struct.pack('<4sII', b'glTF', 2, total_length)
    
    # JSON Chunk
    json_chunk_length = len(json_bytes)
    json_chunk_type = 0x4E4F534A  # "JSON"
    json_chunk_header = struct.pack('<II', json_chunk_length, json_chunk_type)
    
    # BIN Chunk
    bin_chunk_length = len(bin_bytes)
    bin_chunk_type = 0x004E4942  # "BIN\0"
    bin_chunk_header = struct.pack('<II', bin_chunk_length, bin_chunk_type)
    
    with open(path, 'wb') as f:
        f.write(header)
        f.write(json_chunk_header)
        f.write(json_bytes)
        f.write(bin_chunk_header)
        f.write(bin_bytes)


@pytest.fixture
def make_glb_file():
    """
    Фикстура, возвращающая функцию make_glb.
    """
    return make_glb


def make_step_cube(path):
    """
    Создает STEP файл куба 10x10x10.
    """
    try:
        import OCP.BRepPrimAPI
        import OCP.STEPControl
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    except ImportError:
        return False
    
    try:
        # Создаем куб 10x10x10
        box = BRepPrimAPI_MakeBox(10.0, 10.0, 10.0)
        shape = box.Shape()
        
        # Создаем writer
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        status = writer.Write(str(path))
        
        # Проверка статуса (0 = OK)
        return status == 0
    except Exception:
        return False


@pytest.fixture
def make_step_cube_file():
    """
    Фикстура, возвращающая функцию make_step_cube.
    """
    return make_step_cube
