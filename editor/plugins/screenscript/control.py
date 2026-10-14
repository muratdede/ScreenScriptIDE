"""Commands sent to a running script from outside: /pause, /resume and /stop in Telegram, forwarded
by the editor. The script sees them as CONTROL:

    command = CONTROL.poll()          # "pause" / "resume" / "stop" or None, never blocks
    command = CONTROL.wait(5)         # waits up to 5 s for the next command

What a command means is up to the script (a bot may dock before pausing). A script that never
reads CONTROL is still stopped by /stop; /pause and /resume then get a reply that the script does
not handle them.
"""

from __future__ import annotations

import queue
from typing import Optional


class ScriptControl:
    COMMANDS = ("pause", "resume", "stop")

    def __init__(self):
        self._queue: "queue.Queue[str]" = queue.Queue()
        self.used = False      # the script has read CONTROL at least once (it handles the commands)

    def listen(self) -> None:
        """The script declares that it handles the commands itself (poll / wait do this too).
        Call it at start-up: until then /stop falls back to interrupting the script."""
        self.used = True

    def poll(self) -> Optional[str]:
        self.used = True
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def wait(self, timeout: Optional[float] = None) -> Optional[str]:
        self.used = True
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def post(self, command: str) -> None:
        self._queue.put(command)
