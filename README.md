# AI Talent Genome — MVP (Streamlit)

ИИ-помощник рекрутера, интегрированный с **hh.ru**: подтягивает отклики на вакансию, оценивает каждого кандидата с помощью локальной LLM (Ollama) по критериям HR и помогает разобрать отклики — переместить подходящих кандидатов на нужный этап.

Здесь лежит **MVP на Streamlit** — рабочий инструмент: вход через hh.ru, выбор вакансии и коллекций откликов, LLM-скоринг, массовые действия с кандидатами.

Веб-версия (React + FastAPI, регистрация по телефону, JWT, OAuth hh.ru) живёт в отдельном репозитории **[ai-talent-genome](https://github.com/xtron11/ai-talent-genome)**.

## Как работает оценка

Для каждого отклика собираются желаемая должность, краткая выжимка опыта и сопроводительное письмо. Вместе с текстом вакансии и критериями HR они отправляются в LLM. Модель возвращает JSON по схеме [`Schema.json`](Schema.json): балл 0–100, обоснование на русском и флаги (например, нет сопроводительного письма). Ответы кэшируются в локальном SQLite, чтобы не пересчитывать одних и тех же кандидатов.

## Запуск MVP (Streamlit)

```bash
python -m venv .venv
.venv\Scripts\activate                 # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

copy .streamlit\secrets.toml.example .streamlit\secrets.toml
# впиши HH_CLIENT_ID / HH_CLIENT_SECRET своего приложения с dev.hh.ru

ollama pull llama3
streamlit run main.py
```

## Структура MVP

```
main.py              # Streamlit-приложение
analyzer.py          # промпт и вызов LLM, валидация JSON-ответа
SQL.py               # SQLite-кэш оценок
callback_server.py   # приём OAuth-кода от hh.ru
services/            # работа с API hh.ru: авторизация, отклики, действия
state/               # ключи и сброс состояния Streamlit-сессии
Schema.json          # JSON-схема ответа модели
```
