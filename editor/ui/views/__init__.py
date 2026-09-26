"""Yerleşik view'lar. Yeni view eklemek için DockView'dan türetip DEFAULT_VIEWS'e ekleyin
(ya da MainWindow(view_classes=[...]) ile verin)."""

from .base import DockView
from .console_view import ConsoleView
from .debug_views import DebugView
from .images_view import ImagesView
from .problems_view import ProblemsView
from .project_view import ProjectView
from .screen_view import ScreenView

DEFAULT_VIEWS = [ProjectView, ImagesView, ScreenView, ConsoleView, DebugView, ProblemsView]

__all__ = ["DockView", "DEFAULT_VIEWS", "ProjectView", "ImagesView", "ScreenView", "ConsoleView", "DebugView", "ProblemsView"]
