"""
Модуль main приложения hosting_app.
Содержит FastAPI-приложение, маршруты и вспомогательные функции.
"""

import os
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import config, db, page

# Инициализация приложения
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Путь к текущему модулю и шаблонам
MODULE_DIR = Path(__file__).parent
TEMPLATES_DIR = MODULE_DIR / "templates"

# Загрузка заглушки один раз при импорте
STUB = (TEMPLATES_DIR / "stub.html").read_text(encoding="utf-8")

# Глобальное соединение с БД (autocommit, thread-safe)
CONN = db.init()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware для добавления заголовков безопасности во все ответы.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noimageindex"
        return response


app.add_middleware(SecurityHeadersMiddleware)


@app.on_event("startup")
async def startup_event():
    """
    Инициализация базы данных при старте приложения.
    """
    db.init()


def invite_or_404(token: str):
    """
    Получает приглашение по токену или бросает 404.
    """
    row = db.get_invite(CONN, token)
    if not row:
        raise HTTPException(status_code=404, detail="Не найдено")
    return row


def public_model(row) -> dict:
    """
    Формирует публичную модель данных для ответа.
    """
    model_id = row["id"]
    status = row["status"]
    extensions = row["extensions"]

    can_extend = (status == "done") and (extensions < config.MAX_EXTENSIONS)
    
    return {
        "id": model_id,
        "title": row["title"],
        "kind": row["kind"],
        "status": status,
        "error": row["error"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "url": config.PUBLIC_URL + "/m/" + str(model_id) + "/",
        "can_extend": can_extend
    }


def disk_used_pct() -> float:
    """
    Возвращает процент использования диска, на котором находится config.DATA.
    """
    usage = shutil.disk_usage(config.DATA)
    if usage.total == 0:
        return 0.0
    return (usage.used / usage.total) * 100


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Обработчик HTTP-исключений.
    Для /api/ возвращает JSON, для остальных — заглушку со статусом 200.
    """
    if exc.status_code == 404:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                content={"error": "Не найдено"},
                status_code=404,
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer"
                }
            )
        else:
            return HTMLResponse(
                content=STUB,
                status_code=200,
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer"
                }
            )
    
    # Для других статусов возвращаем стандартный ответ
    return JSONResponse(
        content={"error": exc.detail if isinstance(exc.detail, str) else "Ошибка"},
        status_code=exc.status_code,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Обработчик ошибок валидации запроса.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            content={"error": "Не найдено"},
            status_code=404,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer"
            }
        )
    else:
        return HTMLResponse(
            content=STUB,
            status_code=200,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer"
            }
        )


@app.get("/healthz")
async def healthz():
    """
    Проверка здоровья сервиса.
    """
    return PlainTextResponse("ok")


@app.get("/")
async def root():
    """
    Корневой маршрут — возвращает заглушку.
    """
    return HTMLResponse(
        content=STUB,
        status_code=200,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.get("/robots.txt")
async def robots_txt():
    """
    Файл robots.txt.
    """
    return PlainTextResponse(
        content="User-agent: *\nDisallow: /\n",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.get("/u/{token}")
async def upload_page(token: str):
    """
    Страница загрузки файла.
    """
    invite = invite_or_404(token)
    file_path = TEMPLATES_DIR / "upload.html"
    return FileResponse(
        path=str(file_path),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.get("/api/u/{token}/models")
async def list_models(token: str):
    """
    Список моделей для приглашения.
    """
    invite = invite_or_404(token)
    rows = db.list_models(CONN, invite["id"])
    models = [public_model(row) for row in rows]
    
    return JSONResponse(
        content={
            "models": models,
            "limits": {
                "max_mb": config.LIMIT_MB,
                "retention_days": config.RETENTION_DAYS,
                "quota_models": config.QUOTA_MODELS,
                "uploads_per_day": config.QUOTA_UPLOADS_PER_DAY
            }
        },
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.post("/api/u/{token}/upload")
async def upload_file(token: str, request: Request, name: str = "", title: str = ""):
    """
    Загрузка файла.
    """
    # a. Проверка приглашения
    invite = invite_or_404(token)
    
    # b. Определение типа по расширению
    ext = os.path.splitext(name)[1].lower()
    if not ext or ext not in config.EXT_KIND:
        raise HTTPException(
            status_code=400,
            detail="Неподдерживаемый тип файла. Допустимо: STEP, STP, PDF (3D), NWD, GLB"
        )
    kind = config.EXT_KIND[ext]
    
    # c. Проверка места на диске
    if disk_used_pct() >= config.DISK_LIMIT_PCT:
        raise HTTPException(
            status_code=503,
            detail="Сервер временно не принимает файлы: нет места"
        )
    
    # d. Проверка квот
    if db.count_active_models(CONN, invite["id"]) >= config.QUOTA_MODELS:
        raise HTTPException(
            status_code=429,
            detail="Достигнут предел числа моделей — удалите ненужные"
        )
    if db.count_uploads_since(CONN, invite["id"]) >= config.QUOTA_UPLOADS_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail="Достигнут суточный предел загрузок"
        )
    
    # e. Проверка Content-Length
    content_length_header = request.headers.get("Content-Length")
    if content_length_header is None:
        raise HTTPException(status_code=411, detail="Нужен Content-Length")
    
    try:
        content_length = int(content_length_header)
    except (ValueError, TypeError):
        raise HTTPException(status_code=411, detail="Нужен Content-Length")
    
    limit_mb = config.LIMIT_MB[kind]
    limit_bytes = limit_mb * 1024 * 1024
    
    if content_length > limit_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Файл больше {limit_mb} МБ"
        )
    
    if content_length == 0:
        raise HTTPException(status_code=400, detail="Пустой файл")
    
    # f. Создание модели и запись файла
    stem = os.path.splitext(name)[0]
    clean_title = page.clean_title(title if title else stem)
    
    # статус uploading: воркер не берёт модель, пока файл не записан и не проверен
    model_id = db.create_model(CONN, invite["id"], clean_title, kind, status="uploading")
    
    # Определяем расширение из фиксированного набора
    ext_map = {
        "step": ".step",
        "stp": ".stp",
        "pdf": ".pdf",
        "nwd": ".nwd",
        "glb": ".glb"
    }
    file_ext = ext_map.get(kind, ext)
    
    incoming_dir = Path(config.INCOMING) / str(model_id)
    incoming_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = incoming_dir / f"src{file_ext}"
    
    bytes_written = 0
    try:
        with open(file_path, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                bytes_written += len(chunk)
                if bytes_written > limit_bytes:
                    # Превышен лимит — удаляем всё
                    shutil.rmtree(incoming_dir, ignore_errors=True)
                    db.delete_model(CONN, model_id)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Файл больше {limit_mb} МБ"
                    )
        
        # Проверка соответствия Content-Length
        if bytes_written != content_length:
            shutil.rmtree(incoming_dir, ignore_errors=True)
            db.delete_model(CONN, model_id)
            raise HTTPException(status_code=400, detail="Неверный размер файла")
        
    except HTTPException:
        raise
    except Exception:
        # Непредвиденная ошибка — очистка
        shutil.rmtree(incoming_dir, ignore_errors=True)
        db.delete_model(CONN, model_id)
        raise HTTPException(status_code=500, detail="Ошибка при записи файла")
    
    # g. Проверка магических байтов
    try:
        with open(file_path, "rb") as f:
            header = f.read(64)
        
        magic = config.MAGIC[kind]
        
        if kind in ("step", "stp"):
            # Для STEP разрешаем до 16 ведущих пробельных байтов
            stripped = header.lstrip(b" \t\r\n")
            if not stripped.startswith(magic):
                shutil.rmtree(incoming_dir, ignore_errors=True)
                db.delete_model(CONN, model_id)
                raise HTTPException(
                    status_code=400,
                    detail="Содержимое файла не соответствует типу"
                )
        else:
            if not header.startswith(magic):
                shutil.rmtree(incoming_dir, ignore_errors=True)
                db.delete_model(CONN, model_id)
                raise HTTPException(
                    status_code=400,
                    detail="Содержимое файла не соответствует типу"
                )
    except HTTPException:
        raise
    except Exception:
        shutil.rmtree(incoming_dir, ignore_errors=True)
        db.delete_model(CONN, model_id)
        raise HTTPException(status_code=500, detail="Ошибка при проверке файла")
    
    # h. Возврат результата: теперь файл на месте — ставим в очередь
    db.mark_queued(CONN, model_id)
    row = db.get_model(CONN, model_id)
    return JSONResponse(
        content=public_model(row),
        status_code=202,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.post("/api/u/{token}/models/{model_id}/extend")
async def extend_model(token: str, model_id: str):
    """
    Продление срока хранения модели.
    """
    invite = invite_or_404(token)
    
    # Проверка принадлежности модели
    row = db.get_model(CONN, model_id)
    if not row or row["invite_id"] != invite["id"]:
        raise HTTPException(status_code=404, detail="Не найдено")
    
    success = db.extend_model(CONN, model_id, invite["id"])
    
    if not success:
        raise HTTPException(status_code=409, detail="Продление недоступно")
    
    updated_row = db.get_model(CONN, model_id)
    return JSONResponse(
        content={
            "ok": True,
            "expires_at": updated_row["expires_at"]
        },
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


@app.delete("/api/u/{token}/models/{model_id}")
async def delete_model(token: str, model_id: str):
    """
    Удаление модели.
    """
    invite = invite_or_404(token)
    
    # Проверка принадлежности модели
    row = db.get_model(CONN, model_id)
    if not row or row["invite_id"] != invite["id"]:
        raise HTTPException(status_code=404, detail="Не найдено")
    
    # Проверка статуса
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="Модель обрабатывается")
    
    # Удаление директорий
    pages_dir = Path(config.PAGES) / str(model_id)
    incoming_dir = Path(config.INCOMING) / str(model_id)
    work_dir = Path(config.WORK) / str(model_id)
    
    shutil.rmtree(pages_dir, ignore_errors=True)
    shutil.rmtree(incoming_dir, ignore_errors=True)
    shutil.rmtree(work_dir, ignore_errors=True)
    
    # Удаление записи из БД
    db.delete_model(CONN, model_id)
    
    return JSONResponse(
        content={"ok": True},
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer"
        }
    )


# Монтирование статических файлов vendor
VENDOR_DIR = Path("/app/vendor")
if VENDOR_DIR.exists():
    app.mount("/vendor", StaticFiles(directory=str(VENDOR_DIR)), name="vendor")
