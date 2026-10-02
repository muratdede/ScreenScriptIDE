"""Script process. The editor starts this module as a separate Python process (ScriptSession);
it is not an entry point the user runs directly."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

from .. import __file__ as _editor_init
from .. import i18n
from ..core.app_settings import AppConfig, app_data_dir
from ..core.project import Project, ProjectConfig
from ..core.protocol import Command, Event
from ..plugins.screenscript import ScriptRunner
from .context_factory import ContextFactory


def parse_args():
    p = argparse.ArgumentParser(prog="editor.agent.runner")
    p.add_argument("--project", required=True)
    p.add_argument("--script", required=True)
    p.add_argument("--config", help="ProjectConfig JSON file (the project's own is read when empty)")
    p.add_argument("--debug-port", type=int)
    return p.parse_args()


def load_project(args) -> Project:
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            return Project(args.project, ProjectConfig.from_dict(json.load(f)))
    path = os.path.join(args.project, Project.CONFIG_FILE)
    config = ProjectConfig()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            config = ProjectConfig.from_dict(json.load(f))
    return Project(args.project, config)


LOG_DIR_NAME = "logs"
LOG_FILES_KEPT = 30
# Always written to the file at DEBUG, whatever the console level: screen capture problems (stream
# sessions dropping, screencap fallback) can only be diagnosed afterwards from these records.
ALWAYS_DEBUG = ("editor.plugins.adb_capture",)


def setup_file_log(project: Project, script: str, level: int) -> Optional[str]:
    """Also writes this run's log to %LOCALAPPDATA%\\ScreenScript\\logs\\<project>_<script>_<time>.log
    (nothing is written to the project folder). Only the newest LOG_FILES_KEPT files are kept.
    Returns the file path, or None if the file could not be created."""
    try:
        folder = os.path.join(app_data_dir(), LOG_DIR_NAME)
        os.makedirs(folder, exist_ok=True)
        name = "%s_%s_%s.log" % (os.path.basename(os.path.normpath(project.root)),
                                 os.path.splitext(os.path.basename(script))[0],
                                 time.strftime("%Y%m%d_%H%M%S"))
        path = os.path.join(folder, name)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    handler.addFilter(lambda r: r.levelno >= level or r.name.startswith(ALWAYS_DEBUG))
    root = logging.getLogger()
    root.addHandler(handler)
    for prefix in ALWAYS_DEBUG:           # let their DEBUG records reach the handlers at all
        logging.getLogger(prefix).setLevel(logging.DEBUG)
    for handler_ in root.handlers:        # ...without changing what the console shows
        if handler_ is not handler:
            handler_.setLevel(level)
    old = sorted((f for f in os.listdir(folder) if f.endswith(".log")),
                 key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    for f in old[:-LOG_FILES_KEPT]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass
    return path


def main() -> int:
    args = parse_args()
    # same language as the editor (it exports SCREENSCRIPT_LANG); settings.json when run standalone
    i18n.set_language(os.environ.get(i18n.ENV_VAR) or AppConfig.load().language)
    project = load_project(args)
    level = getattr(logging, str(project.config.log_level).upper(), logging.INFO)   # Project Settings > Debugging
    logging.basicConfig(level=level, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    log_path = setup_file_log(project, args.script, level)
    if log_path:
        logging.getLogger("script").info(i18n.tr("Log file: %s"), log_path)
    os.chdir(project.root)
    script = os.path.abspath(args.script)

    try:
        context = ContextFactory(project).build()
    except Exception as e:  # noqa: BLE001
        logging.getLogger("script").error(i18n.tr("Starting failed: %s"), e)
        return 2

    runner = ScriptRunner(context)
    script_args = dict(project.config.script_args)

    if not args.debug_port:
        return runner.run(script, script_args)

    from .debugger import DebugChannel, ScriptDebugger

    channel = DebugChannel(args.debug_port)
    channel.send(Event.READY, pid=os.getpid())
    start = channel.commands.get()
    while start.get("type") != Command.START:
        start = channel.commands.get()

    library_roots = [os.path.dirname(_editor_init)]   # editor + plugins (editor/plugins)
    debugger = ScriptDebugger(
        channel,
        user_roots=[project.root, os.path.dirname(script)],
        library_roots=library_roots,
        hidden_names=lambda: set(context.namespace().keys()),
        stop_on_entry=bool(start.get("stop_on_entry")),
    )
    debugger.apply_breakpoints(start.get("breakpoints", {}))
    code = debugger.runcall(runner.run, script, script_args)
    code = 130 if code is None else code
    sys.stdout.flush()
    channel.send(Event.FINISHED, exit_code=code)
    return code


if __name__ == "__main__":
    sys.exit(main())
