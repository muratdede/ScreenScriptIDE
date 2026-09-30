"""ADB access: locating adb.exe and sending commands to a device (an Android emulator)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

from ...i18n import tr

log = logging.getLogger(__name__)


class AdbError(RuntimeError):
    """All ADB related errors."""


class AdbLocator:
    """Finds the adb executable: given path -> PATH -> Android SDK platform-tools -> the adb bundled
    with a known emulator (LDPlayer, BlueStacks).

    Any adb works with any emulator; a standalone one (PATH / platform-tools) is preferred. Different
    adb versions restart each other's server, so it is best to use a single adb consistently."""

    # emulator -> (registry keys holding an install dir, install dirs relative to drive roots /
    # Program Files, adb file name inside the install dir)
    _EMULATORS = (
        ("LDPlayer", (r"SOFTWARE\leidian\ldplayer9", r"SOFTWARE\leidian\ldplayer"),
         (r"LDPlayer\LDPlayer9", r"LDPlayer\LDPlayer4.0"), "adb.exe"),
        ("BlueStacks", (r"SOFTWARE\BlueStacks_nxt", r"SOFTWARE\BlueStacks"),
         ("BlueStacks_nxt", "BlueStacks"), "HD-Adb.exe"),
    )
    _REG_VALUES = ("InstallDir", "InstallPath")

    @classmethod
    def locate(cls, user_path: Optional[str] = None) -> str:
        if user_path:
            if os.path.isfile(user_path):
                return user_path
            raise AdbError(tr("adb not found: {path}", path=user_path))

        found = shutil.which("adb")
        if found:
            return found

        for candidate in cls._candidates():
            if os.path.isfile(candidate):
                return candidate

        raise AdbError(tr(
            "adb not found. Install Android platform-tools or give a path like "
            "{example} as adb_path.", example=r'"C:\LDPlayer\LDPlayer9\adb.exe"'))

    @classmethod
    def _candidates(cls):
        if not sys.platform.startswith("win"):
            return
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or \
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")
        yield os.path.join(sdk, "platform-tools", "adb.exe")

        bases = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        bases += [f"{d}:\\" for d in "CDE"]
        for _name, reg_keys, folders, exe in cls._EMULATORS:
            for install_dir in cls._registry_dirs(reg_keys):
                yield os.path.join(install_dir, exe)
            for base in bases:
                for folder in folders:
                    yield os.path.join(base, folder, exe)

    @classmethod
    def _registry_dirs(cls, keys):
        try:
            import winreg
        except ImportError:
            return
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in keys:
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for value in cls._REG_VALUES:
                            try:
                                yield winreg.QueryValueEx(k, value)[0]
                            except OSError:
                                continue
                except OSError:
                    continue


class AdbDevice:
    """Represents a single ADB device (e.g. 127.0.0.1:5555)."""

    def __init__(self, serial: str, adb_path: Optional[str] = None, timeout: float = 10.0):
        self._serial = serial
        self._adb = AdbLocator.locate(adb_path)
        self._timeout = timeout
        # no console window (Windows)
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

    # ---- connection ------------------------------------------------------ #
    def connect(self) -> None:
        out = self._run_text("connect", self._serial, with_serial=False)
        if "connected" not in out.lower() or "cannot" in out.lower() or "failed" in out.lower():
            raise AdbError(tr("ADB could not connect ({serial}): {output}", serial=self._serial, output=out.strip()))
        if not self.is_connected():
            raise AdbError(tr("Device is not ready ({serial}). Is ADB enabled in the emulator settings?",
                              serial=self._serial))
        log.info(tr("ADB connected: %s (%s)"), self._serial, self._adb)

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
        log.warning(tr("ADB reconnecting: %s"), self._serial)
        self.disconnect()
        self.connect()

    # ---- commands -------------------------------------------------------- #
    def exec_out(self, *args: str) -> bytes:
        """Runs `adb exec-out ...` and returns the raw output (for binary data)."""
        return self._run("exec-out", *args)

    def shell(self, command: str) -> str:
        return self._run_text("shell", command)

    # ---- internal helpers ------------------------------------------------ #
    def _run(self, *args: str, with_serial: bool = True) -> bytes:
        cmd = [self._adb] + (["-s", self._serial] if with_serial else []) + list(args)
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=self._timeout,
                                 creationflags=self._creationflags)
        except subprocess.TimeoutExpired as e:
            raise AdbError(tr("ADB timeout: {command}", command=" ".join(args))) from e
        except OSError as e:
            raise AdbError(tr("adb could not be run: {error}", error=e)) from e
        if res.returncode != 0:
            raise AdbError(tr("adb {command} failed: {error}", command=" ".join(args),
                              error=res.stderr.decode(errors="ignore").strip()))
        return res.stdout

    def _run_text(self, *args: str, with_serial: bool = True) -> str:
        return self._run(*args, with_serial=with_serial).decode(errors="ignore")

    def __repr__(self) -> str:
        return f"AdbDevice(serial={self._serial!r})"
