import time
import streamlit as st
import pandas as pd
import urllib.parse
import json
import requests  # Для HH API
import logging

from pathlib import Path
from analyzer import analyze_candidate_json # Анализ LLM
from utils import extract_cover_letter # поиск сопроводительного письма
from services.hh_api import load_token_from_disk, save_token_to_disk, token_is_valid, hh_headers, hh_request_with_retry, get_full_resume, summarize_experience
from state.resetters import reset_after_vacancy_change, reset_after_collection_change
from services.hh_auth import hh_logout, exchange_code_for_token
from config import HH_CLIENT_ID, HH_CLIENT_SECRET, HH_REDIRECT_URI

from services.ui_hh_loader import render_hh_loader
from services.hh_service import load_items_from_collections
from services.analysis_service import run_candidate_analysis
from services.hh_actions_service import move_selected_candidates

from SQL import init_cache_db, get_cache, set_cache # локальный кеш кандидатов
from datetime import datetime, UTC


access_token = st.session_state.get("hh_access_token")

init_cache_db() # Создаем / открываем SQLite кеш

# ===== Восстановление токена при запуске =====

if "hh_token" not in st.session_state:
    disk_token = load_token_from_disk()

    if disk_token and token_is_valid(disk_token):
        st.session_state["hh_token"] = disk_token
        st.session_state["hh_access_token"] = disk_token["access_token"]

AUDIT_FILE = Path("audit/actions.jsonl")
AUDIT_FILE.parent.mkdir(exist_ok=True)


auth_url = "https://hh.ru/oauth/authorize?" + urllib.parse.urlencode({
    "response_type": "code",
    "client_id": HH_CLIENT_ID,
    "redirect_uri": HH_REDIRECT_URI,
    "state": "aitg-demo-1",
})

# Функция для записи аудита
def write_audit(vacancy_id, collection_id, negotiation_id, action, result, message=""):
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "vacancy_id": vacancy_id,
        "collection_id": collection_id,
        "negotiation_id": negotiation_id,
        "action": action,
        "result": result,  # "ok" | "error"
        "message": message
    }

    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

st.subheader("HH OAuth")
reason = st.session_state.pop("auth_logout_reason", None)

if reason:
    st.warning(reason)
# если токен уже есть → показать статус и кнопку смены аккаунта
if "hh_token" in st.session_state and token_is_valid(st.session_state["hh_token"]):
    col1, col2 = st.columns(2)

    with col1:
        st.success("Вы авторизованы в HH")

    with col2:
        if st.button("Войти с другим аккаунтом"):
            hh_logout()

else:
    # ссылка на авторизацию
    st.link_button("Войти через HH", auth_url)

    # получаем code из URL
    query_params = st.query_params
    code = query_params.get("code")

    if code:
        with st.spinner("Получаю access_token..."):
            if exchange_code_for_token(code):
                st.success("Успешный вход!")
                st.query_params.clear()  # убираем code из URL
                st.rerun()

    st.stop()
# --- Статус токена (индикатор) ---
t = st.session_state.get("hh_token")

status_text = "Нет токена"
remaining_sec = None

if t:
    expires_in = t.get("expires_in")
    obtained_at = t.get("obtained_at")

    if expires_in and obtained_at:
        remaining_sec = int(obtained_at + int(expires_in) - time.time())
        status_text = "Активен" if remaining_sec > 0 else "Истёк"
    else:
        status_text = "Токен есть (срок неизвестен)"

# Метрики токена
col1, col2 = st.columns(2)
with col1:
    st.metric("HH токен", status_text, border=True)
with col2:
    if remaining_sec is None:
        st.metric("Осталось", "Неизвестно", border=True)
    else:
        mins = max(0, remaining_sec) // 60
        secs = max(0, remaining_sec) % 60
        st.metric("Осталось", f"{mins}m {secs}s", border=True)

if status_text == "Истёк":
    hh_logout("Сессия истекла. Требуется повторный вход в HeadHunter")

# Загрузка вакансий
result = render_hh_loader(access_token)
if not result:
    st.stop()

items, selected_vacancy_id, selected_collections = result

# --- Критерии ---
st.subheader("Критерии от себя")

if "hr_criteria" not in st.session_state:
    st.session_state["hr_criteria"] = ""

hr_criteria = st.text_area(
    "Опиши требования к кандидатам (опыт, стек, софт-скиллы и т.д.)",
    placeholder="Например: опыт Python от 1 до 3 лет, опыт с API, английский B1+",
    height=120,
    key="hr_criteria_input"
)

if st.button("7) Отправить критерии", type="primary"):
    if not hr_criteria.strip():
        st.warning("Введите критерии перед отправкой")
    else:
        st.session_state["hr_criteria"] = hr_criteria
        st.success("Критерии сохранены. Готово к отправке в LLM 🚀")

