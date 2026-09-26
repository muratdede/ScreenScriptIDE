"""ADB erişimi: adb.exe'yi bulma ve bir cihaza (BlueStacks) komut gönderme."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

log = logging.getLogger(__name__)


class AdbError(RuntimeError):
    """ADB ile ilgili tüm hatalar."""


class AdbLocator:
    """adb çalıştırılabilir dosyasını bulur: verilen yol -> PATH -> BlueStacks'in HD-Adb.exe'si."""

    _REG_KEYS = (r"SOFTWARE\BlueStacks_nxt", r"SOFTWARE\BlueStacks")
    _FOLDERS = ("BlueStacks_nxt", "BlueStacks")

    @classmethod
    def locate(cls, user_path: Optional[str] = None) -> str:
        if user_path:
            if os.path.isfile(user_path):
                return user_path
            raise AdbError(f"adb bulunamadı: {user_path}")

        found = shutil.which("adb")
        if found:
            return found

        for candidate in cls._bluestacks_candidates():
            if os.path.isfile(candidate):
                return candidate

        raise AdbError(
            "adb bulunamadı. Android platform-tools kurun ya da adb_path olarak "
            r'"C:\Program Files\BlueStacks_nxt\HD-Adb.exe" gibi bir yol verin.'
        )

    @classmethod
    def _bluestacks_candidates(cls):
        if not sys.platform.startswith("win"):
            return
        try:
            import winreg

            for key in cls._REG_KEYS:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                        yield os.path.join(winreg.QueryValueEx(k, "InstallDir")[0], "HD-Adb.exe")
                except OSError:
                    continue
        except ImportError:
            pass
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for folder in cls._FOLDERS:
                yield os.path.join(base, folder, "HD-Adb.exe")


class AdbDevice:
    """Tek bir ADB cihazını temsil eder (ör. 127.0.0.1:5555)."""

    def __init__(self, serial: str, adb_path: Optional[str] = None, timeout: float = 10.0):
        self._serial = serial
        self._adb = AdbLocator.locate(adb_path)
        self._timeout = timeout
        # Konsol penceresi açılmasın (Windows)
        self._creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    @classmethod
    def from_host_port(cls, host: str = "127.0.0.1", port: int = 5555, **kwargs) -> "AdbDevice":
        return cls(f"{host}:{port}", **kwargs)

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def adb_path(self) -> str:
        return self._adb

    # ---- bağlantı -------------------------------------------------------- #
    def connect(self) -> None:
        out = self._run_text("connect", self._serial, with_serial=False)
        if "connected" not in out.lower() or "cannot" in out.lower() or "failed" in out.lower():
            raise AdbError(f"ADB bağlanamadı ({self._serial}): {out.strip()}")
        if not self.is_connected():
            raise AdbError(f"Cihaz hazır değil ({self._serial}). BlueStacks'te ADB açık mı?")
        log.info("ADB bağlandı: %s (%s)", self._serial, self._adb)

    def disconnect(self) -> None:
        try:
            self._run_text("disconnect", self._serial, with_serial=False)
        except AdbError:
            pass

    def is_connected(self) -> bool:
        try:
            return self._run_text("get-state").strip() == "device"
        except AdbError:
            return False

    def reconnect(self) -> None:
        log.warning("ADB yeniden bağlanıyor: %s", self._serial)
        self.disconnect()
        self.connect()

    # ---- komutlar -------------------------------------------------------- #
    def exec_out(self, *args: str) -> bytes:
        """`adb exec-out ...` çalıştırır ve ham çıktıyı döndürür (ikili veri için)."""
        return self._run("exec-out", *args)

    def shell(self, command: str) -> str:
        return self._run_text("shell", command)

    # ---- iç yardımcılar -------------------------------------------------- #
    def _run(self, *args: str, with_serial: bool = True) -> bytes:
        cmd = [self._adb] + (["-s", self._serial] if with_serial else []) + list(args)
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=self._timeout,
                                 creationflags=self._creationflags)
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"ADB zaman aşımı: {' '.join(args)}") from e
        except OSError as e:
            raise AdbError(f"adb çalıştırılamadı: {e}") from e
        if res.returncode != 0:
            raise AdbError(f"adb {' '.join(args)} başarısız: {res.stderr.decode(errors='ignore').strip()}")
        return res.stdout

    def _run_text(self, *args: str, with_serial: bool = True) -> str:
        return self._run(*args, with_serial=with_serial).decode(errors="ignore")

    def __repr__(self) -> str:
        return f"AdbDevice(serial={self._serial!r})"
