# Сторонние компоненты

| Компонент | Как используется | Лицензия | В репозитории |
|---|---|---|---|
| Asymptote `prc/PRCdouble.cc` (https://github.com/vectorgraphics/asymptote) | таблица частых значений double и экспонент PRC; `scripts/prc/prc_bits.py` читает из неё `_COFDOE` | LGPL-3.0 | да, без изменений: `scripts/prc/vendor/PRCdouble.cc` (см. `vendor/NOTICE.md`) |
| three.js 0.160.0 (https://threejs.org) | вьюер; в одиночных страницах подгружается из CDN, в хостинге (`hosting/`) кладётся в образ (`hosting/vendor.sha256`) | MIT | нет |
| FastAPI, Starlette, pydantic, uvicorn, opentelemetry-api; Caddy 2.9 (`hosting/`) | веб-сервис загрузки и раздача страниц | MIT / BSD / Apache-2.0 | нет, ставятся при сборке образа |
| meshoptimizer (https://github.com/zeux/meshoptimizer) v1.3 | сжатие сеток в GLB (`scripts/nwd/meshopt_glb.py` подгружает собранную `libmeshopt.so` через ctypes) | MIT | нет, собирается в образе из закреплённого коммита |
| Шрифты Golos Text и JetBrains Mono (Google Fonts) | в автономных одиночных страницах подгружаются из CDN Google; в хостинге (`hosting/`) ссылки удаляются, шрифты не поставляются | OFL-1.1 | нет |
| OpenCascade Technology (через `cadquery-ocp`) | чтение STEP, построение граней PRC, сетка | LGPL-2.1 с исключением OCCT | нет, ставится через pip |
| PyMuPDF, trimesh, numpy | извлечение PRC из PDF, сборка сцены, расчёты | AGPL-3.0 / MIT / BSD | нет, ставятся через pip; PyMuPDF — AGPL-3.0: при предоставлении сервиса по сети (`hosting/`) §13 требует предложить пользователям исходный код — ссылка на репозиторий стоит в подвале страницы загрузки |
| nwd-reader (https://github.com/1817716374/nwd-reader) | чтение NWD, линкуется дампером `scripts/nwd/nwd_dump.cpp` | на момент написания **лицензии нет** | **нет**, не распространяется; ставится пользователем после собственной проверки |

Версии и условия лицензий проверяйте по первоисточникам перед распространением: таблица составлена по состоянию на дату публикации и не заменяет юридической проверки.
