import sqlite3
import hashlib
import json
from pathlib import Path
from datetime import datetime

CACHE_FILE = Path("llm_cache.sqlite3")

# --- Создаём таблицу, если её нет ---
def init_cache_db():
    conn = sqlite3.connect(CACHE_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS llm_cache (
            resume_id TEXT,
            vacancy_id TEXT,
            criteria_hash TEXT,
            result_json TEXT,
            updated_at TEXT,
            PRIMARY KEY(resume_id, vacancy_id, criteria_hash)
        )
    """)
    conn.commit()
    conn.close()

init_cache_db()

# --- Функция для хэширования критериев ---
def hash_criteria(criteria: str) -> str:
    return hashlib.sha256(criteria.encode("utf-8")).hexdigest()

# --- Получение результата из кэша ---
def get_cache(resume_id: str, vacancy_id: str, criteria: str):
    criteria_hash = hash_criteria(criteria)
    conn = sqlite3.connect(CACHE_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT result_json FROM llm_cache WHERE resume_id=? AND vacancy_id=? AND criteria_hash=?",
        (resume_id, vacancy_id, criteria_hash)
    )
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None

# --- Сохранение результата в кэш ---
def set_cache(resume_id: str, vacancy_id: str, criteria: str, result: dict):
    criteria_hash = hash_criteria(criteria)
    conn = sqlite3.connect(CACHE_FILE)
    c = conn.cursor()
    c.execute(
        """
        INSERT OR REPLACE INTO llm_cache (resume_id, vacancy_id, criteria_hash, result_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (resume_id, vacancy_id, criteria_hash, json.dumps(result, ensure_ascii=False), datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()