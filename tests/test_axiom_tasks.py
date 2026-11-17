"""Background-task supervisor tests (phase D8).

Threads are replaced by deterministic fakes/events; nothing here touches the
network, the SQLite store or real data.
"""

from __future__ import annotations

import threading
import time

import pytest
import streamlit as st

import axiom_tasks

SESSION_KEYS = ("background_tasks", "background_task_logs")
_MISSING = object()


def _task(name: str, task_id: str = "gemini_ocr_queue") -> dict:
    return {
        "id": task_id,
        "name": name,
        "status": "Online",
        "thread": None,
        "iterations": 0,
        "last_heartbeat": "",
        "stop_event": threading.Event(),
        "pause_event": threading.Event(),
        "error_event": threading.Event(),
    }


@pytest.fixture()
def tasks_env(monkeypatch):
    monkeypatch.setattr(axiom_tasks, "init_workspace_state", lambda: None)
    monkeypatch.setattr(axiom_tasks, "get_active_entity", lambda e=None: "Alpha")
    monkeypatch.setattr(axiom_tasks, "get_active_tax_year", lambda: "FY2026")

    saved = {key: st.session_state.get(key, _MISSING) for key in SESSION_KEYS}
    st.session_state["background_tasks"] = {
        "gemini_ocr_queue": _task("Gemini OCR Queue"),
        "batch_pdf_extractor": _task("Batch PDF Extractor", "batch_pdf_extractor"),
    }
    st.session_state["background_task_logs"] = []
    try:
        yield
    finally:
        for task in st.session_state.get("background_tasks", {}).values():
            thread = task.get("thread")
            if thread is not None:
                task["stop_event"].set()
                thread.join(timeout=2)
        for key, original in saved.items():
            if original is _MISSING:
                st.session_state.pop(key, None)
            else:
                st.session_state[key] = original


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def test_append_task_log_formats_and_caps_buffer():
    buffer: list[str] = []
    axiom_tasks._append_task_log(buffer, "ocr queue", "hello")
    assert len(buffer) == 1
    assert "OCR QUEUE" in buffer[0]
    assert buffer[0].endswith("] hello")
    assert buffer[0].startswith("[")

    for index in range(450):
        axiom_tasks._append_task_log(buffer, "ocr queue", f"line-{index}")
    assert len(buffer) == 400
    assert buffer[-1].endswith("line-449")


# --------------------------------------------------------------------------- #
# worker loop
# --------------------------------------------------------------------------- #
def test_worker_loop_exits_cleanly_when_stop_already_set(tasks_env):
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]
    log: list[str] = []
    task["stop_event"].set()

    axiom_tasks._background_worker_loop(task, log, "Alpha", "FY2026")

    assert task["status"] == "Online"
    assert any("Worker stopped cleanly" in line for line in log)


def test_worker_loop_reports_fatal_error(tasks_env):
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]
    log: list[str] = []
    task["error_event"].set()

    axiom_tasks._background_worker_loop(task, log, "Alpha", "FY2026")

    assert task["status"] == "Error"
    assert any("FATAL ERROR" in line for line in log)


def test_worker_loop_pauses_resumes_and_logs_cycles(tasks_env):
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]
    log: list[str] = []
    task["pause_event"].set()

    worker = threading.Thread(
        target=axiom_tasks._background_worker_loop, args=(task, log, "Alpha", "FY2026"), daemon=True
    )
    worker.start()
    assert _wait_for(lambda: task["status"] == "Paused")

    task["pause_event"].clear()
    assert _wait_for(lambda: task["iterations"] >= 1)
    assert task["status"] == "Running"
    assert any("Cycle #001" in line for line in log)

    task["stop_event"].set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert task["status"] == "Online"


