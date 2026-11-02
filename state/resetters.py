import streamlit as st
from .session_keys import *

def reset_analysis_state():
    st.session_state[ANALYSIS_RESULTS] = False
    st.session_state[ANALYSIS_RUNNING] = False
    st.session_state[START_ANALYSIS] = False
    st.session_state[STOP_ANALYSIS] = False
    st.session_state.pop(TOP_N, None)

def reset_after_collection_change():
    st.session_state.pop(HH_ITEMS, None)
    reset_analysis_state()

def reset_after_vacancy_change():
    st.session_state.pop(HH_COLLECTIONS, None)
    reset_after_collection_change()