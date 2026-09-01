# ppt-bot

Работа с проектом **Автогенератор презентаций** — Telegram-бот для генерации `.pptx` через Ollama Cloud.

---

## Контекст проекта

| Параметр | Значение |
|---|---|
| **Название** | PPTGeneratorBot / generatepresentation_bot |
| **Проблема** | Создание презентации вручную занимает 30–60 мин. Бот генерирует её за минуты. |
| **Пользователь** | Методисты, студенты, школьники (RU) |
| **Основной файл** | `bot.py` — точка входа |
| **Конфиг** | `config.py` — pydantic-settings, читает `.env` |
| **FSM-стейты** | `app/handlers/fsm.py` — States: IDLE, WAITING_TOPIC, WAITING_SLIDES_COUNT, WAITING_PHOTOS, CONFIRM_GENERATION, GENERATING |
| **Репозиторий** | https://github.com/kateridzzze/generatepresentation_bot |

---

## Запуск сервиса

```bash
# 1. Активировать виртуальное окружение
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Убедиться что .env заполнен ( TELEGRAM_BOT_TOKEN, OLLAMA_API_KEY )
cp .env.example .env

# 4. Запустить
python bot.py
```

**Быстрая проверка** — запуск без реального Telegram API (mock-режим): не предусмотрен, бот работает с реальным API.

---

## Структура кода

```
bot.py                        # asyncio.run(main()), polling
config.py                     # Settings() — все настройки из .env
app/
├── handlers/
│   ├── fsm.py               # State(StrEnum): IDLE → GENERATING
│   ├── start.py             # /start, /help, /cancel
│   ├── topic.py             # WAITING_TOPIC, WAITING_SLIDES_COUNT
│   └── photos.py            # WAITING_PHOTOS, CONFIRM_GENERATION, _run_generation()
├── services/
│   ├── ollama_client.py     # OllamaClient.generate_structure() → list[Slide]
│   └── pptx_builder.py      # build_pptx(slides, title, photo_paths, output_path)
├── storage/
│   └── sqlite.py            # SQLiteStorage: FSM, daily_limit, generations log
└── utils/
    ├── logging.py           # setup_logging() — loguru в console + file
    └── validators.py        # validate_topic(), validate_slides_count()
tests/
├── conftest.py              # ROOT = project root
├── test_ollama_parser.py    # _parse_strict(), _extract_json()
├── test_pptx_builder.py    # build_pptx(), _distribute_photos()
└── test_validators.py       # validate_topic(), validate_slides_count()
```

---

## Как вносить изменения

### Добавить новый FSM-стейт

1. `app/handlers/fsm.py` — добавить в `class State(StrEnum)`
2. `app/storage/sqlite.py` — убедиться что `get_active_generations()` корректно обрабатывает новый стейт
3. Если нужен новый хэндлер — создать `app/handlers/new_handler.py`, зарегистрировать роутер в `bot.py`

### Изменить промпт LLM

**Файл:** `app/services/ollama_client.py`
- `_build_prompt()` — системный промпт
- `_call_generate()` — `system` field для retry (более строгий)

### Изменить стиль .pptx

**Файл:** `app/services/pptx_builder.py`
- Константы `COLOR_BG`, `COLOR_FG`, `COLOR_MUTED`, `COLOR_ACCENT` — палитра
- `SLIDE_W`, `SLIDE_H` — размер слайда в дюймах
- `_add_title_slide()`, `_add_content_slide()` — вёрстка слайдов

### Добавить валидацию ввода

**Файл:** `app/utils/validators.py`

### Изменить лимиты

**Файл:** `config.py` — `Field(default=..., alias="...")` или `.env`

---

## Обновление README

После изменения функционала обновить соответственно:

- Раздел «Минимальный функционал (v1)» — таблица функций
- Раздел «Технологии» — список библиотек
- Раздел «Сценарий работы» — диаграмма FSM
- Раздел «Лимиты» — числовые значения
- Раздел «Быстрый старт» — переменные окружения

---

## Проверка ошибок

```bash
# Тесты
pytest tests/ -v

# Линтер
ruff check .

# Проверка .env (секреты не захардкожены)
grep -rE '(token|api_key|secret|password)\s*[:=]\s*["\'][^$]' app/ --include="*.py"
```

**Типичные ошибки:**

| Симптом | Причина |
|---|---|
| `aiogram.exceptions.TelegramUnauthorizedError` | Неверный `TELEGRAM_BOT_TOKEN` |
| `OllamaError: Network error` | Неверный `OLLAMA_API_KEY` или `OLLAMA_BASE_URL` |
| `ValidationError` при старте | Отсутствует обязательная переменная в `.env` |
| `sqlite3.OperationalError: database is locked` | Несколько инстансов бота одновременно |
| Бот не видит фото | `bind_runtime()` не вызван в `bot.py` |

---

## Подготовка к публикации

1. Проверить `.env` — реальные значения не должны попасть в код
2. `grep -rE '"[^"]*token[^"]*"|"[^"]*key[^"]*"' app/` — поиск захардкоженных секретов
3. `.gitignore` должен содержать `.env`, `.env.local`, `data/`, `tmp/`
4. `pytest tests/` — все тесты зелёные
5. `ruff check .` — без warnings
6. README.md актуален
7. `git tag v1.x.x && git push --tags` — тег версии

---

## Ключевые команды бота

| Команда | Стейт | Действие |
|---|---|---|
| `/start` | любой | Приветствие → WAITING_TOPIC |
| `/help` | любой | Справка по командам и лимитам |
| `/cancel` | любой | Сброс FSM → IDLE |

---

## Переменные окружения

| Переменная | Обязательно | По умолчанию |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — |
| `OLLAMA_API_KEY` | ✅ | — |
| `OLLAMA_MODEL` | Нет | `minimax-m3:cloud` |
| `OLLAMA_BASE_URL` | Нет | `https://ollama.com/api` |
| `OLLAMA_TIMEOUT` | Нет | `60` |
| `DAILY_GENERATION_LIMIT` | Нет | `2` |
| `TZ` | Нет | `Europe/Moscow` |
| `LOG_LEVEL` | Нет | `INFO` |
