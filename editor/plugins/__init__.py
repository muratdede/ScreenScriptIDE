"""Backend plugins of the editor. The editor includes them as packages; they have no separate entry points.

    adb_capture    ADB access + frame capture (screencap / h264 stream)
    screenscript   SikuliX style visual automation API (uses adb_capture)

Plugins do not import Qt and do not depend on editor.core / editor.ui, so that the script process
(editor.agent.runner) stays lightweight. The only editor module they use is editor.i18n (Qt-free,
stdlib only) for translated messages. Imports between plugins are relative (from ..adb_capture).
"""
