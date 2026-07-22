#!/usr/bin/env python3
"""
Tests for ghostty-workspace.py

Covers:
- YAML config parsing (valid and invalid inputs)
- to_applescript() serialisation
- build_payload() logic (tab selection, focus key)
- AppleScript literal injection (no JSON, no cd concatenation)
- CLI --dry-run output
- CLI --print-script output
- window.shell default shell resolution
- window.tab_position prepend/append
- window.reuse_existing_tabs global default
- window.always_new config option
- tab enabled: false

Does NOT require macOS, Ghostty, or osascript.
"""
import importlib.util
import io
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Load ghostty-workspace.py as a module without executing __main__
# ---------------------------------------------------------------------------
SCRIPT = Path(__file__).parent / "ghostty-workspace.py"

spec = importlib.util.spec_from_file_location("ghostty_workspace_legacy_script", SCRIPT)
gw = importlib.util.module_from_spec(spec)
sys.modules["ghostty_workspace_legacy_script"] = gw
spec.loader.exec_module(gw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_WINDOW = gw.WindowConfig(shell="/bin/sh")


def make_tabs(raw_yaml: str, window=None):
    import yaml as _yaml
    data = _yaml.safe_load(raw_yaml)
    return gw.parse_tabs(data, window or DEFAULT_WINDOW)


def minimal_yaml(**overrides):
    base = dict(
        key="dev",
        title="Dev",
        working_dir="~/dev",
        command="nvim",
    )
    base.update(overrides)
    fields = "\n    ".join(f'{k}: "{v}"' for k, v in base.items())
    return f"window:\n  shell: /bin/sh\ntabs:\n  - {fields}\n"


# ---------------------------------------------------------------------------
# to_applescript
# ---------------------------------------------------------------------------

class TestToAppleScript(unittest.TestCase):

    def test_bool_true(self):
        self.assertEqual(gw.to_applescript(True), "true")

    def test_bool_false(self):
        self.assertEqual(gw.to_applescript(False), "false")

    def test_string_plain(self):
        self.assertEqual(gw.to_applescript("hello"), '"hello"')

    def test_string_with_double_quotes(self):
        result = gw.to_applescript('say "hi"')
        self.assertIn('\\"hi\\"', result)

    def test_string_with_backslash(self):
        result = gw.to_applescript("a\\b")
        self.assertIn("\\\\", result)

    def test_integer(self):
        self.assertEqual(gw.to_applescript(42), "42")

    def test_list(self):
        result = gw.to_applescript([1, "a"])
        self.assertEqual(result, '{1, "a"}')

    def test_dict(self):
        result = gw.to_applescript({"key": "val", "n": 1})
        self.assertIn('key:"val"', result)
        self.assertIn("n:1", result)

    def test_nested(self):
        result = gw.to_applescript({"tabs": [{"title": "T1"}]})
        self.assertIn('tabs:{', result)
        self.assertIn('title:"T1"', result)


# ---------------------------------------------------------------------------
# normalize_ratio
# ---------------------------------------------------------------------------

class TestNormalizeRatio(unittest.TestCase):

    def test_slash_format(self):
        result = gw.normalize_ratio("70/30")
        left, right = result.split("/")
        self.assertAlmostEqual(float(left), 0.7, places=4)
        self.assertAlmostEqual(float(right), 0.3, places=4)

    def test_decimal_format(self):
        result = gw.normalize_ratio("0.6")
        left, right = result.split("/")
        self.assertAlmostEqual(float(left), 0.6, places=4)

    def test_percentage_format(self):
        result = gw.normalize_ratio("75")
        left, _ = result.split("/")
        self.assertAlmostEqual(float(left), 0.75, places=4)

    def test_invalid_raises(self):
        with self.assertRaises(SystemExit):
            gw.normalize_ratio("notanumber")

    def test_zero_raises(self):
        with self.assertRaises(SystemExit):
            gw.normalize_ratio("0/0")


# ---------------------------------------------------------------------------
# parse_window
# ---------------------------------------------------------------------------

class TestParseWindow(unittest.TestCase):

    def _parse(self, yaml_str):
        import yaml as _yaml
        return gw.parse_window(_yaml.safe_load(yaml_str))

    def test_defaults_when_no_window_key(self):
        wc = self._parse("tabs:\n  - key: a\n    title: A\n")
        self.assertIsNone(wc.shell)
        self.assertEqual(wc.tab_position, "prepend")
        self.assertTrue(wc.reuse_existing_tabs)
        self.assertFalse(wc.always_new)

    def test_shell_expanded(self):
        wc = self._parse("window:\n  shell: ~/bin/fish\n")
        self.assertNotIn("~", wc.shell)

    def test_all_options(self):
        wc = self._parse(textwrap.dedent("""
            window:
              shell: /usr/local/bin/zsh
              tab_position: append
              reuse_existing_tabs: false
              always_new: true
        """))
        self.assertEqual(wc.shell, "/usr/local/bin/zsh")
        self.assertEqual(wc.tab_position, "append")
        self.assertFalse(wc.reuse_existing_tabs)
        self.assertTrue(wc.always_new)

    def test_invalid_tab_position_raises(self):
        with self.assertRaises(SystemExit):
            self._parse("window:\n  tab_position: middle\n")

    def test_window_not_dict_raises(self):
        with self.assertRaises(SystemExit):
            self._parse("window: just-a-string\n")


# ---------------------------------------------------------------------------
# parse_tabs
# ---------------------------------------------------------------------------

class TestParseTabs(unittest.TestCase):

    def _parse(self, yaml_str, window=None):
        import yaml as _yaml
        return gw.parse_tabs(_yaml.safe_load(yaml_str), window or DEFAULT_WINDOW)

    def test_minimal_valid(self):
        tabs = self._parse(minimal_yaml())
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0].key, "dev")
        self.assertEqual(tabs[0].title, "Dev")

    def test_working_dir_expanded(self):
        tabs = self._parse(minimal_yaml(working_dir="~/projects"))
        self.assertNotIn("~", tabs[0].working_dir)

    def test_split_defaults(self):
        tabs = self._parse(minimal_yaml())
        self.assertFalse(tabs[0].split.enabled)

    def test_split_enabled(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev
                title: Dev
                split:
                  enabled: true
                  direction: right
                  ratio: "70/30"
        """)
        tabs = self._parse(yaml)
        self.assertTrue(tabs[0].split.enabled)
        self.assertEqual(tabs[0].split.direction, "right")

    def test_duplicate_key_raises(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev
                title: Dev 1
              - key: dev
                title: Dev 2
        """)
        with self.assertRaises(SystemExit):
            self._parse(yaml)

    def test_duplicate_title_raises(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev1
                title: Dev
              - key: dev2
                title: Dev
        """)
        with self.assertRaises(SystemExit):
            self._parse(yaml)

    def test_missing_key_raises(self):
        yaml = "window:\n  shell: /bin/sh\ntabs:\n  - title: Dev\n"
        with self.assertRaises(SystemExit):
            self._parse(yaml)

    def test_missing_title_allowed(self):
        yaml = "window:\n  shell: /bin/sh\ntabs:\n  - key: dev\n"
        tabs = self._parse(yaml)
        self.assertEqual(len(tabs), 1)
        self.assertIsNone(tabs[0].title)

    def test_no_title_forces_reuse_false(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
              reuse_existing_tabs: true
            tabs:
              - key: dev
        """)
        tabs = self._parse(yaml)
        self.assertFalse(tabs[0].reuse_if_exists)

    def test_no_title_skips_title_in_applescript(self):
        script = gw.APPLE_SCRIPT
        self.assertIn('if titleText is not "" then my setSelectedTabTitle', script)

    def test_no_title_payload_has_empty_string(self):
        yaml = "tabs:\n  - key: dev\n"
        tabs = make_tabs(yaml)
        payload = gw.build_payload(
            tabs, only_keys=None, force_new_window=False, default_shell="/bin/sh",
        )
        self.assertEqual(payload["tabs"][0]["title"], "")

    def test_empty_tabs_raises(self):
        with self.assertRaises(SystemExit):
            self._parse("tabs: []\n")

    def test_focus_flag(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev
                title: Dev
                focus: true
        """)
        tabs = self._parse(yaml)
        self.assertTrue(tabs[0].focus)

    def test_multiple_tabs_order_preserved(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: a
                title: A
              - key: b
                title: B
              - key: c
                title: C
        """)
        tabs = self._parse(yaml)
        self.assertEqual([t.key for t in tabs], ["a", "b", "c"])


