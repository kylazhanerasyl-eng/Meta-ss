**Backend-аудит META — 12 октября 2026**

Исходник: `META_FINAL_CLEAN (15).zip`. Рабочий проект: `META_FINAL_CLEAN`.
Проверены auth/sessions/RBAC/IDOR, WebSocket, CORS, uploads, SQLite/SQLAlchemy,
квоты и согласование заявок, Gale–Shapley, OfficialOrder/OrderItem, DOCX/PDF/QR/SHA-256,
обработка ошибок и транзакции. Новые продуктовые возможности не добавлялись.

**Результат проверок**

Полный запуск `python -m pytest -q`: **172 passed, 0 failed, 0 errors, 0 skipped**.
Всего сохранены 76 исходных тестов и добавлены 96 regression-проверок с учетом параметризации.
Запуск занял около 32 секунд. 1658 предупреждений преимущественно связаны с использованием
`datetime.utcnow()` в приложении, тестах и SQLAlchemy; это предупреждения, не ошибки тестов.
Точная команда финального запуска:

```sh
python -m pytest -q --junitxml=../pytest-results.xml
```

Среда проверки: Python 3.12.14, FastAPI 0.143.0, SQLAlchemy 2.1.4,
Pydantic 2.14.1, pytest 9.1.1, httpx 0.28.1, python-docx 1.2.0, ReportLab 5.0.1.
Зависимости устанавливались из `requirements.txt` в отдельный venv.

На исходном backend полный запуск дал **75 passed, 1 failed**:
`test_full_autopilot_e2e` выбирал тему с доступной квотой, но старым `status='full'`.
Это подтверждает несогласованность данных архива. После введения независимых временных
fixtures тесты проверяют бизнес-процессы на контролируемом наборе данных.
Дополнительно 16 выбранных новых regression-тестов запущены на исходном `main.py`:
**16 failed**, то есть они воспроизводят обходы auth/IDOR, изменение GPA и перезапись
подтвержденного назначения распределением. Это отдельный ожидаемо падающий запуск,
не результат финального pytest.

**Что найдено и исправлено в коде**

