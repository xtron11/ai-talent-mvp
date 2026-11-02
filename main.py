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

HH_CLIENT_ID = st.secrets["HH_CLIENT_ID"]
HH_CLIENT_SECRET = st.secrets["HH_CLIENT_SECRET"]
HH_REDIRECT_URI = st.secrets["HH_REDIRECT_URI"]

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

# ===== HH OAuth FLOW =====

def hh_logout(reason=None):
    if reason:
        st.session_state["auth_logout_reason"] = reason

    for k in ["hh_token", "hh_access_token"]:
        st.session_state.pop(k, None)

    Path("hh_token.json").unlink(missing_ok=True)

    st.query_params.clear()
    st.rerun()


def exchange_code_for_token(code: str):
    r = hh_request_with_retry(
        "POST",
        "https://hh.ru/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": HH_CLIENT_ID,
            "client_secret": HH_CLIENT_SECRET,
            "code": code,
            "redirect_uri": HH_REDIRECT_URI,
        },
        timeout=30,
    )

    token = r.json()

    if "access_token" not in token:
        st.error(token)
        return False

    token["obtained_at"] = time.time()

    st.session_state["hh_token"] = token
    st.session_state["hh_access_token"] = token["access_token"]

    save_token_to_disk(token)
    return True

def reset_after_vacancy_change():
    # очищаем всё что зависит от вакансии
    for key in [
        "hh_collections",
        "hh_items",
        "top_n",
        "vacancy_text",
        "hh_selected_collection_id",
    ]:
        st.session_state.pop(key, None)

    # сброс анализа
    st.session_state["analysis_results"] = False
    st.session_state["analysis_running"] = False
    st.session_state["start_analysis"] = False
    st.session_state["stop_analysis"] = False

def reset_analysis_only():
    # удаляем отклики
    st.session_state.pop("hh_items", None)

    # удаляем результаты анализа
    st.session_state.pop("top_n", None)
    st.session_state.pop("vacancy_text", None)

    # сбрасываем флаги
    st.session_state["analysis_results"] = False
    st.session_state["analysis_running"] = False
    st.session_state["start_analysis"] = False
    st.session_state["stop_analysis"] = False

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

st.subheader("Загрузка вакансий")

if st.button("4) Загрузить мои вакансии"):
    headers = hh_headers(access_token)
    if not headers:
        st.error("Нет access_token в session_state. Сначала нажми кнопку получения токена.")
        st.stop()

    r = hh_request_with_retry("GET",
        "https://api.hh.ru/vacancies",
        params={"employer_id": "5700680", "per_page": 20},
        headers=headers,
        timeout=30,
    )

    data = r.json()
    st.session_state["hh_vacancies"] = data.get("items", [])

# Вакансии
vacancies = st.session_state.get("hh_vacancies", [])
if not vacancies:
    st.info("Сначала нажми: 4) Загрузить мои вакансии")
    st.stop()

vacancy_options = {
    f'{v.get("name")} (id={v.get("id")})': v.get("id")
    for v in vacancies
    if v.get("id")
}
selected_vacancy_label = st.selectbox(
    "Выберите вакансию",
    list(vacancy_options.keys()),
    key="hh_selected_vacancy_label",
    on_change=reset_after_vacancy_change
)
selected_vacancy_id = vacancy_options[selected_vacancy_label]


# Кнопка 5: загрузка коллекций
if st.button("5) Загрузить коллекции откликов"):
    headers = hh_headers(access_token)
    if not headers:
        st.error("Нет access_token.")
        st.stop()

    r = hh_request_with_retry("GET",
        "https://api.hh.ru/negotiations",
        params={"vacancy_id": selected_vacancy_id},
        headers=headers,
        timeout=30,
    )
    data = r.json()
    st.session_state["hh_collections"] = data.get("collections", [])

collections = st.session_state.get("hh_collections", [])
if not collections:
    st.info("Сначала нажми: 5) Загрузить коллекции откликов")
    st.stop()
else:
    col_by_id = {c.get("id"): c for c in collections if c.get("id")}
    col_ids = list(col_by_id.keys())

    selected_col_ids = st.multiselect(
        "Выберите коллекцию",
        options=col_ids,
        format_func=lambda x: f'{col_by_id[x].get("name")} (id={x})',
        key="hh_selected_collection_id",
        on_change=reset_analysis_only,
    )
    selected_collections = [col_by_id[col_id] for col_id in selected_col_ids]

if not selected_collections:
    st.warning("Выберите хотя бы одну коллекцию, чтобы продолжить")
    st.stop()

