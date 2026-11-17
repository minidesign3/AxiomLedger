"""
axiom_tasks
===========

Non-UI background-task supervisor: the thread worker loop, the operator
start / pause / stop / error controls and the aggregate status summary consumed
by the global status indicator. Task state and the terminal log ring buffer
live in ``st.session_state`` so threads survive Streamlit reruns.

Extracted from ``utils.py`` (phase D8) with byte-identical bodies and
re-exported there for existing call sites.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import streamlit as st

from axiom_core import DEFAULT_LOCAL_LLM_MODEL, GEMINI_OCR_MODEL
from axiom_state import get_active_entity, get_active_tax_year, init_workspace_state

_LOG_LOCK = threading.Lock()

def _append_task_log(log_buffer: list[str], task_name: str, message: str) -> None:
    """Thread-safe helper to append a formatted print line to the shared terminal log list."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] [{task_name.upper()}] {message}"
    with _LOG_LOCK:
        log_buffer.append(line)
        if len(log_buffer) > 400:
            del log_buffer[:-400]

def _background_worker_loop(
    task_state: dict,
    log_buffer: list[str],
    entity_snapshot: str,
    tax_year_snapshot: str,
) -> None:
    """
    Background worker target executed via threading.Thread.
    Emits live print logs into `log_buffer` while respecting pause_event, stop_event, and error_event.
    """
    task_name = task_state["name"]
    stop_event: threading.Event = task_state["stop_event"]
    pause_event: threading.Event = task_state["pause_event"]
    error_event: threading.Event = task_state["error_event"]

    _append_task_log(
        log_buffer,
        task_name,
        f"Worker thread started (ident={threading.get_ident()}) for {entity_snapshot} [FY {tax_year_snapshot}].",
    )

    step_messages = {
        "batch_pdf_extractor": [
            "Scanning local ingestion spool for unindexed PDF statements...",
            "Validating pdfplumber table bounding boxes & column alignment...",
            "Normalizing Date, Amount, Description, and Account Code fields...",
            "Verifying checksum integrity against master entity ledger...",
        ],
        "gemini_ocr_queue": [
            "Polling screenshot queue for .png / .jpg financial captures...",
            f"Prepared multimodal payload for {GEMINI_OCR_MODEL} endpoint...",
            "Parsing structured CSV OCR transaction response (Date,Amount,Description)...",
            "Applying Categorization Engine rules & dynamic AI routing...",
        ],
        "local_llm_categorizer": [
            "Inspecting ledger queue for Uncategorized transaction rows...",
            f"Dispatching Description-only payload to local Qwen endpoint ({DEFAULT_LOCAL_LLM_MODEL})...",
            "Resolved IRD tax category mapping from local model response...",
            "Committed categorized Account Code update to workspace buffer...",
        ],
    }
    messages = step_messages.get(task_state["id"], ["Executing background cycle..."])

    try:
        while not stop_event.is_set():
            if error_event.is_set():
                raise RuntimeError("Fault condition triggered in worker pipeline.")

            if pause_event.is_set():
                task_state["status"] = "Paused"
                time.sleep(0.35)
                continue

            task_state["status"] = "Running"
            idx = task_state["iterations"] % len(messages)
            task_state["iterations"] += 1
            task_state["last_heartbeat"] = datetime.now().strftime("%H:%M:%S")

            _append_task_log(
                log_buffer,
                task_name,
                f"Cycle #{task_state['iterations']:03d} ({entity_snapshot} // {tax_year_snapshot}): {messages[idx]}",
            )

            for _ in range(15):
                if stop_event.is_set() or pause_event.is_set() or error_event.is_set():
                    break
                time.sleep(0.1)

        if not error_event.is_set():
            task_state["status"] = "Online"
            _append_task_log(log_buffer, task_name, "Worker stopped cleanly. Status reset to Online.")
    except Exception as exc:
        task_state["status"] = "Error"
        _append_task_log(log_buffer, task_name, f"FATAL ERROR: {exc}")