# ---------------------------------------------------------------------------
# window.shell — shell resolution
# ---------------------------------------------------------------------------

class TestWindowShell(unittest.TestCase):

    def _parse(self, yaml_str, window=None):
        import yaml as _yaml
        data = _yaml.safe_load(yaml_str)
        wc = window or gw.parse_window(data)
        return gw.parse_tabs(data, wc)

    def test_window_shell_used_as_default(self):
        yaml = textwrap.dedent("""
            window:
              shell: /usr/local/bin/zsh
            tabs:
              - key: dev
                title: Dev
        """)
        tabs = self._parse(yaml)
        self.assertEqual(tabs[0].shell, "/usr/local/bin/zsh")

    def test_tab_shell_overrides_window(self):
        yaml = textwrap.dedent("""
            window:
              shell: /usr/local/bin/zsh
            tabs:
              - key: dev
                title: Dev
                shell: /bin/bash
        """)
        tabs = self._parse(yaml)
        self.assertEqual(tabs[0].shell, "/bin/bash")

    def test_no_shell_anywhere_raises(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: dev
                title: Dev
        """)
        with self.assertRaises(SystemExit):
            self._parse(yaml, window=gw.WindowConfig())

    def test_error_message_mentions_window_shell(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: dev
                title: Dev
        """)
        import yaml as _yaml
        data = _yaml.safe_load(yaml)
        buf = io.StringIO()
        with self.assertRaises(SystemExit), patch.object(gw.sys, "stderr", buf):
            gw.parse_tabs(data, gw.WindowConfig())
        self.assertIn("window.shell", buf.getvalue())

    def test_tab_shell_path_expanded(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev
                title: Dev
                shell: ~/bin/fish
        """)
        tabs = self._parse(yaml)
        self.assertNotIn("~", tabs[0].shell)


# ---------------------------------------------------------------------------
# window.tab_position
# ---------------------------------------------------------------------------

class TestTabPosition(unittest.TestCase):

    def test_prepend_in_payload(self):
        tabs = make_tabs(textwrap.dedent("""
            tabs:
              - key: a
                title: A
        """))
        payload = gw.build_payload(
            tabs, only_keys=None, force_new_window=False,
            tab_position="prepend", default_shell="/bin/sh",
        )
        self.assertEqual(payload["tabPosition"], "prepend")

    def test_append_in_payload(self):
        tabs = make_tabs(textwrap.dedent("""
            tabs:
              - key: a
                title: A
        """))
        payload = gw.build_payload(
            tabs, only_keys=None, force_new_window=False,
            tab_position="append", default_shell="/bin/sh",
        )
        self.assertEqual(payload["tabPosition"], "append")

    def test_prepend_script_has_move_tab(self):
        payload = {
            "tabs": [], "forceNewWindow": False, "focusKey": "",
            "defaultShell": "/bin/sh", "tabPosition": "prepend",
        }
        literal = gw.to_applescript(payload)
        script = gw.APPLE_SCRIPT.replace("__PAYLOAD_LITERAL__", literal)
        self.assertIn("move_tab:-1", script)

    def test_append_script_skips_move_tab(self):
        # The script template always contains move_tab:-1, but it's gated
        # behind `if tabPosition is "prepend"`. When tabPosition is "append",
        # the conditional won't execute.
        self.assertIn('if tabPosition is "prepend"', gw.APPLE_SCRIPT)


# ---------------------------------------------------------------------------
# window.reuse_existing_tabs
# ---------------------------------------------------------------------------

class TestReuseExistingTabs(unittest.TestCase):

    def test_default_is_true(self):
        tabs = make_tabs(textwrap.dedent("""
            tabs:
              - key: a
                title: A
        """))
        self.assertTrue(tabs[0].reuse_if_exists)

    def test_window_default_applied(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
        """)
        window = gw.WindowConfig(shell="/bin/sh", reuse_existing_tabs=False)
        tabs = make_tabs(yaml, window)
        self.assertFalse(tabs[0].reuse_if_exists)

    def test_tab_override_wins(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
                reuse_if_exists: true
        """)
        window = gw.WindowConfig(shell="/bin/sh", reuse_existing_tabs=False)
        tabs = make_tabs(yaml, window)
        self.assertTrue(tabs[0].reuse_if_exists)

    def test_tab_override_false_wins(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
                reuse_if_exists: false
        """)
        window = gw.WindowConfig(shell="/bin/sh", reuse_existing_tabs=True)
        tabs = make_tabs(yaml, window)
        self.assertFalse(tabs[0].reuse_if_exists)


