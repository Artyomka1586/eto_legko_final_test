# Keitaro Wrapper

Обёртка над трекером [Keitaro](https://keitaro.io) (тестовый эмулятор): создание кампаний
с двумя потоками и редактор офферов существующих кампаний.

Стек: **FastAPI + SQLAlchemy 2.0 + Alembic (SQLite)**, фронт — серверный рендеринг
Jinja2 + [HTMX](https://htmx.org). Клиент Admin API Keitaro — `httpx`.

## Что умеет

**1. Создаватор кампаний** (`/`)

Форма: название + гео-код страны (ISO, 2 буквы) + ID оффера в Keitaro. Создаёт кампанию:

* имя с префиксом `test_adulybin_` (префикс настраивается в `.env`);
* домен, группа и источник берутся автоматически (первые доступные из API Keitaro);
* поток **Google**: фильтр `country` на указанное гео + redirect на `https://www.google.com/`;
* поток **Offers**: выбранный оффер со 100% весом.

**2. Редактор кампании** (`/campaigns/{id}`)

Работает с любой кампанией Keitaro (не только созданной обёрткой). Два уровня:

* **Страница кампании — управление потоками**: список потоков (тип, число активных
  офферов, состояние, URL редиректа), **добавление нового потока** (офферного или
  редиректа с URL и гео-фильтром), **удаление потока**, **Fetch Streams** — подтянуть
  потоки из Keitaro. Кнопка «Перейти к офферам» открывает редактор офферов.
* **Редактор офферов** (`/campaigns/{id}/offers`): все потоки кампании на одной странице.

Локальная БД — staging-состояние, Keitaro — опубликованное:

* **Fetch Streams** — подтянуть потоки из Keitaro. Потоки без офферов (например, Google)
  показываются заголовком и не редактируются. Если оффер удалили напрямую в Keitarо,
  после Fetch Streams он станет серым с кнопкой **bring back**;
* **Добавить оффер** — поиск по каталогу офферов Keitaro (ID или название);
* **Удалить / bring back** — оффер сереет (доля 0%), возвращается кнопкой;
* **Pin веса (📌)** — закрепить вес оффера: при пересчётах запиненные доли не меняются,
  остальные поровну делят остаток до 100%;
* веса пересчитываются равномерно при каждом добавлении/удалении/возврате
  (остаток от деления уходит первым офферам);
* жёлтая карточка потока = есть непубликoванные изменения;
* **Push** — записать офферы и веса потока в Keitaro;
* **Cancel** — откатить все несохранённые изменения к последнему опубликованному состоянию.

Все действия выполняются без перезагрузки страницы (HTMX). В колонках Stats и Trends
эмулятор Keitaro статистику не отдаёт (Reporting API отсутствует) — оставлены как в ТЗ.

## Запуск

Нужен Python 3.11+.

```bash
git clone <repo> && cd keitaro-wrapper

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
```

Создать `.env` рядом с `requirements.txt` (см. `.env.example`):

```ini
KEITARO_API_URL=https://keitaro-for-tests.etolegko.com/admin_api/v1
KEITARO_API_TOKEN=<ваш ключ API>
```

Ключ берётся в админке эмулятора Keitaro после входа через GitHub (плашка
`Заголовок запроса: Api-Key: ...` вверху страницы).

## Миграции

Схема БД управляется Alembic. Перед первым запуском применить миграции:

```bash
alembic upgrade head
```

Создать новую миграцию после изменения моделей (`app/models.py`):

```bash
alembic revision --autogenerate -m "описание"
alembic upgrade head
```

URL базы данных и параметры Keitaro читаются из `.env` (см. `app/config.py`).
По умолчанию — SQLite `wrapper.db` в корне проекта.

## Запуск приложения

```bash
uvicorn app.main:app --reload
```

Открыть http://127.0.0.1:8000

## Структура проекта

```
app/
  config.py        # настройки (pydantic-settings, .env)
  db.py            # engine/session SQLAlchemy
  models.py        # Campaign, Stream, StreamOffer (staging-модель)
  keitaro.py       # тонкий клиент Admin API Keitaro (httpx)
  services.py      # бизнес-логика: создание кампании, синхронизация, веса, push/cancel
  main.py          # FastAPI
  web/
    routes.py      # страницы и действия: кампания → поток → офферы
    templates/     # Jinja2: index, campaign, stream + HTMX-частички
    static/        # CSS (тёмная тема), JS поиска офферов
alembic/           # миграции
```

## Как устроен staging

В таблице `stream_offers` у каждого оффера хранится два набора значений:

* `share`, `status`, `pinned` — то, что видит и меняет пользователь;
* `published_share` — последний известный вес в Keitaro (`NULL` = оффера в Keitaro нет);
* `ever_published` — был ли оффер когда-либо опубликован в Keitaro.

Push отправляет активные офферы с весами в Keitaro (`PUT /streams/{id}`) и
обновляет `published_share`. Cancel восстанавливает staging из `published_share`:
локально добавленные и не пушнутые записи (`ever_published = false`) удаляются
полностью, офферы, ранее опубликованные и удалённые, остаются в сером архиве
с кнопкой bring back. Синхронизация (Fetch Streams) обновляет `published_share`
и, если поток не «грязный», зеркалит staging.
