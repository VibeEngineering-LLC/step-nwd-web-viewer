"""CLI-инструмент для управления хостингом моделей."""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

from . import config, db


def _get_conn():
    return db.init()


def _date(ts):
    """Секунды эпохи (int) -> YYYY-MM-DD (UTC); пусто -> N/A."""
    if not ts:
        return "N/A"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def _model_dirs(model_id):
    return [os.path.join(p, str(model_id)) for p in (config.PAGES, config.INCOMING, config.WORK)]


def _format_size_mb(size_bytes):
    return round(size_bytes / (1024 * 1024), 1)


def _print_error(message):
    print(message, file=sys.stderr)


def cmd_invite_add(args):
    label = args.label
    if not label or len(label) > 60:
        _print_error("Ошибка: метка должна быть непустой и не длиннее 60 символов.")
        return 2

    conn = _get_conn()
    try:
        token = db.add_invite(conn, label)
    except Exception as e:
        _print_error(f"Ошибка при добавлении приглашения: {e}")
        return 2

    if config.PUBLIC_URL:
        url = f"{config.PUBLIC_URL}/u/{token}"
    else:
        url = f"/u/{token}"

    print(f"Ссылка: {url}")
    print("Токен показывается один раз и нигде не хранится в открытом виде.")
    return 0


def cmd_invite_list(args):
    conn = _get_conn()
    try:
        invites = db.list_invites(conn)
    except Exception as e:
        _print_error(f"Ошибка при получении списка приглашений: {e}")
        return 2

    if not invites:
        print("Приглашений нет.")
        return 0

    header = f"{'ID':<6} {'Метка':<20} {'Создано':<12} {'Статус':<10} {'Модели':<8}"
    print(header)
    print("-" * len(header))

    for inv in invites:
        inv_id = inv["id"]
        label = inv["label"]
        revoked = inv["revoked"]
        model_count = inv["models"]
        date_str = _date(inv["created_at"])

        status = "отозвана" if revoked else "активна"

        print(f"{inv_id:<6} {label:<20} {date_str:<12} {status:<10} {model_count:<8}")

    return 0


def cmd_invite_revoke(args):
    conn = _get_conn()
    try:
        deleted_ids = db.revoke_invite(conn, args.id, purge=args.purge)
    except KeyError:
        _print_error(f"Ошибка: приглашение с ID {args.id} не найдено.")
        return 2
    except Exception as e:
        _print_error(f"Ошибка при отзыве приглашения: {e}")
        return 2

    if args.purge:
        for model_id in deleted_ids:
            for dir_path in _model_dirs(model_id):
                if os.path.exists(dir_path):
                    shutil.rmtree(dir_path, ignore_errors=True)

        print(f"Приглашение {args.id} отозвано. Удалено моделей: {len(deleted_ids)}")
    else:
        print(f"Приглашение {args.id} отозвано.")

    return 0


def cmd_invite_export(args):
    conn = _get_conn()
    try:
        invites = db.list_invites(conn)
    except Exception as e:
        _print_error(f"Ошибка при экспорте приглашений: {e}")
        return 2

    export_data = []
    for inv in invites:
        created = inv["created_at"]
        export_data.append({
            "id": inv["id"],
            "label": inv["label"],
            "created_at": datetime.fromtimestamp(created, timezone.utc).isoformat() if created else None,
            "revoked": bool(inv["revoked"]),
        })

    print(json.dumps(export_data, ensure_ascii=False, indent=2))
    return 0


def cmd_models_list(args):
    conn = _get_conn()
    try:
        if args.invite is not None:
            models = db.list_models(conn, args.invite)
        else:
            models = []
            for inv in db.list_invites(conn):
                models.extend(db.list_models(conn, inv["id"]))
    except Exception as e:
        _print_error(f"Ошибка при получении списка моделей: {e}")
        return 2

    if not models:
        print("Модели не найдены.")
        return 0

    header = f"{'ID':<24} {'Название':<20} {'Тип':<10} {'Статус':<10} {'Истекает':<12} {'Размер (МБ)':<12} {'Приглашение':<12}"
    print(header)
    print("-" * len(header))

    for model in models:
        model_id = model["id"]
        title = model["title"]
        kind = model["kind"]
        status = model["status"]
        size_glb = model["size_glb"] or 0
        invite_id = model["invite_id"]
        date_str = _date(model["expires_at"])

        size_mb = _format_size_mb(size_glb)

        print(f"{model_id:<24} {title:<20} {kind:<10} {status:<10} {date_str:<12} {size_mb:<12.1f} {invite_id:<12}")

    return 0