# ---------------------------------------------------------------------------
# window.always_new
# ---------------------------------------------------------------------------

class TestAlwaysNew(unittest.TestCase):

    def _run_dry(self, yaml_str, extra_args=None):
        import yaml as _yaml
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(yaml_str)
            tmp = f.name
        try:
            args_list = ["-c", tmp, "--dry-run"] + (extra_args or [])
            with patch("sys.argv", ["ghostty-workspace"] + args_list):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    try:
                        gw.main()
                    except SystemExit:
                        pass
            return buf.getvalue()
        finally:
            os.unlink(tmp)

    def test_always_new_in_dry_run(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
              always_new: true
            tabs:
              - key: dev
                title: Dev
        """)
        out = self._run_dry(yaml)
        self.assertIn("new", out)
        self.assertNotIn("reuse front", out)

    def test_cli_flag_overrides_config(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
              always_new: false
            tabs:
              - key: dev
                title: Dev
        """)
        out = self._run_dry(yaml, ["--force-new-window"])
        self.assertIn("new", out)
        self.assertNotIn("reuse front", out)

    def test_neither_set_reuses_front(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev
                title: Dev
        """)
        out = self._run_dry(yaml)
        self.assertIn("reuse front", out)


# ---------------------------------------------------------------------------
# tab enabled: false
# ---------------------------------------------------------------------------

class TestTabEnabled(unittest.TestCase):

    def test_disabled_tab_skipped(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
              - key: b
                title: B
                enabled: false
              - key: c
                title: C
        """)
        tabs = make_tabs(yaml)
        self.assertEqual([t.key for t in tabs], ["a", "c"])

    def test_all_enabled_by_default(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
              - key: b
                title: B
        """)
        tabs = make_tabs(yaml)
        self.assertEqual(len(tabs), 2)

    def test_disabled_tab_not_in_payload(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
              - key: b
                title: B
                enabled: false
        """)
        tabs = make_tabs(yaml)
        payload = gw.build_payload(
            tabs, only_keys=None, force_new_window=False,
            default_shell="/bin/sh",
        )
        keys = [t["key"] for t in payload["tabs"]]
        self.assertEqual(keys, ["a"])

    def test_all_disabled_returns_empty(self):
        yaml = textwrap.dedent("""
            tabs:
              - key: a
                title: A
                enabled: false
        """)
        tabs = make_tabs(yaml)
        self.assertEqual(tabs, [])

    def test_disabled_tabs_dont_conflict_keys(self):
        """A disabled tab's key does not conflict with an enabled tab."""
        yaml = textwrap.dedent("""
            tabs:
              - key: dev
                title: Dev Old
                enabled: false
              - key: dev
                title: Dev New
        """)
        tabs = make_tabs(yaml)
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0].title, "Dev New")


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------

