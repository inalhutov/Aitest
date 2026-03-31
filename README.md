# AI Browser Agent

Локальный автономный браузерный агент на `Playwright` + LLM (tool calling).
Проект предназначен для задач вида: найти товар, кликнуть нужные элементы, заполнить поля, проверить результат и корректно завершить сценарий.

---

## Что это умеет

- Автоматизация в видимом браузере (не headless).
- Подключение к уже открытому Chrome через CDP (`attach` mode).
- Пошаговый цикл `observe -> act -> verify`.
- Выбор действий через инструменты (`click`, `type`, `scroll`, `query`, `screenshot`).
- Базовые safety-правила перед рискованными действиями.
- Скриншоты/состояние страницы для верификации каждого шага.

---


Ниже кратко и по делу, что было сделано по проблеме "агент видит товар, но не всегда понимает, что нужно нажать `+` и считает, что добавил в корзину, когда корзина пуста":

1. Улучшили ранжирование кандидатов на клик в `agent/page.py`.
   - Добавили учет **контекста исходной задачи** (например, "добавь хот-дог").
   - Кнопка `+` теперь получает высокий приоритет только если рядом есть контекст нужного товара.
   - Если `+` не связан с товаром из задачи, такой кандидат штрафуется.

2. Усилили извлечение контекста карточки товара.
   - Контекст рядом с элементом теперь выбирается более осмысленно (не случайный короткий текст).
   - Это сильно помогает для карточек с иконкой `+` без явной подписи "Добавить".

3. Добавили защиту от "ложно-уверенного" клика по `+` в `agent/agent.py`.
   - Если топ-кандидаты слишком близки по score, агент получает ошибку "ambiguous target" и должен уточнить цель.

4. Добавили обязательную верификацию добавления в корзину.
   - После add-click агент явно получает статус `NOT VERIFIED YET`.
   - Перед завершением сценария он должен проверить корзину (рост счетчика или наличие товара в корзине).

5. Обновили модель в конфиге.
   - `OPENAI_MODEL=gpt-5`
   - `OPENAI_FALLBACK_MODEL=gpt-5-mini`
   - Это повышает качество принятия решений в сложных DOM-сценариях.

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

## Работа с уже открытым Chrome (attach mode)

1. Запустить Chrome с debug портом:

```bash
start_chrome_debug.bat
```

2. Запустить агента:

```bash
run_attach.bat
```

Если attach не удался и `ATTACH_FALLBACK_TO_PERSISTENT=1`, агент автоматически перейдет в persistent mode.

---

## Наблюдаемое поведение агента (логика принятия решений)

1. `get_page_state` — снимает структуру DOM + скриншот.
2. `get_action_candidates` — строит список кандидатов и confidence.
3. `click_best_match` / `type_into_best_match` — делает одно действие.
4. Снова `get_page_state` — проверяет изменение состояния.
5. Повторяет цикл до `task_complete`.

Это снижает риск "я сделал" без фактического изменения на странице.
