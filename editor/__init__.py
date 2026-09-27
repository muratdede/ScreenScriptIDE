"""ScreenScript IDE — a SikuliX style editor. This is the application itself.

Running (from the repository root):
    pip install -r requirements.txt
    python main.pyw [project_folder]

Backend work (ADB capture, visual script API) lives as plugins under editor.plugins.
Note: this file, the Qt-free modules of editor.core, editor.i18n, editor.agent and editor.plugins do
not import Qt, so that the script process stays lightweight.
User-visible texts go through editor.i18n.tr (English source strings, catalogs in editor/i18n/locales).
"""

__version__ = "0.1.0"