class TestBuildPayload(unittest.TestCase):

    def _tabs(self):
        import yaml as _yaml
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: coffee
                title: "⌘1 - Coffee"
                working_dir: ~/coffee
                command: "claude -c"
                focus: true
              - key: finance
                title: "⌘2 - Finance"
                working_dir: ~/Documents/financial-plan
                command: "gemini -r"
        """)
        data = _yaml.safe_load(yaml)
        wc = gw.parse_window(data)
        return gw.parse_tabs(data, wc)

    def _payload(self, **kwargs):
        defaults = dict(
            only_keys=None, force_new_window=False, default_shell="/bin/sh",
        )
        defaults.update(kwargs)
        return gw.build_payload(self._tabs(), **defaults)

    def test_all_tabs_included_by_default(self):
        payload = self._payload()
        self.assertEqual(len(payload["tabs"]), 2)

    def test_startup_command_uses_a_non_conflicting_record_key(self):
        payload = self._payload()
        self.assertEqual(payload["tabs"][0]["startupCommand"], "claude -c")
        self.assertNotIn("command", payload["tabs"][0])
        literal = gw.to_applescript(payload)
        self.assertIn('startupCommand:"claude -c"', literal)
        self.assertNotIn('command:"claude -c"', literal)

    def test_only_keys_filters(self):
        payload = self._payload(only_keys=["finance"])
        self.assertEqual(len(payload["tabs"]), 1)
        self.assertEqual(payload["tabs"][0]["key"], "finance")

    def test_only_keys_preserves_config_order(self):
        payload = self._payload(only_keys=["finance", "coffee"])
        self.assertEqual([t["key"] for t in payload["tabs"]], ["coffee", "finance"])

    def test_unknown_key_raises(self):
        with self.assertRaises(SystemExit):
            self._payload(only_keys=["unknown"])

    def test_focus_key_from_config(self):
        payload = self._payload()
        self.assertEqual(payload["focusKey"], "coffee")

    def test_focus_key_defaults_to_first_when_none_marked(self):
        import yaml as _yaml
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: a
                title: A
              - key: b
                title: B
        """)
        data = _yaml.safe_load(yaml)
        wc = gw.parse_window(data)
        tabs = gw.parse_tabs(data, wc)
        payload = gw.build_payload(
            tabs, only_keys=None, force_new_window=False, default_shell="/bin/sh",
        )
        self.assertEqual(payload["focusKey"], "a")

    def test_force_new_window_in_payload(self):
        payload = self._payload(force_new_window=True)
        self.assertTrue(payload["forceNewWindow"])

    def test_no_config_path_in_payload(self):
        payload = self._payload()
        self.assertNotIn("config_path", payload)

    def test_no_dry_run_in_payload(self):
        payload = self._payload()
        self.assertNotIn("dryRun", payload)

    def test_default_shell_in_payload(self):
        payload = self._payload(default_shell="/usr/local/bin/zsh")
        self.assertEqual(payload["defaultShell"], "/usr/local/bin/zsh")

    def test_tab_position_in_payload(self):
        payload = self._payload(tab_position="append")
        self.assertEqual(payload["tabPosition"], "append")


