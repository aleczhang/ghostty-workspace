#!/usr/bin/env python3
"""
Ghostty workspace launcher.

Reads a YAML config and uses embedded AppleScript via osascript to:
- reuse or create a Ghostty window
- create tabs in a defined order
- skip tabs that already exist by title
- optionally create a split in a tab
- resize a right split to an approximate ratio
- set the visible tab title through Ghostty's prompt_tab_title action
- run commands in Ghostty's configured shell

Requirements:
- macOS
- Ghostty with AppleScript support (1.3+)
- Python 3.9+
- PyYAML (`python3 -m pip install pyyaml`)
- Accessibility enabled for "System Events"

Use the packaged `gws` command to create, validate, and launch named
workspaces stored below the user configuration directory.
"""
from __future__ import annotations

import math
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, NoReturn

try:
    import yaml
except Exception as exc:  # pragma: no cover
    yaml = None
    YAML_IMPORT_ERROR = exc
else:
    YAML_IMPORT_ERROR = None


@dataclass
class SplitConfig:
    enabled: bool = False
    direction: str = "right"
    ratio: str = "70/30"
    second_pane_command: Optional[str] = None
    second_pane_working_dir: Optional[str] = None


@dataclass
class WindowConfig:
    tab_position: str = "prepend"
    reuse_existing_tabs: bool = True
    target: str = "new"


@dataclass
class TabConfig:
    key: str
    title: Optional[str]
    working_dir: Optional[str]
    command: Optional[str]
    split: SplitConfig
    focus: bool = False
    reuse_if_exists: bool = True