def start_background_task(task_id: str) -> None:
    """Start or resume a background thread task and store its reference in st.session_state."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    if worker is not None and worker.is_alive() and task_state["pause_event"].is_set():
        task_state["pause_event"].clear()
        task_state["error_event"].clear()
        task_state["status"] = "Running"
        _append_task_log(log_buffer, task_state["name"], "Execution resumed by operator.")
        return

    if worker is not None and worker.is_alive():
        return

    task_state["stop_event"] = threading.Event()
    task_state["pause_event"] = threading.Event()
    task_state["error_event"] = threading.Event()
    task_state["status"] = "Running"

    entity_snapshot = get_active_entity()
    tax_year_snapshot = get_active_tax_year()

    new_thread = threading.Thread(
        target=_background_worker_loop,
        args=(task_state, log_buffer, entity_snapshot, tax_year_snapshot),
        name=f"axiom_{task_id}",
        daemon=True,
    )
    task_state["thread"] = new_thread
    new_thread.start()

def pause_background_task(task_id: str) -> None:
    """Pause a running background thread task."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    if worker is not None and worker.is_alive():
        if not task_state["pause_event"].is_set():
            task_state["pause_event"].set()
            task_state["status"] = "Paused"
            _append_task_log(log_buffer, task_state["name"], "Task paused by operator.")
        else:
            task_state["pause_event"].clear()
            task_state["status"] = "Running"
            _append_task_log(log_buffer, task_state["name"], "Task unpaused by operator.")

def stop_background_task(task_id: str) -> None:
    """Stop/kill a running or paused background thread task and return state to Online."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    task_state["stop_event"].set()
    task_state["pause_event"].clear()
    task_state["error_event"].clear()

    if worker is not None and worker.is_alive():
        _append_task_log(log_buffer, task_state["name"], "SIGTERM / Stop signal sent to worker thread...")
        worker.join(timeout=1.0)

    task_state["thread"] = None
    task_state["status"] = "Online"

def trigger_background_task_error(task_id: str) -> None:
    """Simulate or flag an Error state on a background task."""
    init_workspace_state()
    task_state = st.session_state["background_tasks"][task_id]
    log_buffer = st.session_state["background_task_logs"]
    worker: threading.Thread | None = task_state.get("thread")

    task_state["error_event"].set()
    task_state["pause_event"].clear()
    if worker is not None and worker.is_alive():
        worker.join(timeout=0.8)
    else:
        _append_task_log(log_buffer, task_state["name"], "ERROR state flagged on worker pipeline.")
    task_state["status"] = "Error"

def get_background_status_summary() -> dict[str, object]:
    """
    Read the live status of all background threads in st.session_state and return
    aggregate telemetry for the Global Status Indicator.
    """
    init_workspace_state()
    tasks = st.session_state["background_tasks"]

    running_names: list[str] = []
    paused_names: list[str] = []
    error_names: list[str] = []
    online_names: list[str] = []

    for task_id, task_info in tasks.items():
        worker: threading.Thread | None = task_info.get("thread")
        alive = worker is not None and worker.is_alive()

        if task_info["status"] in ("Running", "Paused") and not alive:
            task_info["status"] = "Error" if task_info["error_event"].is_set() else "Online"

        st_label = task_info["status"]
        if st_label == "Running":
            running_names.append(task_info["name"])
        elif st_label == "Paused":
            paused_names.append(task_info["name"])
        elif st_label == "Error":
            error_names.append(task_info["name"])
        else:
            online_names.append(task_info["name"])

    if error_names:
        overall = "Error"
        headline = f"FAULT: {len(error_names)} TASK(S) IN ERROR"
        detail = ", ".join(error_names)
    elif running_names:
        overall = "Running"
        headline = f"ACTIVE: {len(running_names)} RUNNING IN BACKGROUND"
        detail = ", ".join(running_names)
    elif paused_names:
        overall = "Paused"
        headline = f"SUSPENDED: {len(paused_names)} TASK(S) PAUSED"
        detail = ", ".join(paused_names)
    else:
        overall = "Online"
        headline = f"IDLE // {len(online_names)} DAEMONS ONLINE"
        detail = "No active background jobs executing"

    return {
        "overall": overall,
        "headline": headline,
        "detail": detail,
        "running_count": len(running_names),
        "paused_count": len(paused_names),
        "error_count": len(error_names),
        "online_count": len(online_names),
    }
