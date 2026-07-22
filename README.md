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
python3 -m pip install .
```

The installed `gws` command manages named workspace configurations under `$XDG_CONFIG_HOME/ghostty-workspace/workspaces` (default: `~/.config/ghostty-workspace/workspaces`). The original `ghostty-workspace.py` script remains available for existing YAML files and automation.

## `gws` named workspaces

Create and manage reusable, named configurations:

```bash
# Create ~/.config/ghostty-workspace/workspaces/payments.yaml
# (or $XDG_CONFIG_HOME/ghostty-workspace/workspaces/payments.yaml)
gws new payments

# Edit, inspect, validate, and launch it
gws edit payments
gws show payments
gws validate payments
gws start payments

# Launch selected tabs or override the configured window target
gws start payments --tabs code,server
gws start payments --reuse-front
gws start payments --new-window

# List and manage named configurations
gws list
gws delete payments             # asks, then moves the YAML to gws trash
gws restore payments            # restores the latest deleted revision
gws trash list
gws trash purge payments --yes  # permanently removes trashed revisions

gws doctor                      # checks macOS, osascript, and Ghostty discovery
```

`gws delete` only moves the registered YAML configuration to its private trash directory. It never closes Ghostty windows or kills terminal processes. Workspace names are constrained to safe filename characters, and `gws` does not accept arbitrary file paths for deletion.

Named workspaces support an explicit `window.target` setting:

```yaml
version: 2
name: payments
window:
  target: new                   # new | front; named workspaces should normally use new
  shell: /bin/zsh
```

A new-window launch configures Ghostty's initial terminal from the first selected YAML tab, so it does not intentionally add a spare bootstrap tab. `front` uses the currently frontmost Ghostty window. The legacy `window.always_new` option remains supported for existing files.

## Legacy script usage

The original script remains available and keeps its current-directory-then-home configuration lookup:

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
| `target` | `new` \| `front` | legacy behavior | Explicit launch target. `new` creates an isolated window; `front` reuses the front window. |
| `shell` | string | *(none)* | **Required** at window or tab level. Absolute path to shell. |
| `tab_position` | `prepend` \| `append` | `prepend` | Where workspace tabs are inserted in the tab bar. |
| `reuse_existing_tabs` | bool | `true` | Global default for reusing tabs that match by title. |
| `always_new` | bool | `false` | Legacy alias for opening a new window (same as `--force-new-window`). |

### `tabs[]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `key` | string | *(required)* | Unique identifier for `--tabs` filtering. |
| `title` | string | | Tab title (set via Ghostty's prompt dialog). If omitted, tab keeps Ghostty's default title and `reuse_if_exists` is implicitly `false`. |
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
python3 test_gws_cli.py
```

98 tests cover config parsing, payload generation, AppleScript invariants, legacy CLI compatibility, named workspace management, safe deletion/recovery, and the packaged `gws` CLI. No macOS, Ghostty, or osascript is required to run them.

## Known Caveats

- **AppleScript support is preview.** Ghostty's scripting API may change between releases.
- **Tab ordering uses an action loop.** Ghostty's sdef has no `move tab` command, so tabs are reordered by calling `perform action "move_tab:-1"` repeatedly. This works but adds ~50ms per position moved. Use `tab_position: append` to skip reordering entirely.
- **Tab titles require Accessibility.** Titles are set via Ghostty's `prompt_tab_title` action and System Events UI automation. Without Accessibility permission, tabs are created but titles won't be set.
- **Split resize is approximate.** Horizontal splits are resized by calculating pixel deltas from the window width. The result is close but not pixel-perfect.

## License

MIT
