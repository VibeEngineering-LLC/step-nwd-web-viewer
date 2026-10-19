"""Модуль для сборки безопасного HTML-документа из шаблона просмотрщика.

Импортируется внутри пакета `hosting_app` как `from . import config`.
Используются только стандартные библиотеки: html, json, re, time, datetime.
"""

import html
import json
import re
import time
from datetime import datetime, timezone

from . import config

# Регулярное выражение для поиска плейсхолдеров в шаблоне
_PLACEHOLDER_RE = re.compile(r"\{\{(TITLE|SUBTITLE|MODEL_NAME)\}\}")

# Регулярное выражение для удаления внешних ссылок на шрифты Google
_GOOGLE_FONTS_LINK_RE = re.compile(
    r'<link\b[^>]*href\s*=\s*["\']https://fonts\.(googleapis|gstatic)\.com[^"\']*["\'][^>]*>',
    re.IGNORECASE
)

# Регулярное выражение для удаления управляющих символов (Cc и Cf)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")

# Регулярное выражение для схлопывания пробельных символов
_WHITESPACE_RE = re.compile(r"\s+")


def html_escape(s):
    """Экранирует строку для безопасного вставления в HTML.

    :param s: Исходная строка.
    :return: Экранированная строка.
    """
    return html.escape(s, quote=True)


def js_escape(s):
    """Экранирует строку для безопасного вставления в JavaScript-строку,
    заключенную в одинарные кавычки внутри HTML-тега <script>.

    Порядок экранирования важен: сначала обратный слэш, затем остальные символы.
    :param s: Исходная строка.
    :return: Экранированная строка.
    """
    # Сначала экранируем обратный слэш, чтобы не экранировать уже экранированные символы
    s = s.replace("\\", "\\\\")
    # Затем экранируем одинарные кавычки
    s = s.replace("'", "\\'")
    # Затем экранируем двойные кавычки
    s = s.replace('"', '\\"')
    # Затем экранируем обратные кавычки
    s = s.replace("`", "\\`")
    # Затем экранируем переводы строк и табуляцию
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    # Затем экранируем U+2028 и U+2029
    s = s.replace("\u2028", "\\u2028")
    s = s.replace("\u2029", "\\u2029")
    # Затем экранируем HTML-специальные символы, чтобы они не ломали структуру <script>
    s = s.replace("<", "\\u003c")
    s = s.replace(">", "\\u003e")
    s = s.replace("&", "\\u0026")
    return s


def clean_title(raw, fallback="Модель"):
    """Очищает заголовок от управляющих символов и нормализует пробелы.

    :param raw: Исходный заголовок.
    :param fallback: Запасной заголовок, если исходный пустой.
    :return: Очищенный заголовок.
    """
    if not raw:
        return fallback

    # Удаляем управляющие символы (Cc и Cf)
    cleaned = _CONTROL_CHARS_RE.sub("", raw)

    # Схлопываем последовательности пробельных символов в один пробел
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)

    # Убираем пробелы по краям
    cleaned = cleaned.strip()

    # Обрезаем до 80 символов
    cleaned = cleaned[:80]

    # Если после очистки строка пуста, возвращаем запасной вариант
    if not cleaned:
        return fallback

    return cleaned


def fill_template(fragment, title, subtitle):
    """Заполняет плейсхолдеры в шаблоне безопасными значениями.

    Использует один проход замены, чтобы избежать повторной подстановки
    вставленных значений.

    :param fragment: HTML-фрагмент с плейсхолдерами.
    :param title: Заголовок (будет экранирован для JS).
    :param subtitle: Подзаголовок (будет экранирован для HTML).
    :return: Заполненный HTML-фрагмент.
    """
    def _replace(match):
        placeholder = match.group(1)
        if placeholder == "TITLE":
            return html_escape(title)
        elif placeholder == "SUBTITLE":
            return html_escape(subtitle)
        elif placeholder == "MODEL_NAME":
            return js_escape(title)
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, fragment)


def localize_vendor(text):
    """Локализует внешние ресурсы, заменяя CDN-ссылки на локальные пути
    и удаляя ссылки на внешние шрифты.

    :param text: HTML-текст.
    :return: Модифицированный HTML-текст.
    """
    # Заменяем CDN-ссылки на three.js на локальные пути
    text = text.replace(
        "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
        "/vendor/three/build/three.module.js"
    )
    text = text.replace(
        "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/",
        "/vendor/three/examples/jsm/"
    )

    # Удаляем все <link> элементы, ссылающиеся на Google Fonts
    text = _GOOGLE_FONTS_LINK_RE.sub("", text)

    return text


def wrap_document(fragment, title, expires_ts, fix_prc=False, subtitle=""):
    """Собирает полный HTML-документ из фрагмента.

    :param fragment: HTML-фрагмент (без doctype/head/body).
    :param title: Заголовок документа.
    :param expires_ts: Unix-время истечения срока действия.
    :param fix_prc: Флаг для включения исправления PRC-сетки.
    :param subtitle: Подзаголовок.
    :return: Полный HTML-документ.
    """
    # Форматируем дату истечения в формате DD.MM.YYYY (UTC)
    expires_dt = datetime.fromtimestamp(expires_ts, tz=timezone.utc)
    expires_str = expires_dt.strftime("%d.%m.%Y")

    # Экранируем URL исходного кода
    source_url = html_escape(config.SOURCE_URL)

    # Заполняем шаблон и локализуем ресурсы
    processed_fragment = fill_template(fragment, title, subtitle)
    processed_fragment = localize_vendor(processed_fragment)

    # Формируем блок для исправления PRC, если нужно
    fix_prc_script = ""
    if fix_prc:
        fix_prc_script = "<script>window.FIX_PRC_MESH=true;</script>\n"

    # Собираем полный документ
    document = (
        "<!doctype html>\n"
        "<html lang=\"ru\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        "<meta name=\"robots\" content=\"noindex,nofollow\">\n"
        "<meta name=\"referrer\" content=\"no-referrer\">\n"
        + fix_prc_script +
        processed_fragment +
        "\n<div id=\"shareFooter\" style=\"position:fixed;left:8px;bottom:6px;font:11px/1.3 system-ui,sans-serif;opacity:.65;z-index:5;pointer-events:auto\">Доступно до "
        + expires_str +
        " · <a href=\"" + source_url + "\" rel=\"noopener noreferrer\" target=\"_blank\" style=\"color:inherit\">исходный код</a></div>\n"
        "</body>\n"
        "</html>\n"
    )

    return document
