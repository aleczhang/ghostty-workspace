# ghostty-workspace

A YAML-driven workspace manager for [Ghostty](https://ghostty.org) on macOS. Create, validate, launch, and safely manage named multi-tab layouts with splits, titles, and startup commands.

Ghostty added AppleScript support in v1.3 (March 2026), but has no built-in way to save and restore tab layouts. `gws` stores named YAML configurations and drives Ghostty’s AppleScript API to create tabs, set titles, configure splits, and run startup commands.

## Requirements

- **macOS** (AppleScript-based)
- **Ghostty 1.3+** with AppleScript support (still marked preview)
- **Python 3.9+**
- **PyYAML** — installed automatically with this project
- **Accessibility permission** — System Settings → Privacy & Security → Accessibility → enable the app you run `gws` from (Terminal, Ghostty, etc.)

## Installation

```bash
git clone https://github.com/aleczhang/ghostty-workspace.git
cd ghostty-workspace
python3 -m pip install .
```

The installed `gws` command manages named workspace configurations under `$XDG_CONFIG_HOME/ghostty-workspace/workspaces` (default: `~/.config/ghostty-workspace/workspaces`).

## Named workspaces

Create and manage reusable configurations:

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

A workspace configuration created by `gws new` looks like this:

```yaml
version: 2
name: payments

window:
  target: new                   # new | front
  tab_position: append          # prepend | append
  reuse_existing_tabs: false

tabs:
  - key: code
    title: "payments · Code"
    working_dir: ~/projects/payments
    command: ""
    focus: true
```

`window.target` defaults to `new`: it configures Ghostty’s initial terminal from the first selected YAML tab, so a new-window launch does not intentionally add a spare bootstrap tab. `front` uses the currently frontmost Ghostty window.

`gws` leaves each surface command unset, so new tabs and split panes inherit Ghostty’s configured shell and its normal exit behavior. Configure your shell in Ghostty; `window.shell` and `tabs[].shell` are not supported.

## YAML schema

### `window`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `target` | `new` \| `front` | `new` | Launch in an isolated window or reuse the front Ghostty window. |
| `tab_position` | `prepend` \| `append` | `prepend` | Where workspace tabs are inserted in the tab bar. |
| `reuse_existing_tabs` | bool | `true` | Global default for reusing tabs that match by title. |

### `tabs[]`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `key` | string | *(required)* | Unique identifier for `--tabs` filtering. |
| `title` | string | | Tab title (set via Ghostty's prompt dialog). If omitted, the tab keeps Ghostty's default title and `reuse_if_exists` is implicitly `false`. |
| `working_dir` | string | | Starting directory. Supports `~` and `$ENV_VARS`. |
| `command` | string | | Command to run on tab creation in Ghostty’s configured shell. |
| `focus` | bool | `false` | Give this tab focus after launch. |
| `reuse_if_exists` | bool | `window.reuse_existing_tabs` | Override the global reuse setting. A reused tab is focus-only: it does not rerun commands, alter splits, or move in the tab bar. |
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
python3 test_gws_cli.py
```

The test suite covers named workspace management, safe deletion and recovery, configuration parsing, payload generation, AppleScript invariants, and the packaged `gws` CLI. No macOS, Ghostty, or `osascript` is required to run it.

## Known caveats

- **AppleScript support is preview.** Ghostty's scripting API may change between releases.
- **Tab ordering uses an action loop.** Ghostty's sdef has no `move tab` command, so tabs are reordered by calling `perform action "move_tab:-1"` repeatedly. This works but adds ~50ms per position moved. Use `tab_position: append` to skip reordering entirely.
- **Tab titles require Accessibility.** Titles are set via Ghostty's `prompt_tab_title` action and System Events UI automation. Without Accessibility permission, tabs are created but titles won't be set.
- **Split resize is approximate.** Horizontal splits are resized by calculating pixel deltas from the window width. The result is close but not pixel-perfect.

## License

MIT