def cmd_models_delete(args):
    conn = _get_conn()
    try:
        model = db.get_model(conn, args.model_id)
    except Exception as e:
        _print_error(f"Ошибка при получении модели: {e}")
        return 2

    if model is None:
        _print_error(f"Ошибка: модель с ID {args.model_id} не найдена.")
        return 2

    if model["status"] == "running":
        _print_error("Ошибка: нельзя удалить модель со статусом 'running'.")
        return 2

    model_id = model["id"]
    for dir_path in _model_dirs(model_id):
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
            except Exception as e:
                _print_error(f"Ошибка при удалении директории {dir_path}: {e}")
                return 2

    try:
        db.delete_model(conn, model_id)
    except Exception as e:
        _print_error(f"Ошибка при удалении записи модели: {e}")
        return 2

    print(f"Модель {model_id} удалена.")
    return 0


def cmd_stats(args):
    conn = _get_conn()

    try:
        stats = db.stats(conn)
    except Exception as e:
        _print_error(f"Ошибка при получении статистики: {e}")
        return 2

    data_path = config.DATA
    if os.path.exists(data_path):
        usage = shutil.disk_usage(data_path)
        total = usage.total
        free = usage.free
        used_percent = (1 - (free / total)) * 100 if total > 0 else 0
        free_gb = free / (1024 ** 3)
    else:
        used_percent = 0
        free_gb = 0

    pages_path = config.PAGES
    pages_size_mb = 0
    if os.path.exists(pages_path):
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(pages_path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(filepath)
                except OSError:
                    pass
        pages_size_mb = _format_size_mb(total_size)

    active_invites = stats["invites"]
    active_models = stats["active"]
    queued_models = stats["by_status"].get("queued", 0)

    print(f"Диск (данные): занято {used_percent:.1f}%, свободно {free_gb:.2f} ГБ")
    print(f"Активных приглашений: {active_invites}")
    print(f"Активных моделей: {active_models}")
    print(f"В очереди: {queued_models}")
    print(f"Размер каталога страниц: {pages_size_mb:.1f} МБ")

    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="viewerctl",
        description="Утилита для управления хостингом 3D-моделей"
    )

    subparsers = parser.add_subparsers(dest="command", help="Доступные команды")

    # invite
    invite_parser = subparsers.add_parser("invite", help="Управление приглашениями")
    invite_subparsers = invite_parser.add_subparsers(dest="invite_command", help="Команды приглашений")

    invite_add_parser = invite_subparsers.add_parser("add", help="Добавить приглашение")
    invite_add_parser.add_argument("label", help="Метка приглашения (не более 60 символов)")
    invite_add_parser.set_defaults(func=cmd_invite_add)

    invite_list_parser = invite_subparsers.add_parser("list", help="Список приглашений")
    invite_list_parser.set_defaults(func=cmd_invite_list)

    invite_revoke_parser = invite_subparsers.add_parser("revoke", help="Отозвать приглашение")
    invite_revoke_parser.add_argument("id", type=int, help="ID приглашения")
    invite_revoke_parser.add_argument("--purge", action="store_true", help="Удалить связанные модели")
    invite_revoke_parser.set_defaults(func=cmd_invite_revoke)

    invite_export_parser = invite_subparsers.add_parser("export", help="Экспорт приглашений в JSON")
    invite_export_parser.set_defaults(func=cmd_invite_export)

    # models
    models_parser = subparsers.add_parser("models", help="Управление моделями")
    models_subparsers = models_parser.add_subparsers(dest="models_command", help="Команды моделей")

    models_list_parser = models_subparsers.add_parser("list", help="Список моделей")
    models_list_parser.add_argument("--invite", type=int, help="Фильтр по ID приглашения")
    models_list_parser.set_defaults(func=cmd_models_list)

    models_delete_parser = models_subparsers.add_parser("delete", help="Удалить модель")
    models_delete_parser.add_argument("model_id", help="ID модели")
    models_delete_parser.set_defaults(func=cmd_models_delete)

    # stats
    stats_parser = subparsers.add_parser("stats", help="Статистика системы")
    stats_parser.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
