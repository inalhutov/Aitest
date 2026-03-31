# AI Browser Agent

Локальный автономный браузерный агент на `Playwright` + LLM (tool calling).
Проект предназначен для задач вида: найти товар, кликнуть нужные элементы, заполнить поля, проверить результат и корректно завершить сценарий.

---

## Структура проекта

- `main.py` — точка входа, загрузка `.env`, запуск браузера, цикл задач.
- `agent/agent.py` — основной агент: промпт, вызовы инструментов, orchestration.
- `agent/page.py` — извлечение DOM/контекста и ранжирование кандидатов.
- `agent/browser.py` — операции Playwright (navigate/click/fill/scroll/screenshot).
- `agent/providers.py` — провайдеры LLM (OpenAI-compatible/Anthropic).
- `agent/tools.py` — схемы инструментов для tool-calling.
- `run.bat` — обычный запуск.
- `run_attach.bat` — запуск в режиме подключения к уже открытому Chrome.
- `start_chrome_debug.bat` — запуск Chrome с `--remote-debugging-port=9222`.

---

## Быстрый старт

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
```

Запуск:

```bash
run.bat
```

---

## Настройки `.env`

Минимально важные параметры:

```env
PROVIDER=openai
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-5
OPENAI_FALLBACK_MODEL=gpt-5-mini
OPENAI_BASE_URL=https://api.openai.com/v1

BROWSER_MODE=attach
CHROME_CDP_URL=http://127.0.0.1:9222
ATTACH_FALLBACK_TO_PERSISTENT=1
```

Комментарии:

- `OPENAI_MODEL` — основная модель.
- `OPENAI_FALLBACK_MODEL` — fallback, если основная недоступна.
- `BROWSER_MODE=attach` — работа в уже открытом Chrome (удобно для авторизованных сессий).
- `ATTACH_FALLBACK_TO_PERSISTENT=1` — если attach не удался, агент стартует в своем профиле.

---


