import streamlit as st
import pandas as pd
import logging

def move_selected_candidates(
    edited_df,
    items,
    selected_vacancy_id,
    target_collection_id,
    access_token,
    hh_headers,
    hh_request_with_retry,
    write_audit
):
    if edited_df is None or edited_df.empty:
        st.error("Для начала сделайте анализ кандидатов")
        return

    chosen = edited_df[edited_df["selected"]]
    if chosen.empty:
        st.warning("Никто не выбран")
        return

    headers = hh_headers(access_token)
    if not headers:
        st.error("Нет access_token")
        return

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

            resp = hh_request_with_retry(
                "PUT",
                f"https://api.hh.ru/negotiations/{target_collection_id}/{negotiation_id}",
                headers=headers,
                timeout=10
            )

            if not resp:
                errors.append({"id": negotiation_id, "status": "no_response"})
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

                logging.info(
                    f"Перевод кандидата {negotiation_id} → {target_collection_id} выполнен успешно"
                )

            else:
                errors.append({"id": negotiation_id, "status": resp.status_code})

                write_audit(
                    vacancy_id=selected_vacancy_id,
                    collection_id=target_collection_id,
                    negotiation_id=negotiation_id,
                    action="move",
                    result="error",
                    message=f"HTTP {resp.status_code}"
                )

                logging.error(
                    f"Ошибка перевода {negotiation_id}: HTTP {resp.status_code}"
                )

        except Exception as e:
            errors.append({"id": negotiation_id, "status": "exception"})

            write_audit(
                vacancy_id=selected_vacancy_id,
                collection_id=target_collection_id,
                negotiation_id=negotiation_id,
                action="move",
                result="error",
                message=str(e)
            )

            logging.error(f"Ошибка перевода {negotiation_id}: {e}")

    return success, errors