if st.button("6) Загрузить отклики выбранных коллекций"):
    headers = hh_headers(access_token)
    if not headers:
        st.error("Нет access_token.")
        st.stop()

    all_items = []
    seen_ids = set()

    progress = st.progress(0)
    status = st.empty()

    collections_total = len(selected_collections)
    collections_done = 0
    
    for collection in selected_collections:
        url = collection.get("url")
        if not url:
            continue

        page = 0
        while True:
            r = hh_request_with_retry(
                "GET",
                url,
                headers=headers,
                params={"per_page": 50, "page": page}
            )

            if not r:
                st.error("Ошибка HH API")
                st.stop()

            data = r.json()
            batch = data.get("items", [])
            if not batch:
                break

            for item in batch:
                nid = item.get("id")
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    all_items.append(item)

            page += 1
            # обновляем UI
            status.text(f"Коллекция {collections_done + 1}/{collections_total} • страница {page} • откликов: {len(all_items)}")

            # псевдопрогресс внутри коллекции
            inner_progress = min(page / 10, 0.9)
            progress.progress((collections_done + inner_progress) / collections_total)

        collections_done += 1
        progress.progress(collections_done / collections_total)
    # ✅ СНАЧАЛА сравниваем со старым списком
    new_ids = {it.get("id") for it in all_items}
    old_ids = {it.get("id") for it in st.session_state.get("hh_items", [])}

    if new_ids != old_ids:
        st.session_state["analysis_results"] = False
        st.session_state["analysis_running"] = False
        st.session_state["start_analysis"] = False
        st.session_state["stop_analysis"] = False
        st.session_state.pop("top_n", None)
        st.session_state.pop("analysis_stop_reason", None)

    # ✅ ПОТОМ сохраняем новые данные
    st.session_state["hh_items"] = all_items
    st.success(f"Загружено {len(all_items)} откликов")

# Отображение откликов
items = st.session_state.get("hh_items", [])
if items:
    df = pd.DataFrame([{
        "negotiation_id": it.get("id"),
        "created_at": it.get("created_at"),
        "state": (it.get("state") or {}).get("id"),
        "resume_id": (it.get("resume") or {}).get("id"),
        "resume_title": (it.get("resume") or {}).get("title"),
    } for it in items])

    df = df.reset_index(drop=True)
    df.index += 1

    st.dataframe(df, width="stretch")
else:
    st.info("Нажми: 6) Загрузить отклики этой коллекции")
    st.stop()

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

for key in ["start_analysis", "analysis_running", "stop_analysis", "analysis_results"]:
    if key not in st.session_state:
        st.session_state[key] = False

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
      
if st.session_state.get("analysis_running", False):
    with st.spinner("Анализирую кандидатов..."):
        results = []
        # ── Загрузка текста вакансии (один раз) ──
        if "vacancy_text" not in st.session_state:
            headers = hh_headers(access_token)
            r = hh_request_with_retry("GET",
                f"https://api.hh.ru/vacancies/{selected_vacancy_id}",
                headers=headers,
                timeout=15
            )
            if r.status_code != 200:
                st.error("Не удалось загрузить вакансию")
                st.session_state["analysis_running"] = False
                st.rerun()

            data = r.json()
            st.session_state["vacancy_text"] = data.get("description", "")
            st.session_state["vacancy_name"] = data.get("name", "")
            vacancy_text = st.session_state["vacancy_text"]
        else:
            vacancy_text = st.session_state["vacancy_text"]

        criteria = st.session_state.get("hr_criteria", "")
        total_candidates = len(items)
        progress = st.progress(0)
        status_text = st.empty()  # Для текста прогресса

        # ── Цикл по кандидатам ──
        for i, item in enumerate(items):
            if st.session_state["stop_analysis"]:
                st.session_state["analysis_stop_reason"] = "Анализ остановлен пользователем"
                break

            resume_stub = item.get("resume", {})
            resume_id = resume_stub.get("id", "")
            full_resume = None

            headers = hh_headers(access_token)

            # Проверяем cache
            if resume_id in st.session_state["resume_cache"]:
                full_resume = st.session_state["resume_cache"][resume_id]
            else:
                full_resume = get_full_resume(resume_id, headers)
                if full_resume:
                    st.session_state["resume_cache"][resume_id] = full_resume

            if full_resume:
                desired_position = full_resume.get("title", "")
                experience_summary = summarize_experience(
                    full_resume.get("experience", [])
                )
            else:
                desired_position = resume_stub.get("title", "")
                experience_summary = []

            resume_title = desired_position
            cached_result = get_cache(resume_id, selected_vacancy_id, criteria)

            if cached_result: # Проверяем есть ли кандидат в нашем кеше
                analysis = cached_result
            else:
                cover_letter = extract_cover_letter(item, headers)

                # Передаём ГОТОВЫЕ данные в LLM
                analysis = analyze_candidate_json(
                    vacancy_text=vacancy_text,
                    desired_position=desired_position,
                    experience_summary=experience_summary,
                    cover_letter=cover_letter,
                    hr_criteria=st.session_state.get("hr_criteria", ""),
                    model="llama3:latest"
                )
                set_cache(resume_id, selected_vacancy_id, criteria, analysis) # Кешируем кандидата

            if analysis.get("analysis_error"):
                results.append({
                    "negotiation_id": item["id"],
                    "Резюме": resume_title,
                    "Оценка": "ERROR",
                    "Обоснование": "Ошибка анализа LLM",
                    "Ссылка": f"https://hh.ru/resume/{resume_id}",
                })
                continue

            # Сохраняем результат
            results.append({
                "negotiation_id": item["id"],
                "Резюме": resume_title,
                "Оценка": analysis["score"],
                "Обоснование": analysis["rationale_ru"],
                "Ссылка": f"https://hh.ru/resume/{resume_id}",
            })
            progress.progress((i + 1) / total_candidates)
            status_text.text(f"Проверено {i + 1} из {total_candidates} кандидатов")

        if results:
            df = pd.DataFrame(results)
            df["Оценка"] = pd.to_numeric(df["Оценка"], errors="coerce")
            df_sorted = df.sort_values("Оценка", ascending=False)
            df_sorted = df_sorted.head(n_candidates)
            
            if "selected" not in df.columns:
                df_sorted.insert(0, "selected", False)

            # сохраняем в session_state
            st.session_state["top_n"] = df_sorted
        
        st.session_state["analysis_running"] = False

        if not st.session_state["stop_analysis"]:
            st.session_state["analysis_results"] = True
            st.session_state["start_analysis"] = False
        st.rerun()   # чтобы показать результаты и вернуть интерфейс

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

