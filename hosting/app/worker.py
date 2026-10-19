"""Фоновый воркер обработки моделей: забирает задачи из очереди, конвертирует файлы и чистит устаревшие данные."""

import glob
import os
import shutil
import time

from . import config, db, convert


def process_one(conn) -> bool:
    """Обрабатывает одну задачу из очереди. Возвращает True, если задача была взята."""
    row = db.claim_next(conn)
    if row is None:
        return False

    model_id = row["id"]
    incoming_dir = os.path.join(config.INCOMING, model_id)
    work_dir = os.path.join(config.WORK, model_id)
    page_dir = os.path.join(config.PAGES, model_id)

    # Ищем единственный исходный файл в каталоге входящих
    src_pattern = os.path.join(incoming_dir, "src.*")
    matches = glob.glob(src_pattern)
    if not matches:
        db.finish_model(conn, model_id, "failed", error="Исходный файл не найден")
        shutil.rmtree(incoming_dir, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        return True

    src = matches[0]

    try:
        res = convert.convert(
            row["kind"],
            src,
            work_dir,
            page_dir,
            row["title"],
            row["expires_at"]
        )
        db.finish_model(
            conn,
            model_id,
            "done",
            size_glb=res["size_glb"],
            parts=res["parts"]
        )
    except convert.ConvertError as e:
        db.finish_model(conn, model_id, "failed", error=str(e))
    except Exception as e:
        db.finish_model(conn, model_id, "failed", error="Внутренняя ошибка обработки")
        print(type(e).__name__)
    finally:
        # Всегда удаляем временные каталоги
        shutil.rmtree(incoming_dir, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)

    return True


def cleanup_once(conn, now=None) -> dict:
    """Однократная очистка устаревших данных. Возвращает словарь со счетчиками удаленных элементов."""
    if now is None:
        now = int(time.time())

    counts = {
        "expired": 0,
        "stale_dirs": 0,
        "failed_rows": 0
    }

    # 1. Удаляем истекшие модели
    for row in db.expired_models(conn, now):
        model_id = row["id"]
        shutil.rmtree(os.path.join(config.PAGES, model_id), ignore_errors=True)
        shutil.rmtree(os.path.join(config.INCOMING, model_id), ignore_errors=True)
        shutil.rmtree(os.path.join(config.WORK, model_id), ignore_errors=True)
        db.delete_model(conn, model_id)
        counts["expired"] += 1

    # 2. Удаляем устаревшие каталоги (старше 24 часов) и прерванные загрузки
    active_ids = db.active_model_ids(conn)
    threshold_24h = now - 24 * 3600

    for base_dir in (config.INCOMING, config.WORK):
        if not os.path.exists(base_dir):
            continue
        for name in os.listdir(base_dir):
            path = os.path.join(base_dir, name)
            if not os.path.isdir(path):
                continue
            if name in active_ids:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < threshold_24h:
                shutil.rmtree(path, ignore_errors=True)
                counts["stale_dirs"] += 1

    # Удаляем прерванные загрузки (статус uploading, старше 1 часа)
    for row in db.stale_uploading(conn, 1, now):
        model_id = row["id"]
        shutil.rmtree(os.path.join(config.INCOMING, model_id), ignore_errors=True)
        db.delete_model(conn, model_id)
        counts["stale_dirs"] += 1

    # 3. Удаляем старые неудачные модели (старше 7 дней)
    for row in db.stale_failed(conn, 7, now):
        model_id = row["id"]
        shutil.rmtree(os.path.join(config.PAGES, model_id), ignore_errors=True)
        shutil.rmtree(os.path.join(config.INCOMING, model_id), ignore_errors=True)
        shutil.rmtree(os.path.join(config.WORK, model_id), ignore_errors=True)
        db.delete_model(conn, model_id)
        counts["failed_rows"] += 1

    # 4. Удаляем сиротские каталоги в PAGES (старше 1 часа, не связанные с БД)
    all_ids = db.all_model_ids(conn)
    threshold_1h = now - 3600

    if os.path.exists(config.PAGES):
        for name in os.listdir(config.PAGES):
            path = os.path.join(config.PAGES, name)
            if not os.path.isdir(path):
                continue
            # Безопасность: никогда не удаляем каталоги, связанные с существующими моделями
            if name in all_ids:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime < threshold_1h:
                shutil.rmtree(path, ignore_errors=True)
                counts["stale_dirs"] += 1

    return counts


def main():
    """Главная точка входа воркера."""
    conn = db.init()
    db.reset_running(conn)

    # Создаем необходимые каталоги
    for dir_path in (config.INCOMING, config.WORK, config.PAGES):
        os.makedirs(dir_path, mode=0o755, exist_ok=True)

    print("worker started")

    last_cleanup = time.monotonic()

    try:
        while True:
            try:
                if not process_one(conn):
                    time.sleep(2)

                # Чистим раз в час
                now_mono = time.monotonic()
                if now_mono - last_cleanup >= 3600:
                    cleanup_once(conn)
                    last_cleanup = now_mono

            except Exception as e:
                print(type(e).__name__)
                time.sleep(5)

    except KeyboardInterrupt:
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    main()
