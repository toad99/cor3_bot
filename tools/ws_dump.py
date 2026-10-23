#!/usr/bin/env python3
"""Enregistre ce que fait le jeu pendant que TU joues, pour documenter un écran nouveau ou modifié.

  python tools/ws_dump.py --minutes 10            trames WebSocket décodées -> logs/ws_dump_<date>.jsonl
  python tools/ws_dump.py --rooms market,sai      seulement certaines salles
  python tools/ws_dump.py --dom                   + HTML et sélecteurs stables de l'écran (logs/dom_<date>/) :
                                                  à chaque trame des salles --snap (drone, expéditions, courrier,
                                                  stash) et à chacune de tes actions, puis à la fin ;
                                                  fermer la fenêtre avant la fin ne fait rien perdre

Ouvre cor3.gg dans le profil Chrome dédié du bot (config.toml) : joue normalement dans cette fenêtre,
puis ferme-la (ou attends la fin du minutage). Rien n'est cliqué à ta place.
Les clés qui ressemblent à un secret (token, jwt, password, auth…) et les chaînes JWT sont masquées
avant écriture. Ferme cor3.gg ailleurs : une seule session par compte. Prend le verrou du bot
(cor3_bot.lock) : les tâches planifiées passent leur tour ou attendent pendant l'enregistrement.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cor3_bot  # noqa: E402

SECRET_KEY = re.compile(r"token|jwt|secret|passw|cookie|^auth(?!or)|authoriz|rkey|tkey|api.?key", re.I)
JWT = re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}")

DOM_INVENTORY_JS = r"""
() => {
  const pick = attr => {
    const out = {};
    document.querySelectorAll(`[${attr}]`).forEach(e => {
      const v = e.getAttribute(attr);
      if (!out[v]) out[v] = {count: 0, tag: e.tagName.toLowerCase(), text: (e.innerText || '').trim().slice(0, 40)};
      out[v].count++;
    });
    return out;
  };
  return {url: location.pathname, sentry_component: pick('data-sentry-component'),
          sentry_element: pick('data-sentry-element'), component_name: pick('data-component-name'),
          onboarding_id: pick('data-onboarding-300-id')};
}
"""


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<masqué>" if SECRET_KEY.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return JWT.sub("<jwt masqué>", value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} octets>"
    return value


SNAP_ROOMS = "drone-missions,expeditions,mail,stash"
NOISY = {"get.lots", "get.patches", "get.credits", "get.levels"}     # pas de capture d'écran pour celles-ci
SNAP_EVERY_S = 3
SNAP_MAX = 300


class Recorder(cor3_bot.GameEvents):
    def __init__(self, log: logging.Logger, out, rooms: set[str], snap_rooms: set[str] | None = None):
        super().__init__(log)
        self.out, self.rooms, self.count = out, rooms, 0
        self.snap_rooms = snap_rooms or set()
        self.snap_due: str | None = None        # « salle_action » de la dernière trame qui demande une capture

    def _push(self, direction: str, room: str | None, action: str | None, data: Any) -> None:
        super()._push(direction, room, action, data)
        if self.rooms and room not in self.rooms:
            return
        if direction == "rest":
            data = {"status": (data or {}).get("status")}
        line = {"t": dt.datetime.now().isoformat(timespec="milliseconds"), "dir": direction,
                "room": room, "action": action, "data": scrub(data)}
        self.out.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
        self.out.flush()
        self.count += 1
        player_action = direction == "out" and room == "event"
        if (room in self.snap_rooms or player_action) and action not in NOISY:
            self.snap_due = f"{'toi' if player_action else room}_{action}"


def snapshot(page, folder: Path, label: str, n: int) -> Path:
    """HTML (JWT masqués) et inventaire des sélecteurs stables de l'écran du moment."""
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{n:03d}_{dt.datetime.now():%H%M%S}_{re.sub(r'[^\w.-]+', '-', label)}"
    (folder / f"{name}.html").write_text(JWT.sub("<jwt masqué>", page.content()), encoding="utf-8")
    (folder / f"{name}.json").write_text(json.dumps(page.evaluate(DOM_INVENTORY_JS), ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    return folder / f"{name}.html"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Enregistre les trames du jeu pendant que tu joues")
    ap.add_argument("--config", default=str(cor3_bot.ROOT / "config.toml"))
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--rooms", default="", help="salles à garder, ex. market,sai,network-map")
    ap.add_argument("--dom", action="store_true", help="captures de l'écran (HTML + sélecteurs stables)")
    ap.add_argument("--snap", default=SNAP_ROOMS, help=f"salles qui déclenchent une capture (défaut {SNAP_ROOMS})")
    args = ap.parse_args(argv)

    cfg = cor3_bot.load_config(Path(args.config))
    gen = cfg["general"]
    log_dir = Path(gen.get("log_dir") or "logs")
    log_dir = log_dir if log_dir.is_absolute() else cor3_bot.ROOT / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    out_path = log_dir / f"ws_dump_{stamp}.jsonl"
    log = logging.getLogger("cor3-dump")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    rooms = {r.strip() for r in args.rooms.split(",") if r.strip()}
    lock = cor3_bot.ROOT / "cor3_bot.lock"
    if cor3_bot.lock_busy(lock):
        log.error("Le bot tourne en ce moment (%s) : relance quand il a fini.", lock)
        return 2
    lock.write_text(json.dumps({"pid": os.getpid(), "started": dt.datetime.now().isoformat(timespec="seconds"),
                                "auto": False, "ws_dump": True}), encoding="utf-8")
    try:
        return record(args, gen, log, log_dir, stamp, out_path, rooms)
    finally:
        lock.unlink(missing_ok=True)


def record(args: argparse.Namespace, gen: dict, log: logging.Logger, log_dir: Path, stamp: str, out_path: Path,
           rooms: set[str]) -> int:

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright
    with out_path.open("w", encoding="utf-8") as out, sync_playwright() as pw:
        profile = Path(gen.get("profile_dir") or cor3_bot.ROOT / "chrome-profile").expanduser()
        ctx = pw.chromium.launch_persistent_context(user_data_dir=str(profile),
                                                    channel=gen.get("channel", "chrome") or None,
                                                    headless=False, viewport={"width": 1600, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        snap_rooms = {r.strip() for r in args.snap.split(",") if r.strip()} if args.dom else set()
        rec = Recorder(log, out, rooms, snap_rooms)
        rec.attach(page)
        page.goto(cor3_bot.GAME_URL)
        log.info("Enregistrement dans %s pendant %.0f min : joue, puis ferme la fenêtre.", out_path, args.minutes)
        folder, shots, last = log_dir / f"dom_{stamp}", 0, 0.0
        deadline = time.time() + args.minutes * 60
        while time.time() < deadline and not page.is_closed():
            try:
                page.wait_for_timeout(1000)
                if rec.snap_due and shots < SNAP_MAX and time.time() - last >= SNAP_EVERY_S:
                    label, rec.snap_due = rec.snap_due, None
                    shots += 1
                    last = time.time()
                    log.info("Capture d'écran %s", snapshot(page, folder, label, shots).name)
            except PWError:
                break                               # fenêtre fermée pendant l'attente ou la capture
        if args.dom and not page.is_closed():
            try:
                log.info("Capture finale : %s", snapshot(page, folder, "fin", shots + 1))
            except PWError:
                pass
        try:
            ctx.close()
        except PWError:
            pass
    log.info("%d trames enregistrées.", rec.count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
