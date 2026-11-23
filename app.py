"""Streamlit demo front-end using the same runtime as the HTTP API."""
import logging
import streamlit as st

from app.config import settings
from app.models import User
from app.db import get_session, init_db
from app.repositories import (
    get_or_create_user, get_chat_session, get_pending_fix_attempt,
    list_chat_sessions, list_messages, set_user_name,
)
from app.services.memory import start_session
from app.services.embeddings import EmbeddingService
from app.services.gemini_client import GeminiClient
from app.services.chat_service import ChatService
from app.services.rate_limit import RateLimitExceeded
from app.services.runtime import process_message

logger = logging.getLogger(__name__)


st.set_page_config(page_title="Déjà Fix", page_icon="🛠️", layout="centered")
st.title("🛠️ Déjà Fix")
st.caption("Support that remembers only customer-confirmed fixes.")


@st.cache_resource
def get_services():
    logger.info("streamlit.services_initializing embedding_model=%s gemini_model=%s gemini_key_configured=%s", settings.embedding_model, settings.gemini_model, bool(settings.gemini_api_key))
    embedder = EmbeddingService(settings.embedding_model)
    gemini = GeminiClient(
        settings.gemini_api_key,
        settings.gemini_model,
        settings.gemini_timeout_ms,
        settings.gemini_fallback_models,
    )
    services = ChatService(gemini, embedder)
    logger.info("streamlit.services_initialized")
    return services


logger.info("streamlit.app_start database_backend=%s", "sqlite" if settings.database_url.startswith("sqlite") else "external")
init_db()
rerun = False
with get_session() as db:
    if "customer_ids" not in st.session_state:
        st.session_state.customer_ids = []
    if "user_id" not in st.session_state:
        user = get_or_create_user(db)
        st.session_state.user_id = user.id
        st.session_state.customer_ids = [user.id]
        logger.info("streamlit.customer_created user_id=%s", user.id)
    else:
        user = db.get(User, st.session_state.user_id)
        if user is None:
            st.error("Customer no longer exists. Refresh to begin a new demo.")
            st.stop()

    with st.sidebar:
        st.subheader("Customer")
        with st.form("customer_name", clear_on_submit=False):
            name_input = st.text_input("Name", value=user.name or "", placeholder="Enter customer name")
            if st.form_submit_button("Save name", use_container_width=True):
                set_user_name(db, user.id, name_input)
                st.success("Customer name saved.")
                rerun = True

        st.divider()
        st.subheader("Demo customers")
        ids = st.session_state.customer_ids
        selected = st.selectbox(
            "Customer identity", ids, index=ids.index(user.id),
            format_func=lambda item: f"Customer {ids.index(item) + 1} · {item[:8]}",
        )
        if selected != user.id:
            logger.info("streamlit.customer_switched old_user_id=%s new_user_id=%s", user.id, selected)
            st.session_state.user_id = selected
            st.session_state.pop("session_id", None)
            rerun = True
        if st.button("Create another customer", use_container_width=True):
            other = get_or_create_user(db)
            logger.info("streamlit.customer_created user_id=%s", other.id)
            st.session_state.customer_ids.append(other.id)
            st.session_state.user_id = other.id
            st.session_state.pop("session_id", None)
            rerun = True

    if not rerun:
        current = (
            get_chat_session(db, st.session_state.session_id, user.id)
            if st.session_state.get("session_id") else None
        )
        if current is None:
            current = start_session(db, user.id)
            logger.info("streamlit.session_selected_or_created user_id=%s session_id=%s", user.id, current.id)
            st.session_state.session_id = current.id
        with st.sidebar:
            st.subheader("Sessions for this customer")
            sessions = list_chat_sessions(db, user.id)
            ids = [item.id for item in sessions]
            selected = st.selectbox(
                "Resume session", ids, index=ids.index(current.id),
                format_func=lambda item: f"{next(s.created_at.strftime('%d %b %H:%M') for s in sessions if s.id == item)} · {item[:8]}",
            )
            if selected != current.id:
                logger.info("streamlit.session_resumed user_id=%s session_id=%s", user.id, selected)
                st.session_state.session_id = selected
                rerun = True
            if st.button("Start new session", use_container_width=True):
                new = start_session(db, user.id)
                logger.info("streamlit.new_session user_id=%s session_id=%s", user.id, new.id)
                st.session_state.session_id = new.id
                rerun = True

        if not rerun:
            for msg in list_messages(db, current.id, user.id):
                with st.chat_message(msg.role):
                    st.markdown(msg.content)

            pending_attempt = get_pending_fix_attempt(db, current.id, user.id)
            if pending_attempt:
                st.markdown("**Did the suggested fix resolve the issue completely?**")
                resolved, unresolved = st.columns(2)
                if resolved.button("✅ Resolved", use_container_width=True, type="primary"):
                    process_message(db, user.id, current.id, "Yes, it is fixed completely.", get_services())
                    rerun = True
                if unresolved.button("❌ Not resolved", use_container_width=True):
                    process_message(db, user.id, current.id, "No, it is not fixed and the issue still occurs.", get_services())
                    rerun = True

            prompt = st.chat_input("Describe your technical issue…")
            if prompt:
                # Show the customer's message immediately while processing begins.
                with st.chat_message("user"):
                    st.markdown(prompt)

                try:
                    with st.chat_message("assistant"):
                        with st.spinner("Response arriving…"):
                            process_message(db, user.id, current.id, prompt, get_services())
                    rerun = True
                except RateLimitExceeded as exc:
                    logger.warning("streamlit.rate_limit user_id=%s session_id=%s kind=%s retry_after=%d", user.id, current.id, exc.kind, exc.retry_after)
                    db.rollback()  # do not persist a rejected request
                    st.error(f"Too many requests (HTTP 429): {exc}")
                except Exception:
                    logger.exception("streamlit.message_processing_failed user_id=%s session_id=%s", user.id, current.id)
                    db.rollback()
                    st.error("An unexpected issue occurred. Please retry. Check the console log for details.")

if rerun:
    st.rerun()
