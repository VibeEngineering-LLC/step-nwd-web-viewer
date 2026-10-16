# Грабли и проверки

## Конвертация STEP / PRC

| Симптом | Причина | Обход |
|---|---|---|
| GLB 240 байт, `export_gltf` вернул `True` | `build123d.export_gltf` на импортированной сборке строит пустой XCAF-документ (0 узлов) | путь OCP: `STEPCAFControl_Reader` → `BRepMesh_IncrementalMesh` → `RWGltf_CafWriter` (`scripts/step2glb.py`) |
| узлы GLB называются `NAUO1…NAUO24` | по умолчанию писатель берёт имена экземпляров | `SetNodeNameFormat/SetMeshNameFormat(RWMesh_NameFormat_ProductOrInstance)` |
| в GLB 0 материалов | в STEP из ряда CAD нет цветов | красить во вьюере (`TINTS`) |
| 3D-PDF в просмотрщике пустой, `/Subtype/PRC` | 3D показывает только Adobe Acrobat Reader | STEP от автора модели либо `scripts/prc/prc2glb.py` |
| в PRC нет обычной сетки (тип 172), только тип 173 | CAD кладёт сжатую тесселяцию; открытого декодера нет | брать точный B-rep (тип 156); сначала `geom_census.py` — что в каких секциях |
| `FileNotFoundError … vendor\PRCdouble.cc` | `prc_bits.py` читает таблицу из соседней `vendor/` | копировать скрипты PRC вместе с `vendor/` |
| модель из PRC во вьюере чёрная | грани сетки развёрнуты | `window.FIX_PRC_MESH=true` до скрипта вьюера |
| грань построена без ошибок, но площадь сетки 20 % от B-rep | грани с отверстиями невалидны | сверять площадь сетки с площадью B-rep, перестраивать контуры (`hcg_fix.py`) |
| в составе меньше деталей, чем в GLB | одинаковые детали с суффиксами `-1…-6` сливаются в строки «×N» (не проверено) | при приёмке сверять счётчик с числом листовых мешей GLB |

Проверка GLB (узлы, меши, материалы, вершины):

```bash
python -c "
import json,struct,sys;sys.stdout.reconfigure(encoding='utf-8')
b=open(sys.argv[1],'rb').read();n=struct.unpack('<I',b[12:16])[0];j=json.loads(b[20:20+n])
print('nodes',len(j['nodes']),'meshes',len(j['meshes']),'materials',len(j.get('materials',[])))
print([x.get('name') for x in j['nodes']])
print('verts',sum(j['accessors'][p['attributes']['POSITION']]['count'] for m in j['meshes'] for p in m['primitives']))" model.glb
```

## Вьюер

| Дефект | Как проявлялся | Исправление в шаблоне |
|---|---|---|
| деталью считалась каждая грань | число деталей в сотни раз больше реального | `isPart`: дочерний `Mesh` не отменяет группу |
| имена с хвостами `2`, `3` | GLTFLoader заменяет пробелы на `_` и добавляет суффиксы | исходное имя узла из `gltf.parser.associations` → `userData.cadName` |
| выбор щелчком падал | материал в `userData` → циклическая ссылка | исходный/подсвеченный материал в `WeakMap` |
| разнесение одинаковое для всех | `transformDirection` нормирует вектор | целевая мировая точка → `parent.worldToLocal` |
| сечение падало | `Vector3.setComponent('x', …)` требует индекс | `constant = -n[axis] * pos` |
| кнопки видны без выбора | нет правила `[hidden]` | `[hidden]{display:none!important}` |

## Страница

- Без `<meta charset="utf-8">` кириллица превращается в «кракозябры» — оборачивайте шаблон в полный документ.
- `file://` для `fetch('model.glb')` не работает — проверяйте через `python -m http.server`.
- Большой base64 в начале страницы выталкивает `<title>` за первые 8 КБ — ставьте его в конец.

## NWD

| Симптом | Причина | Мера |
|---|---|---|
| стены в виде «игл», счёт треугольников сошёлся | индексы групп не сдвигались на накопленные вершины | `check_area.py` — площадь по источникам, допуск 0,1 % |
| цвет блёклый | цвета читались как линейные + засветка окружением | `convertSRGBToLinear()`, `envMapIntensity 0.45`, `roughness 0.9` |
| воздуховоды белые, цвет виден только в разрезе | белая изоляция закрывает воздуховод | цвет по `IfcSystem`, изоляция полупрозрачная |
| список состава не сворачивался | CSS по неверному селектору | `.children{…}`, `.row.collapsed + .children{display:none}` |
| клик по стрелке выбирал всю группу | обработчик выбора срабатывал раньше стрелки | проверка `e.target.classList.contains('caret')` |
| метки систем в дереве не появлялись | у частей групп `meshes` пуст до `finalize` | искать меш обходом `object.traverse` |
