import time
from pathlib import Path
import streamlit as st

from services.hh_api import hh_request_with_retry, save_token_to_disk
from config import HH_CLIENT_ID, HH_CLIENT_SECRET, HH_REDIRECT_URI

def hh_logout(reason=None):
    if reason:
        st.session_state["auth_logout_reason"] = reason

    for k in ["hh_token", "hh_access_token"]:
        st.session_state.pop(k, None)

    Path("hh_token.json").unlink(missing_ok=True)

    st.query_params.clear()
    st.rerun()

def exchange_code_for_token(code: str) -> bool:
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