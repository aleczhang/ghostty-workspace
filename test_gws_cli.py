#!/usr/bin/env python3
"""Tests for the packaged gws workspace-management CLI."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ghostty_workspace import cli, core
from ghostty_workspace.registry import WorkspaceError, WorkspaceRegistry


VALID_CONFIG = """\
version: 2
name: demo
window:
  target: new
  shell: /bin/sh
tabs:
  - key: code
    title: Code
    working_dir: ~/
    command: echo ready
"""


class TestWorkspaceRegistry(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "registry"
        self.registry = WorkspaceRegistry(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_list_delete_and_restore(self):
        created = self.registry.create("demo", VALID_CONFIG)
        self.assertTrue(created.is_file())
        self.assertEqual([workspace.name for workspace in self.registry.list_workspaces()], ["demo"])

        entry = self.registry.move_to_trash("demo")
        self.assertFalse(created.exists())
        self.assertTrue(entry.path.is_file())
        self.assertEqual([item.name for item in self.registry.list_trash()], ["demo"])

        restored = self.registry.restore("demo")
        self.assertTrue(restored.is_file())
        self.assertEqual(restored.read_text(encoding="utf-8"), VALID_CONFIG)
        self.assertEqual(self.registry.list_trash(), [])

    def test_purge_removes_all_trashed_revisions(self):
        self.registry.create("demo", VALID_CONFIG)
        self.registry.move_to_trash("demo")
        self.registry.create("demo", VALID_CONFIG.replace("echo ready", "echo again"))
        self.registry.move_to_trash("demo")

        self.assertEqual(len(self.registry.list_trash("demo")), 2)
        self.assertEqual(self.registry.purge("demo"), 2)
        self.assertEqual(self.registry.list_trash("demo"), [])

    def test_restore_selects_the_latest_of_many_same_second_revisions(self):
        class FixedDateTime:
            @classmethod
            def now(cls, _timezone):
                from datetime import datetime, timezone
                return datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)

        with patch("ghostty_workspace.registry.datetime", FixedDateTime):
            for revision in range(12):
                self.registry.create("demo", VALID_CONFIG + f"# revision {revision}\n")
                self.registry.move_to_trash("demo")

        restored = self.registry.restore("demo")
        self.assertIn("# revision 11", restored.read_text(encoding="utf-8"))

    def test_invalid_name_cannot_escape_registry(self):
        for name in ("", ".", "..", "../outside", "with space", "/tmp/config"):
            with self.subTest(name=name):
                with self.assertRaises(WorkspaceError):
                    self.registry.workspace_path(name, must_exist=False)


class TestGwsCli(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "registry"

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--config-dir", str(self.root), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_new_list_validate_show_and_dry_run_start(self):
        code, stdout, stderr = self.run_cli("new", "demo")
        self.assertEqual(code, 0, stderr)
        self.assertIn("created:", stdout)

        code, stdout, stderr = self.run_cli("list")
        self.assertEqual(code, 0, stderr)
        self.assertIn("demo", stdout)

        code, stdout, stderr = self.run_cli("validate", "demo")
        self.assertEqual(code, 0, stderr)
        self.assertIn("valid:", stdout)

        code, stdout, stderr = self.run_cli("show", "demo")
        self.assertEqual(code, 0, stderr)
        self.assertIn("window: new", stdout)

        code, stdout, stderr = self.run_cli("start", "demo", "--dry-run")
        self.assertEqual(code, 0, stderr)
        self.assertIn("window: new", stdout)

    def test_delete_restore_and_purge_are_safe_and_scriptable(self):
        self.assertEqual(self.run_cli("new", "demo")[0], 0)

        code, stdout, stderr = self.run_cli("delete", "demo", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("moved to trash:", stdout)
        self.assertIn("gws restore demo", stdout)

        code, stdout, stderr = self.run_cli("trash", "list")
        self.assertEqual(code, 0, stderr)
        self.assertIn("demo", stdout)

        code, stdout, stderr = self.run_cli("restore", "demo")
        self.assertEqual(code, 0, stderr)
        self.assertIn("restored:", stdout)

        self.assertEqual(self.run_cli("delete", "demo", "--yes")[0], 0)
        code, stdout, stderr = self.run_cli("trash", "purge", "demo", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("permanently deleted", stdout)

    def test_delete_prompts_and_does_not_close_or_remove_on_no(self):
        self.assertEqual(self.run_cli("new", "demo")[0], 0)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("builtins.input", return_value="n"), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["--config-dir", str(self.root), "delete", "demo"])
        self.assertEqual(code, 1, stderr.getvalue())
        self.assertIn("cancelled", stdout.getvalue())
        self.assertTrue(WorkspaceRegistry(self.root).workspace_path("demo").exists())

    def test_rejects_path_like_workspace_name(self):
        code, _, stderr = self.run_cli("new", "../outside")
        self.assertEqual(code, 2)
        self.assertIn("workspace name", stderr)

    def test_start_accepts_an_explicit_config_file(self):
        config = Path(self.tempdir.name) / "custom.yaml"
        config.write_text(VALID_CONFIG, encoding="utf-8")
        code, stdout, stderr = self.run_cli("start", "--config", str(config), "--dry-run", "--reuse-front")
        self.assertEqual(code, 0, stderr)
        self.assertIn("window: reuse front", stdout)

    def test_config_dir_is_accepted_after_a_subcommand(self):
        self.assertEqual(self.run_cli("new", "demo")[0], 0)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["start", "demo", "--config-dir", str(self.root), "--dry-run"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("window: new", stdout.getvalue())


class TestTargetNewWindowBehavior(unittest.TestCase):
    def test_target_is_parsed_and_new_window_uses_first_configured_tab(self):
        window = core.parse_window({"window": {"target": "new", "shell": "/bin/sh"}})
        self.assertEqual(window.target, "new")
        self.assertIn("set createdNewWindow to true", core.APPLE_SCRIPT)
        self.assertIn("set win to new window with configuration firstCfg", core.APPLE_SCRIPT)
        self.assertIn("set tabRef to tab 1 of win", core.APPLE_SCRIPT)

    def test_explicit_front_target_overrides_legacy_always_new(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "migration.yaml"
            config.write_text(
                VALID_CONFIG.replace("target: new", "target: front\n  always_new: true"),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(core.launch_config(config, dry_run=True), 0)
        self.assertIn("window: reuse front", stdout.getvalue())

    def test_invalid_target_is_rejected(self):
        with self.assertRaises(SystemExit):
            core.parse_window({"window": {"target": "named"}})

    def test_nan_and_infinite_split_ratios_are_rejected(self):
        for ratio in ("nan/1", "1/nan", "inf/1", "1/-inf"):
            with self.subTest(ratio=ratio), self.assertRaises(SystemExit):
                core.normalize_ratio(ratio)


if __name__ == "__main__":
    unittest.main()
