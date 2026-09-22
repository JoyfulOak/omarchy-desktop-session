#!/bin/bash
set -euo pipefail
systemctl --user stop omarchy-session-restore.service
omarchy plugin disable justin.session-restore
rm -f "$HOME/.config/omarchy/hooks/post-boot.d/session-restore" "$HOME/.config/systemd/user/omarchy-session-restore.service" "$HOME/.local/share/applications/org.omarchy.SessionRestore.desktop" "$HOME/.local/bin/omarchy-session"
systemctl --user daemon-reload
printf 'Disabled and removed automatic startup. Plugin source, preferences and saved sessions were kept.\n'