# --------------------------------------------------------------------------- #
# operator controls
# --------------------------------------------------------------------------- #
def test_start_pause_resume_stop_cycle(tasks_env, monkeypatch):
    def fake_worker(task, log, entity, tax_year):
        while not task["stop_event"].is_set():
            time.sleep(0.01)

    monkeypatch.setattr(axiom_tasks, "_background_worker_loop", fake_worker)
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]

    axiom_tasks.start_background_task("gemini_ocr_queue")
    assert task["status"] == "Running"
    assert task["thread"].is_alive()

    worker_before = task["thread"]
    axiom_tasks.start_background_task("gemini_ocr_queue")  # already running -> no-op
    assert task["thread"] is worker_before
    assert task["thread"].is_alive()

    axiom_tasks.pause_background_task("gemini_ocr_queue")
    assert task["pause_event"].is_set()
    assert task["status"] == "Paused"

    axiom_tasks.pause_background_task("gemini_ocr_queue")  # toggle back to running
    assert not task["pause_event"].is_set()
    assert task["status"] == "Running"

    axiom_tasks.pause_background_task("gemini_ocr_queue")
    axiom_tasks.start_background_task("gemini_ocr_queue")  # resume via start
    assert not task["pause_event"].is_set()
    assert task["status"] == "Running"
    assert any("Execution resumed by operator" in line for line in st.session_state["background_task_logs"])

    axiom_tasks.stop_background_task("gemini_ocr_queue")
    assert task["thread"] is None
    assert task["status"] == "Online"
    assert any("Stop signal sent" in line for line in st.session_state["background_task_logs"])


def test_stop_without_thread_resets_status(tasks_env):
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]
    task["status"] = "Paused"

    axiom_tasks.stop_background_task("gemini_ocr_queue")

    assert task["status"] == "Online"
    assert task["thread"] is None


def test_trigger_error_without_thread_flags_error(tasks_env):
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]

    axiom_tasks.trigger_background_task_error("gemini_ocr_queue")

    assert task["error_event"].is_set()
    assert task["status"] == "Error"
    assert any("ERROR state flagged" in line for line in st.session_state["background_task_logs"])


def test_trigger_error_joins_live_worker(tasks_env, monkeypatch):
    def fake_worker(task, log, entity, tax_year):
        while not task["error_event"].is_set() and not task["stop_event"].is_set():
            time.sleep(0.01)
        task["status"] = "Error"

    monkeypatch.setattr(axiom_tasks, "_background_worker_loop", fake_worker)
    task = st.session_state["background_tasks"]["gemini_ocr_queue"]
    axiom_tasks.start_background_task("gemini_ocr_queue")

    axiom_tasks.trigger_background_task_error("gemini_ocr_queue")

    assert task["status"] == "Error"
    assert not task["thread"].is_alive()


# --------------------------------------------------------------------------- #
# status summary
# --------------------------------------------------------------------------- #
def test_status_summary_idle(tasks_env):
    summary = axiom_tasks.get_background_status_summary()
    assert summary["overall"] == "Online"
    assert summary["online_count"] == 2
    assert summary["detail"] == "No active background jobs executing"


def test_status_summary_running_paused_and_errors(tasks_env):
    tasks = st.session_state["background_tasks"]

    alive = threading.Event()
    holder = threading.Thread(target=alive.wait, daemon=True)
    holder.start()

    tasks["gemini_ocr_queue"].update(status="Running", thread=holder)
    tasks["batch_pdf_extractor"].update(status="Error")
    tasks["batch_pdf_extractor"]["error_event"].set()

    summary = axiom_tasks.get_background_status_summary()
    assert summary["overall"] == "Error"
    assert summary["error_count"] == 1
    assert summary["running_count"] == 1

    alive2 = threading.Event()
    holder2 = threading.Thread(target=alive2.wait, daemon=True)
    holder2.start()

    tasks["batch_pdf_extractor"].update(status="Paused", thread=holder2)
    tasks["batch_pdf_extractor"]["error_event"].clear()
    summary = axiom_tasks.get_background_status_summary()
    assert summary["overall"] == "Running"
    assert summary["paused_count"] == 1

    tasks["gemini_ocr_queue"]["status"] = "Paused"
    summary = axiom_tasks.get_background_status_summary()
    assert summary["overall"] == "Paused"
    assert summary["paused_count"] == 2

    tasks["gemini_ocr_queue"]["status"] = "Running"  # thread dead -> normalized
    alive.set()
    holder.join(timeout=2)
    summary = axiom_tasks.get_background_status_summary()
    assert tasks["gemini_ocr_queue"]["status"] == "Online"
    assert summary["online_count"] == 1

    alive2.set()
    holder2.join(timeout=2)