def die(message: str, code: int = 2) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        die(f"PyYAML is required to read YAML config files. Import error: {YAML_IMPORT_ERROR}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        die(f"config not found: {path}")
    except Exception as exc:
        die(f"failed to read config {path}: {exc}")
    if not isinstance(data, dict):
        die("top-level config must be a mapping/object")
    return data


def expand_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = os.path.expandvars(value)
    value = os.path.expanduser(value)
    return str(Path(value))


def normalize_ratio(value: str) -> str:
    raw = str(value).strip()
    if "/" in raw:
        left_s, right_s = raw.split("/", 1)
        try:
            left = float(left_s)
            right = float(right_s)
        except ValueError:
            die(f"invalid split ratio: {value!r}")
        if not math.isfinite(left) or not math.isfinite(right):
            die(f"invalid split ratio: {value!r}")
        total = left + right
        if total <= 0:
            die(f"invalid split ratio: {value!r}")
        left = left / total
        right = right / total
    else:
        try:
            left = float(raw)
        except ValueError:
            die(f"invalid split ratio: {value!r}")
        if left > 1:
            left = left / 100.0
        if not 0 < left < 1:
            die(f"split ratio must be between 0 and 1 or like '70/30', got {value!r}")
        right = 1.0 - left

    if right <= 0 or left <= 0:
        die(f"split ratio must leave room for both panes, got {value!r}")
    return f"{left:.6f}/{right:.6f}"


def parse_split(obj: Any) -> SplitConfig:
    if obj in (None, False):
        return SplitConfig(enabled=False)
    if obj is True:
        return SplitConfig(enabled=True)

    if not isinstance(obj, dict):
        die("split must be a boolean or mapping")

    enabled = bool(obj.get("enabled", True))
    direction = str(obj.get("direction", "right"))
    if direction not in {"right", "left", "up", "down"}:
        die(f"invalid split direction: {direction!r}")

    ratio = normalize_ratio(obj.get("ratio", "70/30"))
    second_cmd = obj.get("second_pane_command")
    if second_cmd is not None:
        second_cmd = str(second_cmd)

    second_wd = obj.get("second_pane_working_dir")
    if second_wd is not None:
        second_wd = expand_path(str(second_wd))

    return SplitConfig(
        enabled=enabled,
        direction=direction,
        ratio=ratio,
        second_pane_command=second_cmd,
        second_pane_working_dir=second_wd,
    )


def parse_window(data: Dict[str, Any]) -> WindowConfig:
    win = data.get("window")
    if win is None:
        return WindowConfig()
    if not isinstance(win, dict):
        die("'window' must be a mapping/object")

    if "shell" in win:
        die("window.shell is not supported; configure the shell in Ghostty instead")

    tab_position = str(win.get("tab_position", "prepend"))
    if tab_position not in {"prepend", "append"}:
        die(f"invalid tab_position: {tab_position!r} (must be 'prepend' or 'append')")

    reuse_existing_tabs = bool(win.get("reuse_existing_tabs", True))
    target = str(win.get("target", "new"))
    if target not in {"new", "front"}:
        die(f"invalid window.target: {target!r} (must be 'new' or 'front')")

    return WindowConfig(
        tab_position=tab_position,
        reuse_existing_tabs=reuse_existing_tabs,
        target=target,
    )


def parse_tabs(data: Dict[str, Any], window: WindowConfig) -> List[TabConfig]:
    tabs_obj = data.get("tabs")
    if not isinstance(tabs_obj, list) or not tabs_obj:
        die("config must contain a non-empty 'tabs' list")

    seen_keys: set[str] = set()
    titles: set[str] = set()
    tabs: List[TabConfig] = []

    for i, item in enumerate(tabs_obj, start=1):
        if not isinstance(item, dict):
            die(f"tab #{i} must be a mapping/object")
        if "shell" in item:
            die(f"tab #{i} uses unsupported 'shell'; configure the shell in Ghostty instead")

        if not bool(item.get("enabled", True)):
            continue

        key = str(item.get("key", "")).strip()
        if not key:
            die(f"tab #{i} is missing 'key'")
        if key in seen_keys:
            die(f"duplicate tab key: {key!r}")
        seen_keys.add(key)

        title_raw = item.get("title")
        title: Optional[str] = None
        if title_raw is not None:
            title = str(title_raw).strip() or None
        if title is not None:
            if title in titles:
                die(f"duplicate tab title: {title!r}")
            titles.add(title)

        working_dir = item.get("working_dir")
        if working_dir is not None:
            working_dir = expand_path(str(working_dir))

        command = item.get("command")
        if command is not None:
            command = str(command)

        split = parse_split(item.get("split", False))
        focus = bool(item.get("focus", False))

        reuse_raw = item.get("reuse_if_exists")
        if title is None:
            reuse_if_exists = False
        elif reuse_raw is not None:
            reuse_if_exists = bool(reuse_raw)
        else:
            reuse_if_exists = window.reuse_existing_tabs

        tabs.append(
            TabConfig(
                key=key,
                title=title,
                working_dir=working_dir,
                command=command,
                split=split,
                focus=focus,
                reuse_if_exists=reuse_if_exists,
            )
        )

    return tabs


def build_payload(
    tabs: List[TabConfig],
    *,
    only_keys: Optional[List[str]],
    force_new_window: bool,
    tab_position: str = "prepend",
) -> Dict[str, Any]:
    selected = tabs
    tab_order = [t.key for t in tabs]
    if only_keys:
        wanted = set(only_keys)
        selected = [t for t in tabs if t.key in wanted]
        missing = wanted - {t.key for t in selected}
        if missing:
            die(f"unknown tab key(s): {', '.join(sorted(missing))}")
        selected = sorted(selected, key=lambda t: tab_order.index(t.key))

    focus_key = next((t.key for t in selected if t.focus), None)
    if focus_key is None and selected:
        focus_key = selected[0].key

    payload_tabs: List[Dict[str, Any]] = []
    for t in selected:
        payload_tabs.append(
            {
                "key": t.key,
                "title": t.title or "",
                "workingDir": t.working_dir or "",
                "startupCommand": t.command or "",
                "reuseIfExists": t.reuse_if_exists,
                "split": {
                    "enabled": t.split.enabled,
                    "direction": t.split.direction,
                    "ratio": t.split.ratio,
                    "secondPaneCommand": t.split.second_pane_command or "",
                    "secondPaneWorkingDir": t.split.second_pane_working_dir or "",
                },
            }
        )

    return {
        "tabs": payload_tabs,
        "forceNewWindow": force_new_window,
        "focusKey": focus_key or "",
        "tabPosition": tab_position,
    }


APPLE_SCRIPT = r'''
use AppleScript version "2.4"
use scripting additions

on run
    set payloadRecord to __PAYLOAD_LITERAL__

    set tabList to tabs of payloadRecord
    set forceNewWindow to forceNewWindow of payloadRecord
    set desiredFocusKey to focusKey of payloadRecord
    set tabPosition to tabPosition of payloadRecord

    if (count of tabList) = 0 then return

    -- A new Ghostty window already contains its first terminal/tab. Configure
    -- that surface from the first requested tab instead of creating a spare
    -- bootstrap tab and appending every configured tab after it.
    set createdNewWindow to false
    tell application "Ghostty"
        activate
        if forceNewWindow or (count of windows) = 0 then
            set firstTabRec to item 1 of tabList
            set firstCfg to new surface configuration
            -- Leave command unset so Ghostty inherits its configured shell and normal exit behavior.
            if (workingDir of firstTabRec) is not "" then set initial working directory of firstCfg to workingDir of firstTabRec
            if (startupCommand of firstTabRec) is not "" then set initial input of firstCfg to (startupCommand of firstTabRec) & return
            set win to new window with configuration firstCfg
            set createdNewWindow to true
            delay 0.5
        else
            set win to front window
            activate window win
            delay 0.2
        end if
    end tell

    set focusedTabRef to missing value
    set insertIndex to 1

    repeat with tabItem in tabList
        set tabRec to contents of tabItem
        set keyName to key of tabRec
        set titleText to title of tabRec
        set workingDir to workingDir of tabRec
        set startupCmd to startupCommand of tabRec
        set reuseFlag to reuseIfExists of tabRec
        set splitRec to split of tabRec

        set tabAlreadyExisted to false
        if createdNewWindow and insertIndex is 1 then
            tell application "Ghostty"
                set tabRef to tab 1 of win
                select tab tabRef
                delay 0.15
            end tell
            if titleText is not "" then my setTabTitle(tabRef, titleText)
            if (enabled of splitRec) then
                tell application "Ghostty"
                    my ensureSplit(tabRef, workingDir, splitRec)
                end tell
            end if
        else
            set tabResult to my ensureTab(win, titleText, workingDir, startupCmd, reuseFlag, splitRec)
            set tabRef to item 1 of tabResult
            set tabAlreadyExisted to item 2 of tabResult
        end if

        -- A reused tab is focus-only: do not move it to satisfy configured
        -- ordering, because that would mutate the user's existing layout.
        if tabPosition is "prepend" then
            if tabAlreadyExisted is false then
                -- Slide a newly created tab left until it sits at insertIndex.
                -- Uses the Ghostty action "move_tab:-1" which moves the focused tab one step left.
                tell application "Ghostty"
                    select tab tabRef
                    delay 0.1
                    set currentIndex to index of tabRef
                    set primaryTerm to focused terminal of tabRef
                    repeat while currentIndex > insertIndex
                        perform action "move_tab:-1" on primaryTerm
                        delay 0.05
                        set currentIndex to currentIndex - 1
                    end repeat
                end tell
            end if
        end if

        if keyName is desiredFocusKey then set focusedTabRef to tabRef
        set insertIndex to insertIndex + 1
    end repeat

    if focusedTabRef is not missing value then
        tell application "Ghostty"
            select tab focusedTabRef
        end tell
    end if
end run

on ensureTab(win, titleText, workingDir, startupCmd, reuseFlag, splitRec)
    tell application "Ghostty"
        set existingTab to missing value
        if reuseFlag then set existingTab to my findTabByTitle(win, titleText)

        if existingTab is not missing value then
            -- Reuse is deliberately focus-only. Never re-run a command,
            -- change directories, add a split, rename, or rearrange this tab.
            select tab existingTab
            delay 0.15
            return {existingTab, true}
        end if

        -- initial working directory is set via surface config (verified: Fish respects it).
        -- initial input carries only the startup command — keeping them separate avoids
        -- Ghostty splitting the string on whitespace if combined into one field.
        set cfg to new surface configuration
        if workingDir is not "" then set initial working directory of cfg to workingDir
        if startupCmd is not "" then set initial input of cfg to startupCmd & return

        set t to new tab in win with configuration cfg
        delay 0.4
        select tab t
        delay 0.15
    end tell

    -- Set the tab title via the prompt dialog (skip if no title configured)
    if titleText is not "" then my setTabTitle(t, titleText)

    -- Handle split after title is set; terminal focus is now stable
    if (enabled of splitRec) then
        tell application "Ghostty"
            my ensureSplit(t, workingDir, splitRec)
        end tell
    end if

    return {t, false}
end ensureTab

on ensureSplit(tabRef, workingDir, splitRec)
    tell application "Ghostty"
        if (count of terminals of tabRef) > 1 then return

        select tab tabRef
        delay 0.15
        set primaryTerm to focused terminal of tabRef

        set secondWD to secondPaneWorkingDir of splitRec
        set splitWD to secondWD
        if splitWD is "" then set splitWD to workingDir

        set splitCmd to secondPaneCommand of splitRec

        set splitCfg to new surface configuration
        if splitWD is not "" then set initial working directory of splitCfg to splitWD
        if splitCmd is not "" then set initial input of splitCfg to splitCmd & return

        set directionName to direction of splitRec
        if directionName is "right" then
            set splitTerm to split primaryTerm direction right with configuration splitCfg
        else if directionName is "left" then
            set splitTerm to split primaryTerm direction left with configuration splitCfg
        else if directionName is "up" then
            set splitTerm to split primaryTerm direction up with configuration splitCfg
        else
            set splitTerm to split primaryTerm direction down with configuration splitCfg
        end if

        delay 0.25

        if (directionName is "right") or (directionName is "left") then
            set px to my splitResizePixels(ratio of splitRec, directionName)
            perform action ("resize_split:" & directionName & "," & px) on primaryTerm
        end if

        focus primaryTerm
    end tell
end ensureSplit

on findTabByTitle(win, desiredTitle)
    tell application "Ghostty"
        repeat with t in tabs of win
            try
                if (name of t) is desiredTitle then return t
            end try
        end repeat
    end tell
    return missing value
end findTabByTitle

on setTabTitle(tabRef, newTitle)
    tell application "Ghostty"
        select tab tabRef
        set termRef to focused terminal of tabRef
        perform action "prompt_tab_title" on termRef
    end tell

    set promptSheet to missing value
    tell application "System Events"
        tell process "Ghostty"
            -- The prompt is created asynchronously and window ordering can lag
            -- behind Ghostty's scripting model. Poll all windows for up to 3s.
            repeat 60 times
                repeat with candidateWindow in windows
                    try
                        if exists sheet 1 of candidateWindow then
                            set candidateSheet to sheet 1 of candidateWindow
                            if (exists text field 1 of candidateSheet) and (exists button "OK" of candidateSheet) then
                                set promptSheet to candidateSheet
                                exit repeat
                            end if
                        end if
                    end try
                end repeat
                if promptSheet is not missing value then exit repeat
                delay 0.05
            end repeat

            if promptSheet is missing value then error "Timed out waiting for Ghostty tab title prompt. Check Accessibility permission and close any other Ghostty dialog."

            set value of text field 1 of promptSheet to newTitle
            delay 0.05
            click button "OK" of promptSheet

            -- Do not open the next title prompt until this sheet has closed.
            repeat 20 times
                try
                    if not (exists promptSheet) then exit repeat
                on error
                    exit repeat
                end try
                delay 0.05
            end repeat
        end tell
    end tell

    delay 0.1
end setTabTitle

on splitResizePixels(ratioText, directionName)
    set AppleScript's text item delimiters to "/"
    set ratioParts to text items of ratioText
    set AppleScript's text item delimiters to ""

    if (count of ratioParts) is not 2 then error "Invalid ratio: " & ratioText
    set leftFraction to (item 1 of ratioParts) as real
    set rightFraction to (item 2 of ratioParts) as real

    try
        tell application "System Events"
            tell process "Ghostty"
                set winSize to size of front window
            end tell
        end tell

        set winWidth to item 1 of winSize
        set initialHalf to (winWidth * 0.50) as integer

        if directionName is "right" then
            set targetOtherPane to (winWidth * rightFraction) as integer
        else
            set targetOtherPane to (winWidth * leftFraction) as integer
        end if

        set delta to initialHalf - targetOtherPane
        if delta < 80 then set delta to 80
        return delta
    on error
        return 220
    end try
end splitResizePixels
'''


def to_applescript(value: Any) -> str:
    """Recursively convert a Python value to an AppleScript literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(to_applescript(v) for v in value)
        return "{" + items + "}"
    if isinstance(value, dict):
        pairs = ", ".join(f"{k}:{to_applescript(v)}" for k, v in value.items())
        return "{" + pairs + "}"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def run_osascript(payload: Dict[str, Any], *, verbose: bool) -> int:
    as_literal = to_applescript(payload)
    cmd = ["osascript", "-"]
    # Inject the payload literal directly into the script so AppleScript
    # never has to parse JSON — avoiding the broken heredoc approach.
    script = APPLE_SCRIPT.replace("__PAYLOAD_LITERAL__", as_literal)

    if verbose:
        print("+", " ".join(shlex.quote(part) for part in cmd), file=sys.stderr)

    proc = subprocess.run(
        cmd,
        input=script,
        text=True,
        capture_output=True,
    )

    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        stderr = proc.stderr.strip()
        if "assistive access" in stderr:
            print(
                "error: Accessibility permission required to set tab titles.\n"
                "  Go to System Settings -> Privacy & Security -> Accessibility\n"
                "  and enable the app you are running this script from (e.g. Terminal, Ghostty).",
                file=sys.stderr,
            )
        else:
            print(stderr, file=sys.stderr)
    return proc.returncode



def launch_config(
    config_path: Path,
    *,
    only_keys: Optional[List[str]] = None,
    force_new_window: Optional[bool] = None,
    dry_run: bool = False,
    print_script: bool = False,
    verbose: bool = False,
) -> int:
    """Validate and launch one named-workspace configuration."""
    data = load_yaml(config_path)
    window_config = parse_window(data)
    tabs = parse_tabs(data, window_config)

    configured_new = window_config.target == "new"
    force_new = configured_new if force_new_window is None else force_new_window

    if dry_run:
        selected = tabs
        if only_keys:
            wanted = set(only_keys)
            selected = [t for t in tabs if t.key in wanted]
            missing = wanted - {t.key for t in selected}
            if missing:
                die(f"unknown tab key(s): {', '.join(sorted(missing))}")
        print(f"config: {config_path}")
        print(f"window: {'new' if force_new else 'reuse front'}  tabs={window_config.tab_position}")
        for t in selected:
            split_info = f"  split={t.split.direction} {t.split.ratio}" if t.split.enabled else ""
            focus_marker = " [focus]" if t.focus else ""
            title_display = repr(t.title) if t.title else "(untitled)"
            print(f"  tab {t.key!r}: {title_display}  cmd={t.command or '(none)'}{split_info}{focus_marker}")
        return 0

    if not tabs:
        print("no enabled tabs to open")
        return 0

    payload = build_payload(
        tabs,
        only_keys=only_keys,
        force_new_window=force_new,
        tab_position=window_config.tab_position,
    )

    if print_script:
        as_literal = to_applescript(payload)
        print(APPLE_SCRIPT.replace("__PAYLOAD_LITERAL__", as_literal))
        return 0

    return run_osascript(payload, verbose=verbose)
