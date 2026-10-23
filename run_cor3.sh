#!/bin/sh
# Lanceur Linux (minuteurs systemd du VPS) : l'équivalent de run_cor3.bat.
# Le VPS n'a pas d'écran : Chrome tourne dans un écran virtuel (xvfb-run), comme sur le PC du joueur.
cd "$(dirname "$0")" || exit 1
export TZ="${TZ:-Europe/Paris}"
exec xvfb-run -a -s "-screen 0 1600x900x24" .venv/bin/python cor3_bot.py "$@"
