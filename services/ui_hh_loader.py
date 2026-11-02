import streamlit as st
import pandas as pd
from services.hh_api import hh_headers, hh_request_with_retry
from state.resetters import reset_after_collection_change, reset_after_vacancy_change
from services.hh_service import load_items_from_collections

def render_hh_loader(access_token):
    st.subheader("Загрузка вакансий")

    if st.button("4) Загрузить мои вакансии"):
        headers = hh_headers(access_token)
        if not headers:
            st.error("Нет access_token")
            st.stop()

        r = hh_request_with_retry(
            "GET",
            "https://api.hh.ru/vacancies",
            params={"employer_id": "5700680", "per_page": 20},
            headers=headers,
            timeout=30,
        )
        data = r.json()
        st.session_state["hh_vacancies"] = data.get("items", [])
        st.rerun()
    vacancies = st.session_state.get("hh_vacancies", [])
    if not vacancies:
        st.info("Сначала загрузите вакансии")
        return None

    vacancy_options = {
        f'{v.get("name")} (id={v.get("id")})': v.get("id")
        for v in vacancies if v.get("id")
    }

    selected_vacancy_label = st.selectbox(
        "Выберите вакансию",
        list(vacancy_options.keys()),
        key="hh_selected_vacancy_label",
        on_change=reset_after_vacancy_change,
    )
    selected_vacancy_id = vacancy_options[selected_vacancy_label]

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
        return None
    else:
        col_by_id = {c.get("id"): c for c in collections if c.get("id")}
        col_ids = list(col_by_id.keys())

        selected_col_ids = st.multiselect(
            "Выберите коллекцию",
            options=col_ids,
            format_func=lambda x: f'{col_by_id[x].get("name")} (id={x})',
            key="hh_selected_collection_id",
            on_change=reset_after_collection_change,
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

        progress = st.progress(0)
        status = st.empty()

        def progress_cb(value):
            progress.progress(value)

        def status_cb(col_done, col_total, page, count):
            status.text(
                f"Коллекция {col_done}/{col_total} • страница {page} • откликов: {count}"
            )

        try:
            items = load_items_from_collections(
                selected_collections,
                headers,
                progress_cb=progress_cb,
                status_cb=status_cb
            )

            st.session_state["hh_items"] = items
            st.success(f"Загружено {len(items)} откликов")

        except Exception as e:
            st.error(str(e))

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

    return items, selected_vacancy_id, selected_collections