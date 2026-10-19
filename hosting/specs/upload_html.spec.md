Write ONE complete self-contained HTML5 file `upload.html` (no external resources at all: no CDN, no web fonts, no images; no markdown fences, no explanations, output ONLY the HTML). Russian UI. System font stack `system-ui, "Segoe UI", sans-serif`. Light/dark via `prefers-color-scheme` with CSS variables on `:root`; body has an explicit background; layout works at 375 px width with 16 px side padding and no horizontal scroll; text blocks left-aligned (never `text-align: justify`), `max-width: 72ch`.

## Page content
- `<title>3D-просмотр: загрузка модели</title>`, `<h1>Загрузите модель — получите ссылку на просмотр</h1>`, `<meta name="robots" content="noindex,nofollow">`, `<meta name="referrer" content="no-referrer">`.
- Short note (3 lines): accepted files — STEP/STP, 3D-PDF (с моделью PRC), NWD, GLB; limits are loaded from the server; the source file is deleted right after conversion; the page is stored N days and can be extended; **names of parts and sources inside the model remain visible to viewers**.
- Upload block: a drop zone (dashed border, keyboard-focusable, click opens a hidden `<input type="file" accept=".step,.stp,.pdf,.nwd,.glb">`), a text input `Название` (maxlength 80, optional), a button `Загрузить`, a progress bar `<progress>` plus text status, an error line with `role="alert"`.
- Section `Мои модели` — list refreshed every 3 seconds while any model is `queued` or `running`, otherwise every 30 seconds. Each item shows: title (textContent), kind, status in Russian (`в очереди`, `обрабатывается`, `готово`, `ошибка`), error text if any, `действует до ДД.ММ.ГГГГ`; for `done`: link `Открыть` (opens `url` in a new tab with `rel="noopener noreferrer"`), button `Копировать ссылку`, button `Продлить` (only if `can_extend`), button `Удалить` (asks `confirm`).
- Footer: `Исходный код сервиса` link to `https://github.com/VibeEngineering-LLC/step-nwd-web-viewer` (`rel="noopener noreferrer"`).

## Behavior (vanilla JS, one `<script>` at the end, no modules, no dependencies)
- `token = location.pathname.split('/')[2]`. API base `'/api/u/' + token`.
- Load `GET base + '/models'` -> `{models, limits}`; show limits (max MB per kind, retention days) in the note.
- Upload: validate extension client-side against the allowed list, then `XMLHttpRequest` `POST base + '/upload?name=' + encodeURIComponent(file.name) + '&title=' + encodeURIComponent(title)` with the `File` object as the request body (raw bytes, NOT FormData), `xhr.upload.onprogress` updates the progress bar; on 202 clear the form and refresh the list; on any other status show `JSON.parse(responseText).error` (fallback `Ошибка загрузки (код N)`); disable the button while uploading.
- ALL dynamic text is inserted with `textContent` / DOM methods, never `innerHTML` (titles come from users).
- Copy link: `navigator.clipboard.writeText(url)` in try/catch, fallback `window.prompt('Скопируйте ссылку', url)`; the button text changes to `Скопировано` for 1.5 s.
- Extend: `POST base + '/models/' + id + '/extend'`; Delete: `DELETE base + '/models/' + id`; both then refresh the list; errors shown in the error line.
- If `GET /models` returns 404 show `Ссылка недействительна` and hide the upload block.