# ---------------------------------------------------------------------------
# AppleScript injection — key invariants
# ---------------------------------------------------------------------------

class TestAppleScriptInjection(unittest.TestCase):

    def _rendered(self, payload=None):
        if payload is None:
            payload = {
                "tabs": [], "forceNewWindow": False, "focusKey": "",
                "defaultShell": "/bin/sh", "tabPosition": "prepend",
            }
        literal = gw.to_applescript(payload)
        return gw.APPLE_SCRIPT.replace("__PAYLOAD_LITERAL__", literal)

    def test_no_placeholder_remains(self):
        self.assertNotIn("__PAYLOAD_LITERAL__", self._rendered())

    def test_no_json_parsing_in_script(self):
        # Regression: original script tried to parse JSON inside AppleScript
        script = gw.APPLE_SCRIPT
        self.assertNotIn("do shell script", script)
        self.assertNotIn("json.loads", script)

    def test_no_cd_in_initial_input_logic(self):
        # initial input must never contain a cd prefix — working dir is
        # handled exclusively by initial working directory
        script = gw.APPLE_SCRIPT
        # The only cd allowed is in the reuse path (input text to existing terminal)
        # It must NOT appear inside a "set initial input" assignment
        lines = script.splitlines()
        for line in lines:
            if "initial input" in line and "cd " in line:
                self.fail(f"cd found inside initial input assignment: {line!r}")

    def test_working_dir_uses_initial_working_directory(self):
        script = gw.APPLE_SCRIPT
        self.assertIn("initial working directory", script)

    def test_startup_cmd_uses_initial_input(self):
        script = gw.APPLE_SCRIPT
        self.assertIn("initial input", script)

    def test_first_tab_reads_non_conflicting_startup_command_key(self):
        script = gw.APPLE_SCRIPT
        self.assertIn("startupCommand of firstTabRec", script)
        self.assertIn("startupCommand of tabRec", script)
        self.assertNotIn("command of firstTabRec", script)
        self.assertNotIn("command of tabRec", script)

    def test_existing_tab_reuse_is_focus_only(self):
        script = gw.APPLE_SCRIPT
        start = script.index("if existingTab is not missing value then")
        end = script.index("-- initial working directory", start)
        reuse_branch = script[start:end]
        self.assertIn("select tab existingTab", reuse_branch)
        self.assertIn("return {existingTab, true}", reuse_branch)
        self.assertNotIn("input text", reuse_branch)
        self.assertNotIn("send key", reuse_branch)
        self.assertNotIn("ensureSplit", reuse_branch)
        self.assertNotIn("set termRef", reuse_branch)

    def test_existing_tab_reuse_skips_tab_reordering(self):
        script = gw.APPLE_SCRIPT
        self.assertIn("if tabAlreadyExisted is false then", script)
        self.assertLess(
            script.index("if tabAlreadyExisted is false then"),
            script.index('perform action "move_tab:-1"'),
        )

    def test_no_hardcoded_shell_path(self):
        # The AppleScript must use defaultShell from the payload, not a hardcoded path
        script = gw.APPLE_SCRIPT
        self.assertNotIn("/opt/homebrew/bin/fish", script)
        self.assertIn("defaultShell", script)

    def test_tab_position_gated(self):
        # The move_tab loop must be gated behind a tabPosition check
        script = gw.APPLE_SCRIPT
        self.assertIn('if tabPosition is "prepend"', script)


