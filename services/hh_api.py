import time
import json
import requests
import logging
from pathlib import Path

TOKEN_FILE = Path("hh_token.json")

LOG_FILE = Path("logs/app.log")
LOG_FILE.parent.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def load_token_from_disk():
    if TOKEN_FILE.exists():
        try:
            token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            return token
        except Exception:
            return None
    return None

def save_token_to_disk(token: dict):
    TOKEN_FILE.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")

def token_is_valid(token: dict) -> bool:
    # Если HH вернул expires_in — используем его.
    # Если нет — будем считать, что валидность не проверяем и просто пробуем запрос.
    expires_in = token.get("expires_in")
    obtained_at = token.get("obtained_at")
    if not expires_in or not obtained_at:
        return True
    return (time.time() - obtained_at) < (int(expires_in) - 30)  # запас 30 сек

def hh_request_with_retry(method, url, headers=None, **kwargs):
    timeout = kwargs.pop("timeout", 15)  # берём timeout из kwargs или ставим 15
    for attempt in range(3):
        try:
            resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)

            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue

            return resp  # успешный ответ

        except requests.RequestException:
            time.sleep(2 ** attempt)

    return None

def hh_headers(access_token: str) -> dict:
    token = access_token
    if not token:
        return None

    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "AITGResumeAnalyzer/0.1 (support@aitalentgenome.com)",
    }

def get_full_resume(resume_id: str, headers: dict):
    if not resume_id:
        return None

    r = hh_request_with_retry(
        "GET",
        f"https://api.hh.ru/resumes/{resume_id}",
        headers=headers,
        timeout=15
    )

    if not r or r.status_code != 200:
        logging.error(f"Не удалось загрузить резюме {resume_id}")
        return None

    return r.json()

def summarize_experience(experience_list: list) -> list[str]:
    summary = []

    for exp in experience_list:
        position = exp.get("position", "")
        company = exp.get("company", "")
        
        start = ""
        end = "н.в."

        if exp.get("start"):
            start = exp["start"]
        if exp.get("end"):
            end = exp["end"]

        line = f"{position} — {company} ({start}–{end})".strip()
        if position or company:
            summary.append(line)

    return summary