"""bdb based script debugger and the TCP channel talking to the editor."""

from __future__ import annotations

import bdb
import os
import queue
import reprlib
import socket
import sys
import threading
import traceback
import types
from typing import Callable, Dict, Iterable, List, Optional, Set

from ..core.protocol import Command, Event, LineDecoder, encode
from ..i18n import tr


class DebugChannel:
    """Socket connected to the editor. Commands are read on a background thread.

    PAUSE and SET_BREAKPOINTS must be handled while the script runs too; they go to async_handler.
    Other commands are queued and read when the debugger is paused."""

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self._sock = socket.create_connection((host, port), timeout=10)
        self._sock.settimeout(None)
        self._send_lock = threading.Lock()
        self.commands: "queue.Queue[dict]" = queue.Queue()
        self.async_handler: Optional[Callable[[dict], bool]] = None
        threading.Thread(target=self._read_loop, name="DebugChannel", daemon=True).start()

    def send(self, kind: str, **payload) -> None:
        data = encode(kind, **payload)
        with self._send_lock:
            try:
                self._sock.sendall(data)
            except OSError:
                pass

    def _read_loop(self) -> None:
        decoder = LineDecoder()
        while True:
            try:
                data = self._sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                # the editor closed or the connection dropped: terminate the script too
                sys.stdout.flush()
                os._exit(3)
            for msg in decoder.feed(data):
                handler = self.async_handler
                if handler is None or not handler(msg):
                    self.commands.put(msg)


