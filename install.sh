#!/bin/bash
set -euo pipefail
plugin_dir="$HOME/.config/omarchy/plugins/justin.session-restore"
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
command -v hyprctl >/dev/null
mkdir -p "$plugin_dir" "$HOME/.config/systemd/user" "$HOME/.config/omarchy/hooks/post-boot.d" "$HOME/.local/share/applications" "$HOME/.local/bin"
if [[ "$source_dir" != "$plugin_dir" ]]; then
  for file in session.py BarWidget.qml manifest.json README.md install.sh uninstall.sh test_session.py; do
    cp -- "$source_dir/$file" "$plugin_dir/$file"
  done
fi
cat > "$HOME/.config/systemd/user/omarchy-session-restore.service" <<'UNIT'
[Unit]
Description=Omarchy desktop session checkpoints and restore
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python "%h/.config/omarchy/plugins/justin.session-restore/session.py" daemon
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
UNIT
cat > "$HOME/.config/omarchy/hooks/post-boot.d/session-restore" <<'HOOK'
#!/bin/bash
systemctl --user start omarchy-session-restore.service
HOOK
chmod +x "$HOME/.config/omarchy/hooks/post-boot.d/session-restore"
cat > "$HOME/.local/bin/omarchy-session" <<'CLI'
#!/bin/bash
exec /usr/bin/python "$HOME/.config/omarchy/plugins/justin.session-restore/session.py" "$@"
CLI
chmod +x "$HOME/.local/bin/omarchy-session"
/usr/bin/python - <<'PY'
from pathlib import Path
import sys
root=Path.home()/'.config/omarchy/plugins/justin.session-restore'
sys.path.insert(0,str(root))
import session
if not session.CONFIG.exists():session.atomic(session.CONFIG,session.DEFAULTS)
else:
    cfg=session.config()
    cfg['excluded_classes']=sorted(set(cfg.get('excluded_classes',[])) | set(session.DEFAULTS['excluded_classes']))
    session.atomic(session.CONFIG,cfg)
# Installing on an existing desktop should not rearrange it.
import hashlib,os,time
signature=os.environ.get('HYPRLAND_INSTANCE_SIGNATURE','')
marker=session.RUNTIME/('started-'+hashlib.sha256(signature.encode()).hexdigest()[:16])
session.atomic(marker,{'installed':time.time()})
if not (session.STATE/'latest.json').exists():session.save()
(Path.home()/'.local/share/applications/org.omarchy.SessionRestore.desktop').unlink(missing_ok=True)
PY
systemctl --user daemon-reload
systemctl --user start omarchy-session-restore.service
omarchy-shell shell rescanPlugins
omarchy plugin enable justin.session-restore --section right --before omarchy.power
printf 'Desktop Session installed. Use the desktop-layout button in the bar.\n'
