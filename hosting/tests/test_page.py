"""Тесты модуля hosting_app.page (Python 3.12, только стандартная библиотека)."""

import os
import re
import time
from datetime import datetime, timezone

import pytest

from hosting_app import config, page


def test_js_escape_hostile_string():
    """Проверяем, что js_escape нейтрализует опасные символы и экранирует их."""
    hostile = (
        "'"
        + '"'
        + "`"
        + "</script><img src=x onerror=alert(1)>&"
        + "`"
        + "\n"
        + "\u2028"
        + "\u2029"
    )
    result = page.js_escape(hostile)

    # Запрещённые «сырые» символы не должны встречаться в результате
    forbidden_raw = ["<", ">", "&", "\n", "\r", "\t", "\u2028", "\u2029"]
    for ch in forbidden_raw:
        assert ch not in result, f"Символ {ch!r} не должен встречаться в js_escape"

    # Каждый обратный слэш должен начинать допустимую последовательность
    # (шестисимвольные \uXXXX разбираются как целое, а не парой символов)
    escape_re = re.compile(r"\\(?:[\\'\"`nrt]|u(?:2028|2029|003c|003e|0026))")
    # Ожидаемый результат целиком: отклонение словаря экранирований краснит тест
    expected = (
        "\\'" + '\\"' + "\\`"
        + "\\u003c/script\\u003e\\u003cimg src=x onerror=alert(1)\\u003e\\u0026"
        + "\\`" + "\\n" + "\\u2028" + "\\u2029"
    )
    assert result == expected
    # После вырезания допустимых последовательностей не остаётся ни \, ни кавычек
    rest = escape_re.sub("", result)
    for ch in ("\\", "'", '"', "`"):
        assert ch not in rest, f"Неэкранированный символ {ch!r}: {rest!r}"


def test_fill_template_escaping_and_single_pass():
    """Проверяем экранирование HTML/JS и однократную подстановку."""
    fragment = "<h1>{{TITLE}}</h1><p>{{SUBTITLE}}</p><script>x='{{MODEL_NAME}}'</script>"
    title = 'A<b>"'
    subtitle = "s&t"

    result = page.fill_template(fragment, title, subtitle)

    # HTML-поля экранированы
    assert "A&lt;b&gt;&quot;" in result
    assert "s&amp;t" in result

    # JS-поле содержит результат js_escape
    expected_js = page.js_escape(title)
    assert f"x='{expected_js}'" in result

    # Однократная подстановка: заголовок {{SUBTITLE}} остаётся литералом
    fragment2 = "<h1>{{TITLE}}</h1><p>{{SUBTITLE}}</p>"
    result2 = page.fill_template(fragment2, "{{SUBTITLE}}", "REAL_SUB")
    assert "{{SUBTITLE}}" in result2
    assert "REAL_SUB" not in result2.split("<p>")[0]


def test_clean_title():
    """Проверяем очистку заголовка: управляющие символы, пробелы, длина, fallback."""
    # Управляющие символы удаляются
    assert page.clean_title("a\x00b\x07c") == "abc"

    # Пробельные последовательности схлопываются
    assert page.clean_title("  a \t\n b  ") == "a b"

    # Обрезка до 80 символов
    long_title = "x" * 200
    assert len(page.clean_title(long_title)) == 80

    # Пустая строка -> fallback
    assert page.clean_title("") == "Модель"
    assert page.clean_title("   \t\n  ") == "Модель"

    # Пользовательский fallback
    assert page.clean_title("", fallback="Заголовок") == "Заголовок"


def test_localize_vendor():
    """Проверяем локализацию vendor-ресурсов и удаление Google Fonts."""
    sample = (
        '<script type="importmap">'
        '{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",'
        '"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}'
        "</script>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Roboto&display=swap" rel="stylesheet">'
        '<link rel="stylesheet" href="/local/style.css">'
    )

    result = page.localize_vendor(sample)

    # Путь к three заменён на локальный
    assert "/vendor/three/build/three.module.js" in result
    assert "/vendor/three/examples/jsm/" in result

    # Google Fonts удалены
    assert "fonts.googleapis.com" not in result
    assert "fonts.gstatic.com" not in result

    # Никаких https:// не осталось
    assert "https://" not in result

    # Локальный stylesheet сохранён
    assert '/local/style.css' in result

    # Идемпотентность
    result2 = page.localize_vendor(result)
    assert result2 == result


def test_wrap_document():
    """Проверяем сборку полного HTML-документа."""
    fragment = "<title>Тест</title><h1>{{TITLE}}</h1>"
    expires_ts = int(datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp())

    result = page.wrap_document(fragment, "Заголовок", expires_ts, fix_prc=False)

    assert result.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in result
    assert "noindex" in result
    assert "Доступно до 15.11.2026" in result
    assert config.SOURCE_URL in result
    assert "window.FIX_PRC_MESH=true" not in result

    result_prc = page.wrap_document(fragment, "Заголовок", expires_ts, fix_prc=True)
    assert "window.FIX_PRC_MESH=true" in result_prc


def test_wrap_document_with_real_template():
    """Интеграционный тест с реальным шаблоном viewer-template.html."""
    template_path = os.path.join(config.ASSETS, "viewer-template.html")
    if not os.path.exists(template_path):
        pytest.skip("Шаблон viewer-template.html не найден")

    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    hostile_title = "</script><img src=x onerror=alert(1)>'\"`"
    expires_ts = int(datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp())

    result = page.wrap_document(template, hostile_title, expires_ts, fix_prc=False)

    # Опасная подстрока не должна присутствовать в сыром виде
    assert "<img src=x" not in result

    # В шаблоне нет <img>; заголовок попал в страницу только в экранированном виде
    assert "&lt;img src=x" in result or "\\u003cimg src=x" in result

    # Внешние CDN и Google Fonts отсутствуют
    assert "https://cdn.jsdelivr.net" not in result
    assert "fonts.googleapis.com" not in result

    # Количество </script> равно количеству в шаблоне (без fix_prc wrapper не добавляет)
    template_script_count = template.count("</script>")
    result_script_count = result.count("</script>")
    assert result_script_count == template_script_count

    # С fix_prc=True wrapper добавляет ровно один </script>
    result_prc = page.wrap_document(template, hostile_title, expires_ts, fix_prc=True)
    assert result_prc.count("</script>") == template_script_count + 1