class ScriptDebugger(bdb.Bdb):
    """Stops only in user files; library frames (screenscript, adb_capture, stdlib) are not traced,
    so the script runs at almost normal speed."""

    def __init__(self, channel: DebugChannel, user_roots: Iterable[str], library_roots: Iterable[str],
                 hidden_names: Callable[[], Set[str]], stop_on_entry: bool = False):
        super().__init__()
        self._channel = channel
        self._user_roots = [self._norm(r) for r in user_roots]
        self._library_roots = [self._norm(r) for r in library_roots]
        self._hidden_names = hidden_names
        self._hidden_cache: Optional[Set[str]] = None
        self._stop_on_entry = stop_on_entry
        self._entered = False
        self._pause_requested = False
        self._user_cache: Dict[str, bool] = {}
        self._lock = threading.Lock()
        self._repr = reprlib.Repr()
        self._repr.maxstring = 200
        self._repr.maxother = 200
        self._repr.maxlist = self._repr.maxtuple = self._repr.maxdict = 30
        channel.async_handler = self._handle_async

    # ---- which frames are traced ---------------------------------------- #
    @staticmethod
    def _norm(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def is_user_file(self, filename: str) -> bool:
        cached = self._user_cache.get(filename)
        if cached is not None:
            return cached
        if filename.startswith("<"):
            result = False
        else:
            p = self._norm(filename)
            inside = lambda root: p == root or p.startswith(root + os.sep)
            result = any(inside(r) for r in self._user_roots) and not any(inside(r) for r in self._library_roots)
        self._user_cache[filename] = result
        return result

    def _is_user(self, frame: types.FrameType) -> bool:
        return self.is_user_file(frame.f_code.co_filename)

    def stop_here(self, frame):
        if not self._is_user(frame):
            return False
        if self._pause_requested:
            return True
        return super().stop_here(frame)

    def break_anywhere(self, frame):
        # always trace user frames (needed for PAUSE); never trace the others.
        return self._is_user(frame)

    def set_continue(self):
        # bdb turns tracing off completely without breakpoints; we keep it on for PAUSE.
        self._set_stopinfo(self.botframe, None, -1)

    # ---- breakpoints ----------------------------------------------------- #
    def apply_breakpoints(self, mapping: Dict[str, List[int]]) -> None:
        with self._lock:
            self.clear_all_breaks()
            for filename, lines in (mapping or {}).items():
                for line in lines:
                    err = self.set_break(os.path.abspath(filename), int(line))
                    if err:
                        self._channel.send(Event.BREAKPOINT_ERROR, file=filename, line=line, message=err)

    def _handle_async(self, msg: dict) -> bool:
        kind = msg.get("type")
        if kind == Command.PAUSE:
            self._pause_requested = True
            return True
        if kind == Command.SET_BREAKPOINTS:
            self.apply_breakpoints(msg.get("breakpoints", {}))
            return True
        if kind == Command.STOP:
            self._channel.send(Event.FINISHED, exit_code=130)
            sys.stdout.flush()
            os._exit(130)
        return False

    # ---- pausing --------------------------------------------------------- #
    def user_line(self, frame):
        if self._pause_requested:
            self._pause_requested = False
            reason = "pause"
        elif self.get_breaks(self.canonic(frame.f_code.co_filename), frame.f_lineno):
            reason = "breakpoint"
        elif not self._entered and not self._stop_on_entry:
            # bdb starts in step mode on the first user line; continue if no stop on entry is wanted
            self._entered = True
            self.set_continue()
            return
        else:
            reason = "entry" if not self._entered else "step"
        self._entered = True
        self._interact(frame, reason)

    def _interact(self, frame, reason: str) -> None:
        stack = self._user_stack(frame)
        self._channel.send(Event.PAUSED, file=frame.f_code.co_filename, line=frame.f_lineno,
                           reason=reason, stack=[self._describe(f) for f in stack])
        while True:
            msg = self._channel.commands.get()
            kind = msg.get("type")
            if kind == Command.CONTINUE:
                self.set_continue()
            elif kind == Command.STEP_INTO:
                self.set_step()
            elif kind == Command.STEP_OVER:
                self.set_next(frame)
            elif kind == Command.STEP_OUT:
                self.set_return(frame)
            elif kind == Command.EVALUATE:
                self._evaluate(msg, stack)
                continue
            else:
                continue
            break
        self._channel.send(Event.RESUMED)

    def _user_stack(self, frame) -> List[types.FrameType]:
        entries, _ = self.get_stack(frame, None)
        return [f for f, _ in reversed(entries) if self._is_user(f)]

    def _describe(self, frame) -> dict:
        return {"file": frame.f_code.co_filename, "line": frame.f_lineno,
                "function": frame.f_code.co_name, "variables": self._variables(frame)}

    def _variables(self, frame) -> List[dict]:
        is_module = frame.f_locals is frame.f_globals
        hidden = self._hidden() if is_module else set()
        out = []
        for name, value in list(frame.f_locals.items()):
            if name.startswith("__") or name in hidden:
                continue
            if is_module and isinstance(value, (types.ModuleType, type)):
                continue
            out.append({"name": name, "type": type(value).__name__, "value": self._safe_repr(value)})
        return sorted(out, key=lambda v: v["name"])

    def _hidden(self) -> Set[str]:
        if self._hidden_cache is None:
            try:
                self._hidden_cache = set(self._hidden_names())
            except Exception:  # noqa: BLE001
                self._hidden_cache = set()
        return self._hidden_cache

    def _safe_repr(self, value) -> str:
        try:
            return self._repr.repr(value)
        except Exception as e:  # noqa: BLE001
            return tr("<repr error: {error}>", error=e)

    def _evaluate(self, msg: dict, stack: List[types.FrameType]) -> None:
        expr = msg.get("expression", "")
        index = min(max(int(msg.get("frame", 0)), 0), len(stack) - 1) if stack else 0
        frame = stack[index] if stack else None
        g = frame.f_globals if frame else {}
        loc = frame.f_locals if frame else {}
        try:
            try:
                value = eval(compile(expr, "<debug>", "eval"), g, loc)
                text = self._safe_repr(value)
            except SyntaxError:
                exec(compile(expr, "<debug>", "exec"), g, loc)
                text = ""
            ok = True
        except Exception:  # noqa: BLE001
            text = "".join(traceback.format_exception_only(*sys.exc_info()[:2])).strip()
            ok = False
        self._channel.send(Event.EVAL_RESULT, id=msg.get("id"), ok=ok, value=text,
                           variables=self._variables(frame) if frame else [])
