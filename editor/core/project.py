"""Proje: bir klasör + .screenscript.json ayar dosyası. Qt bağımlılığı yoktur."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Dict, List, Optional

SCRIPT_TEMPLATE = '''"""
{name}

Hazır isimler: SCREEN, Pattern, Region, Location, Key, find, exists, wait, click, ...
Ctrl+Space ile tamamlama listesini açabilirsiniz.
"""

# Sadece ekranın sol üst çeyreğinde ara:
# alan = Region(0, 0, SCREEN.w // 2, SCREEN.h // 2)
# m = alan.exists(Pattern("button.png").similar(0.85), timeout=5)
# if m:
#     click(m)

log("Merhaba! Ekran: %dx%d" % (SCREEN.w, SCREEN.h))
'''


@dataclass
class ProjectConfig:
    """Projeye özgü çalışma ayarları. Yeni alan eklemek geriye dönük uyumludur."""

    host: str = "127.0.0.1"
    port: int = 5555
    adb_path: str = ""
    screencap_format: str = "raw"
    similarity: float = 0.7
    timeout: float = 3.0
    scan_rate: float = 3.0
    # Çoklu ölçekli arama her zaman açıktır; sadece denenecek ölçekler ayarlanır.
    # (Eski projelerdeki "multiscale" anahtarı from_dict'te yok sayılır.)
    scales: List[float] = field(default_factory=lambda: [0.8, 0.9, 1.0, 1.1, 1.2])
    grayscale: bool = False
    humanize: bool = False
    background: bool = False
    dry_run: bool = False
    save_on_fail: bool = True
    main_script: str = "main.py"
    script_args: Dict[str, str] = field(default_factory=dict)
    image_dirs: List[str] = field(default_factory=lambda: ["images"])
    # push_notification: Telegram bot token'ı (boşsa TELEGRAM_BOT_TOKEN ortam değişkeni) ve
    # bildirim alacak kullanıcı adı. Kullanıcının bota bir kez Start demiş olması gerekir.
    telegram_token: str = ""
    telegram_username: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        known = {f.name for f in fields(cls)}
        config = cls(**{k: v for k, v in (data or {}).items() if k in known})
        config.scales = cls.normalize_scales(config.scales)
        return config

    @staticmethod
    def normalize_scales(scales) -> List[float]:
        """Geçersizleri atar, 1.0'ı her zaman içerir, sıralar."""
        out = set()
        for s in scales or []:
            try:
                v = round(float(s), 3)
            except (TypeError, ValueError):
                continue
            if 0.1 <= v <= 5.0:
                out.add(v)
        out.add(1.0)
        return sorted(out)


class Project:
    CONFIG_FILE = ".screenscript.json"
    TELEGRAM_CACHE_FILE = ".telegram_chats.json"
    IMAGES_DIR = "images"

    def __init__(self, root: str, config: Optional[ProjectConfig] = None):
        self._root = os.path.abspath(root)
        self.config = config or ProjectConfig()

    # ---- oluşturma / yükleme -------------------------------------------- #
    @classmethod
    def open(cls, root: str) -> "Project":
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            raise FileNotFoundError(f"Klasör yok: {root}")
        path = os.path.join(root, cls.CONFIG_FILE)
        config = ProjectConfig()
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                config = ProjectConfig.from_dict(json.load(f))
        project = cls(root, config)
        project.ensure_layout()
        return project

    def ensure_layout(self) -> None:
        """Eksikse images/ klasörünü, ayar dosyasını ve ana script'i oluşturur."""
        os.makedirs(self.images_dir, exist_ok=True)
        if not os.path.isfile(self.config_path):
            self.save()
        main = self.main_script_path
        if main and not os.path.exists(main):
            self.create_script(os.path.basename(main))

    def save(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config.to_dict(), f, indent=2, ensure_ascii=False)

    # ---- yollar ---------------------------------------------------------- #
    @property
    def root(self) -> str:
        return self._root

    @property
    def name(self) -> str:
        return os.path.basename(self._root)

    @property
    def config_path(self) -> str:
        return os.path.join(self._root, self.CONFIG_FILE)

    @property
    def telegram_cache_path(self) -> str:
        return os.path.join(self._root, self.TELEGRAM_CACHE_FILE)

    @property
    def images_dir(self) -> str:
        return os.path.join(self._root, self.IMAGES_DIR)

    @property
    def main_script_path(self) -> Optional[str]:
        return self.resolve(self.config.main_script) if self.config.main_script else None

    def resolve(self, relative: str) -> str:
        return relative if os.path.isabs(relative) else os.path.join(self._root, relative)

    def relative(self, path: str) -> str:
        try:
            return os.path.relpath(path, self._root)
        except ValueError:  # farklı sürücü (Windows)
            return path

    def contains(self, path: str) -> bool:
        p = os.path.normcase(os.path.abspath(path))
        return p == os.path.normcase(self._root) or p.startswith(os.path.normcase(self._root) + os.sep)

    def image_search_dirs(self) -> List[str]:
        return [self.resolve(d) for d in self.config.image_dirs]

    # ---- dosya işlemleri ------------------------------------------------- #
    def create_script(self, name: str, directory: Optional[str] = None) -> str:
        if not name.endswith(".py"):
            name += ".py"
        path = os.path.join(directory or self._root, name)
        if os.path.exists(path):
            raise FileExistsError(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(SCRIPT_TEMPLATE.format(name=os.path.splitext(name)[0]))
        return path

    IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")

    def list_images(self) -> List[str]:
        """Görsel klasörlerindeki + proje kökündeki görseller (script çalışırken bunların hepsi
        adıyla bulunabilir)."""
        out: List[str] = []
        seen = set()
        for d in self.image_search_dirs() + [self._root]:
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                path = os.path.join(d, n)
                key = os.path.normcase(os.path.abspath(path))
                if n.lower().endswith(self.IMAGE_EXTENSIONS) and key not in seen and os.path.isfile(path):
                    seen.add(key)
                    out.append(path)
        return out

    def unique_image_path(self, base_name: str) -> str:
        base = "".join(c if c.isalnum() or c in "-_" else "_" for c in base_name) or "image"
        path = os.path.join(self.images_dir, base + ".png")
        i = 2
        while os.path.exists(path):
            path = os.path.join(self.images_dir, f"{base}_{i}.png")
            i += 1
        return path
