"""Single source of truth for the images of the project.

Catches changes in three ways:
  1. QFileSystemWatcher  — instant (a file is added to/removed from a folder)
  2. Polling             — for cases the watcher misses: a folder deleted and recreated,
                           network drives, a file overwritten in place
  3. Window focus        — refresh immediately when returning to the editor
If the list really changed, the `changed` signal is emitted once; views only subscribe to it.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .project import Project

Snapshot = Dict[str, Tuple[float, int]]   # path -> (mtime, size)


class ImageRepository(QObject):
    changed = Signal(list)   # sorted image paths

    POLL_INTERVAL_MS = 2000
    DEBOUNCE_MS = 250

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._snapshot: Snapshot = {}
        self._images: List[str] = []

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule)
        self._watcher.fileChanged.connect(self._schedule)

        self._debounce = QTimer(self, singleShot=True, interval=self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)
        self._poll = QTimer(self, interval=self.POLL_INTERVAL_MS)
        self._poll.timeout.connect(self.refresh)

        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

    # ---- public ---------------------------------------------------------- #
    @property
    def images(self) -> List[str]:
        return list(self._images)

    def set_project(self, project: Optional[Project]) -> None:
        self._project = project
        self._snapshot = {}
        self._images = []
        if project is None:
            self._poll.stop()
            self._rewatch([])
            self.changed.emit([])
            return
        self.refresh(force=True)
        self._poll.start()

    def refresh(self, force: bool = False) -> bool:
        """Scans the folders; emits changed if the list or file contents changed."""
        if self._project is None:
            return False
        snapshot = self._scan()
        self._rewatch(self._watch_targets())
        if not force and snapshot == self._snapshot:
            return False
        self._snapshot = snapshot
        self._images = sorted(snapshot, key=lambda p: (os.path.dirname(p), os.path.basename(p).lower()))
        self.changed.emit(self.images)
        return True

    # ---- internal -------------------------------------------------------- #
    def _scan(self) -> Snapshot:
        snap: Snapshot = {}
        for path in self._project.list_images():
            try:
                st = os.stat(path)
                snap[path] = (st.st_mtime, st.st_size)
            except OSError:
                pass   # deleted during the scan
        return snap

    def _watch_targets(self) -> List[str]:
        # the project root is watched too: so we notice if images/ is created or deleted later
        dirs = [self._project.root] + self._project.image_search_dirs()
        return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]

    def _rewatch(self, targets: List[str]) -> None:
        current = set(self._watcher.directories())
        wanted = set(targets)
        stale = list(current - wanted)
        if stale:
            self._watcher.removePaths(stale)
        missing = [d for d in targets if d not in current]
        if missing:
            self._watcher.addPaths(missing)

    def _schedule(self, *_):
        self._debounce.start()

    def _on_app_state(self, state) -> None:
        if state == Qt.ApplicationActive:
            self._schedule()
