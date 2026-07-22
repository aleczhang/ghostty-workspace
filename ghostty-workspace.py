#!/usr/bin/env python3
"""Backward-compatible entry point for the packaged ghostty-workspace core."""

# This module is also loaded by older tests under the name
# ``ghostty_workspace``. Loading the sibling core by path avoids colliding
# with that temporary module name while keeping this script executable alone.
import importlib.util
import sys
from pathlib import Path

_CORE_NAME = "_ghostty_workspace_legacy_core"
_CORE_PATH = Path(__file__).with_name("ghostty_workspace") / "core.py"
_core_spec = importlib.util.spec_from_file_location(_CORE_NAME, _CORE_PATH)
if _core_spec is None or _core_spec.loader is None:  # pragma: no cover
    raise RuntimeError(f"could not load core module: {_CORE_PATH}")
_core = importlib.util.module_from_spec(_core_spec)
sys.modules[_CORE_NAME] = _core
_core_spec.loader.exec_module(_core)
for _name in dir(_core):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_core, _name)


if __name__ == "__main__":
    raise SystemExit(main())
