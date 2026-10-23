#!/bin/sh
# Connexion à cor3.gg dans le profil du bot, sur un VPS sans écran.
# Ouvre Chrome dans un écran virtuel et le partage en VNC, sur le VPS seulement (localhost, rien d'ouvert sur
# Internet). Depuis ton PC :
#   1. ssh -L 5901:localhost:5901 utilisateur@vps     (laisse cette fenêtre ouverte)
#   2. un client VNC (TigerVNC, RealVNC Viewer...) sur localhost:5901
#   3. connecte-toi à cor3.gg dans la fenêtre Chrome, puis ferme-la : le script s'arrête tout seul.
# Le bot ne lit jamais tes identifiants : c'est toi qui tapes, dans la fenêtre.
set -eu
cd "$(dirname "$0")/.."
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"
# --login ne prend pas le verrou : on met les minuteurs en pause pour que le bot n'ouvre pas le même profil
$SUDO systemctl stop cor3_bot_auto.timer cor3_bot.timer 2>/dev/null || true
while systemctl is-active --quiet cor3_bot_auto.service cor3_bot.service; do
    echo "Un passage du bot est en cours : j'attends qu'il finisse..."
    sleep 30
done
DISPLAY_NUM=":42"
XVFB="" VNC=""
trap 'kill $VNC $XVFB 2>/dev/null || true; $SUDO systemctl start cor3_bot_auto.timer cor3_bot.timer 2>/dev/null || true' EXIT
trap 'exit 1' INT TERM
Xvfb "$DISPLAY_NUM" -screen 0 1600x900x24 >/dev/null 2>&1 &
XVFB=$!
sleep 1
x11vnc -display "$DISPLAY_NUM" -localhost -rfbport 5901 -forever -shared -nopw -quiet >/dev/null 2>&1 &
VNC=$!
echo "Écran prêt. Depuis ton PC : ssh -L 5901:localhost:5901 $(id -un)@<ip du vps>, puis VNC sur localhost:5901."
echo "Les minuteurs du bot sont en pause jusqu'à la fermeture de la fenêtre."
DISPLAY="$DISPLAY_NUM" TZ="${TZ:-Europe/Paris}" .venv/bin/python cor3_bot.py --login
echo "Fenêtre fermée : connexion enregistrée dans le profil du bot."
