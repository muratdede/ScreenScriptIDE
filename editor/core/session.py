"""Script'i ayrı bir süreçte çalıştırır; debug modunda agent ile TCP üzerinden konuşur."""

from __future__ import annotations

import codecs
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from .project import Project
from .protocol import Command, Event, LineDecoder, encode


class SessionState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class LaunchRequest:
    project: Project
    script: str
    debug: bool = False
    breakpoints: Dict[str, List[int]] = field(default_factory=dict)
    stop_on_entry: bool = False


class ScriptSession(QObject):
    state_changed = Signal(object)     # SessionState
    output = Signal(str, str)          # metin, tür (out | sys | err)
    started = Signal(str, bool)
    finished = Signal(int)
    paused = Signal(dict)
    resumed = Signal()
    eval_result = Signal(dict)
    breakpoint_error = Signal(dict)

    def __init__(self, install_root: str, python: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._install_root = install_root
        self._python = python or sys.executable
        self._proc: Optional[QProcess] = None
        self._server: Optional[QTcpServer] = None
        self._socket: Optional[QTcpSocket] = None
        self._decoder = LineDecoder()
        self._text_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._request: Optional[LaunchRequest] = None
        self._state = SessionState.IDLE
        self._config_file: Optional[str] = None
        self._eval_id = 0

    # ---- durum ----------------------------------------------------------- #
    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state is not SessionState.IDLE

    @property
    def is_debugging(self) -> bool:
        return self.is_active and bool(self._request and self._request.debug)

    def _set_state(self, state: SessionState) -> None:
        if state is not self._state:
            self._state = state
            self.state_changed.emit(state)

    # ---- başlat / durdur ------------------------------------------------- #
    def start(self, request: LaunchRequest) -> None:
        if self.is_active:
            raise RuntimeError("Zaten çalışan bir script var")
        self._request = request
        self._decoder = LineDecoder()
        self._text_decoder.reset()

        # Kaydedilmemiş ayarlar da kullanılsın diye config geçici dosyaya yazılır
        fd, self._config_file = tempfile.mkstemp(prefix="screenscript_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(request.project.config.to_dict(), f)

        args = ["-u", "-m", "editor.agent.runner", "--project", request.project.root,
                "--script", request.script, "--config", self._config_file]
        if request.debug:
            self._server = QTcpServer(self)
            self._server.newConnection.connect(self._on_connection)
            if not self._server.listen(QHostAddress.LocalHost, 0):
                raise RuntimeError(f"Debug sunucusu açılamadı: {self._server.errorString()}")
            args += ["--debug-port", str(self._server.serverPort())]

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(request.project.root)
        env = QProcessEnvironment.systemEnvironment()
        paths = [self._install_root, request.project.root]
        if env.contains("PYTHONPATH"):
            paths.append(env.value("PYTHONPATH"))
        env.insert("PYTHONPATH", os.pathsep.join(paths))
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc

        mode = "Debug" if request.debug else "Çalıştır"
        self.output.emit(f"▶ {mode}: {request.project.relative(request.script)}\n", "sys")
        self._set_state(SessionState.RUNNING)
        proc.start(self._python, args)
        self.started.emit(request.script, request.debug)

    def stop(self) -> None:
        if not self._proc:
            return
        if self._socket is not None:
            self._send(Command.STOP)
            QTimer.singleShot(800, self._kill)
        else:
            self._kill()

    def _kill(self) -> None:
        if self._proc and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    # ---- debug komutları ------------------------------------------------- #
    def continue_(self):
        self._resume(Command.CONTINUE)

    def step_over(self):
        self._resume(Command.STEP_OVER)

    def step_into(self):
        self._resume(Command.STEP_INTO)

    def step_out(self):
        self._resume(Command.STEP_OUT)

    def pause(self):
        if self._state is SessionState.RUNNING:
            self._send(Command.PAUSE)

    def evaluate(self, expression: str, frame: int = 0) -> int:
        self._eval_id += 1
        self._send(Command.EVALUATE, expression=expression, frame=frame, id=self._eval_id)
        return self._eval_id

    def set_breakpoints(self, breakpoints: Dict[str, List[int]]) -> None:
        if self._socket is not None:
            self._send(Command.SET_BREAKPOINTS, breakpoints=breakpoints)

    def write_stdin(self, text: str) -> None:
        if self._proc and self._proc.state() == QProcess.Running:
            self._proc.write(text.encode("utf-8"))

    def _resume(self, command: str) -> None:
        if self._state is SessionState.PAUSED:
            self._send(command)

    def _send(self, kind: str, **payload) -> None:
        if self._socket is not None and self._socket.state() == QTcpSocket.ConnectedState:
            self._socket.write(encode(kind, **payload))
            self._socket.flush()

    # ---- süreç olayları -------------------------------------------------- #
    def _on_stdout(self) -> None:
        data = bytes(self._proc.readAllStandardOutput())
        text = self._text_decoder.decode(data)
        if text:
            self.output.emit(text, "out")

    def _on_error(self, error) -> None:
        if error == QProcess.FailedToStart:
            self.output.emit(f"Python başlatılamadı: {self._python}\n", "err")
            self._on_finished(-1, None)

    def _on_finished(self, code, _status) -> None:
        if self._state is SessionState.IDLE:
            return
        tail = self._text_decoder.decode(b"", final=True)
        if tail:
            self.output.emit(tail, "out")
        self.output.emit(f"■ Bitti (çıkış kodu {code})\n\n", "sys")
        self._cleanup()
        self._set_state(SessionState.IDLE)
        self.finished.emit(int(code))

    def _cleanup(self) -> None:
        if self._socket is not None:
            self._socket.abort()
            self._socket.deleteLater()
            self._socket = None
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._proc is not None:
            self._proc.deleteLater()
            self._proc = None
        if self._config_file and os.path.exists(self._config_file):
            try:
                os.remove(self._config_file)
            except OSError:
                pass
        self._config_file = None

    # ---- debug soketi ---------------------------------------------------- #
    def _on_connection(self) -> None:
        sock = self._server.nextPendingConnection()
        if self._socket is not None:
            sock.abort()
            return
        self._socket = sock
        sock.readyRead.connect(self._on_socket_data)

    def _on_socket_data(self) -> None:
        for msg in self._decoder.feed(bytes(self._socket.readAll())):
            self._handle_event(msg)

    def _handle_event(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == Event.READY:
            self._send(Command.START, breakpoints=self._request.breakpoints,
                       stop_on_entry=self._request.stop_on_entry)
        elif kind == Event.PAUSED:
            self._set_state(SessionState.PAUSED)
            self.paused.emit(msg)
        elif kind == Event.RESUMED:
            self._set_state(SessionState.RUNNING)
            self.resumed.emit()
        elif kind == Event.EVAL_RESULT:
            self.eval_result.emit(msg)
        elif kind == Event.BREAKPOINT_ERROR:
            self.breakpoint_error.emit(msg)