# ---------------------------------------------------------------------------
# CLI: --dry-run
# ---------------------------------------------------------------------------

class TestDryRun(unittest.TestCase):

    def _run_dry(self, yaml_str, extra_args=None):
        import yaml as _yaml
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(yaml_str)
            tmp = f.name
        try:
            args_list = ["-c", tmp, "--dry-run"] + (extra_args or [])
            with patch("sys.argv", ["ghostty-workspace"] + args_list):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    try:
                        gw.main()
                    except SystemExit:
                        pass
            return buf.getvalue()
        finally:
            os.unlink(tmp)

    def test_dry_run_lists_tabs(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: dev
                title: "⌘1 - Dev"
                working_dir: ~/dev
                command: nvim
        """)
        out = self._run_dry(yaml)
        self.assertIn("dev", out)
        self.assertIn("nvim", out)

    def test_dry_run_shows_window_mode(self):
        yaml = minimal_yaml()
        out = self._run_dry(yaml)
        self.assertIn("reuse front", out)

    def test_dry_run_force_new_window(self):
        yaml = minimal_yaml()
        out = self._run_dry(yaml, ["--force-new-window"])
        self.assertIn("new", out)

    def test_dry_run_subset_tabs(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
            tabs:
              - key: a
                title: A
              - key: b
                title: B
        """)
        out = self._run_dry(yaml, ["--tabs", "a"])
        self.assertIn("'a'", out)
        self.assertNotIn("'b'", out)

    def test_dry_run_shows_shell(self):
        yaml = textwrap.dedent("""
            window:
              shell: /usr/local/bin/zsh
            tabs:
              - key: dev
                title: Dev
        """)
        out = self._run_dry(yaml)
        self.assertIn("/usr/local/bin/zsh", out)

    def test_dry_run_shows_tab_position(self):
        yaml = textwrap.dedent("""
            window:
              shell: /bin/sh
              tab_position: append
            tabs:
              - key: dev
                title: Dev
        """)
        out = self._run_dry(yaml)
        self.assertIn("append", out)


