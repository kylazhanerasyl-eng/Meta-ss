# DEPLOY.md — Инструкция по развёртыванию META

## Быстрый старт (Production)

### Требования
- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) v2+

### Запуск в 1 команду

```bash
docker compose up -d
```

Платформа будет доступна по адресу: **http://localhost**

---

## Что происходит при запуске

```
[Nginx :80] → обратный прокси → [FastAPI :8000]
                                        ↓
                                  [SQLite DB]
                              (volume: db_data)
```

| Сервис | Образ | Порт | Назначение |
|--------|-------|------|------------|
| `meta_nginx` | nginx:1.25-alpine | **80** (публичный) | Обратный прокси, Gzip, SSL/Security headers |
| `meta_fastapi` | python:3.11-slim | 8000 (внутренний) | FastAPI бэкенд + WebSockets |

---

## Полезные команды

```bash
# Посмотреть логи в реальном времени
docker compose logs -f

# Только логи бэкенда
docker compose logs -f app

# Остановить и удалить контейнеры (данные БД сохранятся!)
docker compose down

# Полный сброс включая данные БД
docker compose down -v

# Пересобрать после изменений в коде
docker compose up -d --build
```

---

## Локальная разработка (без Docker)

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск сервера с автоперезагрузкой
python -m uvicorn main:app --reload --port 8000
```

Сервер доступен по: **http://localhost:8000**

---

## Тестовые аккаунты

| Роль | Email | Пароль |
|------|-------|--------|
| Студент | student@test.com | 123456 |
| Преподаватель | teacher@test.com | 123456 |
| Заведующий кафедрой | head@test.com | 123456 |

---

## Структура проекта

```
.
├── main.py              # FastAPI бэкенд
├── index.html           # SPA фронтенд (Vanilla JS + Tailwind CSS)
├── requirements.txt     # Python зависимости
├── diploma.db           # SQLite база данных (auto-created)
├── Dockerfile           # Docker образ FastAPI
├── nginx.conf           # Конфигурация Nginx
├── docker-compose.yml   # Оркестрация сервисов
└── DEPLOY.md            # Эта инструкция
```
