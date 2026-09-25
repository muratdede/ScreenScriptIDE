"""EditorContext: tüm servislerin tek erişim noktası (view'lar ve controller'lar bunu alır)."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject, QSettings

from .app_settings import AppConfig, create_ide_settings, move_session_files
from .breakpoints import BreakpointStore
from .device_input import DeviceInput
from .completion import FallbackCompletionProvider, create_default_provider
from .events import EventBus
from .grabber import ScreenGrabber
from .image_repository import ImageRepository
from .project import Project
from .session import ScriptSession


class DocumentService:
    """Açık belgeleri yöneten bileşenin arayüzü (UI'daki EditorTabs uygular).
    Not: Qt sınıflarıyla birlikte miras alındığı için ABCMeta kullanılmıyor."""

    def current_path(self) -> Optional[str]:
        raise NotImplementedError

    def open_path(self, path: str, line: int = 0) -> None:
        raise NotImplementedError

    def save_all(self) -> bool:
        raise NotImplementedError

    def insert_text(self, text: str) -> None:
        raise NotImplementedError

    def open_paths(self) -> List[str]:
        raise NotImplementedError


class EditorContext(QObject):
    def __init__(self, install_root: str, parent=None):
        super().__init__(parent)
        self.install_root = install_root
        self.settings: QSettings = create_ide_settings()   # %LOCALAPPDATA%\ScreenScript\ide.ini
        move_session_files(install_root)                    # oturum dosyaları çalışma alanında kalmasın
        self.app_config = AppConfig.load()                  # %LOCALAPPDATA%\ScreenScript\settings.json
        self.bus = EventBus(self)
        self.breakpoints = BreakpointStore(self)
        self.session = ScriptSession(install_root, parent=self)
        self.grabber = ScreenGrabber(self)
        self.device_input = DeviceInput(self)   # Ekran view'ındaki tıklayıcı modu
        self.images = ImageRepository(self)
        self.completion: FallbackCompletionProvider = create_default_provider(None, [install_root])
        self.project: Optional[Project] = None
        self.documents: Optional[DocumentService] = None

        self.grabber.grabbed.connect(self.bus.screenshot_ready)
        self.grabber.failed.connect(self.bus.screenshot_failed)
        # Görsel kaydeden/silen her bileşen images_changed yayar -> depo hemen tazelenir
        self.bus.images_changed.connect(lambda: self.images.refresh())

    # ---- proje ----------------------------------------------------------- #
    def open_project(self, root: str) -> Project:
        project = Project.open(root)
        self.app_config = AppConfig.load()   # proje eski bağlantı ayarlarını taşımış olabilir
        self.bus.app_config_changed.emit(self.app_config)
        self.grabber.stop_live()
        self.grabber.close_stream()
        self.project = project
        primary = self.completion.primary
        if primary is not None and hasattr(primary, "set_project"):
            primary.set_project(project.root, [self.install_root])
        self._remember_recent(project.root)
        self.bus.project_opened.emit(project)
        self.images.set_project(project)
        return project

    def save_project_config(self) -> None:
        if self.project:
            self.project.save()
            self.bus.project_config_changed.emit(self.project)
            self.images.set_project(self.project)   # görsel klasörleri değişmiş olabilir

    def save_app_config(self, config: AppConfig) -> None:
        config.save()
        self.app_config = config
        if self.grabber.is_live:   # bağlantı ayarları değişmiş olabilir: yeni ayarlarla sürdür
            self.grabber.stop_live()
            self.grabber.start_live(config)
        self.bus.app_config_changed.emit(config)

    def recent_projects(self) -> List[str]:
        value = self.settings.value("recent_projects", [])
        if isinstance(value, str):
            value = [value]
        return list(value or [])

    def _remember_recent(self, root: str) -> None:
        items = [r for r in self.recent_projects() if r != root]
        self.settings.setValue("recent_projects", [root] + items[:9])

    def grab_screen(self) -> None:
        """Tek kare yakalar."""
        if self.project:
            self.grabber.grab(self.app_config)

    def toggle_live_screen(self) -> None:
        """Canlı görüntüyü başlatır / durdurur (durdurunca son kare ekranda kalır)."""
        if self.grabber.is_live:
            self.grabber.stop_live()
        elif self.project:
            self.grabber.start_live(self.app_config)
