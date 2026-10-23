#!/bin/sh
# Installation de cor3_bot sur un serveur Debian / Ubuntu (VPS), à lancer depuis le dossier du projet :
#   sh install_linux.sh            installe Python, Chrome et l'écran virtuel, puis les minuteurs systemd
#   sh install_linux.sh --remove   retire les minuteurs (le dossier et chrome-profile restent)
# Minuteurs (heure de Paris) : passage complet à 02:15, --auto à 00:05 puis tous les quarts d'heure.
# Les tâches du Planificateur Windows doivent être arrêtées : deux bots se volent la session.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

if [ "${1:-}" = "--remove" ]; then
    $SUDO systemctl disable --now cor3_bot.timer cor3_bot_auto.timer 2>/dev/null || true
    $SUDO rm -f /etc/systemd/system/cor3_bot.service /etc/systemd/system/cor3_bot.timer \
        /etc/systemd/system/cor3_bot_auto.service /etc/systemd/system/cor3_bot_auto.timer
    $SUDO systemctl daemon-reload
    echo "Minuteurs retirés."
    exit 0
fi

echo "== Paquets système (Python, écran virtuel, VNC pour la connexion)"
$SUDO apt-get update
$SUDO apt-get install -y python3 python3-venv xvfb x11vnc

echo "== Environnement Python"
[ -d "$DIR/.venv" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

echo "== Navigateur"
if [ "$(dpkg --print-architecture)" = "amd64" ]; then
    # Google Chrome, comme sur le PC (channel = "chrome")
    $SUDO "$DIR/.venv/bin/python" -m playwright install --with-deps chrome
else
    # Pas de Google Chrome pour ARM : Chromium de Playwright (channel = "" dans config.toml)
    $SUDO "$DIR/.venv/bin/python" -m playwright install-deps chromium
    "$DIR/.venv/bin/python" -m playwright install chromium
    echo "!! Processeur $(dpkg --print-architecture) : mets channel = \"\" dans [general] de config.toml"
fi

chmod +x "$DIR/run_cor3.sh" "$DIR/tools/login_vps.sh"
if [ ! -f "$DIR/config.toml" ]; then
    cp "$DIR/config.example.toml" "$DIR/config.toml"
    echo "!! config.toml créé depuis config.example.toml : reprends tes réglages du PC."
fi
if grep -q '^profile_dir *= *"[A-Za-z]:' "$DIR/config.toml"; then
    echo "!! profile_dir pointe vers un chemin Windows : mets profile_dir = \"\" (dossier chrome-profile du projet)."
fi

echo "== Minuteurs systemd (utilisateur $USER_NAME)"
unit() { $SUDO tee "/etc/systemd/system/$1" >/dev/null; }
unit cor3_bot.service <<EOF
[Unit]
Description=cor3_bot : passage complet (daily, jobs, mercenaires, drone)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$DIR
Environment=TZ=Europe/Paris
ExecStart=$DIR/run_cor3.sh
TimeoutStartSec=3h
EOF
unit cor3_bot.timer <<EOF
[Unit]
Description=cor3_bot : tous les jours à 02:15 (heure de Paris)

[Timer]
OnCalendar=*-*-* 02:15:00 Europe/Paris
Persistent=true

[Install]
WantedBy=timers.target
EOF
unit cor3_bot_auto.service <<EOF
[Unit]
Description=cor3_bot --auto : seulement ce qui est dû (minuteurs, drone, daily)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$DIR
Environment=TZ=Europe/Paris
ExecStart=$DIR/run_cor3.sh --auto
TimeoutStartSec=2h
EOF
unit cor3_bot_auto.timer <<EOF
[Unit]
Description=cor3_bot --auto : tous les quarts d'heure (00:05, 00:20...)

[Timer]
OnCalendar=*-*-* *:05/15:00 Europe/Paris

[Install]
WantedBy=timers.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now cor3_bot.timer cor3_bot_auto.timer
systemctl list-timers 'cor3_bot*' --no-pager || true

cat <<EOF

Installé. Il reste à te connecter une fois à cor3.gg dans le profil du bot :
  sh tools/login_vps.sh        (sur le VPS ; il t'explique comment ouvrir la fenêtre depuis ton PC)
Puis un essai sans rien valider :
  ./run_cor3.sh --dry-run
Journal : logs/cor3_AAAA-MM-JJ.log ; état des minuteurs : systemctl list-timers 'cor3_bot*'
EOF