collection_map = {c["name"]: c["id"] for c in collections}

target_collection_label = st.selectbox(
        "Куда перевести кандидатов",
        options=list(collection_map.keys())
    )

# Экспорт выбранных
if st.button("📤 Перевести выбранных в HH"):
    if "top_n" not in st.session_state or st.session_state["top_n"].empty:
        st.error("Для начала сделайте анализ кандидатов")
        st.stop()
    
    chosen = edited[edited["selected"]] # Выбранные чек боксы
    if chosen.empty:
        st.warning("Никто не выбран")
        st.stop()

    headers = hh_headers(access_token)
    if not headers:
        st.error("Нет access_token")
        st.stop()

    target_collection_id = collection_map[target_collection_label]
    success, errors = [], []

    for _, row in chosen.iterrows():
        negotiation_id = row["negotiation_id"]

        try:
            original_item = next(
                (it for it in items if it.get("id") == negotiation_id),
                None
            )

            current_collection = (original_item.get("state") or {}).get("id")

            if current_collection == target_collection_id:
                st.info(f"Кандидат {negotiation_id} уже в этой коллекции")
                continue
            
            resp = hh_request_with_retry("PUT",
                f"https://api.hh.ru/negotiations/{target_collection_id}/{negotiation_id}",
                headers=headers,
                timeout=10
            )

            if not resp:
                errors.append({
                    "id": negotiation_id,
                    "status": "no_response"
                })
                continue

            if resp.status_code == 204:
                original_item["state"]["id"] = target_collection_id
                success.append(negotiation_id)

                write_audit(
                    vacancy_id=selected_vacancy_id,
                    collection_id=target_collection_id,
                    negotiation_id=negotiation_id,
                    action="move",
                    result="ok"
                )
                logging.info(f"Перевод кандидата {negotiation_id} → {target_collection_id} выполнен успешно")

            else:
                errors.append({
                    "id": negotiation_id,
                    "status": resp.status_code
                })

                write_audit(
                    vacancy_id=selected_vacancy_id,
                    collection_id=target_collection_id,
                    negotiation_id=negotiation_id,
                    action="move",
                    result="error",
                    message=f"HTTP {resp.status_code}"
                )
                st.error(f"Произошла ошибка перевода {negotiation_id}")
                logging.error(f"Ошибка перевода {negotiation_id}: HTTP {resp.status_code}")

        except Exception as e:
            errors.append({
                "id": negotiation_id,
                "status": "exception"
            })

            write_audit(
                vacancy_id=selected_vacancy_id,
                collection_id=target_collection_id,
                negotiation_id=negotiation_id,
                action="move",
                result="error",
                message=str(e)
            )
            st.error(f"Произошла ошибка перевода {negotiation_id}")
            logging.error(f"Ошибка перевода {negotiation_id}: {e}")

    st.divider()
    st.subheader("📊 Итоги перевода")

    st.write(f"✅ Успешно переведено: {len(success)}")
    st.write(f"❌ Ошибок: {len(errors)}")

    if errors:
        st.error("Обнаружены ошибки при переводе")

        error_df = pd.DataFrame(errors)
        st.dataframe(error_df)

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