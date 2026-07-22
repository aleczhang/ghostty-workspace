"""Filesystem registry for named Ghostty workspace configurations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from typing import Iterable, Mapping, Optional


class WorkspaceError(Exception):
    """A user-facing workspace registry error."""


@dataclass(frozen=True)
class Workspace:
    name: str
    path: Path


@dataclass(frozen=True)
class TrashEntry:
    name: str
    path: Path
    deleted_at: str


def default_config_root(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Return the per-user config root, honoring XDG_CONFIG_HOME."""
    environ = os.environ if environ is None else environ
    if environ.get("XDG_CONFIG_HOME"):
        return Path(environ["XDG_CONFIG_HOME"]).expanduser() / "ghostty-workspace"
    return (home or Path.home()) / ".config" / "ghostty-workspace"


class WorkspaceRegistry:
    """Manage named YAML files below one controlled configuration root."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or default_config_root()).expanduser()
        self.workspaces_dir = self.root / "workspaces"
        self.trash_dir = self.root / "trash"

    @staticmethod
    def validate_name(name: str) -> str:
        value = str(name).strip()
        if not value or value in {".", ".."}:
            raise WorkspaceError("workspace name must not be empty, '.' or '..'")
        if not value[0].isalnum() or any(not (char.isalnum() or char in "._-") for char in value):
            raise WorkspaceError(
                "workspace name may contain only letters, numbers, '.', '_' and '-' "
                "and must start with a letter or number"
            )
        return value

    def _candidate_paths(self, name: str) -> Iterable[Path]:
        yield self.workspaces_dir / f"{name}.yaml"
        yield self.workspaces_dir / f"{name}.yml"

    def workspace_path(self, name: str, *, must_exist: bool = True) -> Path:
        name = self.validate_name(name)
        candidates = [path for path in self._candidate_paths(name) if path.is_file()]
        if len(candidates) > 1:
            raise WorkspaceError(f"workspace {name!r} has both .yaml and .yml files")
        if candidates:
            return candidates[0]
        if must_exist:
            raise WorkspaceError(f"workspace not found: {name}")
        return self.workspaces_dir / f"{name}.yaml"

    def list_workspaces(self) -> list[Workspace]:
        if not self.workspaces_dir.is_dir():
            return []
        entries: dict[str, Path] = {}
        for path in sorted(self.workspaces_dir.glob("*.yaml")) + sorted(self.workspaces_dir.glob("*.yml")):
            name = path.stem
            if name in entries:
                raise WorkspaceError(f"workspace {name!r} has both .yaml and .yml files")
            entries[name] = path
        return [Workspace(name, entries[name]) for name in sorted(entries)]

    def create(self, name: str, content: str) -> Path:
        path = self.workspace_path(name, must_exist=False)
        if path.exists() or any(candidate.exists() for candidate in self._candidate_paths(name)):
            raise WorkspaceError(f"workspace already exists: {name}")
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def move_to_trash(self, name: str) -> TrashEntry:
        name = self.validate_name(name)
        source = self.workspace_path(name)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        deleted_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # The zero-padded revision makes lexical order match deletion order,
        # including when many deletes happen in the same second.
        revision = 1
        destination = self.trash_dir / f"{deleted_at}-{revision:04d}--{name}{source.suffix}"
        while destination.exists():
            revision += 1
            destination = self.trash_dir / f"{deleted_at}-{revision:04d}--{name}{source.suffix}"
        shutil.move(str(source), str(destination))
        return TrashEntry(name=name, path=destination, deleted_at=deleted_at)

    def list_trash(self, name: Optional[str] = None) -> list[TrashEntry]:
        if name is not None:
            name = self.validate_name(name)
        if not self.trash_dir.is_dir():
            return []
        sortable_entries: list[tuple[tuple[str, int, str], TrashEntry]] = []
        for path in sorted(self.trash_dir.glob("*.yaml")) + sorted(self.trash_dir.glob("*.yml")):
            try:
                prefix, entry_name = path.stem.split("--", 1)
            except ValueError:
                continue
            if not entry_name or (name is not None and entry_name != name):
                continue
            deleted_at, separator, revision_text = prefix.rpartition("-")
            if not separator or not revision_text.isdigit():
                # Support trash entries created by the initial format, which
                # had no explicit revision after its timestamp.
                deleted_at, revision = prefix, 0
            else:
                revision = int(revision_text)
            entry = TrashEntry(name=entry_name, path=path, deleted_at=deleted_at)
            sortable_entries.append(((deleted_at, revision, path.name), entry))
        return [entry for _, entry in sorted(sortable_entries)]

    def restore(self, name: str) -> Path:
        name = self.validate_name(name)
        entries = self.list_trash(name)
        if not entries:
            raise WorkspaceError(f"no deleted workspace found: {name}")
        destination = self.workspace_path(name, must_exist=False)
        if destination.exists() or any(candidate.exists() for candidate in self._candidate_paths(name)):
            raise WorkspaceError(f"cannot restore {name!r}: workspace already exists")
        source = entries[-1].path
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return destination

    def purge(self, name: str) -> int:
        entries = self.list_trash(name)
        if not entries:
            raise WorkspaceError(f"no deleted workspace found: {name}")
        for entry in entries:
            entry.path.unlink()
        return len(entries)
