"""Editör <-> script süreci (agent) arasındaki debug protokolü.

TCP üzerinden satır başına bir JSON mesaj. Qt bağımlılığı yoktur; iki taraf da kullanır.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


class Command:
    """Editör -> agent."""
    START = "start"                  # {breakpoints, stop_on_entry}
    SET_BREAKPOINTS = "set_breakpoints"  # {breakpoints: {dosya: [satır,...]}}
    CONTINUE = "continue"
    STEP_INTO = "step_into"
    STEP_OVER = "step_over"
    STEP_OUT = "step_out"
    PAUSE = "pause"
    EVALUATE = "evaluate"            # {expression, frame, id}
    STOP = "stop"


class Event:
    """Agent -> editör."""
    READY = "ready"
    PAUSED = "paused"                # {file, line, reason, stack:[{file,line,function,variables}]}
    RESUMED = "resumed"
    EVAL_RESULT = "eval_result"      # {id, ok, value}
    FINISHED = "finished"            # {exit_code}
    BREAKPOINT_ERROR = "breakpoint_error"  # {file, line, message}


def encode(kind: str, **payload: Any) -> bytes:
    payload["type"] = kind
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


class LineDecoder:
    """Parça parça gelen baytları tam JSON mesajlarına çevirir."""

    def __init__(self):
        self._buffer = b""

    def feed(self, data: bytes) -> List[Dict[str, Any]]:
        self._buffer += data
        messages = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip():
                messages.append(json.loads(line.decode("utf-8")))
        return messages