| Риск | Уровень | Исходная проблема | Исправление и доказательство |
|---|---|---|---|
| Анонимные изменения | CRITICAL | `student_id` позволял без сессии подавать заявки и менять чужие приоритеты; review/done/upload также допускали отсутствие сессии. | Обязательная сессия и роли, запрет подмены actor ID; parametrized auth tests и проверки отсутствия side effects. |
| IDOR и утечка данных | HIGH | Чужие заявки, рекомендации, приоритеты, отчеты, версии и файлы читались без ownership-проверки. | Студент видит свои данные, преподаватель — данные своих назначенных студентов, head/admin — разрешенную область. List endpoints фильтруют результаты. |
| Подмена руководителя/компании | HIGH | Имя руководителя проверялось через substring; company API доверял ID в запросе и не требовал сессии. | Проверка по FK/ID, сессия компании, связь ее тем по ID; тест имени чужого преподавателя и company spoofing. |
| Демонстрационные привилегии | CRITICAL | Startup создавал известные привилегированные аккаунты, мог восстановить известный пароль и активировать отключенного пользователя. | Seed только при явном `META_SEED_DEMO=1`; существующие credentials/disabled state сохраняются. Известный demo-пароль и старые привилегированные demo-токены запрещены вне demo-режима. |
| Манипуляция академическим рейтингом | HIGH | Публичный signup позволял самому указать GPA, влияющий на распределение мест. | Signup создает нейтральный рейтинг 3.5; supplied GPA/rating не повышает рейтинг. Неверные и неограниченные значения отклоняются. |
| WebSocket lifetime | HIGH | Сессия проверялась один раз, SQLAlchemy connection удерживался всю жизнь соединения; private события рассылались всем. | Короткие DB-сессии, повторная проверка каждые 30 секунд/при сообщении/перед отправкой, cleanup в finally, origin-check и адресные private события. |
| Гонки approvals | HIGH | Атомарный increment квоты не защищал от повторного approve той же заявки и двух тем одного студента при конкурентных запросах. | SQLite `BEGIN IMMEDIATE` до auth/business reads, единая транзакция, частичные unique indexes. Barrier-тесты: одна заявка, два разных topic одного студента, три кандидата на последнее место. |
| Частичные коммиты | HIGH | Заявка/квота/custom-topic могли сохраниться до провала последующих шагов; helpers самостоятельно commit и подавляли ошибки. | Helpers flush без скрытого commit; rollback сохраняет согласованность. Fault injection проверяет approval, регистрацию, custom approval и upload. Commit происходит до успешного HTTP-ответа через function-scoped dependencies. |
| Дубликаты и quota release | HIGH | REJECT мог вставить уже существующий fallback и не освобождал использованную квоту; повторные переходы меняли финальное состояние. | Fallback не дублируется, quota освобождается один раз, REJECTED/CONFIRMED не одобряются повторно. DB-инвариант запрещает второе назначение. |
| Инициативные темы | HIGH | Head мог обойти supervisor approval, повторный head approval создавал новые topic/application; возможны две темы одного студента. | Переход только после SUPERVISOR_APPROVED, контроль owner/role/статуса, single transaction, запрет повторного решения и второго назначения. |
| Gale–Shapley | HIGH | Перезаписывал уже утвержденные заявки, обнулял/пересчитывал квоты без учета занятых мест, создавал APPROVED без teacher approval; capacity=0 приводила к ошибке. | Фиксированные назначения сохраняются, доступная capacity учитывает занятые места; результат записывается как ALLOCATED. Обязательное approval руководителя перед confirm-all. Проверены dry-run без DB-изменений, apply, повторный запуск, полная тема и сохранение исходного rank. |
| confirm-all | HIGH | Через APPROVED, созданный алгоритмом/custom workflow, можно было косвенно обойти согласование преподавателя. | ALLOCATED и SUBMITTED исключены; проверки conflicts до изменений. Новые тесты проводят весь путь allocation → teacher approve → confirmation и проверяют отсутствие повторного increment. |
| OfficialOrder/OrderItem | HIGH | Подписанный приказ пересобирался из живых заявок, включал teacher-only APPROVED; пустые строки заменялись вымышленными тестовыми назначениями. | SIGNED/ACTIVE snapshots неизменяемы; DRAFT строится только из CONFIRMED_BY_HEAD своего academic_year, без fabricated fallback. Unique(order,student), корректный 404 для неизвестного приказа. |
| PDF integrity | HIGH | Подмененный PDF автоматически пересоздавался с новым trusted hash; verify мог показывать старый hash без проверки файла. | Проверка настоящих байтов SHA-256. При missing/tampered signed PDF — 409, без перезаписи оригинала/hash. Public verify не подтверждает поврежденный файл. |
| HTML/PDF markup injection | HIGH | Пользовательские поля попадали без escaping в HTML и ReportLab Paragraph; injected img мог инициировать чтение file/URL. | Text escaping dynamic полей; regression на script/img и file URL. Проверены actual PDF generation, текущий QR target и SHA-256 сохраненных байтов. |
| Backup/restore | HIGH | Backup отдавал live .db, restore записывал произвольные байты поверх открытого SQLite-файла. | SQLite backup snapshots; validation SQLite/header/schema/FKs/invariants до restore; SQLite backup protocol вместо замены live inode. Старые session tokens в восстановленной копии инвалидируются. Invalid restore не меняет DB. |
| CORS | MEDIUM | Wildcard origins сочетались с credentials. | Явный список origins, credentials=False для Bearer-сессий, разрешенные headers и expose headers документов. Unknown origin отклоняется preflight-тестом. |
| Uploads / DOCX | MEDIUM | Ограничивался compressed размер DOCX, но не распакованный объем; не проверялись PDF/DOCX signatures, realpath/symlink и orphan cleanup. | Проверки content/container/20 MiB upload/15 MiB DOCX/64 MiB expanded DOCX/2048 ZIP entries; realpath confinement; attachment+nosniff; cleanup собственного файла при rollback. |
| Миграции / exception handling | HIGH | Автоматические DROP/rebuild таблиц и broad except скрывали сбои; существующие ограничения могли отличаться от models. | Только additive schema/index changes; missing FK и legacy conflicts требуют отдельного ремонта. В business helpers ошибки не подавляются. Failed-login audit сохраняется отдельным завершенным коммитом. |

**Измененные файлы**

- `main.py`: перечисленные security и integrity исправления.
- `requirements.txt`: FastAPI minimum повышен с 0.100.0 до 0.121.0, поскольку нужен поддерживаемый `Depends(..., scope="function")` для commit/error до отправки ответа.
- `test_main.py`: существующие проверки отчетов/download теперь используют Bearer и настоящую supervisory relationship. Assertions безопасности не отключались.
- `.github/workflows/ci.yml`: запускается полный `pytest -v`, включая новые regression и исходные suites.
- Добавлены `conftest.py`, `test_backend_audit.py`, этот отчет.

`index.html` и прочие frontend-файлы не менялись. `diploma.db`, `diploma_OLD_BACKUP.db`,
исходные uploads, fonts и другие исходные файлы сверены с SHA-256 архива и сохранены.
Все delete/reset/restore в тестах выполнялись исключительно на временных SQLite fixtures.
Рабочая база пользователя не использовалась для pytest и не удалялась.

