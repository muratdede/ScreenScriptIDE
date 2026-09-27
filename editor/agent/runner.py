"""Script process. The editor starts this module as a separate Python process (ScriptSession);
it is not an entry point the user runs directly."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .. import __file__ as _editor_init
from .. import i18n
from ..core.app_settings import AppConfig
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


def main() -> int:
    args = parse_args()
    # same language as the editor (it exports SCREENSCRIPT_LANG); settings.json when run standalone
    i18n.set_language(os.environ.get(i18n.ENV_VAR) or AppConfig.load().language)
    project = load_project(args)
    level = getattr(logging, str(project.config.log_level).upper(), logging.INFO)   # Project Settings > Debugging
    logging.basicConfig(level=level, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
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
