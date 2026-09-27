"""Application-wide event bus. Views don't know each other; they only subscribe here."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    # project
    project_opened = Signal(object)          # Project
    project_config_changed = Signal(object)  # Project
    app_config_changed = Signal(object)      # AppConfig (application settings: device connection ...)

    # editor
    open_file_requested = Signal(str, int)   # path, line (0 = none)
    insert_text_requested = Signal(str)      # insert text at the cursor of the active editor
    active_file_changed = Signal(str)
    file_renamed = Signal(str, str)          # old, new
    run_requested = Signal(str, bool)        # script, debug
    status_message = Signal(str)             # short-lived message in the status bar

    # running
    run_started = Signal(str, bool)          # script, debug
    run_finished = Signal(int)               # exit code
    output = Signal(str, str)                # text, kind: out | err | sys

    # debug
    debug_paused = Signal(dict)              # PAUSED message
    debug_resumed = Signal()
    debug_frame_selected = Signal(int, dict)  # index, frame
    eval_result = Signal(dict)

    # screen
    screenshot_ready = Signal(object)        # numpy BGR
    screenshot_failed = Signal(str)
    images_changed = Signal()
    pattern_test_requested = Signal(str)     # image path: show matches on the screenshot
