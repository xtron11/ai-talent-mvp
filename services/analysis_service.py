import streamlit as st
import pandas as pd

def run_candidate_analysis(
    items,
    selected_vacancy_id,
    access_token,
    n_candidates,
    hh_headers,
    hh_request_with_retry,
    get_full_resume,
    summarize_experience,
    extract_cover_letter,
    analyze_candidate_json,
    get_cache,
    set_cache,
):
    results = []

    # ── Загрузка текста вакансии ──
    if "vacancy_text" not in st.session_state:
        headers = hh_headers(access_token)
        r = hh_request_with_retry(
            "GET",
            f"https://api.hh.ru/vacancies/{selected_vacancy_id}",
            headers=headers,
            timeout=15
        )

        if r.status_code != 200:
            st.error("Не удалось загрузить вакансию")
            return

        data = r.json()
        st.session_state["vacancy_text"] = data.get("description", "")
        st.session_state["vacancy_name"] = data.get("name", "")

    vacancy_text = st.session_state["vacancy_text"]

    criteria = st.session_state.get("hr_criteria", "")
    total_candidates = len(items)

    progress = st.progress(0)
    status_text = st.empty()

    for i, item in enumerate(items):

        if st.session_state.get("stop_analysis"):
            st.session_state["analysis_stop_reason"] = "Анализ остановлен пользователем"
            break

        resume_stub = item.get("resume", {})
        resume_id = resume_stub.get("id", "")

        headers = hh_headers(access_token)

        if resume_id in st.session_state["resume_cache"]:
            full_resume = st.session_state["resume_cache"][resume_id]
        else:
            full_resume = get_full_resume(resume_id, headers)
            if full_resume:
                st.session_state["resume_cache"][resume_id] = full_resume

        if full_resume:
            desired_position = full_resume.get("title", "")
            experience_summary = summarize_experience(full_resume.get("experience", []))
        else:
            desired_position = resume_stub.get("title", "")
            experience_summary = []

        cached_result = get_cache(resume_id, selected_vacancy_id, criteria)

        if cached_result:
            analysis = cached_result
        else:
            cover_letter = extract_cover_letter(item, headers)

            analysis = analyze_candidate_json(
                vacancy_text=vacancy_text,
                desired_position=desired_position,
                experience_summary=experience_summary,
                cover_letter=cover_letter,
                hr_criteria=criteria,
                model="llama3:latest"
            )

            set_cache(resume_id, selected_vacancy_id, criteria, analysis)

        if analysis.get("analysis_error"):
            results.append({
                "negotiation_id": item["id"],
                "Резюме": desired_position,
                "Оценка": "ERROR",
                "Обоснование": "Ошибка анализа LLM",
                "Ссылка": f"https://hh.ru/resume/{resume_id}",
            })
            continue

        results.append({
            "negotiation_id": item["id"],
            "Резюме": desired_position,
            "Оценка": analysis["score"],
            "Обоснование": analysis["rationale_ru"],
            "Ссылка": f"https://hh.ru/resume/{resume_id}",
        })

        progress.progress((i + 1) / total_candidates)
        status_text.text(f"Проверено {i + 1} из {total_candidates}")

    if results:
        df = pd.DataFrame(results)
        df["Оценка"] = pd.to_numeric(df["Оценка"], errors="coerce")
        df_sorted = df.sort_values("Оценка", ascending=False).head(n_candidates)
        df_sorted.insert(0, "selected", False)
        st.session_state["top_n"] = df_sorted