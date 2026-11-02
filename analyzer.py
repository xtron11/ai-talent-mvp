import streamlit as st
import ollama
import json
import jsonschema
import time
import logging

with open("Schema.json", "r", encoding="utf-8") as f:
    ANALYSIS_SCHEMA = json.load(f)

logger = logging.getLogger(__name__)

def analyze_candidate_json(
    vacancy_text: str,
    desired_position: str,
    experience_summary: list[str],
    cover_letter: str,
    hr_criteria: str,
    model="llama3:latest",
    max_retries: int = 3,        # макс. число повторов
    retry_delay: float = 1.0     # пауза между попытками в секундах
) -> dict:

    prompt = f"""
Ты — HR-специалист.
Оцени релевантность кандидата вакансии.

Правила:
- Используй ТОЛЬКО переданные данные
- Ничего не выдумывай
- Ответ строго в формате JSON по схеме
- Оцени кандидата от 0 до 100
- Поле rationale_ru ОБЯЗАТЕЛЬНО должно содержать минимум 3-4 предложения. Нельзя оставлять его пустым.

КРИТЕРИИ:
{hr_criteria}

ВАКАНСИЯ:
{vacancy_text}

КАНДИДАТ:
Желаемая должность: {desired_position}

Опыт работы (кратко):
{chr(10).join(experience_summary) if experience_summary else "не указано"}

Сопроводительное письмо:
{cover_letter}
"""
    
    attempt = 0
    last_error = None
    
    while attempt < max_retries:
        attempt += 1
        try:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format=ANALYSIS_SCHEMA
            )

            raw = response["message"]["content"]

            parsed = json.loads(raw)
            jsonschema.validate(parsed, ANALYSIS_SCHEMA)

            parsed["analysis_error"] = False
            parsed["attempts"] = attempt
            return parsed

        except (json.JSONDecodeError,
                jsonschema.exceptions.ValidationError) as e:

            logger.warning(
                f"LLM invalid response (attempt {attempt}/{max_retries})",
                exc_info=True
            )

            last_error = str(e)

            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return {
                    "analysis_error": True,
                    "error_type": "invalid_or_noncompliant_json",
                    "error_details": last_error,
                    "attempts": attempt
                }

        except Exception as e:
            logger.error(
                f"Ollama crashed on attempt {attempt}",
                exc_info=True
            )

            last_error = str(e)

            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return {
                    "analysis_error": True,
                    "error_type": "llm_runtime_error",
                    "error_details": last_error,
                    "attempts": attempt
                }