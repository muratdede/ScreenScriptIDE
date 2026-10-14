"""Debug protocol between the editor <-> script process (agent).

One JSON message per line over TCP. No Qt dependency; both sides use it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


class Command:
    """Editor -> agent."""
    START = "start"                  # {breakpoints, stop_on_entry}
    SET_BREAKPOINTS = "set_breakpoints"  # {breakpoints: {file: [line,...]}}
    CONTINUE = "continue"
    STEP_INTO = "step_into"
    STEP_OVER = "step_over"
    STEP_OUT = "step_out"
    PAUSE = "pause"
    EVALUATE = "evaluate"            # {expression, frame, id}
    STOP = "stop"
    CONTROL = "control"              # {command}: pause / resume / stop for the script (control channel)


class Event:
    """Agent -> editor."""
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
    """Turns bytes arriving in chunks into complete JSON messages."""

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
