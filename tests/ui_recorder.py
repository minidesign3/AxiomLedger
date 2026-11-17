"""Recording fake of the ``streamlit`` API for headless UI-render tests.

Every ``st.<call>`` made by a render function is appended to ``FakeSt.log`` as
``(name, args, kwargs)`` (context managers also log ``ENTER``/``EXIT``). Widgets
return scripted values so a render path can be driven deterministically:

* ``clicks``  – set of widget keys whose ``st.button`` returns ``True``.
* ``values``  – ``{widget_key: value}`` overriding input/select return values.
* ``st.rerun()`` raises :class:`RerunSignal` (as the real one aborts the script).

Used both for permanent smoke tests and (via ``run_render``) for golden-master
comparisons of a refactored view against the pre-refactor implementation.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


class RerunSignal(Exception):
    """Raised by ``FakeSt.rerun`` to mimic Streamlit aborting the script run."""


class _SessionState(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def _norm(obj: Any, options: Any = None) -> Any:
    if isinstance(obj, pd.DataFrame):
        return ("DF", list(obj.columns), obj.astype(object).where(obj.notna(), None).values.tolist())
    if isinstance(obj, dict):
        return {str(k): _norm(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_norm(v) for v in obj]
    if callable(obj):
        if options is not None:
            try:
                return ("CALLABLE", [obj(o) for o in options])
            except Exception:  # noqa: BLE001
                return ("CALLABLE", "err")
        return ("CALLABLE",)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(type(obj).__name__)


class _Ctx:
    """Context-manager / container object that also records widget calls."""

    def __init__(self, fake: FakeSt, name: str) -> None:
        self._fake = fake
        self._name = name

    def __enter__(self) -> _Ctx:
        self._fake.log.append(("ENTER", self._name))
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._fake.log.append(("EXIT", self._name))
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fake, name)


class _Progress:
    """Minimal progress-bar handle (``st.progress`` returns one in real Streamlit)."""

    def __init__(self, fake: FakeSt, value: float, text: str | None) -> None:
        self._fake = fake
        fake.log.append(("progress", _norm((value,)), _norm({"text": text})))

    def progress(self, value: float, text: str | None = None) -> None:
        self._fake.log.append(("progress.update", _norm((value,)), _norm({"text": text})))

    def empty(self) -> None:
        self._fake.log.append(("progress.empty",))


class _ColumnConfig:
    def __init__(self, fake: FakeSt) -> None:
        self._fake = fake

    def __getattr__(self, name: str) -> Any:
        def _make(*args: Any, **kwargs: Any) -> Any:
            return ("column_config." + name, _norm(args), _norm(kwargs))

        return _make


class FakeSt:
    def __init__(
        self,
        clicks: set[str] | None = None,
        values: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
    ) -> None:
        self.log: list[Any] = []
        self.clicks = set(clicks or ())
        self.values = dict(values or {})
        self.session_state = _SessionState(session or {})
        self.column_config = _ColumnConfig(self)

    # ---- layout --------------------------------------------------------
    def columns(self, spec: Any, **kwargs: Any) -> list[_Ctx]:
        n = spec if isinstance(spec, int) else len(spec)
        self.log.append(("columns", _norm(spec), _norm(kwargs)))
        return [_Ctx(self, f"col{i}") for i in range(n)]

    def tabs(self, labels: Any, **kwargs: Any) -> list[_Ctx]:
        self.log.append(("tabs", _norm(labels), _norm(kwargs)))
        return [_Ctx(self, f"tab{i}") for i in range(len(labels))]

    def _ctx(self, name: str, args: Any, kwargs: Any) -> _Ctx:
        self.log.append((name, _norm(args), _norm(kwargs)))
        return _Ctx(self, name)

    def container(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("container", a, k)

    def popover(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("popover", a, k)

    def expander(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("expander", a, k)

    def form(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("form", a, k)

    def spinner(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("spinner", a, k)

    def empty(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("empty", a, k)

    def status(self, *a: Any, **k: Any) -> _Ctx:
        return self._ctx("status", a, k)

    # ---- control flow --------------------------------------------------
    def rerun(self) -> None:
        self.log.append(("rerun",))
        raise RerunSignal

    def stop(self) -> None:
        self.log.append(("stop",))
        raise RerunSignal

    # ---- widgets -------------------------------------------------------
    def _key(self, kwargs: dict[str, Any]) -> Any:
        return kwargs.get("key")

    def button(self, label: str, *a: Any, **k: Any) -> bool:
        self.log.append(("button", _norm((label, *a)), _norm(k)))
        return self._key(k) in self.clicks

    def download_button(self, label: str, *a: Any, **k: Any) -> bool:
        self.log.append(("download_button", _norm((label,)), _norm({**k, "data": None})))
        return False

    def form_submit_button(self, label: str, *a: Any, **k: Any) -> bool:
        return self.button(label, *a, **k)

    def text_input(self, label: str, value: str = "", *a: Any, **k: Any) -> Any:
        self.log.append(("text_input", _norm((label, value, *a)), _norm(k)))
        return self.values.get(self._key(k), value)

    def text_area(self, label: str, value: str = "", *a: Any, **k: Any) -> Any:
        self.log.append(("text_area", _norm((label, value, *a)), _norm(k)))
        return self.values.get(self._key(k), value)

    def number_input(self, label: str, *a: Any, **k: Any) -> Any:
        self.log.append(("number_input", _norm((label, *a)), _norm(k)))
        return self.values.get(self._key(k), k.get("value", 0.0))

    def checkbox(self, label: str, value: bool = False, *a: Any, **k: Any) -> Any:
        self.log.append(("checkbox", _norm((label, value, *a)), _norm(k)))
        return self.values.get(self._key(k), value)

    def toggle(self, label: str, value: bool = False, *a: Any, **k: Any) -> Any:
        self.log.append(("toggle", _norm((label, value, *a)), _norm(k)))
        return self.values.get(self._key(k), value)

    def selectbox(self, label: str, options: Any = (), index: int = 0, *a: Any, **k: Any) -> Any:
        opts = list(options)
        self.log.append(("selectbox", _norm((label, opts, index, *a)), _norm(k, opts)))
        if self._key(k) in self.values:
            return self.values[self._key(k)]
        if not opts or index is None:
            return None
        return opts[index]

    def radio(self, label: str, options: Any = (), index: int = 0, *a: Any, **k: Any) -> Any:
        opts = list(options)
        self.log.append(("radio", _norm((label, opts, index, *a)), _norm(k, opts)))
        if self._key(k) in self.values:
            return self.values[self._key(k)]
        return opts[index] if opts else None

    def multiselect(self, label: str, options: Any = (), *a: Any, **k: Any) -> Any:
        opts = list(options)
        self.log.append(("multiselect", _norm((label, opts, *a)), _norm(k, opts)))
        return self.values.get(self._key(k), k.get("default", []))

    def file_uploader(self, label: str, *a: Any, **k: Any) -> Any:
        self.log.append(("file_uploader", _norm((label, *a)), _norm(k)))
        return self.values.get(self._key(k))

    def data_editor(self, data: Any, *a: Any, **k: Any) -> Any:
        self.log.append(("data_editor", _norm((data, *a)), _norm(k)))
        return self.values.get(self._key(k), data)

    def progress(self, value: float = 0.0, text: str | None = None, **k: Any) -> _Progress:
        return _Progress(self, value, text)

    def dataframe(self, data: Any, *a: Any, **k: Any) -> None:
        self.log.append(("dataframe", _norm((data, *a)), _norm(k)))

    # ---- generic sink for markdown/caption/success/... ------------------
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> None:
            self.log.append((name, _norm(args), _norm(kwargs)))

        return _call


def run_render(fn: Any, fake: FakeSt, *args: Any, **kwargs: Any) -> list[Any]:
    """Run ``fn`` and return the recorded log (RerunSignal is swallowed, logged)."""
    try:
        fn(*args, **kwargs)
    except RerunSignal:
        fake.log.append(("RERUN_ABORT",))
    return fake.log
