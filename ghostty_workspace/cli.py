"""The installed ``gws`` command-line interface."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
from typing import Optional, Sequence

from . import __version__
from . import core
from .registry import WorkspaceError, WorkspaceRegistry


def _workspace_template(name: str) -> str:
    shell = os.environ.get("SHELL", "/bin/zsh")
    return f'''# Created by gws new {name}
version: 2
name: {name}

window:
  # new creates an isolated Ghostty window; front reuses the front window.
  target: new
  shell: {shell}
  tab_position: append
  reuse_existing_tabs: false

tabs:
  - key: code
    title: "{name} · Code"
    working_dir: ~/
    command: ""
    focus: true
'''


def _parse_tab_keys(value: Optional[str]) -> Optional[list[str]]:
    if value is None:
        return None
    keys = [part.strip() for part in value.split(",") if part.strip()]
    if not keys:
        raise WorkspaceError("--tabs was provided but no valid keys were found")
    return keys


def _registry(args: argparse.Namespace) -> WorkspaceRegistry:
    root = getattr(args, "config_dir", None)
    return WorkspaceRegistry(Path(root) if root is not None else None)


def _resolve_config(args: argparse.Namespace, registry: WorkspaceRegistry) -> Path:
    config = getattr(args, "config", None)
    name = getattr(args, "name", None)
    if config and name:
        raise WorkspaceError("use either a workspace name or --config, not both")
    if config:
        return Path(core.expand_path(config))
    if name == ".":
        return Path.cwd() / core.CONFIG_NAME
    if name:
        return registry.workspace_path(name)
    # Keep the old current-directory then home lookup for bare `gws start`.
    return core.resolve_config()


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _add_config_selector(parser: argparse.ArgumentParser, *, require_name: bool = False) -> None:
    parser.add_argument("name", nargs=None if require_name else "?", help="Registered workspace name, or '.' for the current directory config.")
    parser.add_argument("-c", "--config", help="Path to a YAML config; cannot be combined with NAME.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gws",
        description="Manage and launch Ghostty workspaces from YAML.",
    )
    parser.add_argument("--version", action="version", version=f"gws {__version__}")
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="Override the workspace registry root (default: $XDG_CONFIG_HOME/ghostty-workspace or ~/.config/ghostty-workspace).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", aliases=["init"], help="Create a named workspace from a template.")
    new.add_argument("name", help="New workspace name.")

    commands.add_parser("list", aliases=["ls"], help="List registered workspaces.")

    start = commands.add_parser("start", aliases=["up"], help="Launch a workspace.")
    _add_config_selector(start)
    start.add_argument("--tabs", help="Comma-separated tab keys to launch, in configured order.")
    target = start.add_mutually_exclusive_group()
    target.add_argument("--new-window", action="store_true", help="Create a new Ghostty window.")
    target.add_argument("--reuse-front", action="store_true", help="Reuse the front Ghostty window.")
    start.add_argument("--dry-run", action="store_true", help="Validate and print the launch plan only.")
    start.add_argument("--print-script", action="store_true", help="Print generated AppleScript only.")
    start.add_argument("-v", "--verbose", action="store_true", help="Show the osascript invocation.")

    validate = commands.add_parser("validate", help="Validate a workspace without opening Ghostty.")
    _add_config_selector(validate)

    show = commands.add_parser("show", help="Show the resolved launch plan without opening Ghostty.")
    _add_config_selector(show)
    show.add_argument("--tabs", help="Comma-separated tab keys to show, in configured order.")
    target = show.add_mutually_exclusive_group()
    target.add_argument("--new-window", action="store_true")
    target.add_argument("--reuse-front", action="store_true")

    edit = commands.add_parser("edit", help="Open a registered workspace with $EDITOR.")
    edit.add_argument("name", help="Registered workspace name.")

    delete = commands.add_parser("delete", aliases=["rm"], help="Move a registered workspace config to gws trash.")
    delete.add_argument("name", help="Registered workspace name.")
    delete.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    restore = commands.add_parser("restore", help="Restore the most recently deleted workspace config.")
    restore.add_argument("name", help="Workspace name to restore.")

    trash = commands.add_parser("trash", help="Inspect or permanently purge recoverable deleted configs.")
    trash_commands = trash.add_subparsers(dest="trash_command", required=True)
    trash_commands.add_parser("list", help="List recoverable deleted configs.")
    purge = trash_commands.add_parser("purge", help="Permanently remove all deleted revisions of one workspace.")
    purge.add_argument("name", help="Workspace name to permanently remove from trash.")
    purge.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    commands.add_parser("doctor", help="Check the local Ghostty workspace environment.")
    return parser


def _window_override(args: argparse.Namespace) -> Optional[bool]:
    if getattr(args, "new_window", False):
        return True
    if getattr(args, "reuse_front", False):
        return False
    return None


def _start(args: argparse.Namespace) -> int:
    path = _resolve_config(args, _registry(args))
    return core.launch_config(
        path,
        only_keys=_parse_tab_keys(args.tabs),
        force_new_window=_window_override(args),
        dry_run=args.dry_run,
        print_script=args.print_script,
        verbose=args.verbose,
    )


def _validate(args: argparse.Namespace) -> int:
    path = _resolve_config(args, _registry(args))
    data = core.load_yaml(path)
    window = core.parse_window(data)
    tabs = core.parse_tabs(data, window)
    print(f"valid: {path} ({len(tabs)} enabled tab(s))")
    return 0


def _show(args: argparse.Namespace) -> int:
    path = _resolve_config(args, _registry(args))
    return core.launch_config(
        path,
        only_keys=_parse_tab_keys(args.tabs),
        force_new_window=_window_override(args),
        dry_run=True,
    )


def _doctor() -> int:
    is_macos = platform.system() == "Darwin"
    osascript = shutil.which("osascript")
    ghostty_app = Path("/Applications/Ghostty.app").exists() or Path.home().joinpath("Applications/Ghostty.app").exists()
    print(f"macOS: {'ok' if is_macos else 'required (not detected)'}")
    print(f"osascript: {osascript or 'not found'}")
    print(f"Ghostty application: {'found' if ghostty_app else 'not found in /Applications or ~/Applications'}")
    print("Accessibility: grant the invoking terminal app permission before launching titled tabs.")
    return 0 if is_macos and osascript and ghostty_app else 1


def run(args: argparse.Namespace) -> int:
    command = args.command
    registry = _registry(args)
    if command in {"new", "init"}:
        path = registry.create(args.name, _workspace_template(registry.validate_name(args.name)))
        print(f"created: {path}")
        return 0
    if command in {"list", "ls"}:
        entries = registry.list_workspaces()
        if not entries:
            print("no registered workspaces")
            return 0
        for entry in entries:
            print(f"{entry.name}\t{entry.path}")
        return 0
    if command in {"start", "up"}:
        return _start(args)
    if command == "validate":
        return _validate(args)
    if command == "show":
        return _show(args)
    if command == "edit":
        path = registry.workspace_path(args.name)
        editor = shlex.split(os.environ.get("EDITOR", "vi"))
        return subprocess.call([*editor, str(path)])
    if command in {"delete", "rm"}:
        path = registry.workspace_path(args.name)
        if not _confirm(f"Move workspace {args.name!r} to trash?\nConfig: {path}\nThis does not close Ghostty windows.", args.yes):
            print("cancelled")
            return 1
        entry = registry.move_to_trash(args.name)
        print(f"moved to trash: {entry.path}")
        print(f"restore with: gws restore {entry.name}")
        return 0
    if command == "restore":
        path = registry.restore(args.name)
        print(f"restored: {path}")
        return 0
    if command == "trash":
        if args.trash_command == "list":
            entries = registry.list_trash()
            if not entries:
                print("trash is empty")
                return 0
            for entry in entries:
                print(f"{entry.name}\t{entry.deleted_at}\t{entry.path}")
            return 0
        if args.trash_command == "purge":
            entries = registry.list_trash(args.name)
            if not entries:
                raise WorkspaceError(f"no deleted workspace found: {args.name}")
            if not _confirm(
                f"Permanently delete {len(entries)} trashed revision(s) of workspace {args.name!r}?",
                args.yes,
            ):
                print("cancelled")
                return 1
            count = registry.purge(args.name)
            print(f"permanently deleted {count} trashed revision(s) for {args.name}")
            return 0
    if command == "doctor":
        return _doctor()
    raise WorkspaceError(f"unsupported command: {command}")


def _normalize_global_options(argv: Optional[Sequence[str]]) -> list[str]:
    """Let --config-dir work before or after a subcommand.

    argparse normally accepts a top-level option only before a subcommand.
    This normalisation keeps the documented global spelling while allowing the
    common `gws start NAME --config-dir DIR` form as well.
    """
    source = list(sys.argv[1:] if argv is None else argv)
    config_options: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(source):
        argument = source[index]
        if argument == "--config-dir":
            if index + 1 >= len(source):
                remaining.append(argument)
                index += 1
            else:
                config_options.extend([argument, source[index + 1]])
                index += 2
        elif argument.startswith("--config-dir="):
            config_options.append(argument)
            index += 1
        else:
            remaining.append(argument)
            index += 1
    return [*config_options, *remaining]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    try:
        args = parser.parse_args(_normalize_global_options(argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        return run(args)
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        # core.die() has already written a helpful error message.
        return int(exc.code) if isinstance(exc.code, int) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
