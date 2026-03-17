# ghostty-workspace

Save and restore multi-tab terminal layouts in [Ghostty](https://ghostty.org) — the missing workspace/arrangement feature.

Ghostty added AppleScript support in v1.3 (March 2026), but has no built-in way to save and restore tab layouts. This script reads a YAML config and drives Ghostty's AppleScript API to create tabs, set titles, configure splits, and run startup commands.

## Requirements

- **macOS** (AppleScript-based)
- **Ghostty 1.3+** with AppleScript support (still marked preview)
- **Python 3.9+**
- **PyYAML** — `pip install pyyaml`
- **Accessibility permission** — System Settings → Privacy & Security → Accessibility → enable the app you run the script from (Terminal, Ghostty, etc.)

## Installation

```bash
git clone https://github.com/manonstreet/ghostty-workspace.git
cd ghostty-workspace
pip install pyyaml
```

Copy `example.yaml` to `~/ghostty-workspace.yaml` (or into a project directory) and edit it for your setup. The script checks the current directory first, then `~/`.

## Usage

```bash
# Launch workspace (checks ./ghostty-workspace.yaml then ~/ghostty-workspace.yaml)
python3 ghostty-workspace.py

# Use a different config
python3 ghostty-workspace.py -c ~/work.yaml

# Open only specific tabs
python3 ghostty-workspace.py --tabs code,server

# Validate config without touching Ghostty
python3 ghostty-workspace.py --dry-run

# Debug: print the generated AppleScript
python3 ghostty-workspace.py --print-script

# Force a new window instead of reusing the front one
python3 ghostty-workspace.py --force-new-window
```

## YAML Schema

See [`example.yaml`](example.yaml) for a fully commented reference. Summary:

### `window`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `shell` | string | *(none)* | **Required** at window or tab level. Absolute path to shell. |
| `tab_position` | `prepend` \| `append` | `prepend` | Where workspace tabs are inserted in the tab bar. |
| `reuse_existing_tabs` | bool | `true` | Global default for reusing tabs that match by title. |
| `always_new` | bool | `false` | Always create a new window (same as `--force-new-window`). |

### `tabs[]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `key` | string | *(required)* | Unique identifier for `--tabs` filtering. |
| `title` | string | *(required)* | Tab title (set via Ghostty's prompt dialog). |
| `working_dir` | string | | Starting directory. Supports `~` and `$ENV_VARS`. |
| `command` | string | | Command to run on tab creation. |
| `shell` | string | `window.shell` | Per-tab shell override. |
| `focus` | bool | `false` | Give this tab focus after launch. |
| `reuse_if_exists` | bool | `window.reuse_existing_tabs` | Override the global reuse setting. |
| `enabled` | bool | `true` | Set to `false` to skip without removing from config. |
| `split` | object | | Split pane configuration (see below). |

### `tabs[].split`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable the split. |
| `direction` | `right` \| `left` \| `up` \| `down` | `right` | Split direction. |
| `ratio` | string | `"70/30"` | Size ratio. Accepts `"70/30"`, `"0.7"`, or `"70"`. |
| `second_pane_command` | string | | Command for the second pane. |
| `second_pane_working_dir` | string | | Working directory for the second pane. |

## Tests

```bash
python3 test_ghostty_workspace.py
```

81 tests covering config parsing, payload generation, AppleScript invariants, and CLI behavior. No macOS, Ghostty, or osascript required to run them.

## Known Caveats

- **AppleScript support is preview.** Ghostty's scripting API may change between releases.
- **Tab ordering uses an action loop.** Ghostty's sdef has no `move tab` command, so tabs are reordered by calling `perform action "move_tab:-1"` repeatedly. This works but adds ~50ms per position moved. Use `tab_position: append` to skip reordering entirely.
- **Tab titles require Accessibility.** Titles are set via Ghostty's `prompt_tab_title` action and System Events UI automation. Without Accessibility permission, tabs are created but titles won't be set.
- **Split resize is approximate.** Horizontal splits are resized by calculating pixel deltas from the window width. The result is close but not pixel-perfect.

## License

MIT
