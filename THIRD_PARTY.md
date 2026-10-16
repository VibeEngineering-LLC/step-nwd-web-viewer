# Сторонние компоненты

| Компонент | Как используется | Лицензия | В репозитории |
|---|---|---|---|
| Asymptote `prc/PRCdouble.cc` (https://github.com/vectorgraphics/asymptote) | таблица частых значений double и экспонент PRC; `scripts/prc/prc_bits.py` читает из неё `_COFDOE` | LGPL-3.0 | да, без изменений: `scripts/prc/vendor/PRCdouble.cc` (см. `vendor/NOTICE.md`) |
| three.js 0.160.0 (https://threejs.org) | вьюер, подгружается страницей из CDN | MIT | нет |
| OpenCascade Technology (через `cadquery-ocp`) | чтение STEP, построение граней PRC, сетка | LGPL-2.1 с исключением OCCT | нет, ставится через pip |
| PyMuPDF, trimesh, numpy | извлечение PRC из PDF, сборка сцены, расчёты | AGPL-3.0 / MIT / BSD | нет, ставятся через pip; PyMuPDF — AGPL: учтите при распространении собственных производных сервисов |
| nwd-reader (https://github.com/1817716374/nwd-reader) | чтение NWD, линкуется дампером `scripts/nwd/nwd_dump.cpp` | на момент написания **лицензии нет** | **нет**, не распространяется; ставится пользователем после собственной проверки |

Версии и условия лицензий проверяйте по первоисточникам перед распространением: таблица составлена по состоянию на дату публикации и не заменяет юридической проверки.
