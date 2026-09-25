"""Çalıştırma/debug kararlarını veren controller. UI eylemleri buraya bağlanır."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QObject

from .context import EditorContext
from .session import LaunchRequest, SessionState


class RunController(QObject):
    def __init__(self, ctx: EditorContext, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._frame_index = 0
        s = ctx.session
        bus = ctx.bus
        s.output.connect(bus.output)
        s.started.connect(bus.run_started)
        s.finished.connect(bus.run_finished)
        s.paused.connect(self._on_paused)
        s.resumed.connect(bus.debug_resumed)
        s.eval_result.connect(bus.eval_result)
        s.breakpoint_error.connect(
            lambda m: bus.output.emit(f"Breakpoint geçersiz: {os.path.basename(m['file'])}:{m['line']} "
                                      f"({m['message']})\n", "err"))
        bus.debug_frame_selected.connect(lambda i, _f: setattr(self, "_frame_index", i))
        ctx.breakpoints.changed.connect(self._on_breakpoints_changed)

    # ---- hangi script ---------------------------------------------------- #
    TARGET_CURRENT = "current"   # aktif sekme (proje içindeki .py ise), değilse ana script
    TARGET_MAIN = "main"         # her zaman projenin ana script'i

    @property
    def target_mode(self) -> str:
        mode = self._ctx.settings.value("run/target_mode", self.TARGET_CURRENT)
        return mode if mode in (self.TARGET_CURRENT, self.TARGET_MAIN) else self.TARGET_CURRENT

    def set_target_mode(self, mode: str) -> None:
        self._ctx.settings.setValue("run/target_mode", mode)

    def target_script(self) -> Optional[str]:
        """Aktif sekme proje içindeki bir .py ise onu, değilse projenin ana script'ini döndürür
        (target_mode == "main" ise her zaman ana script'i)."""
        project = self._ctx.project
        if project is None:
            return None
        docs = self._ctx.documents
        current = docs.current_path() if docs else None
        if self.target_mode == self.TARGET_CURRENT and current and current.endswith(".py") \
                and project.contains(current):
            return current
        main = project.main_script_path
        return main if main and os.path.isfile(main) else None

    # ---- eylemler -------------------------------------------------------- #
    def run(self, debug: bool = False, script: Optional[str] = None, stop_on_entry: bool = False) -> bool:
        session = self._ctx.session
        if session.state is SessionState.PAUSED:
            session.continue_()
            return True
        if session.is_active:
            return False
        script = script or self.target_script()
        if not script:
            self._ctx.bus.output.emit("Çalıştırılacak script yok. Bir proje açın ve .py dosyası seçin.\n", "err")
            return False
        if self._ctx.documents and not self._ctx.documents.save_all():
            return False
        self._frame_index = 0
        session.start(LaunchRequest(project=self._ctx.project, script=script, debug=debug,
                                    breakpoints=self._ctx.breakpoints.as_dict(),
                                    stop_on_entry=stop_on_entry))
        return True

    def stop(self):
        self._ctx.session.stop()

    def continue_(self):
        self._ctx.session.continue_()

    def pause(self):
        self._ctx.session.pause()

    def step_over(self):
        self._ctx.session.step_over()

    def step_into(self):
        self._ctx.session.step_into()

    def step_out(self):
        self._ctx.session.step_out()

    def evaluate(self, expression: str) -> None:
        self._ctx.session.evaluate(expression, self._frame_index)

    # ---- iç olaylar ------------------------------------------------------ #
    def _on_paused(self, msg: dict) -> None:
        self._frame_index = 0
        self._ctx.bus.debug_paused.emit(msg)

    def _on_breakpoints_changed(self, _path: str) -> None:
        if self._ctx.session.is_debugging:
            self._ctx.session.set_breakpoints(self._ctx.breakpoints.as_dict())