# ---------------------------------------------------------------------------
# run_osascript error handling
# ---------------------------------------------------------------------------

class TestRunOsascript(unittest.TestCase):

    def _run(self, returncode, stdout, stderr):
        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.returncode = returncode
        mock_proc.stdout = stdout
        mock_proc.stderr = stderr
        buf = io.StringIO()
        with patch("subprocess.run", return_value=mock_proc), \
             patch.object(gw.sys, "stderr", buf):
            result = gw.run_osascript({}, verbose=False)
        return result, buf.getvalue()

    def test_accessibility_error_translated(self):
        _, stderr = self._run(
            1,
            "",
            "7434:7471: execution error: System Events got an error: osascript is not allowed assistive access. (-1719)",
        )
        self.assertIn("System Settings", stderr)
        self.assertIn("Accessibility", stderr)
        self.assertNotIn("-1719", stderr)

    def test_other_errors_passed_through(self):
        _, stderr = self._run(1, "", "some other applescript error")
        self.assertIn("some other applescript error", stderr)

    def test_success_returns_zero(self):
        result, _ = self._run(0, "", "")
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# resolve_config — CWD-first lookup
# ---------------------------------------------------------------------------

class TestResolveConfig(unittest.TestCase):

    def test_cwd_file_preferred(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        cwd_cfg = Path(d) / gw.CONFIG_NAME
        cwd_cfg.write_text("tabs:\n  - key: a\n    title: A\n")
        try:
            with patch.object(Path, "cwd", return_value=Path(d)):
                result = gw.resolve_config()
            self.assertEqual(result, cwd_cfg)
        finally:
            os.unlink(cwd_cfg)
            os.rmdir(d)

    def test_falls_back_to_home(self):
        import tempfile, os
        # Use an empty tmpdir as CWD (no config there)
        empty = tempfile.mkdtemp()
        home = tempfile.mkdtemp()
        home_cfg = Path(home) / gw.CONFIG_NAME
        home_cfg.write_text("tabs:\n  - key: a\n    title: A\n")
        try:
            with patch.object(Path, "cwd", return_value=Path(empty)), \
                 patch.object(Path, "home", return_value=Path(home)):
                result = gw.resolve_config()
            self.assertEqual(result, home_cfg)
        finally:
            os.unlink(home_cfg)
            os.rmdir(home)
            os.rmdir(empty)

    def test_returns_cwd_path_when_neither_exists(self):
        import tempfile, os
        empty = tempfile.mkdtemp()
        empty2 = tempfile.mkdtemp()
        try:
            with patch.object(Path, "cwd", return_value=Path(empty)), \
                 patch.object(Path, "home", return_value=Path(empty2)):
                result = gw.resolve_config()
            self.assertEqual(result, Path(empty) / gw.CONFIG_NAME)
        finally:
            os.rmdir(empty)
            os.rmdir(empty2)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("PyYAML required: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    unittest.main(verbosity=2)
