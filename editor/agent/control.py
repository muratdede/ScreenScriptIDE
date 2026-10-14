"""Control channel: the editor forwards Telegram /pause, /resume and /stop to the script process over
a local TCP socket (one JSON line per command, protocol.Command.CONTROL). Separate from the debug
channel so it works in normal runs too, and from stdin so it never competes with input().

The command goes to the script's CONTROL. A script that never reads CONTROL cannot react to it:
/stop then interrupts the script like the stop button would, /pause and /resume get a reply
saying they are not supported.
"""

from __future__ import annotations

import logging
import signal
import socket
import threading

from .. import i18n
from ..core.protocol import Command, LineDecoder
from ..plugins.screenscript import ScriptContext

log = logging.getLogger("script")


class ControlChannel:
    def __init__(self, port: int, context: ScriptContext, host: str = "127.0.0.1"):
        self._context = context
        self._sock = socket.create_connection((host, port), timeout=10)
        self._sock.settimeout(None)
        threading.Thread(target=self._read_loop, name="ControlChannel", daemon=True).start()

    def _read_loop(self) -> None:
        decoder = LineDecoder()
        while True:
            try:
                data = self._sock.recv(4096)
            except OSError:
                return
            if not data:
                return                    # the editor closed; ScriptSession kills the process anyway
            for msg in decoder.feed(data):
                if msg.get("type") == Command.CONTROL:
                    self._deliver(str(msg.get("command", "")))

    def _deliver(self, command: str) -> None:
        control = self._context.control
        if command not in control.COMMANDS:
            return
        log.info(i18n.tr("Command received: %s"), command)
        if control.used:
            control.post(command)
            return
        if command == "stop":
            self._reply(i18n.tr("The script is being stopped."))
            # KeyboardInterrupt in the script, like the stop button. raise_signal also wakes a
            # time.sleep() on Windows; _thread.interrupt_main() waited for the sleep to end.
            signal.raise_signal(signal.SIGINT)
        else:
            self._reply(i18n.tr("This script does not handle /{command}.", command=command))

    def _reply(self, text: str) -> None:
        try:
            self._context.notifier.send(text)
        except Exception as e:  # noqa: BLE001 — NotificationError or a network error
            log.warning(i18n.tr("Reply could not be sent: %s"), e)
