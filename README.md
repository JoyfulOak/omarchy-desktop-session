# Desktop Session for Omarchy

A user-owned Omarchy bar plugin and session service for Hyprland 0.56's Lua
interface. Click the desktop-layout icon near Power to open its dropdown.

It automatically checkpoints open windows and restores supported apps once on the
next desktop login. The bar dropdown provides one toggle for automatic saving and
restoration. A final checkpoint is taken during shutdown, restart, or logout
before the desktop closes its windows.

## What returns

- Open applications with an unambiguous installed desktop launcher; missing launchers
  are reported instead of guessed. Launcher identity uses the running process's
  executable/argv to distinguish apps that share a window class (for example stable
  and nightly AppImage builds); captured argv is never replayed. Terminal support: Foot, Alacritty, Kitty, Ghostty.
- Workspace names, monitor assignments (with fallback when disconnected), active
  workspace/focus, floating size and position, fullscreen and floating pinned state.
- Terminal working directories, using the shell's current folder where available.
- Terminal sessions running inside tmux, by reconnecting to their saved tmux
  session when that tmux server is available.
- Tiled mode is preserved by default; windows are opened in spatial order, but complex
  tiling splits, custom layout algorithms and groups are not reconstructed.
- **Restore exact geometry as floating windows** opts into saved rectangles for all
  windows. Geometry scales/clamps if monitor dimensions or scale have changed. It
  does not change the physical monitor configuration.

## Limits

This reopens applications, not process memory. Save your work. Unsaved documents,
terminal commands/jobs, terminal scrollback, and application-internal state cannot
be recovered generically. Browser tabs and editor sessions require the application's
own session restore setting. A single-instance app may decline a second window;
failures appear in the UI. Terminal commands are never replayed from process arguments.
For terminal session persistence, run the terminal shell inside tmux; ordinary
terminal windows still restore their working directory only. A tmux server that
is stopped during logout cannot be reconnected to.
Unsupported windows and window groups are kept in the snapshot for reference.
Terminal windows can be excluded from saving with `excluded_classes` in
`~/.config/omarchy/session-restore.json`; this setup excludes Foot because
terminal screen contents cannot be restored exactly.

Snapshots contain window titles and folder names and are private user files (0600).
Only restore your own trusted snapshots; they name desktop applications to launch.
The service polls every two seconds. Stable additions/layout edits are saved after
three seconds, while removals settle for twenty seconds so Omarchy's shutdown
window-close sequence cannot erase the previous desktop. At shutdown, windows that
have already disappeared during teardown are carried forward from the last checkpoint.
The logind shutdown signal and service stop both take a final checkpoint before the
desktop closes its windows. Startup launches missing apps together, then places them
as their windows appear. Abrupt power loss recovers the latest completed checkpoint,
not necessarily the last few seconds. **Save now** captures immediately.

On partial restore, autosaving pauses so a failed reopen cannot replace the source
snapshot. Review the listed issues, then choose **Save now** or **Resume automatic
saving**. A manual restore first reuses matching existing windows; it never closes
unrelated windows. Applications with indistinguishable titles/classes after reboot
may have their window order swapped.

## Install

On an active Omarchy desktop, install directly from GitHub:

```bash
omarchy plugin add https://github.com/JoyfulOak/omarchy-desktop-session.git --enable --yes
```

The installer sets up the user systemd service, post-boot hook, CLI, preferences,
and bar widget. No system packages or packaged Omarchy files are modified.
Post-boot starts the service after Omarchy imports its graphical session
environment. The service stops with the graphical session and does not save an
empty desktop on exit. Installing/restarting it in the same desktop session does
not trigger another automatic restore.

To install from a local clone instead:

```bash
bash install.sh
```

## Update

```bash
omarchy plugin update justin.session-restore
```

## Remove automatic startup

```bash
bash uninstall.sh
```

This stops and removes automatic startup while retaining saved sessions and source.

## Installed files

- Plugin: `~/.config/omarchy/plugins/justin.session-restore/`
- Preferences: `~/.config/omarchy/session-restore.json`
- Snapshots: `~/.local/state/omarchy/session-restore/`
- Service: `omarchy-session-restore.service`
- CLI: `omarchy-session save|restore|status|resume`
- Earlier checkpoint: `omarchy-session restore --file /path/to/snapshot.json`
- Logs: `journalctl --user -u omarchy-session-restore.service`

Run `bash uninstall.sh` to stop it and remove automatic startup while retaining
saved sessions and source. Removing only the bar button does not stop the saver;
use the bar dropdown toggle or uninstall script.

Implementation references: [Hyprland dispatchers](https://wiki.hypr.land/Configuring/Basics/Dispatchers/)
and [IPC](https://wiki.hypr.land/IPC/). Uses the installed Omarchy manifest contract.
