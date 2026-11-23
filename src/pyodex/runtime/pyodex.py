"""App authoring API. The builder installs this module inside page-based Pyodide."""

from __future__ import annotations

import inspect
import traceback
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

import pyodex_runtime as _runtime

P = ParamSpec("P")
T = TypeVar("T")
_startup: Callable[[], Any] | None = None


class _Document:
    def element(self, id: str) -> Any:
        """Get a required element by ID, without a CSS selector or a null check."""
        node = _runtime.document.getElementById(id)
        if not node:
            raise LookupError(f"No element with id {id!r}")
        return node

    def __getattr__(self, name: str) -> Any:
        return getattr(_runtime.document, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(_runtime.document, name, value)


document = _Document()


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(_runtime, name)


def start(fn: Callable[[], Any]) -> Callable[[], Any]:
    """Mark one function as the app entry point. Pyodex calls it after loading the file."""
    global _startup
    if _startup is not None:
        raise RuntimeError("An app entry file can have only one @start function")
    _startup = fn
    return fn


async def _run_start() -> None:
    global _startup
    fn, _startup = _startup, None
    if fn is not None:
        result = fn()
        if inspect.isawaitable(result):
            await result


def on(target: Any, event: str, *, prevent_default: bool = False) -> Callable[[Callable], Callable]:
    """Bind a Python handler, sync or async. Report errors raised after it yields."""
    def decorate(fn: Callable) -> Callable:
        async def invoke(event_arg: Any) -> None:
            try:
                result = fn(event_arg)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _runtime._report_exception(traceback.format_exc())

        # Pyodide enters async proxies in a suspendable stack. The browser does
        # not await listeners, so default cancellation must precede that handoff.
        callback = _runtime.js_callback(invoke)
        if prevent_default:
            @_runtime.on(target, event)
            def receive(event_arg: Any) -> None:
                event_arg.preventDefault()
                callback(event_arg)
        else:
            target.addEventListener(event, callback)
        return fn

    return decorate


def blocking(fn: Callable[P, Awaitable[T]]) -> Callable[P, T]:
    """Expose an async implementation as a sync API in a JSPI-enabled entry point or handler."""
    @wraps(fn)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        message = f"{fn.__name__} needs a JSPI-enabled Python context (such as @start or @on)"
        try:
            from pyodide.ffi import can_run_sync, run_sync
        except ImportError as exc:
            raise RuntimeError(message) from exc
        if not can_run_sync():
            raise RuntimeError(message)
        return run_sync(fn(*args, **kwargs))

    return call
