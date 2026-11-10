**META_AUDITED_172 — compatibility-pass, 13 октября 2026**

Demo-версия создана отдельно от `/workspace/META-audit/META_FINAL_CLEAN`.
Исходный проект и его `diploma.db` не изменены. Backend-код и production security
совпадают с предыдущим audited backend; auth bypass не добавлен.

**Точный ремонт данных**

Перед ремонтом создан побайтовый backup `backups/diploma_before_compat.db`.
Сохранены все записи, IDs, пользователи, темы, приказы, уведомления и checkpoints.

| Таблица / ID | До | После |
|---|---|---|
| applications / 6 | CONFIRMED_BY_HEAD, student=2, topic=2, priority=1 | REJECTED; добавлен repair reason; остальные поля, включая priority и timestamps, сохранены |
| applications / 2 | APPROVED, student=2, topic=1 | Без изменений: эту тему подтверждает подписанный OfficialOrder / OrderItem |
| topics / 1 | quota_used=5, quota_total=5 | 202 / 202 |
| topics / 3 | 5 / 5 | 22 / 22 |
| topics / 4 | 4 / 4 | 18 / 18 |
| topics / 5 | 5 / 5 | 28 / 28 |
| topics / 2 | 4 / 4 при пяти assigned applications | Значения остались 4 / 4; после отклонения дубля №6 assigned applications также четыре |

Увеличение quota_total четырех тем до 202, 22, 18 и 28 явно согласовано пользователем.
Одна исправленная заявка одновременно устранила duplicate assigned status и duplicate active priority.
Счетчики всех шести topics согласованы с назначениями и не превышают quota_total.

Для `checkpoints.student_id → users.id` добавлен реальный SQLite FK. Только эта таблица
пересоздана в offline-транзакции с точным копированием всех 2836 строк, колонок, IDs
и исходного индекса. Связи всех 71 checkpoint_submissions сохранились. Добавлены
девять unique indexes из audited backend schema validation. Прочие таблицы не пересоздавались.

Сохранены counts: users 795; applications 551; topics 6; checkpoints 2836;
checkpoint_submissions 71; notifications 806; official_orders 1; order_items 274;
preferences 530; sessions 2649; audit_logs 3908; custom_topic_proposals 0.
Помимо application №6 и topics №1/3/4/5 никаких значений данных не изменено.
Независимая read-only проверка подтвердила эти различия.

`PRAGMA integrity_check = ok`, `PRAGMA foreign_key_check = []`.
Backend `migrate_database()` успешно проходит на repaired DB и не меняет ее SHA-256.

**SHA-256 перед первым demo-запуском**

- Оригинальная DB и backup:
  `60b5546d158030339e0efd5de5915005b95cdeda3a3a91d764c1afd93339de38`
- Repaired DB:
  `010b7c13cd9982863aafc929b308fe661d36dd6a1cdabebeca63a64f7a3694e3`
- Подписанный PDF ORDER-2026-01, неизменный и совпадающий с document_hash:
  `60d66d709ad00c596b5d512ab4b212e6f45d727ab9e41d24ba1094fe314e10ad`

После обычных пользовательских login/add/update DB SHA естественно изменится из-за
sessions/audit и других операций. Для compatibility-smoke использовалась дополнительная
validation-копия, поэтому packaged repaired DB сохраняет указанный SHA и только пять
исправленных строк данных.

**Измененные / добавленные файлы относительно audited версии**

- Изменены только `diploma.db` и `index.html`.
- В `index.html` две checkpoint-download ссылки student/teacher используют существующий
  authenticated fetch: Bearer из текущего meta_token, затем Blob и download. Из Content-Disposition
  читается оригинальное имя файла, включая UTF-8. Дизайн, тексты, страницы и остальные flows не менялись.
- Добавлены `repair_demo_database.py` и `DB_REPAIR_REPORT.json` для воспроизводимого,
  проверяемого ремонта копии. Ремонт уже выполнен; повторно запускать script для demo не требуется.
- Добавлены `test_database_compat.py` и `test_download_compat.py`, backup, данный отчет
  и результаты проверок. `main.py`, requirements и production config не изменены.

**Проверки**

Полный pytest: **198 passed, 0 failed, 0 errors, 0 skipped**. Сохранены 172 предыдущие
проверки; добавлены 20 JS execution regression-тестов downloads и 6 проверок actual DB repair.
Исходные frontend-тесты также выполнены. JS тесты исполняют реальные функции из index.html
с mock DOM/fetch через Node, включая текущий Bearer, Blob, UTF-8 filename, 401/403/404/500,
ошибку сети и отсутствие token; это не браузерный E2E.

Repair tests проверяют точечные изменения, сохранение всех rows/IDs, actual FK enforcement,
child references, реальные backend schema checks, rollback при неподходящих observations,
защиту от ремонта исходного каталога/повторного запуска и hash подписанного PDF.

Actual uvicorn smoke: **11 проверок PASS**, результаты в `DEMO_SMOKE_RESULTS.json`.
Проверены startup на побайтовой копии repaired DB, отсутствие изменений от seed,
login student/teacher/head и auth/me, checkpoint download для student/teacher (200),
anonymous download (401), protected OfficialOrder DOCX/PDF (200), student export (403),
PDF SHA = header = database hash, Gale–Shapley dry-run без изменений DB.
QR извлечен из настоящего 19-page PDF и декодирован через OpenCV:
`http://localhost:8000/verify/ORDER-2026-01`; public verify возвращает 200,
registered status и правильный SHA. Validation-server после проверок остановлен.
Выполненные login создают sessions только в дополнительной validation-копии.
Канонический repaired DB не запускался для mutable HTTP smoke.

Для полного pytest нужен Python с requirements и Node 18+ для JS execution checks.
Node не нужен для самого demo-приложения. OpenCV/pypdf использовались только для
независимого декодирования QR в validation-среде и не добавлялись в production requirements.

**PowerShell: установка и запуск**

Из каталога, содержащего распакованную папку:

```powershell
Set-Location .\META_AUDITED_172
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Точная команда запуска из `META_AUDITED_172`:

```powershell
.\.venv\Scripts\python.exe -c "import os, uvicorn; os.environ.update(META_SEED_DEMO='1', DATABASE_URL='sqlite:///./diploma.db', BASE_URL='http://localhost:8000', CORS_ALLOWED_ORIGINS='http://localhost:8000,http://127.0.0.1:8000', UPLOADS_DIR=os.path.abspath('uploads')); uvicorn.run('main:app', host='127.0.0.1', port=8000)"
```

Demo flag устанавливается только внутри дочернего Python-процесса. Environment самого PowerShell не изменяется.
В `.env`, backend, Docker Compose или постоянные production settings он не добавлен.
Demo-аккаунты: `student@test.com`, `teacher@test.com`, `head@test.com`; существующий
demo-пароль — `123456`, как в исходном README. Вне demo-режима audited запрет известного
привилегированного demo-пароля и старых demo-токенов остается активен.

Windows-команда приведена для PowerShell и Python 3.12; фактические startup/pytest проверки
выполнены в Linux cloud environment. Старые production-риски из BACKEND_AUDIT.md, не связанные
с данным compatibility-pass, не превращают эту локальную demo-версию в production deployment.

**READY FOR DIPLOMA DEMO: YES**