**Какие regression-тесты добавлены**

`test_backend_audit.py`: 96 проверок с учетом параметризации. Группы:

- 13 protected read paths и 5 anonymous mutations; отсутствие upload side effects.
- Student IDOR, teacher ownership по ID, company identity, scope фильтров списков, private WebSocket events.
- Default privileged passwords/tokens, disabled/expired/wrong-user/origin WebSocket sessions, connection release/revocation, upgrade legacy hashes, сохранение disabled user при seed.
- GPA tampering, invalid registration/application/preferences, unknown records и корректные HTTP ошибки.
- Настоящие concurrent duplicate application/double approval/two topics/last quota place; database-level invariant и rollback fault injection.
- Custom supervisor/head workflow, repeated decision, second topic prevention и atomic creation.
- GS dry-run/apply/repeat, no teacher bypass, сохранение confirmed topic и full capacity, сохранение rank после rejected preference.
- Signed order immutability, confirmed-only draft rows, unknown-order 404 без создания новых записей, actual PDF/QR/hash, missing/tampered PDF, HTML и ReportLab escaping.
- Upload signatures, размера/ZIP bomb, orphan cleanup, symlink escape, unsubmitted checkpoint review.
- Consistent backup, invalid/valid restore, invalidation restored sessions, durability failed-login audit, exception connection cleanup и commit error до HTTP success.
- Отказ неразрушающей миграции при legacy duplicates/missing FK без удаления исходных строк.

Полный перечень функций тестов приведен в `test_backend_audit.py`; JUnit и полный лог
находятся рядом с архивом исправленного проекта.

**Оставшиеся production-риски и ограничения**

1. **Старая БД требует отдельной проверенной работы с данными и схемой.** Read-only проверка
   присланной `diploma.db` выявила один student с несколькими assigned statuses, одну группу
   повторного active priority, несовпадение quota_used с назначениями у пяти topics и отсутствие
   реального FK `checkpoints.student_id → users.id`. SQLite integrity_check=ok и текущий
   foreign_key_check без violations не доказывают наличие этого отсутствующего FK.
   На отдельной копии этой же БД запуск новой миграции подтвержденно останавливается с
   `Missing foreign keys in checkpoints; offline reviewed migration required`; байты копии
   при этом также остались неизменны. Оригинал намеренно не исправлялся и не переписывался.
   **Присланный production dataset сейчас нельзя считать готовым к запуску нового backend.**
2. **Frontend downloads.** В неизмененном `index.html` ссылки отчетов используют обычный
   `<a href>` и не передают Authorization. После закрытия анонимного доступа такие downloads
   возвращают 401. Потребуется отдельная адаптация клиента для authenticated download;
   auth bypass обратно не включается.
3. **Credentials/secrets.** Существующие demo-пароли в БД не изменены; вне явного demo-режима
   известный привилегированный пароль/токены заблокированы. Перед production нужно сменить
   demo-пароли и отозвать старые сессии. Токены по-прежнему хранятся plaintext в server-side DB,
   PBKDF2 использует 100k iterations, rate limiting и отдельный logout/revoke API отсутствуют.
   Их полноценное внедрение требует отдельной задачи; новых продуктовых возможностей здесь нет.
4. **Документы и хранение.** Существующий SIGNED приказ является историческим snapshot и больше
   не дополняется новыми подтверждениями. Выпуск следующего приказа требует отдельного утвержденного
   workflow. SHA-256/QR подтверждает связь с локальным реестром и файлом, не юридическую ЭЦП.
   Signed PDF нельзя автоматически пересоздавать; хранить/backup нужно DB вместе с uploads.
   Текущий docker-compose персистит только DB volume, не uploads — это риск потери файлов при redeploy.
5. **Конфигурация deployment.** Настроить BASE_URL и CORS_ALLOWED_ORIGINS на реальные public origins,
   включая origin WebSocket; default localhost не является production адресом. META_SEED_DEMO=1
   предназначен только для изолированного demo/test. Reverse proxy должен ограничивать body size,
   частоту login/upload/norm-control и публичных вычислений. Отдельная malware-проверка загружаемых
   архивов не добавлялась.
6. **Границы проверки.** Зависимости кроме необходимого FastAPI minimum остаются с открытыми
   диапазонами; production желательно фиксировать проверенные версии. Проверены SQLite и текущая
   Python 3.12 среда, не PostgreSQL, multi-worker нагрузка, браузерный E2E или юридический процесс
   подписания. 172 passing tests подтверждают описанные сценарии на временных fixtures, не ремонт
   исходной БД и не отсутствие всех потенциальных уязвимостей.