st.subheader("Настройки отбора")

n_candidates = st.number_input(
    "Сколько откликов показать",
    min_value=1,
    max_value=len(items),
    value=min(10, len(items)), # Показываем изначальное число 10, если кандидатов меньше 10 то выдаем длину кол-ва откликов
    step=1,
    key="n_candidates_input"
)

st.subheader("Анализ кандидатов")

if "resume_cache" not in st.session_state:
    st.session_state["resume_cache"] = {}

# КНОПКА ОСТАНОВКИ
if st.session_state["analysis_running"]:
    if st.button("🛑 Остановить анализ"):
        st.session_state["stop_analysis"] = True

# КНОПКА ЗАПУСКА
if not st.session_state["analysis_results"] and not st.session_state["analysis_running"]:
    if st.button("Анализировать топ-N кандидатов", type="primary"):
        st.session_state["stop_analysis"] = False
        st.session_state["analysis_running"] = True
        st.session_state["start_analysis"] = True  # для UI
        st.rerun()

if st.session_state["start_analysis"] and st.session_state["analysis_running"]:
    st.info("Анализ запущен…")
      
if st.session_state.get("analysis_running"):
    with st.spinner("Анализирую кандидатов..."):

        run_candidate_analysis(
            items=items,
            selected_vacancy_id=selected_vacancy_id,
            access_token=access_token,
            n_candidates=n_candidates,
            hh_headers=hh_headers,
            hh_request_with_retry=hh_request_with_retry,
            get_full_resume=get_full_resume,
            summarize_experience=summarize_experience,
            extract_cover_letter=extract_cover_letter,
            analyze_candidate_json=analyze_candidate_json,
            get_cache=get_cache,
            set_cache=set_cache,
        )

        st.session_state["analysis_running"] = False

        if not st.session_state.get("stop_analysis"):
            st.session_state["analysis_results"] = True
            st.session_state["start_analysis"] = False

        st.rerun()

reason = st.session_state.pop("analysis_stop_reason", None)
if reason:
    st.warning(reason)

if not st.session_state["analysis_results"]:
    st.stop()

if st.button("✅ Выбрать всех") and "top_n" in st.session_state:
    st.session_state["top_n"]["selected"] = True

if st.button("❌ Снять выбор"):
    st.session_state["top_n"]["selected"] = False

# Редактируем таблицу через st.data_editor
edited = st.data_editor(
    st.session_state.get("top_n", pd.DataFrame()),
    use_container_width=True,
    column_config={
        "selected": st.column_config.CheckboxColumn("Выбрать"),
        "Ссылка": st.column_config.LinkColumn("Резюме", display_text="Открыть"),
        "negotiation_id": None,  # ← скрываем
    },
)

collection_map = {c["name"]: c["id"] for c in selected_collections}

target_collection_label = st.selectbox(
        "Куда перевести кандидатов",
        options=list(collection_map.keys())
    )

# Экспорт выбранны
if st.button("📤 Перевести выбранных в HH"):
    if "top_n" not in st.session_state:
        st.error("Для начала сделайте анализ кандидатов")
        st.stop()

    target_collection_id = collection_map[target_collection_label]

    success, errors = move_selected_candidates(
        edited_df=edited,
        items=items,
        selected_vacancy_id=selected_vacancy_id,
        target_collection_id=target_collection_id,
        access_token=access_token,
        hh_headers=hh_headers,
        hh_request_with_retry=hh_request_with_retry,
        write_audit=write_audit
    ) or ([], [])

    if success or errors:
        st.divider()
        st.subheader("📊 Итоги перевода")

        st.write(f"✅ Успешно переведено: {len(success)}")
        st.write(f"❌ Ошибок: {len(errors)}")

        if errors:
            st.error("Обнаружены ошибки при переводе")
            st.dataframe(pd.DataFrame(errors))

# <-- СЮДА вставляем блок "Письмо/переписка"

st.subheader("Письмо/переписка (полный текст)")

nid = st.text_input("Вставь negotiation_id из таблицы", key="hh_nid_input")

if st.button("7) Показать сообщения по отклику"):
    headers = hh_headers(access_token)
    if not headers:
        st.error("Нет access_token.")
        st.stop()

    if not nid.strip():
        st.error("Вставь negotiation_id.")
        st.stop()

    msg_url = f"https://api.hh.ru/negotiations/{nid.strip()}/messages"
    r = hh_request_with_retry("GET", msg_url, headers=headers, timeout=30)
    st.write("HTTP статус:", r.status_code)

    data = r.json()
    msgs = data.get("items", [])
    full_text = "\n\n---\n\n".join([m.get("text", "") for m in msgs if m.get("text")])

    st.text_area("Полный текст", full_text, height=300)