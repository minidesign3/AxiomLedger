from datetime import datetime

import streamlit as st

from utils import (
    get_background_status_summary,
    init_workspace_state,
    pause_background_task,
    render_workspace_header,
    require_auth,
    start_background_task,
    stop_background_task,
    trigger_background_task_error,
)

st.set_page_config(
    page_title="Axiom Ledger | Script Manager",
    page_icon="▪",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_auth()
init_workspace_state()
render_workspace_header(show_status_indicator=True)

st.markdown('<div class="axiom-eyebrow">MODULE 03 // BACKGROUND THREAD SUPERVISOR</div>', unsafe_allow_html=True)
st.title("Background Script Manager")
st.caption(
    "⚠ Demonstration mode: these worker threads emit heartbeat and log telemetry to "
    "showcase the supervisor UI. They do not yet perform live PDF extraction, OCR, or "
    "categorization — wire them to the real pipelines before relying on them in production."
)
st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)

summary = get_background_status_summary()

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric(label="ONLINE (IDLE)", value=str(summary["online_count"]), delta="READY")
with m2:
    st.metric(label="RUNNING THREADS", value=str(summary["running_count"]), delta="ACTIVE")
with m3:
    st.metric(label="PAUSED THREADS", value=str(summary["paused_count"]), delta="SUSPENDED")
with m4:
    st.metric(label="ERROR / FAULT", value=str(summary["error_count"]), delta="MONITORED")

st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)
st.markdown('<div class="axiom-eyebrow">REGISTERED BACKGROUND WORKER PIPELINES</div>', unsafe_allow_html=True)

tasks = st.session_state["background_tasks"]

status_badge_palette = {
    "Online": ("#8E939F", "#16181C", "#3A3F4B"),
    "Running": ("#E2E4E9", "#282C35", "#9CA3AF"),
    "Paused": ("#B8BCC6", "#22252C", "#6C727F"),
    "Error": ("#E2E4E9", "#2E2528", "#8A6B70"),
}

for task_id, task_info in tasks.items():
    worker = task_info.get("thread")
    is_alive = worker is not None and worker.is_alive()
    thread_ident = worker.ident if (worker is not None and is_alive) else "—"
    state_label = task_info["status"]
    fg_col, bg_col, border_col = status_badge_palette.get(
        state_label, status_badge_palette["Online"]
    )

    with st.container():
        st.markdown(
            f"""
            <div class="axiom-panel" style="padding: 1.15rem 1.35rem; margin-bottom: 0.65rem;">
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.35rem;">
                    <div>
                        <span class="axiom-eyebrow" style="margin-right: 0.75rem;">TASK ID // {task_id.upper()}</span>
                        <span style="font-size: 0.75rem; color: #7D828F;">
                            THREAD IDENT: <code>{thread_ident}</code> &nbsp;|&nbsp;
                            CYCLES: <code>{task_info['iterations']}</code> &nbsp;|&nbsp;
                            LAST HEARTBEAT: <code>{task_info['last_heartbeat']}</code>
                        </span>
                    </div>
                    <span style="
                        font-size: 0.72rem;
                        font-weight: 600;
                        letter-spacing: 0.1em;
                        text-transform: uppercase;
                        color: {fg_col};
                        background-color: {bg_col};
                        border: 1px solid {border_col};
                        border-radius: 3px;
                        padding: 0.18rem 0.6rem;
                    ">▪ {state_label}</span>
                </div>
                <h3 style="margin: 0.1rem 0 0.25rem 0; font-size: 1.05rem;">{task_info['name']}</h3>
                <p style="margin: 0; color: #A0A5B1; font-size: 0.88rem;">{task_info['description']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        b_col1, b_col2, b_col3, b_col4, _ = st.columns([1.0, 1.0, 1.1, 1.1, 2.8])
        with b_col1:
            if st.button(
                "Start",
                key=f"btn_start_{task_id}",
                use_container_width=True,
                disabled=(state_label == "Running"),
            ):
                start_background_task(task_id)
                st.rerun()
        with b_col2:
            pause_label = "Resume" if state_label == "Paused" else "Pause"
            if st.button(
                pause_label,
                key=f"btn_pause_{task_id}",
                use_container_width=True,
                disabled=state_label not in ("Running", "Paused"),
            ):
                pause_background_task(task_id)
                st.rerun()
        with b_col3:
            if st.button(
                "Stop / Kill",
                key=f"btn_stop_{task_id}",
                use_container_width=True,
                disabled=(state_label == "Online" and not is_alive),
            ):
                stop_background_task(task_id)
                st.rerun()
        with b_col4:
            if st.button(
                "Simulate Error",
                key=f"btn_err_{task_id}",
                use_container_width=True,
                disabled=(state_label == "Error"),
            ):
                trigger_background_task_error(task_id)
                st.rerun()

st.markdown('<div class="axiom-divider"></div>', unsafe_allow_html=True)

# -------------------------------------------------------------------------
# TERMINAL-LIKE LIVE LOG STREAM
# -------------------------------------------------------------------------
term_hdr_col, term_ctrl_col1, term_ctrl_col2, term_ctrl_col3 = st.columns([2.6, 0.9, 0.9, 1.0])
with term_hdr_col:
    st.markdown(
        '<div class="axiom-eyebrow">LIVE THREAD STDOUT // TERMINAL LOG BUFFER</div>',
        unsafe_allow_html=True,
    )
with term_ctrl_col1:
    if st.button("Refresh Logs", use_container_width=True):
        st.rerun()
with term_ctrl_col2:
    if st.button("Clear Terminal", use_container_width=True):
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state["background_task_logs"] = [
            f"[{ts}] [SYSTEM] Terminal log buffer cleared by operator."
        ]
        st.rerun()
with term_ctrl_col3:
    auto_stream = st.checkbox(
        "Live Poll (1.5s)",
        value=st.session_state.get("_script_mgr_autopoll", False),
        key="_script_mgr_autopoll",
    )

_poll_interval = 1.5 if (auto_stream and summary["running_count"] > 0) else None


@st.fragment(run_every=_poll_interval)
def _render_live_log() -> None:
    """Auto-refresh only the log terminal (not the whole page) while polling is on.

    Replaces the previous `time.sleep(1.5); st.rerun()` pattern, which blocked the
    server script thread and forced a full-page rerun (losing scroll position /
    flicker) every 1.5s. `st.fragment(run_every=...)` isolates the rerun scope to
    just this block.
    """
    log_lines = st.session_state.get("background_task_logs", [])
    terminal_text = "\n".join(log_lines[-80:]) if log_lines else "[NO LOG OUTPUT]"

    st.code(terminal_text, language="log")

    st.text_area(
        "Raw Thread Log Buffer (Read-Only Stream)",
        value=terminal_text,
        height=200,
        disabled=True,
        label_visibility="collapsed",
    )


_render_live_log()
