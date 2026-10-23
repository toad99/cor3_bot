#!/usr/bin/env python3
"""cor3_bot : automatisation quotidienne de cor3.gg (ARG Fragmentary Order).

Commandes (depuis le dossier du script, venv activé) :
  python cor3_bot.py --login          1re fois : ouvre le profil Chrome dédié pour te connecter
  python cor3_bot.py                  exécution normale (daily, jobs, mercenaires selon config.toml)
  python cor3_bot.py --dry-run        simulation : lit et résout tout, mais ne valide rien
  python cor3_bot.py --only daily     une seule partie : daily, jobs, markets ou mercs (répétable)
  python cor3_bot.py --auto           passage du quart d'heure : ne fait que ce qui est dû (state.json)
  python cor3_bot.py --test-notify    envoie une alerte de test

Codes de sortie : 0 = tout OK, 1 = au moins une étape en échec, 2 = config/connexion/session.
Le Planificateur de tâches affiche ce code dans « Résultat de la dernière exécution ».
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import time
import tomllib
import traceback
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import msgpack
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout

from solvers import (
    CODE_LENGTH_BY_DIFFICULTY, ERROR_LABELS, Cell, Glyph, SignalError,
    analyse_log_line, decode_signal, ice_anchor_index, solve_ice_wall_partial,
)

ROOT = Path(__file__).resolve().parent
GAME_URL = "https://cor3.gg/"
TESTED_FRONTEND = "v1.25.71"
UTC = dt.timezone.utc

HOME_SERVER = "Home Server"
# Les 4 marchés (guide + inspection du 21/10/2026) : chacun a ses contrats, sa réputation et son
# minuteur de renouvellement (nextJobsResetAt). Une fois le minuteur échu, il ne repart que quand
# on rouvre l'onglet Contrats de ce marché : c'est ce que fait le bot.
DEFAULT_MARKETS = [HOME_SERVER, "D4RK RM7MI", "SRM7-M", "URM7-M"]
ACTIVE_EXPEDITION = {"PREPARING", "RUNNING", "EVENT", "RETURNING"}
# Job signé en échec : repris 15 min plus tard, au passage --auto suivant (tous les quarts d'heure).
JOB_RETRY = dt.timedelta(minutes=15)
ICE_JOB_TYPES = {"DecryptFile", "DecryptDownloadedFile"}     # passent par le mini-jeu ICE WALL

# ---------------------------------------------------------------------------
# Sélecteurs. On s'appuie sur des attributs posés par le build du jeu :
#   data-onboarding-300-id, data-sentry-component / data-sentry-element,
#   data-component-name, et quelques classes CSS « en clair » (pulse-bar, log-entry…).
# Les classes générées « go1234567 » changent d'un build à l'autre : ne pas les utiliser.
# ---------------------------------------------------------------------------
DOCK = {
    "game_center": "TabBarItem-019a0c41-b17f-7abc-1234-567890abcde2",
    "network_map": "TabBarItem-NETWORK_MAP",
    "expeditions": "TabBarItem-EXPEDITIONS",
    "loadout": "LoadoutTabBarItem",
    "stash": "StashTabBarItem",
}
APP_SIGNAL_DECODE = "019aea65-b0c2-70e9-bbbc-8caed416854c"


def comp(name: str) -> str:
    return f'[data-sentry-component="{name}"]'


def cname(name: str) -> str:
    return f'[data-component-name="{name}"]'


WINDOW = comp("AppWindow")
CLOSE_BTN = cname("close-app-btn")
SAI_APP = comp("ServerAdministrationInterfaceApplication")
SAI_LOGIN = comp("SaiLogin")


def rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# Bandeau du bureau : « 17.119 RENOMMÉE » puis « [107/900] » (niveau, points dans le palier, taille du palier).
RENOWN = re.compile(r"(\d+)(?:[.,]\d+)?\s*(?:RENOMMÉE|RENOWN)\s*\[\s*(\d+)\s*/\s*(\d+)\s*\]", re.I)


def renown_gain(before: tuple[int, int, int], after: tuple[int, int, int]) -> int | None:
    """Points gagnés entre deux lectures (niveau, points, palier) ; None si on a sauté un palier inconnu."""
    if after[0] == before[0]:
        return max(after[1] - before[1], 0)
    if after[0] == before[0] + 1:
        return before[2] - before[1] + after[1]
    return None


# Libellés FR | EN (le bureau suit la langue choisie, les mini-jeux restent en anglais).
TXT = {
    "daily_card": r"^\s*(OPÉRATIONS QUOTIDIENNES|DAILY OPS)\s*$",
    "open_module": r"^\s*Open Module\s*$",
    "market": r"^\s*(Marché|Market)\s*$",
    "contracts": r"^\s*(Contrats|Contracts)\s*$",
    "hire_tab": r"^\s*(Engager|Hire)\s*$",
    "read_more": r"^\s*(LIRE PLUS|READ MORE)\s*$",
    "sign": r"^\s*(Signer|Sign)\s*$",
    "complete": r"^\s*(Terminer|Complete)\s*$",
    "infos": r"^\s*(Infos|Info)\s*$",
    "ready": r"PRÊT À TERMINER|READY TO COMPLETE",
    "in_progress": r"EN COURS|IN PROGRESS",
    "connect": r"^\s*(Connecter|Connect)\s*$",
    "login": r"^\s*(Connexion|Login)\s*$",
    "decrypt": r"^\s*(Déchiffrer|Decrypt)\s*$",
    "tab_logs": r"^\s*(JOURNAUX|LOGS)\s*$",
    "tab_files": r"^\s*(FICHIERS|FILES)\s*$",
    "tab_transit": r"^\s*(ACCÈS DE TRANSIT|TRANSIT ACCESS)\s*$",
    "presets": r"^\s*(Préréglages|Presets)\s*$",
    "apply": r"^\s*(APPLIQUER|APPLY)\s*$",
    "back": r"^\s*(Retour|Back)\s*$",
    "hire_pay": r"verser un acompte|pay.*deposit|hire and",
    "drone_tab": r"Drone personnel|Personal drone",
    "drone_launch": r"Démarrer la sortie|Start sortie|Launch",
    "drone_send": r"^\s*(Envoyer|Send)\s*$",
    "claim_all": r"^\s*(Tout récupérer|Claim all|Collect all)\s*$",
    "disconnected": r"DÉCONNECTÉ|DISCONNECTED",
}


class SessionLost(RuntimeError):
    """Le serveur a donné la session à un autre onglet ou appareil."""


class StepFailed(RuntimeError):
    """Étape impossible : on arrête proprement cette partie et on alerte."""


class LoginRequired(RuntimeError):
    """Le profil dédié n'est pas (ou plus) connecté à cor3.gg."""


class Unavailable(StepFailed):
    """Serveur en maintenance, injoignable ou absent de la carte : état normal du jeu, on réessaiera."""

    def __init__(self, message: str, retry_at: dt.datetime | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class IceWallLocked(StepFailed):
    """ICE WALL verrouillé (20 min après un échec) ou limite quotidienne atteinte : plus de déchiffrement
    ni de piratage par mini-jeu pendant ce passage ; retry_at = fin du verrou."""

    def __init__(self, message: str, retry_at: dt.datetime | None = None):
        super().__init__(message)
        self.retry_at = retry_at


class Hopeless(StepFailed):
    """Job signé impossible à finir (module de déchiffrement trop faible pour le fichier tiré) : le dépôt
    ou l'amende sont perdus, inutile de réessayer."""


class Simulated(StepFailed):
    """Mode --dry-run : la suite dépend d'une action qu'on n'a pas faite (ex. piratage)."""


# ---------------------------------------------------------------------------
# Configuration, journal, alertes
# ---------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Config introuvable : {path}. Copie config.example.toml en config.toml.")
    with path.open("rb") as fh:
        cfg = tomllib.load(fh)
    cfg.setdefault("general", {})
    for section in ("daily", "jobs", "markets", "mercenaries", "drone", "notify", "auto"):
        cfg.setdefault(section, {})
    return cfg


def market_names(cfg: dict) -> list[str]:
    return list(cfg.get("markets", {}).get("names") or DEFAULT_MARKETS)


def parse_time(value: Any) -> dt.datetime | None:
    """Date du jeu -> datetime UTC. Le serveur envoie des chaînes ISO (« 2026-10-21T09:38:18.462Z »),
    des timestamps msgpack (déjà convertis en datetime) ou des millisecondes."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def local_time(value: Any) -> str:
    """Heure locale lisible (journal, alertes)."""
    when = parse_time(value)
    return when.astimezone().strftime("%d/%m %H:%M") if when else "?"


def stem(name: str) -> str:
    """Nom de fichier sans son extension : « db_6531.txt » -> « db_6531 »."""
    return name.rsplit(".", 1)[0] if "." in name else name


def xpath_literal(text: str) -> str:
    """Chaîne XPath 1.0 sûre (guillemets simples et doubles)."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in text.split("'")) + ")"


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


class State:
    """Mémoire entre deux passages (state.json) : daily faite, minuteur de chaque marché, jobs signés
    à reprendre, expédition en cours. Le mode --auto s'en sert pour ne lancer le navigateur que si besoin."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"daily_done": None, "daily_warned": None, "markets": {}, "pending_jobs": {},
                           "expedition_active": False}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            pass                                  # fichier abîmé : on repart de zéro
        if not isinstance(self.data.get("markets"), dict):
            self.data["markets"] = {}
        if not isinstance(self.data.get("pending_jobs"), dict):
            self.data["pending_jobs"] = {}

    # ----- marchés -----
    def set_market_reset(self, name: str, value: Any, now: dt.datetime | None = None) -> None:
        now = now or utc_now()
        when = parse_time(value)
        entry = self.data["markets"].setdefault(name, {})
        entry["checked"] = now.isoformat(timespec="seconds")
        entry["next_reset"] = when.isoformat(timespec="seconds") if when else None
        entry.pop("problem", None)
        entry.pop("retry_at", None)
        if when and when <= now:                  # minuteur échu mais pas relancé : on repassera
            entry["retry_at"] = (now + dt.timedelta(minutes=30)).isoformat(timespec="seconds")

    def set_market_retry(self, name: str, when: dt.datetime | None, problem: str,
                         now: dt.datetime | None = None) -> None:
        now = now or utc_now()
        entry = self.data["markets"].setdefault(name, {})
        entry["retry_at"] = (when or now + dt.timedelta(hours=1)).isoformat(timespec="seconds")
        entry["problem"] = problem

    def market_due(self, name: str, now: dt.datetime | None = None) -> bool:
        now = now or utc_now()
        entry = self.data["markets"].get(name)
        if not entry:
            return True
        retry = parse_time(entry.get("retry_at"))
        if entry.get("problem") and retry and now < retry:
            return False                          # marché lui-même indisponible (maintenance) : on attend
        if any(p.get("market") == name for p in self.pending_due(now)):
            return True
        if retry:
            return now >= retry
        reset = parse_time(entry.get("next_reset"))
        if reset:
            return now >= reset
        checked = parse_time(entry.get("checked"))
        return checked is None or now - checked >= dt.timedelta(hours=6)

    def set_market_revisit(self, name: str, when: dt.datetime) -> None:
        """Jobs faisables laissés sur ce marché (fusible, ICE WALL bloqué, budget) : y repasser à `when`,
        sans attendre le minuteur. Inutile si le minuteur tombe avant : la liste sera remplacée."""
        entry = self.data["markets"].setdefault(name, {})
        reset = parse_time(entry.get("next_reset"))
        if reset and reset <= when:
            return
        entry["retry_at"] = when.isoformat(timespec="seconds")
        entry.pop("problem", None)

    def next_market_time(self, name: str) -> dt.datetime | None:
        entry = self.data["markets"].get(name) or {}
        return parse_time(entry.get("retry_at")) or parse_time(entry.get("next_reset"))

    # ----- jobs signés pas encore terminés -----
    def pending_jobs(self) -> dict:
        return self.data["pending_jobs"]

    def set_job_pending(self, job_id: Any, market: str, name: str, problem: str,
                        retry_at: dt.datetime | None = None, now: dt.datetime | None = None,
                        deadline: dt.datetime | None = None) -> None:
        """Job signé mais pas terminé (maintenance, route coupée, échec) : on revient sur son marché à
        retry_at (JOB_RETRY par défaut) au lieu d'attendre le renouvellement du minuteur. deadline : échéance
        du job (pour information : un job expiré disparaît des jobs signés et sort d'ici)."""
        now = now or utc_now()
        self.data["pending_jobs"][str(job_id)] = {
            "market": market, "name": name, "problem": problem,
            "retry_at": (retry_at or now + JOB_RETRY).isoformat(timespec="seconds"),
            "deadline": deadline.isoformat(timespec="seconds") if deadline else None}

    def note_hack_power(self, power: Any) -> None:
        """Puissance du meilleur outil de piratage vu (tous préréglages confondus), pour refuser d'avance un
        contrat sur un serveur trop défendu."""
        try:
            value = float(power)
        except (TypeError, ValueError):
            return
        if value > float(self.data.get("hack_power") or 0):
            self.data["hack_power"] = value

    def mark_job_lost(self, job_id: Any) -> None:
        """Job signé impossible à finir : ne plus jamais le retenter (il expirera tout seul)."""
        self.clear_job_pending(job_id)
        lost = self.data.setdefault("lost_jobs", [])
        if str(job_id) not in lost:
            lost.append(str(job_id))
        del lost[:-50]

    def job_lost(self, job_id: Any) -> bool:
        return str(job_id) in (self.data.get("lost_jobs") or [])

    def clear_job_pending(self, job_id: Any) -> None:
        self.data["pending_jobs"].pop(str(job_id), None)

    def prune_pending(self, market: str, taken_ids: set) -> None:
        """Oublie les jobs de ce marché qui ne sont plus signés (terminés à la main, expirés ou échoués)."""
        taken = {str(i) for i in taken_ids}
        for job_id, entry in list(self.data["pending_jobs"].items()):
            if entry.get("market") == market and job_id not in taken:
                del self.data["pending_jobs"][job_id]

    def pending_due(self, now: dt.datetime | None = None) -> list[dict]:
        """Jobs signés à reprendre maintenant."""
        now = now or utc_now()
        return [p for p in self.data["pending_jobs"].values()
                if isinstance(p, dict) and now >= (parse_time(p.get("retry_at")) or now)]

    # ----- daily (reset à 00:00 UTC) -----
    def daily_done_today(self, now: dt.datetime | None = None) -> bool:
        return self.data.get("daily_done") == (now or utc_now()).date().isoformat()

    def mark_daily_done(self, now: dt.datetime | None = None) -> None:
        self.data["daily_done"] = (now or utc_now()).date().isoformat()

    # ----- mercenaires -----
    def set_expedition(self, active: bool) -> None:
        self.data["expedition_active"] = bool(active)

    # ----- drone personnel -----
    def set_drone_next(self, when: dt.datetime | None, why: str = "") -> None:
        """Prochain passage utile pour le drone : retour de mission, batterie rechargée (None = au plus tôt)."""
        self.data["drone"] = {"next": when.isoformat(timespec="seconds") if when else None, "why": why}

    def drone_due(self, now: dt.datetime | None = None) -> bool:
        entry = self.data.get("drone") or {}
        when = parse_time(entry.get("next")) if isinstance(entry, dict) else None
        return when is None or (now or utc_now()) >= when

    def due(self, cfg: dict, now: dt.datetime | None = None) -> dict[str, str]:
        """Parties à lancer maintenant (mode --auto), avec la raison."""
        now = now or utc_now()
        due: dict[str, str] = {}
        if cfg["daily"].get("enabled", True) and not self.daily_done_today(now):
            due["daily"] = "daily pas encore faite aujourd'hui (UTC)"
        late = [n for n in market_names(cfg) if self.market_due(n, now)]
        if late:
            resume = [p for p in self.pending_due(now) if p.get("market") in late]
            timers = [n for n in late if n not in {p.get("market") for p in resume}]
            reasons = []
            if timers:
                reasons.append("minuteur échu ou inconnu : " + ", ".join(timers))
            if resume:
                reasons.append("job signé à reprendre : "
                               + ", ".join(f"{p.get('name')} ({p.get('market')})" for p in resume))
            why = " ; ".join(reasons)
            if cfg["jobs"].get("enabled", False):
                due["jobs"] = why
            elif cfg["markets"].get("enabled", True):
                due["markets"] = why
        if cfg["mercenaries"].get("enabled", False) and self.data.get("expedition_active"):
            due["mercs"] = "expédition en cours"
        if cfg["drone"].get("enabled", False) and self.drone_due(now):
            due["drone"] = "drone : retour de mission ou batterie rechargée"
        return due

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.path)


def pid_alive(pid: int) -> bool:
    """Le processus existe-t-il encore ? (sous Windows, os.kill(pid, 0) tuerait le processus)"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5                   # accès refusé = il existe
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259                     # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_busy(lock: Path, max_age_h: float = 3) -> bool:
    """Verrou d'une autre exécution encore vivante (un verrou laissé par un processus tué est ignoré)."""
    try:
        if time.time() - lock.stat().st_mtime > max_age_h * 3600:
            return False
        info = json.loads(lock.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True                                   # ancien format : on se fie à l'âge
    return pid_alive(int(info.get("pid", 0))) if isinstance(info, dict) else True


def user_idle_seconds() -> float | None:
    """Secondes depuis la dernière action clavier/souris sous Windows (None ailleurs)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("cor3")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_dir / f"cor3_{dt.date.today():%Y-%m-%d}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.handlers[:] = [fh, ch]
    return log


class Notifier:
    def __init__(self, cfg: dict, log: logging.Logger):
        self.cfg, self.log = cfg, log

    def send(self, title: str, message: str, failure: bool = True) -> None:
        if not failure and not self.cfg.get("notify_on_success", False):
            return
        if self.cfg.get("windows_toast", True) and sys.platform == "win32":
            self._toast(title, message)
        if url := self.cfg.get("discord_webhook"):
            self._post_json(url, {"content": f"**{title}**\n{message}"[:1900]})
        if topic := self.cfg.get("ntfy_topic"):
            self._post_raw(f"https://ntfy.sh/{topic}", message.encode(), {"Title": title.encode("ascii", "ignore").decode()})

    def _toast(self, title: str, message: str) -> None:
        def esc(s: str) -> str:
            return s.replace("'", "''")[:250]
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$n = $t.GetElementsByTagName('text');"
            f"$n.Item(0).AppendChild($t.CreateTextNode('{esc(title)}')) > $null;"
            f"$n.Item(1).AppendChild($t.CreateTextNode('{esc(message)}')) > $null;"
            "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($t))"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           timeout=20, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:  # une alerte ne doit jamais faire planter le script
            self.log.warning("Notification Windows impossible : %s", exc)

    def _post_json(self, url: str, payload: dict) -> None:
        self._post_raw(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})

    def _post_raw(self, url: str, body: bytes, headers: dict) -> None:
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "cor3_bot", **headers})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as exc:
            self.log.warning("Alerte %s impossible : %s", url.split("/")[2], exc)


# ---------------------------------------------------------------------------
# Écoute du WebSocket du jeu (socket.io + msgpack) et des réponses REST utiles
# ---------------------------------------------------------------------------
@dataclass
class WsEvent:
    seq: int
    t: float
    direction: str          # "in" (serveur -> client), "out" ou "rest"
    room: str | None        # ex. "market", "expeditions", "sai", "session-replaced"
    action: str | None      # ex. "get.jobs", "respond.event"
    data: Any


def _ext_hook(code: int, data: bytes) -> Any:
    return msgpack.ExtType(code, data)


class GameEvents:
    def __init__(self, log: logging.Logger):
        self.log = log
        self.events: deque[WsEvent] = deque(maxlen=20000)
        self.seq = 0
        self.session_lost = False
        self.frontend_version: str | None = None
        self.servers: dict[str, dict] = {}      # id -> serveur (carte du réseau, tenu à jour)

    def attach(self, page: Page) -> None:
        page.on("websocket", self._on_websocket)
        page.on("response", self._on_response)

    def _on_websocket(self, ws) -> None:
        if "socket.io" not in ws.url:      # les mini-jeux (Colyseus) ne nous intéressent pas
            return
        self.log.debug("WebSocket ouvert : %s", ws.url.split("?")[0])
        ws.on("framereceived", lambda payload: self._on_frame("in", payload))
        ws.on("framesent", lambda payload: self._on_frame("out", payload))

    def _on_frame(self, direction: str, payload: Any) -> None:
        if not isinstance(payload, (bytes, bytearray)):
            return
        try:
            msg = msgpack.unpackb(payload, raw=False, strict_map_key=False, timestamp=3, ext_hook=_ext_hook)
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        data = msg.get("data")
        room, body = None, data
        if isinstance(data, list) and data:
            room = data[0] if isinstance(data[0], str) else None
            body = data[1] if len(data) > 1 else None
        action, payload_data = None, body
        if isinstance(body, dict) and isinstance(body.get("event"), dict):
            action = body["event"].get("action")
            payload_data = body.get("data")
        if room is not None:     # nom seulement, jamais le contenu : sert à suivre une nouvelle version du jeu
            self.log.debug("WS %s %s", "←" if direction == "in" else "→", f"{room}:{action}" if action else room)
        if room == "session-replaced" and direction == "in":
            self.session_lost = True
            self.log.error("Le serveur signale « session-replaced » : un autre onglet/appareil a pris la main.")
        if room == "connected" and isinstance(body, dict):
            self.frontend_version = body.get("frontendVersion")
        if room == "network-map" and direction == "in" and isinstance(payload_data, dict):
            self._merge_map(payload_data)
        self._push(direction, room, action, payload_data)

    def _merge_map(self, data: dict) -> None:
        """get.map renvoie un « snapshot », set.endpoint et les mises à jour des « delta »."""
        state = data.get("state") or {}
        servers = state.get("servers") or []
        if data.get("kind") == "snapshot":
            self.servers = {}
        for srv in servers:
            if isinstance(srv, dict) and srv.get("id"):
                self.servers[srv["id"]] = {**self.servers.get(srv["id"], {}), **srv}

    def server(self, name: str) -> dict | None:
        return next((s for s in self.servers.values() if s.get("serverName") == name), None)

    def endpoint(self) -> str | None:
        return next((s.get("serverName") for s in self.servers.values() if s.get("isEndpoint")), None)

    def _on_response(self, resp) -> None:
        if "/api/user-daily-claim" in resp.url:
            path = urllib.parse.urlparse(resp.url).path
            self._push("rest", path, resp.request.method, {"status": resp.status, "response": resp})

    def _push(self, direction: str, room: str | None, action: str | None, data: Any) -> None:
        self.seq += 1
        self.events.append(WsEvent(self.seq, time.time(), direction, room, action, data))

    def mark(self) -> int:
        return self.seq

    def find(self, since: int = 0, room: str | None = None, action: str | None = None,
             direction: str = "in", pred: Callable[[WsEvent], bool] | None = None) -> list[WsEvent]:
        return [e for e in list(self.events)
                if e.seq > since and e.direction == direction
                and (room is None or e.room == room) and (action is None or e.action == action)
                and (pred is None or pred(e))]

    def last(self, room: str | None = None, action: str | None = None, direction: str = "in",
             since: int = 0) -> WsEvent | None:
        found = self.find(since, room, action, direction)
        return found[-1] if found else None


# ---------------------------------------------------------------------------
# Script injecté pour lire le mur ICE WALL (SVG) : glyphes cibles + cellules
# ---------------------------------------------------------------------------
ICE_WALL_JS = r"""
() => {
  const iw = document.querySelector('[data-sentry-component="IceWallBreakApplication"]');
  if (!iw) return null;
  const tp = iw.querySelector('[data-sentry-component="TargetPreview"]');
  const wb = iw.querySelector('[data-sentry-component="WallBoard"]');
  const sig = g => [...g.querySelectorAll('path')]
      .filter(p => p.getAttribute('fill-opacity') !== '0.2')
      .map(p => (p.getAttribute('d') || '').replace(/\s+/g, ' ')).sort().join('|');
  const tr = s => { const m = /translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\)/.exec(s || ''); return m ? [+m[1], +m[2]] : [NaN, NaN]; };
  const targets = tp ? [...tp.children].filter(c => c.tagName.toLowerCase() === 'g').map(g => {
      const t = g.getAttribute('transform') || ''; const [x, y] = tr(t);
      return {sig: sig(g), x, y, flip: /scale/.test(t)}; }) : [];
  const layer = wb ? wb.querySelector(':scope > g') : null;
  const cells = layer ? [...layer.children].map((c, i) => {
      const inner = c.firstElementChild; const t = inner ? (inner.getAttribute('transform') || '') : '';
      const [x, y] = tr(t); const r = c.getBoundingClientRect(); const flip = /scale/.test(t);
      return {index: i, sig: inner ? sig(inner) : '', x, y, flip,
              npath: inner ? inner.querySelectorAll('path').length : 0,
              cx: r.x + r.width / 2, cy: r.y + r.height * (flip ? 1 / 3 : 2 / 3),
              cls: String(c.className && c.className.baseVal !== undefined ? c.className.baseVal : c.className),
              opacity: getComputedStyle(c).opacity}; }) : [];
  const m = iw.innerText.match(/(\d+)\s*\/\s*(\d+)/);
  return {targets, cells, done: m ? +m[1] : null, total: m ? +m[2] : null,
          limit: /daily limit reached|limite quotidienne/i.test(iw.innerText)};
}
"""


# ---------------------------------------------------------------------------
# Le bot
# ---------------------------------------------------------------------------
class Bot:
    def __init__(self, page: Page, cfg: dict, log: logging.Logger, events: GameEvents, dry_run: bool,
                 state: "State | None" = None):
        self.page, self.cfg, self.log, self.events, self.dry_run = page, cfg, log, events, dry_run
        self.state = state
        self.auto_mode = False          # --auto : ne visiter que les marchés dont le minuteur est échu
        self.debug_dir = Path(cfg["general"].get("log_dir", ROOT / "logs")) / "debug"
        self._market_name: str | None = None
        self._original_endpoint: str | None = None
        self._current_preset: str | None = None
        self._renown: tuple[int, int, int] | None = None

    # ----- outils génériques -------------------------------------------------
    def check_session(self) -> None:
        if self.events.session_lost:
            raise SessionLost("session reprise par un autre onglet ou appareil")

    def pause(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)
        self.check_session()

    def wait_event(self, room: str | None, action: str | None, since: int, timeout: float = 15,
                   pred: Callable[[WsEvent], bool] | None = None, direction: str = "in") -> WsEvent:
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.events.find(since, room, action, direction, pred)
            if found:
                return found[-1]
            self.pause(150)
        raise StepFailed(f"pas de réponse serveur « {room}:{action} » en {timeout:.0f} s")

    def window(self, inner: str) -> Locator:
        return self.page.locator(WINDOW).filter(has=self.page.locator(inner))

    def close_window(self, inner: str) -> None:
        win = self.window(inner)
        for _ in range(win.count()):
            try:
                win.first.locator(CLOSE_BTN).first.click(timeout=3000)
                self.pause(300)
            except PWError:
                break

    def close_all_windows(self) -> None:
        for _ in range(12):
            btn = self.page.locator(f"{WINDOW} {CLOSE_BTN}")
            if not btn.count():
                return
            try:
                btn.last.click(timeout=3000)
            except PWError:
                return
            self.pause(250)

    def dock(self, key: str) -> None:
        # v1.25.71 : le dock peut être masqué (fenêtre Marché ouverte en grand) : clic DOM si besoin
        self.safe_click(self.page.locator(f'[data-onboarding-300-id="{DOCK[key]}"]').first)
        self.pause(600)

    def button(self, scope: Locator | Page, key_or_pattern: str) -> Locator:
        pattern = TXT.get(key_or_pattern, key_or_pattern)
        return scope.get_by_role("button", name=rx(pattern))

    def safe_click(self, target: Locator, timeout: float = 5000) -> None:
        """Clic normal ; si l'élément est caché par une autre fenêtre ou hors de la zone visible de la
        carte, clic DOM direct (React le reçoit pareil)."""
        try:
            target.click(timeout=timeout)
        except PWError:
            self.log.debug("Clic bloqué (fenêtre devant ou hors champ) : clic DOM à la place")
            target.dispatch_event("click")

    def allowed(self, what: str) -> bool:
        """Point de passage obligatoire avant toute validation (mode simulation)."""
        if self.dry_run:
            self.log.info("[SIMULATION] je m'arrête avant : %s", what)
            return False
        self.log.info("Action : %s", what)
        return True

    def snapshot(self, label: str) -> None:
        """Capture d'écran + HTML pour diagnostiquer un écran qui a changé."""
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^a-z0-9]+', '_', label.lower())}"
            self.page.screenshot(path=str(self.debug_dir / f"{stamp}.png"))
            (self.debug_dir / f"{stamp}.html").write_text(self.page.content(), encoding="utf-8")
            self.log.info("Diagnostic enregistré : debug/%s.(png|html)", stamp)
        except Exception as exc:
            self.log.warning("Diagnostic impossible : %s", exc)

    def balance(self) -> int | None:
        ev = self.events.last("profile", "get.credits") or self.events.last("profile", "receive.credits")
        if ev and isinstance(ev.data, dict):
            return ev.data.get("balance", ev.data.get("newBalance"))
        return None

    # ----- démarrage -------------------------------------------------------------
    def open_game(self) -> None:
        self.log.info("Ouverture de %s", GAME_URL)
        self.page.goto(GAME_URL, wait_until="domcontentloaded", timeout=60000)
        dock = self.page.locator(f'[data-onboarding-300-id="{DOCK["game_center"]}"]')
        loadout = self.page.locator(comp("LoadoutHeader"))
        try:
            self.page.locator(f'[data-onboarding-300-id="{DOCK["game_center"]}"]:visible, '
                              f'{comp("LoadoutHeader")}:visible').first.wait_for(timeout=90000)
            if not dock.is_visible() and loadout.count():
                self.leave_loadout()        # le jeu se rouvre sur le dernier écran (passage interrompu)
                dock.wait_for(timeout=30000)
        except PWTimeout:
            if self.page.locator("input[type=email], input[type=password]").count():
                raise LoginRequired("profil non connecté : lance `python cor3_bot.py --login`")
            self.snapshot("bureau_introuvable")
            raise StepFailed("le bureau du jeu ne s'est pas affiché")
        self.wait_event("connected", None, 0, timeout=45)
        version = self.events.frontend_version
        self.log.info("Connecté au serveur temps réel (frontend %s)", version)
        if version and version != TESTED_FRONTEND:
            self.log.warning("Version du jeu %s différente de celle testée (%s) : surveille le journal.",
                             version, TESTED_FRONTEND)
        self.close_all_windows()

    def leave_loadout(self) -> None:
        """Écran Loadout resté ouvert : le rallumer s'il est éteint, puis « Retour »."""
        self.log.warning("Le jeu s'est ouvert sur l'écran Loadout : je le rallume si besoin et je le quitte")
        close = self.page.locator('[data-sentry-element="LoadoutModalCloseButtonStyled"]')
        if close.count():
            close.first.click()
            self.pause(400)
        if self.page.evaluate("() => localStorage.getItem('loadout-powered')") != "true":
            self.page.locator('[data-sentry-element="LoadoutHeaderPowerButtonStyled"]').click()
            self.pause(1500)
        self.button(self.page, "back").click()
        self.pause(1200)

    def renown(self) -> tuple[int, int, int] | None:
        """Bandeau de Renommée du bureau (« 17.119 RENOMMÉE [107/900] ») : (niveau, points, points du palier)."""
        loc = self.page.locator(cname("LevelIndicatorTop"))
        for i in range(loc.count()):
            if m := RENOWN.search(loc.nth(i).text_content() or ""):
                return int(m.group(1)), int(m.group(2)), int(m.group(3))
        return None

    def note_renown(self, moment: str) -> None:
        """Journalise la Renommée (lue par --bilan) et ce qu'elle a gagné depuis la lecture précédente."""
        try:
            now = self.renown()
        except PWError:
            now = None
        if now is None:
            self.log.debug("Renommée illisible (%s) : bandeau LevelIndicator absent", moment)
            return
        gain = renown_gain(self._renown, now) if self._renown else None
        self._renown = now
        self.log.info("Renommée : niveau %d, %d/%d (%s%s)", *now, moment, f", +{gain}" if gain else "")

    def ensure_connected(self) -> None:
        self.check_session()
        label = self.page.get_by_text(rx(r"Terminal local|Local Terminal")).first
        if label.count():
            bar = label.locator("xpath=..").inner_text()
            if re.search(TXT["disconnected"], bar):
                raise SessionLost("terminal local DÉCONNECTÉ (session ouverte ailleurs ?)")

    # =========================================================================
    # 1. Daily tasks
    # =========================================================================
    def run_daily(self) -> str:
        self.ensure_connected()
        self.close_all_windows()
        mark = self.events.mark()
        self.dock("game_center")
        gc = self.window(comp("GameCenterApplication"))
        gc.wait_for(timeout=15000)
        title = gc.get_by_text(rx(TXT["daily_card"])).first
        # Plus petit ancêtre qui contient « Open Module », quelle que soit sa balise : la carte est un
        # <div> en v1.25.51 et un <button> en v1.25.71 (ancestor::div remontait alors jusqu'à la grille).
        card = title.locator("xpath=ancestor::*[.//*[normalize-space()='Open Module']][1]")
        card.get_by_text(rx(TXT["open_module"])).click()
        main = self.window(comp("DailyOpsMainScreen"))
        main.wait_for(timeout=15000)

        state = self._daily_state(mark)
        self.log.info("Daily : jeu=%s, série=%s, difficulté=%s, déjà faite=%s",
                      state.get("currentGameId"), state.get("currentStreak"),
                      state.get("difficulty"), state.get("hasClaimedToday"))
        if state.get("hasClaimedToday"):
            self.close_all_windows()
            if self.state is not None:
                self.state.mark_daily_done()
            return "déjà faite aujourd'hui"

        start = main.locator(comp("DailyOpsStartButton"))
        if not start.count():
            start = main.get_by_role("button").last
        start.click()
        self.pause(1500)

        if state.get("currentGameId") == APP_SIGNAL_DECODE or self.window(cname(f"Application-{APP_SIGNAL_DECODE}")).count():
            result = self.solve_signal_decode(state.get("difficulty"))
        else:
            result = self.solve_log_integrity()
        self.close_all_windows()
        if self.state is not None and not result.startswith("simulation"):
            self.state.mark_daily_done()
        return result

    def _daily_state(self, since: int) -> dict:
        """État de la daily (GET /api/user-daily-claim lu au passage, sans rejouer l'appel)."""
        try:
            ev = self.wait_event("/api/user-daily-claim", "GET", since, timeout=12, direction="rest")
        except StepFailed:
            ev = self.events.last("/api/user-daily-claim", "GET", direction="rest")
        if ev is not None:
            try:
                return ev.data["response"].json()
            except Exception as exc:
                self.log.warning("Réponse daily illisible (%s) : je lis l'écran", exc)
        text = self.window(comp("DailyOpsMainScreen")).inner_text()
        done = bool(re.search(r"Toutes les tâches sont terminées|All tasks (are )?(completed|done)", text, re.I))
        game = APP_SIGNAL_DECODE if re.search(r"Décodage du signal|Signal Decode", text, re.I) else None
        return {"hasClaimedToday": done, "currentGameId": game, "difficulty": None, "currentStreak": None}

    def _wait_claim(self, since: int) -> None:
        ev = self.wait_event("/api/user-daily-claim", "POST", since, timeout=20, direction="rest")
        status = ev.data["status"]
        if status not in (200, 201):
            raise StepFailed(f"le serveur a refusé la validation de la daily (HTTP {status})")
        body = ev.data["response"].json()
        self.log.info("Daily validée : série=%s, prochaine tâche %s", body.get("currentStreak"), body.get("nextTaskTime"))

    # ----- Signal Decode -----------------------------------------------------
    def solve_signal_decode(self, difficulty: str | None) -> str:
        win = self.window(cname(f"Application-{APP_SIGNAL_DECODE}"))
        win.wait_for(timeout=15000)
        code_length = CODE_LENGTH_BY_DIFFICULTY.get(difficulty or "")
        for attempt in range(1, 4):
            get_signal = self.button(win, r"^\s*Get Signal\s*$")
            if get_signal.count():
                get_signal.click()
            groups = win.locator(".pulse-timeline .pulse-group")
            groups.first.wait_for(timeout=10000)
            pulses = groups.evaluate_all(
                "gs => gs.map(g => g.querySelector('.pulse-bar')?.classList.contains('long') ? 'L' : 'S').join('')")
            try:
                encoding, code = decode_signal(pulses, code_length=code_length)
            except SignalError as exc:
                self.log.warning("Signal ambigu (%s) : je ferme et je relance (essai %d)", exc, attempt)
                self.close_window(cname(f"Application-{APP_SIGNAL_DECODE}"))
                self.window(comp("DailyOpsMainScreen")).locator(comp("DailyOpsStartButton")).click()
                win.wait_for(timeout=15000)
                continue
            self.log.info("Signal %s (%d impulsions) -> %s : %s", pulses, len(pulses), encoding, code)

            win.locator(".next-button").click()                                   # Select Encoding
            win.locator(".encoding-option").filter(has_text=rx("Morse" if encoding == "morse" else "Binary")).click()
            win.locator(".next-button").click()                                   # Decode Signal
            field = win.locator("input.code-input")
            field.wait_for(timeout=10000)
            maxlen = int(field.get_attribute("maxlength") or 0)
            rows = win.locator(".encoding-table-full .table-row").evaluate_all(
                "rs => rs.map(r => [r.querySelector('.pulses').textContent.replace(/[^SL]/g, ''),"
                " r.querySelector('.digit').textContent.replace(/[^0-9]/g, '')])")
            table = {p: d for p, d in rows if p and d}
            _, check = decode_signal(pulses, code_length=maxlen or None,
                                     morse_table=table if encoding == "morse" else None,
                                     binary_table=table if encoding == "binary" else None)
            if check != code or (maxlen and len(code) != maxlen):
                self.snapshot("signal_incoherent")
                raise StepFailed(f"code {code} incohérent avec la page (table -> {check}, longueur {maxlen})")
            field.fill(code)
            if not self.allowed(f"Signal Decode : valider le code {code}"):
                return f"simulation : code {code}"
            mark = self.events.mark()
            win.locator(".submit-button").click()
            try:
                win.locator(".result-screen").wait_for(timeout=8000)
            except PWTimeout:
                self.log.warning("Code refusé, nouveau signal (essai %d)", attempt)
                continue
            self._wait_claim(mark)
            return f"Signal Decode réussi ({code})"
        raise StepFailed("Signal Decode : 3 essais sans succès")

    # ----- System Log Integrity ---------------------------------------------
    # Écran non disponible le jour de l'inspection : sélecteurs tirés du code du
    # jeu et du guide. Lance une fois en --dry-run quand cette daily tombe.
    def solve_log_integrity(self) -> str:
        win = self.page.locator(WINDOW).filter(
            has=self.page.locator(f"{comp('GameWaitingScreen')}, .game-container")
        ).filter(has_not=self.page.locator(f"{comp('DailyOpsMainScreen')}, .signal-display")).last
        win.wait_for(timeout=15000)
        for attempt in range(1, 4):
            intro = win.locator('[data-sentry-element="GameWaitingScreenGameActionButtonStyled"]')
            if intro.count():
                intro.first.click()
            entries = win.locator(".log-entries .log-entry")
            try:
                entries.first.wait_for(timeout=4000)
            except PWTimeout:                       # écran d'accueil propre au mini-jeu (« Press START »)
                start = self.button(win, r"^\s*(Start|Start scan|Scan|Démarrer|Lancer)\b.*$")
                if start.count():
                    start.first.click()
                entries.first.wait_for(timeout=20000)
            previous = -1
            for _ in range(40):                     # les lignes s'affichent une par une
                count = entries.count()
                if count == previous and not win.locator(".scanning-animation").count():
                    break
                previous = count
                self.pause(250)
            lines = [self._log_text(t) for t in entries.all_inner_texts()]
            expected = 2
            m = re.search(r"Select (\d+) log entr", win.inner_text())
            if m:
                expected = int(m.group(1))
            invalid = {i: analyse_log_line(line) for i, line in enumerate(lines)}
            invalid = {i: e for i, e in invalid.items() if e}
            self.log.info("Logs : %d lignes, %d invalides attendues -> %s", len(lines), expected,
                          {lines[i]: sorted(e) for i, e in invalid.items()})
            if len(invalid) != expected:
                self.snapshot("log_integrity_comptage")
                raise StepFailed(f"{len(invalid)} ligne(s) invalide(s) trouvée(s) au lieu de {expected}")
            for i in invalid:
                entries.nth(i).click()
                self.pause(150)
            self.button(win, r"^\s*Confirm Selection\s*$").click()

            blocks = win.locator(".error-analysis-block")
            blocks.first.wait_for(timeout=10000)
            for b in range(blocks.count()):
                block = blocks.nth(b)
                line = self._log_text(block.locator(".log-line-display").inner_text())
                errors = analyse_log_line(line)
                block.locator(".fix-error-button").click()
                for err in errors:
                    block.locator(".error-type-button").filter(has_text=ERROR_LABELS[err]).first.click()
                    self.pause(120)
                self.log.info("Correction : %s -> %s", line, sorted(errors))
            if not self.allowed("System Log Integrity : confirmer les corrections et relancer le scan"):
                return "simulation : corrections préparées"
            mark = self.events.mark()
            self.button(win, r"^\s*Confirm Fixes\s*$").click()
            self.pause(800)
            self.button(win, r"Re-?scan").click()
            win.locator(".result-screen").wait_for(timeout=15000)
            if win.locator(".result-screen.success").count():
                self._wait_claim(mark)
                return "System Log Integrity réussi"
            self.log.warning("Scan refusé (essai %d) : nouvelle tentative", attempt)
            self.snapshot("log_integrity_echec")
            retry = win.locator(".retry-button")
            if retry.count():
                retry.click()
        raise StepFailed("System Log Integrity : 3 essais sans succès")

    @staticmethod
    def _log_text(text: str) -> str:
        m = re.search(r"\[[^\]]*\].*", text.replace("\n", " "))
        return (m.group(0) if m else text).strip()

    # =========================================================================
    # 2. Carte du réseau, marchés et jobs
    # =========================================================================
    def open_network_map(self) -> Locator:
        nm = self.window(comp("NetworkMapApplication"))
        if not nm.count():
            mark = self.events.mark()
            self.dock("network_map")
            nm.wait_for(timeout=15000)
            try:
                self.wait_event("network-map", "get.map", mark, timeout=15)
            except StepFailed:
                self.log.warning("Carte du réseau : état des serveurs non reçu")
        nm.wait_for(timeout=15000)
        return nm

    def select_server(self, name: str) -> Locator:
        nm = self.open_network_map()
        node = self.page.locator(f'[data-onboarding-300-id="ServerItem-{name}"]')
        if not node.count():
            raise Unavailable(f"{name} absent de la carte (pas encore découvert ?)",
                              utc_now() + dt.timedelta(hours=12))
        self.safe_click(node.first)
        self.pause(500)
        return nm

    def check_server(self, name: str) -> dict:
        """État du serveur d'après la carte (get.map + deltas). Maintenance -> Unavailable."""
        srv = self.events.server(name) or {}
        if srv.get("isInMaintenance"):
            ends = parse_time(srv.get("maintenanceEndsAt"))
            raise Unavailable(f"{name} en maintenance jusqu'à {local_time(ends)}",
                              ends + dt.timedelta(minutes=2) if ends else None)
        return srv

    def _route_cut(self, name: str) -> Unavailable:
        return Unavailable(f"{name} injoignable : « Connecter » impossible (nœud de la route en maintenance "
                           "ou serveur fermé)")

    def ensure_reachable(self, name: str) -> Locator:
        """Sélectionne un serveur et s'y connecte (« Connecter » déplace le point de sortie :
        network-map:set.endpoint). Maintenance ou route coupée -> Unavailable : on réessaiera."""
        nm = self.select_server(name)
        self.check_server(name)
        connect = self.button(nm, "connect")
        if connect.count():
            if not connect.first.is_enabled():
                srv = self.events.server(name) or {}
                if srv.get("canSetEndpoint") is False or srv.get("isReachable") is False:
                    raise self._route_cut(name)
                raise StepFailed(f"bouton Connecter inactif sur {name}")
            if self._original_endpoint is None:
                self._original_endpoint = self.events.endpoint() or ""
            mark = self.events.mark()
            self.safe_click(connect.first)
            try:
                self.wait_event("network-map", "set.endpoint", mark, timeout=15)
            except StepFailed:
                self.check_server(name)
                if (self.events.server(name) or {}).get("isReachable") is False:
                    raise self._route_cut(name)
                raise StepFailed(f"connexion à {name} sans réponse du serveur")
            self.pause(500)
        srv = self.check_server(name)
        if srv.get("isReachable") is False:
            raise self._route_cut(name)
        return nm

    def restore_endpoint(self) -> None:
        """Remet le point de sortie que le joueur avait avant le passage du bot."""
        name, self._original_endpoint = self._original_endpoint, None
        if not name or self.events.endpoint() == name:
            return
        try:
            self.close_window(comp("MarketSite"))
            self._market_name = None
            nm = self.select_server(name)
            connect = self.button(nm, "connect")
            if connect.count() and connect.first.is_enabled():
                mark = self.events.mark()
                self.safe_click(connect.first)
                self.wait_event("network-map", "set.endpoint", mark, timeout=15)
                self.log.info("Point de sortie remis sur %s", name)
        except Exception as exc:
            self.log.warning("Impossible de remettre le point de sortie %s : %s", name, exc)

    def open_market(self, name: str = HOME_SERVER) -> Locator:
        """Ouvre le site Marché d'un serveur. Home Server : bouton « Marché » du panneau.
        Marchés distants (D4RK RM7MI, SRM7-M, URM7-M) : il faut d'abord s'y connecter, puis
        cliquer l'icône MarketIconButton."""
        market = self.window(comp("MarketSite"))
        if market.count() and self._market_name == name and (
                name == HOME_SERVER or self.events.endpoint() == name):
            return market
        self.close_window(comp("MarketSite"))
        self._market_name = None
        nm = self.select_server(name)
        self.check_server(name)
        text_btn = self.button(nm, "market")
        if not text_btn.count():
            nm = self.ensure_reachable(name)
            text_btn = self.button(nm, "market")
        icon = f'{cname("MarketIconButton")} button'
        candidates = [self.page.locator(f'[data-onboarding-300-id="ServerItem-{name}"] {icon}'),
                      nm.locator(icon), self.page.locator(icon)]
        icon_btn = next((c for c in candidates if c.count()), candidates[-1])
        mark = self.events.mark()
        if text_btn.count():
            self.safe_click(text_btn.first)
        elif icon_btn.count():
            self.safe_click(icon_btn.first)
        else:
            raise StepFailed(f"pas de bouton Marché sur {name}")
        market.wait_for(timeout=15000)
        try:
            opts = self.wait_event("market", "get.options", mark, timeout=15).data
            if isinstance(opts, dict) and opts.get("userCredits") is not None:
                self.log.info("Solde : %s CR", opts["userCredits"])           # lu par --bilan
            expected = (self.events.server(name) or {}).get("marketId")
            if isinstance(opts, dict) and expected and opts.get("marketId") not in (None, expected):
                raise StepFailed(f"le marché ouvert n'est pas celui de {name}")
        except StepFailed as exc:
            if "n'est pas celui" in str(exc):
                raise
            self.log.warning("Marché %s : options non reçues", name)
        self._market_name = name
        return market

    def market_tab(self, market: Locator, key: str) -> None:
        market.locator(comp("MarketNav")).get_by_role("button", name=rx(TXT[key])).first.click()
        self.pause(800)

    def fetch_jobs(self, market: Locator, since: int | None = None) -> dict:
        """Liste des contrats (market:get.jobs). since : le jeu l'a déjà redemandée de lui-même (après une
        signature). Recliquer l'onglet Contrats déjà ouvert ne redemande rien (v1.25.71) : on passe alors
        par l'onglet Marché et on revient."""
        if since is not None:
            found = self.events.last("market", "get.jobs", since=since)
            if found:
                return found.data or {}
        mark = self.events.mark()
        self.market_tab(market, "contracts")
        try:
            return self.wait_event("market", "get.jobs", mark, timeout=4).data or {}
        except StepFailed:
            self.log.debug("Onglet Contrats déjà ouvert : passage par l'onglet Marché pour recharger la liste")
        mark = self.events.mark()
        self.market_tab(market, "market")
        self.market_tab(market, "contracts")
        return self.wait_event("market", "get.jobs", mark, timeout=15).data or {}

    def load_jobs(self, market: Locator, name: str | None = None, since: int | None = None) -> dict:
        """Onglet Contrats. L'ouvrir après l'échéance du minuteur relance la liste et le minuteur.
        since : voir fetch_jobs."""
        name = name or self._market_name or HOME_SERVER
        data: dict = {}
        for attempt in (1, 2):
            data = self.fetch_jobs(market, since if attempt == 1 else None)
            reset = parse_time(data.get("nextJobsResetAt"))
            if reset is None or reset > utc_now() or attempt == 2:
                break
            self.log.info("Marché %s : minuteur échu (%s), je recharge les contrats", name, local_time(reset))
            self.pause(1500)
        for job in (data.get("jobs") or []) + (data.get("recentJobs") or []):
            job["_market"] = name
        if self.state is not None:
            self.state.set_market_reset(name, data.get("nextJobsResetAt"))
            self.state.prune_pending(name, {j.get("id") for j in data.get("recentJobs") or []
                                            if j.get("status") == "TAKEN"})
        taken = sum(1 for j in data.get("recentJobs") or [] if j.get("status") == "TAKEN")
        self.log.info("Marché %s : %d contrat(s) proposé(s), %d signé(s) en cours, renouvellement %s",
                      name, len(data.get("jobs") or []), taken, local_time(data.get("nextJobsResetAt")))
        if data.get("jobs") or data.get("recentJobs"):
            try:
                market.locator(comp("JobCard")).first.wait_for(timeout=10000)
            except PWTimeout:                   # 22/10 : liste reçue mais cartes pas affichées (autre onglet)
                self.log.debug("Cartes de contrats absentes : je recharge l'onglet Contrats")
                data = self.fetch_jobs(market) or data
                market.locator(comp("JobCard")).first.wait_for(timeout=10000)
        return data

    def visit_market(self, name: str, notes: list[str], failed: list[str]) -> dict | None:
        """Ouvre un marché et ses contrats ; range l'indisponibilité dans notes, le reste dans failed."""
        try:
            return self.load_jobs(self.open_market(name), name)
        except Unavailable as exc:
            notes.append(f"marché {name} : {exc}")
            self.log.warning("Marché %s indisponible : %s", name, exc)
            if self.state is not None:
                self.state.set_market_retry(name, exc.retry_at, str(exc))
        except (StepFailed, PWTimeout) as exc:
            failed.append(f"marché {name} : {exc}")
            self.log.warning("Marché %s : %s", name, exc)
            self.snapshot(f"marche_{name}")
            self.close_window(comp("MarketSite"))
            self._market_name = None
        return None

    def run_markets(self, only_due: bool | None = None) -> str:
        """Relance le minuteur des marchés échus (ouvre leur onglet Contrats), sans signer de job."""
        only_due = True if only_due is None else only_due
        names = [n for n in market_names(self.cfg)
                 if not only_due or self.state is None or self.state.market_due(n)]
        if not names:
            return "aucun minuteur de marché échu"
        self.ensure_connected()
        self.close_all_windows()
        refreshed: list[str] = []
        notes: list[str] = []
        failed: list[str] = []
        try:
            for name in names:
                data = self.visit_market(name, notes, failed)
                if data is not None:
                    refreshed.append(f"{name} ({len(data.get('jobs') or [])} contrats, "
                                     f"prochain renouvellement {local_time(data.get('nextJobsResetAt'))})")
        finally:
            self.cleanup()
        summary = ("marchés relancés : " + " | ".join(refreshed)) if refreshed else "aucun marché relancé"
        if notes:
            summary += " ; " + " | ".join(notes)
        if failed:
            raise StepFailed(summary + " ; échecs : " + " | ".join(failed))
        return summary

    def cleanup(self) -> None:
        """Remet le loadout et le point de sortie du joueur, ferme les fenêtres (sans jamais planter)."""
        for step in (self.restore_preset, self.restore_endpoint, self.close_all_windows):
            try:
                step()
            except (SessionLost, LoginRequired):
                raise
            except Exception as exc:
                self.log.warning("Remise en ordre (%s) impossible : %s", step.__name__, exc)

    @staticmethod
    def job_details(job: dict) -> dict:
        """Fusionne les « details » des conditions (IP, fichiers, logs ciblés)."""
        merged: dict = {}
        for item in (job.get("conditions") or {}).get("items") or []:
            if isinstance(item.get("details"), dict):
                merged.update(item["details"])
        return merged

    @staticmethod
    def job_server(job: dict) -> str | None:
        servers = job.get("relatedServers") or []
        return servers[0].get("serverName") if servers else None

    def find_job_card(self, market: Locator, job: dict) -> Locator:
        """Retrouve la carte d'un job (les cartes n'exposent pas l'id) : nom + serveur + gains.
        Les jobs signés n'affichent que « Temps restant / EN COURS » : on déplie pour voir le serveur."""
        server = self.job_server(job) or "N/A"
        cr = f"{job.get('rewardCredits', 0):,}"
        taken = job.get("status") == "TAKEN"
        corp = job.get("corporation")
        cards = market.locator(comp("JobCard"))
        for i in range(cards.count()):
            card = cards.nth(i)
            text = card.inner_text()
            if job["name"] not in text:
                continue
            is_running = bool(re.search(f"{TXT['in_progress']}|{TXT['ready']}", text, re.I))
            if taken != is_running:
                continue
            if not taken and cr not in text:
                continue
            if server not in text or (corp and corp not in text):
                more = self.button(card, "read_more")
                if more.count():
                    more.first.click()
                    self.pause(400)
                    text = card.inner_text()
            if server in text and (not corp or corp in text):
                return card
        raise StepFailed(f"carte introuvable pour le job {job['name']} ({server})")

    def sign_job(self, market: Locator, job: dict) -> int | None:
        """Signe le job ; renvoie le repère des trames d'avant la signature (None en --dry-run) : le jeu
        recharge ensuite la liste de lui-même, load_jobs(since=...) la reprend."""
        self.close_window(comp("ServerAdministrationInterfaceApplication"))   # règle du guide
        if not market.locator(comp("JobCard")).count():   # marché rouvert (après un job) sur l'onglet Marché
            self.load_jobs(market)
        card = self.find_job_card(market, job)
        more = self.button(card, "read_more")
        if more.count():
            more.click()
            self.pause(400)
        self.button(card, "sign").first.click()
        side = self.page.locator(cname("JobSideout"))
        side.wait_for(timeout=8000)
        text = side.inner_text()
        if job.get("corporation") and job["corporation"] not in text:
            self.button(side, r"^\s*(Annuler|Cancel)\s*$").click()
            raise StepFailed("le panneau de signature ne correspond pas au job attendu")
        if not self.allowed(f"signer le job {job['name']} ({self.job_server(job) or '-'}, "
                            f"{job.get('rewardCredits')} CR, dépôt {job.get('deposit', 0)} CR)"):
            self.button(side, r"^\s*(Annuler|Cancel)\s*$").click()
            return None
        mark = self.events.mark()
        self.button(side, "sign").click()
        ev = self.wait_event("market", "job.take", mark, timeout=15)
        if (ev.data or {}).get("status") != "ok":
            raise StepFailed(f"signature refusée : {ev.data}")
        return mark

    def complete_job(self, market_name: str, job: dict) -> None:
        market = self.open_market(market_name)
        current = job
        for attempt in (1, 2):
            jobs = self.load_jobs(market, market_name)
            current = next((j for j in jobs.get("recentJobs", []) if j.get("id") == job.get("id")), job)
            if current.get("canComplete") is not False or self.dry_run or attempt == 2:
                break
            self.pause(2500)                       # le serveur valide parfois avec un temps de retard
        if current.get("status") == "TAKEN" and current.get("canComplete") is False and not self.dry_run:
            raise StepFailed(f"le job {current['name']} n'est pas encore validé par le serveur")
        card = self.find_job_card(market, current)
        more = self.button(card, "read_more")
        if more.count():
            more.click()
            self.pause(400)
        card.locator(comp("Button")).last.click()                           # bouton « Infos »
        side = self.page.locator(cname("JobSideout"))
        side.wait_for(timeout=8000)
        if not self.allowed(f"terminer le job {current['name']} ({market_name})"):
            return
        mark = self.events.mark()
        self.button(side, "complete").click()
        ev = self.wait_event("market", "job.complete", mark, timeout=15)
        if (ev.data or {}).get("status") != "ok":
            raise StepFailed(f"fin de job refusée : {ev.data}")
        self.log.info("Job terminé : %s sur %s (+%s CR, +%s REP)", current["name"], market_name,
                      current.get("rewardCredits"), current.get("rewardReputation"))
        if self.state is not None:
            self.state.clear_job_pending(current.get("id"))

    def run_jobs(self, only_due: bool | None = None) -> str:
        """Pour chaque marché (config [markets] names) : ouvre l'onglet Contrats (ce qui relance son
        minuteur), termine les jobs déjà signés, puis signe et fait de nouveaux jobs selon la politique.
        En mode --auto, seuls les marchés dont le minuteur est échu sont visités."""
        only_due = self.auto_mode if only_due is None else only_due
        jcfg = self.cfg["jobs"]
        names = [n for n in market_names(self.cfg)
                 if not only_due or self.state is None or self.state.market_due(n)]
        if not names:
            return "aucun minuteur de marché échu"
        take_on = set(jcfg.get("markets") or market_names(self.cfg))
        budget = int(jcfg.get("max_jobs_per_run", 3))
        self.ensure_connected()
        self.close_all_windows()
        done: list[str] = []
        blown: set[str] = set()     # fusible : types qui ont échoué après signature pendant ce passage
        ice: IceWallLocked | None = None    # ICE WALL bloqué : plus de déchiffrement ni de piratage
        failed: list[str] = []      # échecs : déclenchent une alerte
        notes: list[str] = []       # laissé de côté volontairement ou indisponible pour l'instant
        seen: list[str] = []
        handled: set = set()
        try:
            for name in names:
                data = self.visit_market(name, notes, failed)
                if data is None:
                    continue
                seen.append(name)
                # a) jobs déjà signés : terminer ceux qui sont prêts, faire les autres
                for job in data.get("recentJobs") or []:
                    if job.get("status") != "TAKEN" or job.get("id") in handled:
                        continue
                    handled.add(job.get("id"))
                    if self.state is not None and self.state.job_lost(job.get("id")):
                        self.log.info("Job signé %s (%s) déjà classé perdu : je n'y touche plus", job["name"], name)
                        continue
                    refusal = self.job_type_refusal(job)
                    blocked = None if refusal else self.server_problem(job)
                    if (refusal or blocked) and not job.get("canComplete"):
                        reason = refusal or blocked
                        notes.append(f"{job['name']} ({name}, signé) laissé de côté : {reason}{self.deadline_text(job)}")
                        self.log.warning("Job signé laissé de côté : %s (%s) : %s", job["name"], name, reason)
                        if blocked:                                 # empêchement passager : on y reviendra
                            self._job_pending(job, name, blocked, self.server_retry_at(job))
                        continue
                    try:
                        if not job.get("canComplete"):
                            self.perform_job(job)
                        self.complete_job(name, job)
                        done.append(f"{job['name']} ({name})")
                    except (StepFailed, PWTimeout) as exc:      # maintenance : on réessaiera plus tard
                        self._job_error(exc, f"{job['name']} ({name}, signé)", job, notes, failed, soft=True,
                                        market=name)
                        if isinstance(exc, IceWallLocked):
                            ice = exc
                # b) nouveaux jobs selon la politique
                if name not in take_on:
                    continue
                left: list[dict] = []       # faisables mais remis à plus tard : on repassera sur ce marché
                for job in sorted(data.get("jobs") or [],
                                  key=lambda j: (self.job_risk(j), -int(j.get("rewardCredits", 0) or 0))):
                    if job.get("id") in handled:
                        continue
                    reason = self.job_refusal_reason(job)
                    if reason:
                        self.log.debug("Job ignoré %s (%s, %s) : %s", job["name"], name, self.job_server(job), reason)
                        continue
                    hold = ("nombre de jobs du passage atteint (max_jobs_per_run)" if budget <= 0
                            else f"un job {job.get('jobType')} a échoué pendant ce passage" if job.get("jobType") in blown
                            else f"{ice}" if ice and self.job_risk(job) > 0 else None)
                    if hold:
                        self.log.info("Job remis à plus tard : %s (%s, %s) : %s", job["name"], name,
                                      self.job_server(job) or "-", hold)
                        left.append(job)
                        continue
                    handled.add(job.get("id"))
                    signed, current = False, job
                    try:
                        since = self.sign_job(self.open_market(name), job)
                        budget -= 1                 # compté dès la signature, même si la suite échoue
                        if self.dry_run:
                            continue
                        signed = True
                        recent = self.load_jobs(self.open_market(name), name, since=since).get("recentJobs") or []
                        current = next((j for j in recent if j.get("id") == job.get("id")), job)   # cibles, échéance
                        self.perform_job(current)
                        self.complete_job(name, current)
                        done.append(f"{job['name']} ({name})")
                    except (StepFailed, PWTimeout) as exc:
                        self._job_error(exc, f"{job['name']} ({name})", current, notes, failed, soft=False,
                                        market=name if signed else None)
                        if isinstance(exc, IceWallLocked):
                            ice = exc
                        elif signed and not isinstance(exc, (Unavailable, Hopeless)):
                            blown.add(job.get("jobType"))
                if left and self.state is not None and not self.dry_run:
                    when = utc_now() + JOB_RETRY
                    if ice and ice.retry_at and all(self.job_risk(j) > 0 for j in left):
                        when = max(when, ice.retry_at)
                    self.state.set_market_revisit(name, when)
                    notes.append(f"{len(left)} job(s) de {name} remis au passage de {local_time(when)} ou après")
        finally:
            self.cleanup()
        summary = f"{len(done)} job(s) terminé(s)"
        if done:
            summary += " : " + ", ".join(done)
        summary += f" ; marchés vus : {', '.join(seen) or 'aucun'}"
        if notes:
            summary += " ; " + " | ".join(notes)
        if failed:
            summary += f" ; {len(failed)} en échec : " + " | ".join(failed)
            raise StepFailed(summary)
        return summary

    def _job_error(self, exc: Exception, label: str, job: dict, notes: list[str], failed: list[str],
                   soft: bool, market: str | None = None) -> None:
        """Simulation (et indisponibilité d'un job déjà signé) -> note ; le reste -> échec + alerte.
        Job signé (market donné) : noté pour être repris à la fin de la maintenance, sinon au passage suivant."""
        if market and isinstance(exc, Hopeless):
            if self.state is not None:
                self.state.mark_job_lost(job.get("id"))
            label += self.deadline_text(job)
        elif market and not isinstance(exc, Simulated):
            problem = (str(exc).splitlines() or [type(exc).__name__])[0][:200]
            self._job_pending(job, market, problem, getattr(exc, "retry_at", None))
            label += self.deadline_text(job)
        if isinstance(exc, Simulated) or (soft and isinstance(exc, Unavailable)):
            notes.append(f"{label} : {exc}")
            self.log.warning("Job %s : %s", label, exc)
            return
        failed.append(f"{label} : {exc}")
        self.log.warning("Job %s en échec : %s", label, exc)
        self.snapshot(f"job_{job.get('jobType')}")

    def needs_hack(self, job: dict) -> bool:
        """Serveur sans accès (ni « Access Grant », ni accès déjà piraté) : il faudra le pirater."""
        server = self.job_server(job)
        if not server or self.job_access(job) == "grant":
            return False
        return (self.events.server(server) or {}).get("accessType") not in ("regular", "admin")

    def job_risk(self, job: dict) -> int:
        """Ordre de passage sur un marché : 0 = sans mini-jeu sur un serveur accessible, 1 = déchiffrement,
        2 = piratage à faire. Un ICE WALL raté bloque le mini-jeu 20 min : les jobs sûrs passent avant."""
        if self.needs_hack(job):
            return 2
        return 1 if job.get("jobType") in ICE_JOB_TYPES else 0

    def job_type_refusal(self, job: dict) -> str | None:
        """Le type de job est-il autorisé par la config ? (vaut aussi pour les jobs déjà signés)"""
        jcfg = self.cfg["jobs"]
        jtype = job.get("jobType", "")
        allowed = set(jcfg.get("types", ["DecryptFile", "DownloadLog", "DownloadFile"]))
        if jtype in {"DeleteIps", "DeleteLog", "DeleteFile"}:
            if not jcfg.get("allow_deletions", False):
                return "suppression non autorisée (allow_deletions = false)"
            allowed.add(jtype)
        if jtype == "AddIps":
            if not jcfg.get("allow_ip_add", False):
                return "ajout d'IP non autorisé (allow_ip_add = false)"
            allowed.add(jtype)
        if jtype not in allowed:
            return f"type {jtype} non géré ou non autorisé"
        return None

    def job_refusal_reason(self, job: dict) -> str | None:
        jcfg = self.cfg["jobs"]
        if reason := self.job_type_refusal(job):
            return reason
        if int(job.get("deposit", 0) or 0) > int(jcfg.get("max_deposit_cr", 0)):
            return f"dépôt {job.get('deposit')} CR > max_deposit_cr"
        if float(job.get("reputationPenalty", 0) or 0) > float(jcfg.get("max_rep_penalty", 100)):
            return "pénalité de réputation trop forte"
        server = self.job_server(job)
        if server and server in jcfg.get("skip_servers", []):
            return "serveur exclu"
        if problem := self.server_problem(job):
            return problem
        if problem := self.access_problem(job):
            return problem
        return None

    def access_problem(self, job: dict) -> str | None:
        """Pourra-t-on se connecter au serveur du job ? (contrat et carte, relevés en v1.25.71)
        « Access Grant » : l'accès vient avec le job. « Access Acquisition » : il faut un accès déjà acquis
        (accessType regular / admin, réutilisé par l'outil du loadout), sinon pirater : refusé par défaut."""
        server = self.job_server(job)
        access = self.job_access(job)
        if not server or access == "grant":
            return None
        jcfg = self.cfg["jobs"]
        if not jcfg.get("allow_hack", True):
            return "il faut pirater le serveur ou réutiliser un accès piraté (allow_hack = false)"
        srv = self.events.server(server) or {}
        if srv.get("accessType") in ("regular", "admin"):
            return None
        if jcfg.get("sign_without_access", False):
            best = float((self.state.data.get("hack_power") if self.state is not None else None) or 0)
            defence = srv.get("serverDefenceRate")
            if best and defence is not None and float(defence) > best:
                return f"défense de {server} ({defence}) plus forte que ton meilleur outil de piratage ({best:g})"
            return None
        return (f"il faudrait pirater {server} (aucun accès, défense {srv.get('serverDefenceRate', '?')}) : "
                "refusé tant que sign_without_access = false")

    def server_problem(self, job: dict) -> str | None:
        """Serveur du job impossible à atteindre d'après la carte : carte pas reçue, serveur absent, en
        maintenance ou route coupée. On ne signe pas ; un job déjà signé attend (state.json).
        Ne dit pas si on pourra s'y connecter (accès par tâche, piratage) : ça se voit au terminal SAI."""
        server = self.job_server(job)
        if not server:
            return None                                   # déchiffrement : rien à atteindre
        if not self.events.servers:
            return "carte du réseau pas encore reçue"
        srv = self.events.server(server)
        if srv is None:
            return f"{server} absent de la carte (pas encore découvert ?)"
        if srv.get("isInMaintenance"):
            return f"{server} en maintenance jusqu'à {local_time(srv.get('maintenanceEndsAt'))}"
        # v1.25.71 : isReachable dépend du point de sortie du moment (que le bot déplace) ; canSetEndpoint
        # dit si « Connecter » est possible, et c'est ce que fait le bot avant d'ouvrir le terminal.
        if srv.get("canSetEndpoint") is False:
            return str(self._route_cut(server))
        return None

    def server_retry_at(self, job: dict) -> dt.datetime | None:
        """Fin de maintenance du serveur du job (+ 2 min) : l'heure à laquelle y revenir."""
        srv = self.events.server(self.job_server(job) or "") or {}
        ends = parse_time(srv.get("maintenanceEndsAt")) if srv.get("isInMaintenance") else None
        return ends + dt.timedelta(minutes=2) if ends else None

    def _job_pending(self, job: dict, market: str, problem: str, retry_at: dt.datetime | None = None) -> None:
        """Job signé pas terminé : noté dans state.json pour que --auto revienne sur son marché."""
        if self.state is not None and job.get("id"):
            self.state.set_job_pending(job["id"], market, job.get("name") or "?", problem, retry_at,
                                       deadline=parse_time(job.get("endTime")))

    @staticmethod
    def deadline_text(job: dict) -> str:
        """Échéance d'un job signé (endTime, v1.25.71), pour les alertes : passé ce délai, c'est la pénalité."""
        return f" [à finir avant {local_time(job['endTime'])}]" if parse_time(job.get("endTime")) else ""

    @staticmethod
    def job_access(job: dict) -> str | None:
        """Condition d'accès du job : « grant » = accès donné tant que le job est actif (GrantServerAccess),
        « acquisition » = il faut avoir piraté le serveur (GetServerAccess), relevés en v1.25.71.
        Sinon, lecture tolérante des conditions."""
        types = {str(i.get("type")) for i in (job.get("conditions") or {}).get("items") or [] if isinstance(i, dict)}
        if "GetServerAccess" in types:
            return "acquisition"
        if "GrantServerAccess" in types:
            return "grant"
        blob = json.dumps(job.get("conditions") or {}, default=str).lower()
        if "acquisition" in blob or "acquire" in blob:
            return "acquisition"
        if "grant" in blob:
            return "grant"
        return None

    def perform_job(self, job: dict) -> None:
        jtype = job.get("jobType")
        details = self.job_details(job)
        self.log.info("Exécution du job %s (%s) : %s", job["name"], jtype, json.dumps(details, default=str)[:300])
        if jtype == "DecryptFile":
            self.do_decrypt_job(details)
        elif jtype in {"DownloadLog", "DeleteLog"}:
            self.do_sai_list_job(job, details, tab="tab_logs", room_action="get.logs", list_key="logs",
                                 delete=(jtype == "DeleteLog"))
        elif jtype in {"DownloadFile", "DeleteFile"}:
            self.do_sai_list_job(job, details, tab="tab_files", room_action="get.files", list_key="files",
                                 delete=(jtype == "DeleteFile"))
        elif jtype == "DecryptDownloadedFile":
            # « Decrypt & Extract » (d'après le joueur) : télécharger le fichier sur le nœud, puis le
            # déchiffrer dans Downloads
            self.do_sai_list_job(job, details, tab="tab_files", room_action="get.files", list_key="files",
                                 delete=False)
            self.do_decrypt_job(details)
        elif jtype == "UploadFile":
            self.do_upload_job(job, details)
        elif jtype in {"DeleteIps", "AddIps"}:
            self.do_transit_job(job, details, add=(jtype == "AddIps"))
        else:
            raise StepFailed(f"type de job non géré : {jtype}")

    # ----- Loadout / préréglages ------------------------------------------------
    def apply_preset(self, name: str) -> None:
        if not name:
            return
        if self._current_preset == name:
            return
        if not self.allowed(f"appliquer le préréglage Loadout « {name} »"):
            return
        self.dock("loadout")
        header = self.page.locator(comp("LoadoutHeader"))
        header.wait_for(timeout=15000)
        power = self.page.locator('[data-sentry-element="LoadoutHeaderPowerButtonStyled"]')

        def powered() -> bool:
            return self.page.evaluate("() => localStorage.getItem('loadout-powered')") == "true"

        if powered():
            power.click()
            self.pause(1200)
        self.button(self.page, "presets").click()
        modal = self.page.locator(cname("PresetsModal"))
        try:
            modal.wait_for(timeout=8000)
            rows = modal.locator(comp("PresetRow"))
            rows.first.wait_for(timeout=8000)           # les lignes arrivent après la fenêtre
            self.pause(300)
            row = None
            for i in range(rows.count()):
                # v1.25.71 : le nom a son propre élément (le texte de la ligne mélange nom et composants)
                label = rows.nth(i).locator('[data-sentry-element="PresetRowNameStyled"]')
                text = label.first.inner_text() if label.count() else rows.nth(i).inner_text().split("\n")[0]
                if text.strip() == name:
                    row = rows.nth(i)
            if row is None:
                raise StepFailed(f"préréglage « {name} » introuvable parmi {rows.count()} préréglage(s)")
            mark = self.events.mark()
            apply_btn = self.button(row, "apply")
            if apply_btn.count():                    # sinon il est déjà ACTIF
                apply_btn.click()
                self.wait_event("loadout", "apply.preset", mark, timeout=15)
        finally:
            # quoi qu'il arrive : fermer la fenêtre, rallumer le Loadout, quitter l'écran
            # (fermer avec la croix : le 1er bouton de la fenêtre est « Enregistrer la configuration »)
            try:
                close = self.page.locator('[data-sentry-element="LoadoutModalCloseButtonStyled"]')
                if close.count():
                    close.first.click()
                    self.pause(400)
                if not powered():
                    power.click()
                    self.pause(1500)
                self.button(self.page, "back").click()
                self.pause(1200)
            except PWError as exc:
                self.log.warning("Loadout : remise en ordre incomplète (%s) : vérifie qu'il est allumé", exc)
        self._current_preset = name
        self.log.info("Préréglage « %s » actif", name)

    def restore_preset(self) -> None:
        name = self.cfg["jobs"].get("restore_preset")
        if name and self._current_preset not in (None, name):
            self.apply_preset(name)

    # ----- Déchiffrement (ICE WALL Break) ------------------------------------
    def do_decrypt_job(self, details: dict) -> None:
        files = details.get("files") or []
        if not files:
            raise StepFailed("job de déchiffrement sans fichier")
        target = files[0]
        announced = [e.get("ext") for e in details.get("extensions") or [] if isinstance(e, dict) and e.get("ext")]
        ext = (announced[0] if announced else target.get("name", "").rsplit(".", 1)[-1] or "").lstrip(".").lower()
        presets = self.cfg["jobs"].get("decrypt_presets", {})
        self.apply_preset(presets.get(ext, presets.get("default", "decrypt")))

        self.close_all_windows()
        shortcut = self.page.locator(comp("DesktopShortcutComponent")).filter(has_text=rx(r"^\s*Downloads\s*$")).first
        try:
            shortcut.dblclick(timeout=5000)
        except PWError:                         # une fenêtre devant : double-clic DOM
            self.log.debug("Double-clic bloqué sur Downloads : double-clic DOM à la place")
            shortcut.dispatch_event("dblclick")
        folder = self.window(comp("FolderApplication"))
        folder.wait_for(timeout=10000)
        icon = folder.locator(f'{comp("FolderShortcut")}[data-app-id="{target["id"]}"]')
        if not icon.count():
            icon = folder.locator(comp("FolderShortcut")).filter(has_text=target.get("name", "")[:12])
        if not icon.count():        # Decrypt & Extract : le fichier téléchargé n'a pas l'extension annoncée
            icon = folder.locator(comp("FolderShortcut")).filter(has_text=stem(target.get("name", "")))
        try:
            icon.first.dblclick(timeout=5000)
        except PWError:
            icon.first.dispatch_event("dblclick")
        analysis = self.window(comp("FileAnalysisProtocolApplication"))
        analysis.wait_for(timeout=10000)
        decrypt = self.button(analysis, "decrypt")
        try:
            decrypt.wait_for(timeout=8000)
        except PWTimeout:
            if self.dry_run:
                self.log.info("[SIMULATION] pas de bouton Déchiffrer (le préréglage n'a pas été appliqué)")
                return
            raise Hopeless("pas de bouton Déchiffrer avec ce préréglage : module trop faible pour ce fichier "
                           f"(niveau {details.get('encryptionLevel')}), job perdu")
        text = analysis.inner_text()
        level = re.search(r"(?:NIVEAU DE CHIFFREMENT|ENCRYPTION LEVEL)\s*([\d.]+)", text, re.I)
        power = re.search(r"(?:PUISSANCE DE D[ÉE]CHIFFREMENT|DECRYPTION POWER)\s*([\d.]+)", text, re.I)
        if level and power:
            self.log.info("%s : niveau de chiffrement %s, puissance de déchiffrement %s (préréglage « %s »)",
                          target.get("name"), level.group(1), power.group(1), self._current_preset)
        if decrypt.first.is_disabled() or (level and power and float(level.group(1)) > float(power.group(1))):
            # 22/10 : niveau 25 contre puissance 21, bouton Déchiffrer grisé
            raise Hopeless(f"{target.get('name')} : niveau de chiffrement {level.group(1) if level else '?'} > "
                           f"puissance de déchiffrement {power.group(1) if power else '?'} : job perdu")
        if not self.allowed(f"lancer le déchiffrement de {target.get('name')}"):
            return
        mark = self.events.mark()
        decrypt.click()
        ev = self.wait_event("minigames", "start.minigame", mark, timeout=15)
        info = ev.data or {}
        if not info.get("url") and info.get("lockExpiresAt"):
            raise IceWallLocked(f"mini-jeu verrouillé jusqu'à {local_time(info['lockExpiresAt'])} (échec précédent)",
                                parse_time(info["lockExpiresAt"]))
        self.solve_ice_wall()
        deadline = time.time() + 20
        while time.time() < deadline:
            lock = self.events.find(mark, "minigames", "start.minigame",
                                    pred=lambda e: bool((e.data or {}).get("lockExpiresAt")))
            if lock:
                until = lock[-1].data["lockExpiresAt"]
                raise IceWallLocked(f"ICE WALL perdu, mini-jeu verrouillé jusqu'à {local_time(until)}", parse_time(until))
            # Decrypt & Extract : l'id du job (« j_… ») n'est pas celui du fichier téléchargé
            real_id = not str(target.get("id", "")).startswith("j_")
            updated = self.events.find(mark, "desktop", "update.file",
                                       pred=lambda e: not real_id or ((e.data or {}).get("file") or {}).get("id") == target["id"])
            if updated or self.events.find(mark, "market", "job.can-complete"):
                self.log.info("Fichier %s déchiffré", target.get("name"))
                break
            self.pause(500)
        else:
            raise StepFailed("pas de confirmation du déchiffrement")
        self.close_all_windows()

    def solve_ice_wall(self, timeout_s: float = 150) -> None:
        win = self.window(comp("IceWallBreakApplication"))
        win.wait_for(timeout=20000)
        tried: set[tuple[int, ...]] = set()
        deadline = time.time() + timeout_s
        last_done = -1
        memory: dict[int, str] = {}
        round_key = None
        while time.time() < deadline:
            self.check_session()
            state = self.page.evaluate(ICE_WALL_JS)
            if state is None:
                return                                   # fenêtre fermée : fin du mini-jeu
            if state.get("limit"):
                raise IceWallLocked("ICE WALL : limite quotidienne de parties atteinte",
                                    utc_now().replace(hour=0, minute=5, second=0) + dt.timedelta(days=1))
            done, total = state.get("done"), state.get("total")
            if done is not None and done != last_done:
                self.log.info("ICE WALL : %s/%s", done, total)
                last_done = done
            if total and done is not None and done >= total:
                self.pause(1500)
                return
            targets = [Glyph(t["sig"], t["x"], t["y"], t["flip"]) for t in state["targets"]]
            key = (done, tuple(t.sig for t in targets))
            if key != round_key:                         # nouvelle manche : nouveau mur
                round_key, memory, tried = key, {}, set()
            for c in state["cells"]:
                if c.get("npath", 3) > 2:
                    memory[c["index"]] = c["sig"]
            cells = [Cell(memory.get(c["index"], c["sig"]), c["x"], c["y"], c["flip"], index=c["index"],
                          cx=c["cx"], cy=c["cy"], hidden=c["index"] not in memory) for c in state["cells"]]
            if not targets:
                self.pause(300)
                continue
            group, why = solve_ice_wall_partial(targets, cells)
            key = tuple(sorted(c.index for c in group)) if group else None
            if group is None or key in tried:
                self.pause(250)                          # pas encore assez de glyphes visibles
                continue
            tried.add(key)
            cell = group[ice_anchor_index(targets)]
            # UN seul clic : le jeu pose tout le motif à partir de la cellule ancre.
            # Un mauvais placement coûte 20 s ; un échec verrouille le mini-jeu 20 min.
            self.log.info("ICE WALL : motif trouvé (%s, %d/%d cases visibles), clic sur l'ancre %d",
                          why, sum(not c.hidden for c in cells), len(cells), cell.index)
            self.page.mouse.move(cell.cx, cell.cy)
            self.pause(150)
            self.page.mouse.click(cell.cx, cell.cy)
            self.pause(1400)                             # réponse du serveur (match / pénalité)
        self.snapshot("ice_wall_timeout")
        raise StepFailed("ICE WALL non résolu dans le temps imparti")

    # ----- Jobs serveur (SAI) : connexion, accès par tâche, piratage ------------
    def open_sai(self, server: str) -> Locator:
        """Ouvre le Terminal SAI d'un serveur.
        1) accès « par tâche » (condition Grant du job) s'il est proposé ;
        2) sinon outil de piratage du loadout : le jeu réutilise l'accès déjà piraté, ou repirate
           (après une maintenance, l'accès piraté est perdu) : direct si l'outil est bien plus
           puissant que la défense, sinon mini-jeu ICE WALL. Le « force hack » payant : jamais."""
        self.close_window(SAI_APP)
        self.close_window(SAI_LOGIN)
        self.close_window(comp("MarketSite"))       # il cacherait la carte ; complete_job le rouvre
        self._market_name = None
        win = self.open_login(server)
        if win.locator(SAI_APP).count():
            return self.window(SAI_APP)
        if win.locator(cname("SaiAccessItem")).count():
            return self.login_with_access(server, win)
        return self.login_with_tool(server, win)

    def open_login(self, server: str) -> Locator:
        """« Connexion » dans le panneau du serveur -> fenêtre SaiLogin (ou directement le terminal)."""
        nm = self.ensure_reachable(server)
        mark = self.events.mark()
        btn = self.page.locator('[data-onboarding-300-id="ServerInfoPanelLoginButton"]')
        if not btn.count():
            btn = self.button(nm, "login")
        self.safe_click(btn.first)
        win = self.page.locator(WINDOW).filter(has=self.page.locator(f"{SAI_LOGIN}, {SAI_APP}")).last
        win.wait_for(timeout=15000)
        try:
            self.wait_event("sai", "get.login.status", mark, timeout=8)
        except StepFailed:
            self.log.debug("SAI %s : pas d'état de connexion reçu", server)
        return win

    def login_with_access(self, server: str, win: Locator) -> Locator:
        mark = self.events.mark()
        self.safe_click(win.locator(cname("SaiAccessItem")).first)
        ev = self.wait_event("sai", "login.with-access", mark, timeout=15)
        if not (ev.data or {}).get("success"):
            raise StepFailed(f"connexion SAI refusée sur {server} : {ev.data}")
        app = self.window(SAI_APP)
        app.wait_for(timeout=15000)
        return app

    def hack_preset_for(self, type_name: str | None) -> str | None:
        """Préréglage Loadout à mettre avant un piratage, selon serverTypeName (config hack_presets)."""
        presets = self.cfg["jobs"].get("hack_presets") or {}
        if type_name:
            if type_name in presets:
                return presets[type_name]
            low = type_name.lower()
            for key, value in presets.items():
                if key != "default" and key.lower() in low:
                    return value
        return presets.get("default")

    def login_with_tool(self, server: str, win: Locator) -> Locator:
        if not self.cfg["jobs"].get("allow_hack", True):
            raise StepFailed(f"{server} : aucun accès par tâche, il faut pirater (allow_hack = false)")
        srv = self.events.server(server) or {}
        preset = self.hack_preset_for(srv.get("serverTypeName"))
        if preset and preset != self._current_preset and not self.dry_run:
            self.log.info("%s (%s) : préréglage de piratage « %s »", server, srv.get("serverTypeName"), preset)
            self.close_window(SAI_LOGIN)
            self.apply_preset(preset)
            win = self.open_login(server)
            if win.locator(SAI_APP).count():
                return self.window(SAI_APP)
            if win.locator(cname("SaiAccessItem")).count():
                return self.login_with_access(server, win)
        for attempt in (1, 2):
            outcome = self.click_hack_tool(server, win)
            if outcome == "simulation":
                raise Simulated(f"connexion à {server} par l'outil de piratage non lancée (simulation)")
            if outcome == "logged":
                return self.window(SAI_APP)
            # piratage réussi : le jeu propose alors l'accès, ou le réutilise au prochain clic sur l'outil
            self.pause(1000)
            if self.window(SAI_APP).count():
                return self.window(SAI_APP)
            self.close_window(SAI_LOGIN)
            win = self.open_login(server)
            if win.locator(SAI_APP).count():
                return self.window(SAI_APP)
            if win.locator(cname("SaiAccessItem")).count():
                return self.login_with_access(server, win)
        raise StepFailed(f"{server} : piratage terminé mais connexion impossible")

    def click_hack_tool(self, server: str, win: Locator) -> str:
        """Clique l'outil le plus puissant. Renvoie « logged » (accès réutilisé, terminal ouvert),
        « hacked » (nouvel accès obtenu) ou « simulation »."""
        status_ev = self.events.last("sai", "get.login.status")
        status = (status_ev.data if status_ev and isinstance(status_ev.data, dict) else None) or {}
        srv = self.events.server(server) or {}
        defence = status.get("serverDefenceRate", srv.get("serverDefenceRate"))
        if status.get("isLocked"):
            raise StepFailed(f"connexion à {server} verrouillée (encore {status.get('lockExpiresIn')})")
        box = win.locator(comp("SaiHackTools"))
        if not box.count():
            if win.locator(comp("SaiNoTools")).count():
                raise StepFailed(f"{server} : aucun outil de piratage assez puissant (défense {defence}) ; "
                                 "vérifie hack_presets")
            raise StepFailed(f"{server} : ni accès par tâche ni outil de piratage")
        tools = [t for t in status.get("hackTools") or [] if isinstance(t, dict) and t.get("moduleName")]
        tool = max(tools, key=lambda t: float(t.get("hackPower") or 0)) if tools else None
        if tool and self.state is not None:
            self.state.note_hack_power(tool.get("hackPower"))
        try:
            too_weak = float(tool.get("hackPower") or 0) < float(defence) if tool and defence is not None else False
        except (TypeError, ValueError):
            too_weak = False
        if too_weak and srv.get("accessType") not in ("regular", "admin"):
            # 22/10 : puissance 19 contre défense 24, le jeu ne lance rien (ni piratage, ni ICE WALL)
            raise Hopeless(f"{server} : outil le plus puissant {tool['moduleName']} ({tool.get('hackPower')}) "
                           f"plus faible que la défense ({defence}) : piratage impossible, job perdu")
        target = box.get_by_text(tool["moduleName"], exact=True).first if tool else None
        if target is None or not target.count():
            raise StepFailed(f"{server} : outil de piratage introuvable dans la fenêtre ({tools})")
        if not self.allowed(f"se connecter à {server} avec {tool['moduleName']} "
                            f"(puissance {tool.get('hackPower')}, défense {defence})"):
            return "simulation"
        mark = self.events.mark()
        self.safe_click(target)
        deadline = time.time() + 25
        while time.time() < deadline:
            if self.events.find(mark, "sai", "login.with-access",
                                pred=lambda e: bool((e.data or {}).get("success"))):
                self.window(SAI_APP).wait_for(timeout=15000)
                self.log.info("%s : accès piraté encore valable, connexion directe", server)
                return "logged"
            started = self.events.find(mark, "sai", "hack.start")
            if started and isinstance(started[-1].data, dict) and started[-1].data.get("autoHacked"):
                self.log.info("%s : piraté sans mini-jeu (outil bien plus puissant que la défense)", server)
                return "hacked"
            game = self.events.find(mark, "minigames", "start.minigame")
            if game:
                info = game[-1].data or {}
                if not info.get("url") and info.get("lockExpiresAt"):
                    raise IceWallLocked(f"ICE WALL verrouillé jusqu'à {local_time(info['lockExpiresAt'])} (échec récent)",
                                        parse_time(info["lockExpiresAt"]))
                self.log.info("%s : piratage par ICE WALL (défense %s)", server, defence)
                self.solve_ice_wall()
                self.wait_hack_finished(server, mark)
                return "hacked"
            self.pause(200)
        self.snapshot("piratage_sans_reponse")
        raise StepFailed(f"{server} : le piratage n'a pas démarré")

    def wait_hack_finished(self, server: str, mark: int, timeout: float = 20) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            fin = self.events.find(mark, "sai", "update",
                                   pred=lambda e: isinstance(e.data, dict) and e.data.get("reason") == "hack.finished")
            if fin:
                if fin[-1].data.get("success") is False:
                    raise StepFailed(f"{server} : piratage échoué")
                self.log.info("%s : piratage réussi", server)
                return
            lock = self.events.find(mark, "minigames", "start.minigame",
                                    pred=lambda e: bool((e.data or {}).get("lockExpiresAt")))
            if lock:
                until = lock[-1].data["lockExpiresAt"]
                raise IceWallLocked(f"ICE WALL perdu : verrouillé jusqu'à {local_time(until)}", parse_time(until))
            self.pause(300)
        raise StepFailed(f"{server} : pas de confirmation de fin de piratage")

    def do_sai_list_job(self, job: dict, details: dict, tab: str, room_action: str,
                        list_key: str, delete: bool) -> None:
        server = self.job_server(job)
        if not server:
            raise StepFailed("job serveur sans serveur")
        sai = self.open_sai(server)
        ev = self.sai_tab(sai, tab, room_action)
        items = (ev.data or {}).get(list_key) or []
        index = self._find_target(items, details, list_key)
        panel = sai.locator(comp("SaiLogs" if list_key == "logs" else "SaiFiles"))
        icon = "TrashIcon" if delete else "DownloadIcon"
        panel.locator(comp("ScrollArea")).locator("button").first.wait_for(timeout=10000)
        buttons = panel.locator("button").filter(has=self.page.locator(comp(icon)))
        if buttons.count() == len(items):
            button = buttons.nth(index)
        else:   # v1.25.71, FICHIERS : icônes sans nom, deux boutons par ligne (télécharger, puis supprimer)
            pairs = panel.locator(comp("ScrollArea")).locator("button")
            if pairs.count() != 2 * len(items):
                raise StepFailed(f"{pairs.count()} boutons pour {len(items)} lignes dans {list_key} : rien n'est fait")
            button = pairs.nth(2 * index + (1 if delete else 0))
        # la ligne = le plus grand bloc qui ne contient que ses deux boutons (v1.25.71 : les boutons sont dans
        # une cellule « Action » à part, sans le nom)
        row = button.locator("xpath=ancestor::div[count(.//button)=2][last()]")
        label = items[index].get("message") or items[index].get("name") or ""
        if label and label[:25] not in row.inner_text():
            raise StepFailed(f"la ligne {index} ne correspond pas à « {label} » : rien n'est fait")
        verb = "supprimer" if delete else "télécharger"
        if not self.allowed(f"{verb} « {label} » sur {server}"):
            return
        mark = self.events.mark()
        button.click()
        if delete:      # une fenêtre de confirmation s'ouvre (non vérifié en direct)
            modal = self.page.locator(cname("SaiDeleteModal"))
            modal.wait_for(timeout=8000)
            modal.locator(cname("SaiDeleteButtons")).get_by_role("button").last.click()
        self._expect_can_complete(mark)
        self.close_window(comp("ServerAdministrationInterfaceApplication"))

    def _expect_can_complete(self, mark: int, timeout: float = 15) -> None:
        """Le serveur pousse market:job.can-complete si la fenêtre du Market est ouverte ;
        sinon complete_job revérifie canComplete dans la liste des jobs."""
        if not self.window(comp("MarketSite")).count():
            self.pause(1500)
            return
        try:
            self.wait_event("market", "job.can-complete", mark, timeout=timeout,
                            pred=lambda e: (e.data or {}).get("canComplete") is True)
        except StepFailed:
            self.log.info("Pas de notification « can-complete » : vérification dans la liste des jobs")

    @staticmethod
    def _find_target(items: list[dict], details: dict, list_key: str) -> int:
        """Index de la cible dans la liste du serveur. Les doublons de noms sont
        départagés par le numéro de séquence fourni par le job, sinon par source == 'job'."""
        files = [f for f in details.get("files") or [] if isinstance(f, dict)]
        seqs = details.get("logSeqs") or details.get("fileSeqs") or []
        # « j_… » n'est pas l'id du fichier sur le serveur (le même revient sur plusieurs jobs)
        ids = [i for i in (details.get("fileIds") or details.get("logIds") or [f.get("id") for f in files])
               if i and not str(i).startswith("j_")]
        names = (details.get("logNames") or details.get("fileNames") or details.get("names")
                 or [f.get("name") for f in files if f.get("name")])
        for i, it in enumerate(items):
            if seqs and it.get("seq") in seqs:
                return i
            if ids and it.get("id") in ids:
                return i
        candidates = [i for i, it in enumerate(items)
                      if (it.get("message") or it.get("name")) in names] if names else []
        if len(candidates) > 1:
            candidates = [i for i in candidates if items[i].get("source") == "job"] or candidates
        if len(candidates) == 1:
            return candidates[0]
        if not candidates and names and list_key == "files":
            # Decrypt & Extract : le job annonce « db_6531.txt », le nœud a « db_6531.bak » (extension inconnue)
            stems = {stem(n) for n in names}
            candidates = [i for i, it in enumerate(items) if stem(it.get("name") or "") in stems]
            if len(candidates) == 1:
                return candidates[0]
        raise StepFailed(f"cible introuvable ou ambiguë dans {list_key} : {details}")

    SAI_TAB_IDS = {"tab_transit": "SaiTabs-TRANSIT", "tab_files": "SaiTabs-FILES", "tab_logs": "SaiTabs-LOGS"}

    def sai_tab(self, sai: Locator, tab: str, room_action: str) -> WsEvent:
        """Onglet du terminal SAI et sa liste (sai:get.transit / get.files / get.logs). Un clic trop tôt après
        la connexion, ou sur l'onglet déjà ouvert, n'envoie rien (22/10) : on passe par « Résumé » et on revient."""
        header = sai.locator(comp("SaiHeader"))
        by_id = header.locator(f'[data-onboarding-300-id="{self.SAI_TAB_IDS[tab]}"]')
        button = by_id.first if by_id.count() else header.get_by_role("button", name=rx(TXT[tab])).first
        mark = self.events.mark()
        button.click()
        try:
            return self.wait_event("sai", room_action, mark, timeout=5)
        except StepFailed:
            self.log.debug("Onglet %s sans réponse : passage par « Résumé » et retour", tab)
        mark = self.events.mark()
        header.get_by_role("button", name=rx(r"^\s*(Résumé|Summary)\s*$")).first.click()
        self.pause(800)
        button.click()
        return self.wait_event("sai", room_action, mark, timeout=15)

    def do_upload_job(self, job: dict, details: dict) -> None:
        """« Data Upload » (d'après le joueur, jamais vu par le bot) : terminal SAI → FICHIERS → icône
        d'envoi, puis naviguer dans Downloads et cliquer le fichier cible."""
        server = self.job_server(job)
        files = [f for f in details.get("files") or [] if isinstance(f, dict)]
        names = [f.get("name") for f in files if f.get("name")] or details.get("fileNames") or []
        if not server or not names:
            raise StepFailed(f"job d'envoi incomplet : {details}")
        sai = self.open_sai(server)
        self.sai_tab(sai, "tab_files", "get.files")
        upload = sai.locator("button").filter(
            has=self.page.locator('[data-sentry-element="UploadIcon"], [data-sentry-component="UploadIcon"]'))
        if not upload.count():
            raise StepFailed("pas d'icône d'envoi (UploadIcon) dans l'onglet FICHIERS")
        upload.first.click()
        # v1.25.71 : fenêtre « Sélectionner un fichier à envoyer », ouverte sur ./Bureau/Downloads
        items = self.page.locator(cname("FilePickerItem"))
        try:
            items.first.wait_for(timeout=10000)
            crumbs = self.page.locator(cname("FilePickerBreadcrumbs"))
            if crumbs.count() and "Downloads" not in crumbs.inner_text():
                items.filter(has_text=rx(r"^\s*Downloads\s*$")).first.dblclick()
                self.pause(800)
            target = items.filter(has_text=stem(names[0]))
            if not target.count():
                raise StepFailed(f"fichier {names[0]} introuvable dans Downloads (fenêtre d'envoi)")
            target.first.click()
            self.pause(300)
            chosen = self.page.locator(cname("FilePickerFileName")).inner_text().strip()
            if chosen != names[0]:
                raise StepFailed(f"fichier sélectionné « {chosen} » au lieu de « {names[0]} » : rien n'est envoyé")
            if not self.allowed(f"envoyer {names[0]} sur {server}"):
                return
            mark = self.events.mark()
            self.page.locator(cname("FilePickerAttachButton")).click()
            try:                        # nom exact de la trame pas encore relevé : la liste des jobs tranchera
                self.wait_event("sai", "file.upload", mark, timeout=20)
            except StepFailed:
                self.log.info("Pas de trame « sai:file.upload » : vérification dans la liste des jobs")
        finally:
            close = self.page.locator(cname("FilePickerCloseButton"))
            if close.count() and close.first.is_visible():
                close.first.click()
                self.pause(300)
        self._expect_can_complete(mark)
        self.close_window(comp("ServerAdministrationInterfaceApplication"))

    def do_transit_job(self, job: dict, details: dict, add: bool) -> None:
        # Écran non testé en direct (le job IP Injection en cours n'était pas à moi) :
        # désactivé par défaut (allow_ip_add / allow_deletions dans config.toml).
        server = self.job_server(job)
        ips = details.get("ips") or []
        if not server or not ips:
            raise StepFailed(f"job transit incomplet : {details}")
        sai = self.open_sai(server)
        mark = self.events.mark()
        self.sai_tab(sai, "tab_transit", "get.transit")
        for ip in ips:
            if add:
                if not self.allowed(f"ajouter l'IP {ip} sur {server}"):
                    continue
                # v1.25.71 : data-component-name="SaiAddButton" (icône AddUserIcon), au-dessus de la « Liste noire »
                sai.locator(f'{cname("SaiAddButton")}, {comp("SaiAddButton")}').first.click()
                modal = self.page.locator(cname("SaiAddIpModal"))
                modal.wait_for(timeout=8000)
                modal.locator(cname("SaiAddIpModalIpInput")).fill(ip)
                modal.locator(cname("SaiAddIpModalSaveButton")).click()
            else:
                # v1.25.71 : ligne IP / durée / bouton poubelle sans icône nommée ; la ligne = le plus grand
                # bloc autour de l'IP qui ne contient qu'un bouton
                row = sai.get_by_text(ip, exact=True).first.locator("xpath=ancestor::div[count(.//button)=1][last()]")
                if not row.count():
                    raise StepFailed(f"IP {ip} absente de la liste de transit")
                if not self.allowed(f"supprimer l'IP {ip} sur {server}"):
                    continue
                row.locator("button").last.click()
                modal = self.page.locator(cname("SaiDeleteModal"))
                modal.wait_for(timeout=8000)
                modal.locator(cname("SaiDeleteButtons")).get_by_role("button").last.click()
            self.pause(1200)
        self._expect_can_complete(mark)
        self.close_window(comp("ServerAdministrationInterfaceApplication"))

    # =========================================================================
    # 3. Mercenaires
    # =========================================================================
    @staticmethod
    def _missions(data: Any) -> list[dict]:
        return [m for m in (data if isinstance(data, list) else [data] if data else []) if isinstance(m, dict)]

    def active_expeditions(self, since: int = 0) -> list[dict]:
        """Expéditions d'après la dernière trame expeditions:get.active reçue après `since`.
        v1.25.71 : le serveur l'envoie quand le client rejoint la salle, à la connexion ; rien n'est
        envoyé à l'ouverture de la fenêtre (relevé du 21/10/2026)."""
        self.close_window(comp("ExpeditionsApplication"))
        self.dock("expeditions")
        win = self.window(comp("ExpeditionsApplication"))
        win.wait_for(timeout=15000)
        try:
            ev = self.wait_event("expeditions", "get.active", since, timeout=15)
        except StepFailed:
            # Aucune trame : l'écran « Il n'y a aucune expédition active » fait foi ; sinon c'est un échec.
            if win.locator(comp("ExpeditionsActiveEmpty")).count():
                self.log.info("Expéditions : aucune expédition active (lu à l'écran, pas de trame get.active)")
                return []
            raise
        return self._missions(ev.data)

    def run_mercs(self) -> str:
        mcfg = self.cfg["mercenaries"]
        self.ensure_connected()
        self.close_all_windows()
        notes = []
        try:
            active = self.active_expeditions()
            # Engager (dépense de CR) : seulement au passage complet, jamais en mode --auto horaire.
            if not active and mcfg.get("hire", False) and not self.auto_mode:
                hired_at = self.events.mark()
                try:
                    notes.append(self.hire_mercenary())
                finally:
                    self.restore_endpoint()
                active = self.active_expeditions(since=hired_at)     # une liste plus récente que l'engagement
            if active:
                notes.append(self.watch_expeditions(float(mcfg.get("watch_minutes", 40))))
        finally:
            if self.state is not None:
                ev = self.events.last("expeditions", "get.active")
                missions = self._missions(ev.data) if ev else []
                self.state.set_expedition(any(m.get("status") in ACTIVE_EXPEDITION for m in missions))
        return "; ".join(n for n in notes if n) or "aucune expédition en cours"

    def hire_mercenary(self) -> str:
        mcfg = self.cfg["mercenaries"]
        market = self.open_market(mcfg.get("hire_market") or HOME_SERVER)
        mark = self.events.mark()
        self.market_tab(market, "hire_tab")
        ev = self.wait_event("expeditions", "get.mercenaries", mark, timeout=15)
        mercs = [m for m in (ev.data or {}).get("mercenaries", []) if m.get("status") == "AVAILABLE"]
        prefs = mcfg.get("preferred", [])
        mercs.sort(key=lambda m: prefs.index(m["callsign"]) if m.get("callsign") in prefs else len(prefs))
        if prefs and mcfg.get("only_preferred", False):
            mercs = [m for m in mercs if m.get("callsign") in prefs]
        if not mercs:
            return "aucun mercenaire disponible"
        merc = mercs[0]
        card = market.locator(comp("MercenaryCard")).filter(has_text=merc["callsign"]).first
        more = card.locator(cname("MercenaryCardCollapsedReadMore"))
        if more.count() and re.search(TXT["read_more"], more.first.inner_text(), re.I):
            more.first.click()
            self.pause(400)
        mark = self.events.mark()
        card.locator(comp("Button")).filter(has_text=rx(TXT["hire_tab"])).first.click()
        panel = self.page.locator(cname("HireSideoutPanel"))
        panel.wait_for(timeout=10000)
        for idx, wanted in ((0, mcfg.get("location")), (1, mcfg.get("goal"))):
            if wanted:
                panel.locator(cname("CustomSelectTrigger")).nth(idx).click()
                self.page.get_by_text(wanted, exact=True).last.click()
                self.pause(600)
        quote_ev = self.wait_event("expeditions", "configure", mark, timeout=15)
        quote = self.events.last("expeditions", "configure").data or quote_ev.data or {}
        total = int(quote.get("totalCost") or 0)
        death = float((quote.get("outcomeChances") or {}).get("deathChance") or 0)
        risk = quote.get("riskLevel")
        balance = self.balance()
        self.log.info("Devis %s : %s CR (acompte %s), risque %s, mort %s %%, durée %s min",
                      merc["callsign"], total, quote.get("prepaymentAmount"), risk, death,
                      round((quote.get("duration") or 0) / 60000))
        problems = []
        if total > int(mcfg.get("max_spend_cr", 0)):
            problems.append(f"coût {total} CR > max_spend_cr")
        if death > float(mcfg.get("max_death_chance", 0)):
            problems.append(f"risque de mort {death} %")
        if balance is not None and balance - total < int(mcfg.get("keep_min_balance", 0)):
            problems.append(f"solde {balance} CR insuffisant")
        if problems:
            panel.locator(cname("SideoutHeaderCloseButton")).click()
            return f"{merc['callsign']} non engagé : " + ", ".join(problems)
        if not self.allowed(f"engager {merc['callsign']} pour {total} CR (acompte {quote.get('prepaymentAmount')})"):
            panel.locator(cname("SideoutHeaderCloseButton")).click()
            return f"simulation : {merc['callsign']} pour {total} CR"
        panel.locator(cname("HireSideoutDisclaimerCheckbox")).click()
        mark = self.events.mark()
        panel.get_by_role("button", name=rx(TXT["hire_pay"])).click()
        ev = self.wait_event("expeditions", "launch", mark, timeout=20)
        self.log.info("Expédition lancée : %s (%s)", merc["callsign"], (ev.data or {}).get("status"))
        self.close_all_windows()
        return f"{merc['callsign']} engagé ({total} CR)"

    def choose_option(self, message: dict) -> dict | None:
        options = message.get("decisionOptions") or []
        if not options:
            return None
        policy = self.cfg["mercenaries"].get("event_policy", "prudent")
        if policy == "auto":            # on laisse le jeu choisir l'option par défaut à l'échéance
            return None
        max_risk = float(self.cfg["mercenaries"].get("max_risk_modifier", 10))

        def risk(o): return float(o.get("riskModifier") or 0)

        def loot(o): return float(o.get("lootModifier") or 0)
        if policy == "gourmand":
            pool = [o for o in options if risk(o) <= max_risk] or options
            return max(pool, key=lambda o: (loot(o), -risk(o)))
        return min(options, key=lambda o: (risk(o), -loot(o)))       # prudent

    def watch_expeditions(self, max_minutes: float) -> str:
        deadline = time.time() + max_minutes * 60
        answered: set[str] = set()
        win = self.window(comp("ExpeditionsApplication"))
        last_status = None
        while time.time() < deadline:
            self.check_session()
            if not win.count():
                self.dock("expeditions")
                win.wait_for(timeout=15000)
            active_ev = self.events.last("expeditions", "get.active")
            ev = active_ev or self.events.last("expeditions", "respond.event")
            missions = self._missions(ev.data) if active_ev else (ev.data if ev and isinstance(ev.data, list) else [])
            if not missions:
                return "expéditions terminées"
            for mission in missions:
                status = mission.get("status")
                if status != last_status:
                    self.log.info("Expédition %s : %s %s", (mission.get("mercenary") or {}).get("callsign"),
                                  status, mission.get("outcome") or "")
                    last_status = status
                for msg in mission.get("messages") or []:
                    if msg.get("messageType") != "DECISION" or msg.get("isResolved") or msg.get("id") in answered:
                        continue
                    answered.add(msg["id"])
                    option = self.choose_option(msg)
                    self.log.info("Événement : %s | options : %s | choix : %s", msg.get("content"),
                                  [(o.get("label"), o.get("riskModifier"), o.get("lootModifier"))
                                   for o in msg.get("decisionOptions") or []],
                                  option.get("label") if option else "défaut du jeu")
                    if option and self.allowed(f"répondre « {option['label']} »"):
                        card = win.locator(comp("ExpeditionsEventCard")).filter(
                            has_text=(msg.get("content") or "")[:40]).first
                        mark = self.events.mark()
                        card.locator(comp("ChamferedButton")).filter(has_text=option["label"]).first.click()
                        self.wait_event("expeditions", "respond.event", mark, timeout=15)
                if status == "COMPLETED":
                    return self.collect_expedition(win, mission)
            self.pause(2000)
        return "surveillance terminée (durée max atteinte)"

    def collect_expedition(self, win: Locator, mission: dict) -> str:
        mcfg = self.cfg["mercenaries"]
        remaining = int(mission.get("remainingAmount") or 0)
        name = (mission.get("mercenary") or {}).get("callsign")
        outcome = mission.get("outcome")
        if remaining and not mcfg.get("pay_on_return", False):
            return f"{name} : {outcome}, solde de {remaining} CR à payer à la main (pay_on_return = false)"
        if remaining > int(mcfg.get("max_spend_cr", 0)):
            return f"{name} : solde {remaining} CR > max_spend_cr, à payer à la main"
        container = win.locator(cname("ExpeditionsEventContainerButton"))
        if container.count() and container.first.is_enabled():
            if not self.allowed(f"ouvrir le conteneur de {name}"):
                return "simulation : conteneur non ouvert"
            mark = self.events.mark()
            container.first.click()
            self.wait_event("expeditions", "open.container", mark, timeout=15)
        collect = win.locator(comp("ExpeditionsClaimFooter")).get_by_role("button", name=rx(r"Tout récupérer|Collect all"))
        try:
            collect.first.wait_for(timeout=10000)
        except PWTimeout:
            return f"{name} : {outcome}, rien à récupérer (ou écran inattendu, voir logs/debug)"
        # Constaté le 21/10/2026 : « Payer et ouvrir » ouvre le conteneur, le solde est débité
        # au moment de « Tout récupérer » (collect.all).
        if not self.allowed(f"récupérer le butin de {name} et payer le solde ({remaining} CR)"):
            return "simulation : butin non récupéré"
        mark = self.events.mark()
        collect.first.click()
        self.wait_event("expeditions", "collect.all", mark, timeout=15)
        return f"{name} : {outcome}, butin récupéré, solde payé {remaining} CR"

    # =========================================================================
    # 4. Drone personnel (Expéditions → Drone personnel), relevé le 23/10/2026 en v1.25.71
    # =========================================================================
    def run_drone(self) -> str:
        """Récupère le butin au courrier, lance une mission dès que la batterie le permet et, si `watch`,
        reste jusqu'au retour pour répondre aux événements. Une mission ne coûte que de la batterie ;
        la réparation (CR) n'est jamais faite par le bot (`repair_below` ne fait qu'alerter)."""
        dcfg = self.cfg["drone"]
        watch, minutes = dcfg.get("watch", True), float(dcfg.get("watch_minutes", 25))
        self.ensure_connected()
        self.close_all_windows()
        notes: list[str] = []
        opts: dict = {}
        mission: dict | None = None
        try:
            opts = self.open_drone()
            if opts.get("activeMissionId"):
                mission = self.drone_mission(opts["activeMissionId"])
                if not watch:
                    return "mission en cours"
                notes.append(self.watch_drone(mission, minutes))
                opts = self.open_drone()
            if opts.get("activeMissionId"):
                return "; ".join(notes)
            if dcfg.get("claim_loot", True):
                notes.append(self.claim_mail())
                opts = self.open_drone()
            note, mission = self.launch_drone(opts)
            notes.append(note)
            if mission and watch:
                notes.append(self.watch_drone(mission, minutes))
                if dcfg.get("claim_loot", True):
                    notes.append(self.claim_mail())
                opts = self.open_drone()
            elif mission:
                opts = {**opts, "activeMissionId": mission.get("id")}
            notes.append(self.drone_repair_note(opts))
        finally:
            try:
                self.close_all_windows()
            except PWError:
                pass
            if self.state is not None and not self.dry_run:
                when, why = self.drone_next_time(opts, mission)
                self.state.set_drone_next(when, why)
                if why:
                    notes.append(why)
        return "; ".join(n for n in notes if n) or "rien à faire"

    def drone_repair_note(self, opts: dict) -> str:
        """`repair_below` (0 = jamais) : alerte quand la durabilité passe sous le seuil. L'écran de réparation
        n'a pas encore été relevé : le bot ne clique pas « Réparation » (dépense de CR)."""
        below = int(self.cfg["drone"].get("repair_below", 0) or 0)
        drone = opts.get("drone") or {}
        durability = drone.get("durability")
        if not below or durability is None or int(durability) >= below:
            return ""
        rate = (opts.get("repairRate") or {}).get("creditsPerPoint") or 200
        cost = (int(drone.get("maxDurability") or 100) - int(durability)) * int(rate)
        return f"drone à réparer : durabilité {durability} < repair_below ({cost} CR pour tout réparer, à faire à la main)"

    def open_drone(self) -> dict:
        """Ouvre l'onglet Drone personnel ; renvoie drone-missions:get.options (drone, batterie, missions)."""
        mark = self.events.mark()
        self.close_window(comp("ExpeditionsApplication"))
        self.dock("expeditions")
        win = self.window(comp("ExpeditionsApplication"))
        win.wait_for(timeout=15000)
        tab = win.locator(comp("ExpeditionsTabItem")).filter(has_text=rx(TXT["drone_tab"])).locator("button")
        self.safe_click(tab.first)
        ev = self.wait_event("drone-missions", "get.options", mark, timeout=15)
        win.locator(comp("ExpeditionsPidlDroneTab")).wait_for(timeout=10000)
        data = ev.data if isinstance(ev.data, dict) else {}
        drone, battery = data.get("drone") or {}, data.get("battery") or {}
        self.log.info("Drone %s : durabilité %s/%s, batterie %s/%s, mission en cours : %s", drone.get("name"),
                      drone.get("durability"), drone.get("maxDurability"), battery.get("current"),
                      battery.get("max"), "oui" if data.get("activeMissionId") else "non")
        return data

    def drone_mission(self, mission_id: str) -> dict:
        """La mission en cours, d'après la dernière trame qui la décrit (départ, connexion, réponse)."""
        for action in ("resolve.event", "launch", "get.active"):
            ev = self.events.last("drone-missions", action)
            for m in self._missions(ev.data if ev else None):
                if m.get("id") == mission_id:
                    return m
        return {"id": mission_id}

    def drone_choice(self, event: dict) -> dict | None:
        """Option choisie pour un événement du drone (DECISION, HAZARD), d'après `event_policy`.
        Les options qui demandent un module du drone (`requiredModuleId`) sont écartées : le joueur n'en a pas."""
        dcfg = self.cfg["drone"]
        policy = dcfg.get("event_policy", "gourmand")
        options = [o for o in event.get("options") or []
                   if not (isinstance(o.get("requiredModuleId"), str) and o["requiredModuleId"])]
        if not options or policy == "auto":
            return None
        max_risk = float(dcfg.get("max_risk_delta", 0.15))

        def risk(o): return float((o.get("effect") or {}).get("riskDelta") or 0)

        def loot(o): return float((o.get("effect") or {}).get("lootDelta") or 0)
        if policy == "gourmand":
            pool = [o for o in options if risk(o) <= max_risk] or options
            return max(pool, key=lambda o: (loot(o), -risk(o)))
        return min(options, key=lambda o: (risk(o), -loot(o)))       # prudent

    def watch_drone(self, mission: dict, max_minutes: float) -> str:
        """Répond aux événements (trames drone-missions:event) jusqu'au retour (drone-missions:completed)."""
        mission_id = mission.get("id")
        win = self.window(comp("ExpeditionsApplication"))
        deadline = time.time() + max_minutes * 60
        answered = {e.get("eventId") for e in mission.get("channelEvents") or [] if e.get("isResolved")}
        # mission reprise en cours de route : les événements déjà arrivés sont dans la mission (isResolvable)
        for e in mission.get("channelEvents") or []:
            if e.get("isResolvable") and e.get("eventId") not in answered:
                answered.add(e.get("eventId"))
                self.answer_drone_event(win, mission_id, e)
        while time.time() < deadline:
            self.check_session()
            done = self.events.last("drone-missions", "completed")
            if done and isinstance(done.data, dict) and done.data.get("missionId") == mission_id:
                d = done.data
                loot = ", ".join(f"{x.get('name')} x{x.get('quantity', 1)}" for x in d.get("loot") or []) or "rien"
                self.log.info("Drone de retour : %s, dégâts %s (durabilité %s), butin : %s", d.get("outcome"),
                              d.get("damageDealt"), d.get("durabilityAfter"), loot)
                return f"mission {d.get('outcome')} (durabilité {d.get('durabilityAfter')}, butin : {loot})"
            for ev in self.events.find(0, "drone-missions", "event"):
                data = ev.data if isinstance(ev.data, dict) else {}
                event = data.get("event") or {}
                if data.get("missionId") != mission_id or not event.get("isResolvable") \
                        or event.get("eventId") in answered:
                    continue
                answered.add(event.get("eventId"))
                self.answer_drone_event(win, mission_id, event)
            self.pause(2000)
        return "surveillance du drone terminée (durée max atteinte)"

    def answer_drone_event(self, win: Locator, mission_id: str, event: dict) -> None:
        option = self.drone_choice(event)
        self.log.info("Drone, événement %s : %s | options : %s | choix : %s", event.get("type"), event.get("message"),
                      [(o.get("label"), (o.get("effect") or {}).get("riskDelta"),
                        (o.get("effect") or {}).get("lootDelta")) for o in event.get("options") or []],
                      option.get("label") if option else "aucun (défaut du jeu)")
        if not option or not self.allowed(f"répondre « {option['label']} » au drone"):
            return
        if not win.count() or not win.locator(comp("ExpeditionsPidlDroneTab")).count():
            self.open_drone()
        card = win.locator(comp("ExpeditionsEventCard")).filter(has_text=(event.get("message") or "")[:40]).first
        button = card.locator(comp("ChamferedButton")).filter(has_text=option["label"]).first
        try:
            button.wait_for(timeout=10000)
        except PWTimeout:
            self.snapshot("drone_evenement")
            self.log.warning("Drone : bouton « %s » introuvable, le jeu choisira", option["label"])
            return
        mark = self.events.mark()
        self.safe_click(button)
        self.wait_event("drone-missions", "resolve.event", mark, timeout=15)

    def launch_drone(self, opts: dict) -> tuple[str, dict | None]:
        """Lance la mission (la première proposée, ou `mission`) ; renvoie la note et la mission partie."""
        dcfg = self.cfg["drone"]
        drone, battery = opts.get("drone") or {}, opts.get("battery") or {}
        missions = [(loc, m) for loc in opts.get("locations") or [] for m in loc.get("missions") or []]
        wanted = dcfg.get("mission") or ""
        pick = next(((loc, m) for loc, m in missions if m.get("name") == wanted), missions[0] if missions else None)
        if pick is None:
            return "pas de départ : aucune mission proposée", None
        loc, mission = pick
        cost = int(mission.get("batteryCost") or 0)
        durability = int(drone.get("durability") or 0)
        if drone.get("isDestroyed") or drone.get("isRepairing"):
            return "pas de départ : drone " + ("détruit" if drone.get("isDestroyed") else "en réparation"), None
        if int(battery.get("current") or 0) < cost:
            return f"pas de départ : batterie {battery.get('current')} < {cost}", None
        if durability < int(dcfg.get("min_durability", 30)):
            return f"pas de départ : durabilité {durability} < min_durability", None
        if not self.allowed(f"lancer le drone {drone.get('name')} : {mission.get('name')} ({loc.get('name')}), "
                            f"{cost} de batterie"):
            return "simulation : drone non lancé", None
        win = self.window(comp("ExpeditionsApplication"))
        start = win.locator(comp("ExpeditionsPidlDroneLaunch")).locator(comp("Button")).filter(
            has_text=rx(TXT["drone_launch"])).first
        self.safe_click(start)
        # v1.25.71 (23/10) : « Démarrer la sortie » ouvre un panneau (Lieu, Objectif, Infos sortie) ;
        # le drone part à « Envoyer ».
        sideout = self.page.locator(cname("ExpeditionsPidlDroneSideout"))
        sideout.wait_for(timeout=10000)
        for idx, name in ((0, loc.get("name")), (1, mission.get("name"))):
            trigger = sideout.locator(cname("CustomSelectTrigger")).nth(idx)
            if name and trigger.count() and name not in trigger.inner_text():
                trigger.click()
                self.page.get_by_text(name, exact=True).last.click()
                self.pause(500)
        mark = self.events.mark()
        self.safe_click(sideout.get_by_role("button", name=rx(TXT["drone_send"])).first)
        try:
            ev = self.wait_event("drone-missions", "launch", mark, timeout=12)
        except StepFailed:
            self.snapshot("drone_depart")
            close = sideout.locator('[data-sentry-element="PidlDroneSideoutHeaderBtnStyled"]')
            if close.count():
                self.safe_click(close.first)
            raise StepFailed("« Envoyer » cliqué, mais le drone n'est pas parti : écran à relever "
                             "(logs/debug/*drone_depart*)")
        started = ev.data if isinstance(ev.data, dict) else {}
        self.log.info("Drone lancé : %s, risque %s, %s de batterie, retour vers %s", mission.get("name"),
                      started.get("finalRisk"), cost, local_time(started.get("endTime")))
        return f"drone lancé ({mission.get('name')}, risque {started.get('finalRisk')})", started

    def claim_mail(self) -> str:
        """Stash → Boîte aux lettres → « Tout récupérer » (mail:claim.all) : le butin du drone est gratuit."""
        pending = self.events.last("mail", "update.state") or self.events.last("mail", "get.state")
        if pending and isinstance(pending.data, dict) and not pending.data.get("pendingCount"):
            return ""
        mark = self.events.mark()
        self.close_all_windows()
        self.dock("stash")
        page = self.page.locator(cname("StashPage"))
        try:
            page.wait_for(timeout=15000)
            panel = page.locator(cname("MailboxPanel"))
            panel.wait_for(timeout=10000)
            claim = panel.locator(cname("StashButton")).filter(has_text=rx(TXT["claim_all"])).first
            if not claim.count() or not claim.is_enabled():
                return "courrier : rien à récupérer"
            if not self.allowed("récupérer le butin du courrier (Tout récupérer)"):
                return "simulation : butin non récupéré"
            self.safe_click(claim)
            ev = self.wait_event("mail", "claim.all", mark, timeout=15)
            got = [r for r in (ev.data or {}).get("results") or [] if r.get("status") == "claimed"] \
                if isinstance(ev.data, dict) else []
            return f"courrier : {len(got)} objet(s) récupéré(s)"
        finally:
            back = self.page.locator(cname("StashHeader")).locator(cname("StashButton")).filter(has_text=rx(TXT["back"]))
            if back.count():
                self.safe_click(back.first)
                self.pause(800)

    def drone_next_time(self, opts: dict, mission: dict | None) -> tuple[dt.datetime | None, str]:
        """Quand repasser : au retour de la mission en cours, ou quand la batterie suffira pour la suivante."""
        now = utc_now()
        if opts.get("activeMissionId"):
            end = parse_time((mission or {}).get("endTime")) or now + dt.timedelta(minutes=20)
            return end + dt.timedelta(minutes=1), f"drone en mission jusqu'à {local_time(end)}"
        drone, battery = opts.get("drone") or {}, opts.get("battery") or {}
        if not drone:
            return None, ""
        dcfg = self.cfg["drone"]
        if int(drone.get("durability") or 0) < int(dcfg.get("min_durability", 30)):
            later = now + dt.timedelta(hours=6)
            return later, (f"drone à réparer (durabilité {drone.get('durability')} < min_durability), "
                           f"je repasserai vers {local_time(later)}")
        costs = [int(m.get("batteryCost") or 0) for loc in opts.get("locations") or [] for m in loc.get("missions") or []]
        cost, current = min(costs or [30]), float(battery.get("current") or 0)
        if current >= cost:
            return None, ""
        rate = float(battery.get("restorePerHour") or 8.33) or 8.33
        when = now + dt.timedelta(hours=(cost - current) / rate, minutes=2)
        return when, f"batterie {current:g}/{cost} : prochain départ vers {local_time(when)}"


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------
SECTION = {"daily": "daily", "jobs": "jobs", "markets": "markets", "mercs": "mercenaries", "drone": "drone"}
DEFAULT_ON = {"daily", "markets"}


def in_hours(hours: Any, now_local: dt.datetime) -> bool:
    """Heures autorisées [début, fin[ en heure locale ; [22, 6] passe minuit."""
    try:
        start, end = int(hours[0]), int(hours[1])
    except (TypeError, ValueError, IndexError):
        return True
    if start == end or (start <= 0 and end >= 24):
        return True
    if start < end:
        return start <= now_local.hour < end
    return now_local.hour >= start or now_local.hour < end


def auto_decision(cfg: dict, state: State, now: dt.datetime, idle_s: float | None,
                  only: list[str] | None = None) -> tuple[list[str], str]:
    """Mode --auto : parties à lancer maintenant (liste vide = ne rien lancer) et pourquoi."""
    acfg = cfg.get("auto", {})
    due = state.due(cfg, now)
    if only:
        due = {k: v for k, v in due.items() if k in only}
    if not due:
        return [], "rien à faire"
    reasons = " ; ".join(f"{k} : {v}" for k, v in due.items())
    if not in_hours(acfg.get("hours", [0, 24]), now.astimezone()):
        return [], f"hors des heures autorisées [auto] hours ({reasons})"
    min_idle = float(acfg.get("skip_if_user_active_min", 10)) * 60
    if idle_s is not None and min_idle > 0 and idle_s < min_idle:
        return [], f"PC utilisé il y a {int(idle_s)} s, je ne prends pas ta session ({reasons})"
    return [p for p in SECTION if p in due], reasons


def warn_daily_if_late(cfg: dict, state: State, notifier: Notifier, why: str, now: dt.datetime) -> None:
    """Daily pas faite et reset proche (00:00 UTC) : une alerte par jour au lieu de forcer."""
    if not cfg["daily"].get("enabled", True) or state.daily_done_today(now):
        return
    reset = now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    left_h = (reset - now).total_seconds() / 3600
    if left_h > float(cfg["auto"].get("warn_daily_hours", 3)) or state.data.get("daily_warned") == now.date().isoformat():
        return
    state.data["daily_warned"] = now.date().isoformat()
    notifier.send("cor3 : daily pas encore faite",
                  f"Reset dans {left_h:.1f} h ({local_time(reset)}). Le bot ne l'a pas lancée : {why}.", failure=True)


JOB_DONE = re.compile(r"^(\d{4}-\d\d-\d\d) \S+ +INFO +Job terminé : (.+?) sur .+? \(\+(\d+) CR, \+(\d+) REP\)")
JOB_LOST = re.compile(r"^(\d{4}-\d\d-\d\d) .*job perdu")
BALANCE = re.compile(r"^(\d{4}-\d\d-\d\d \S+) +INFO +Solde : (\d+) CR")
RENOWN_LINE = re.compile(r"^((\d{4}-\d\d-\d\d) \S+) +INFO +Renommée : niveau (\d+), (\d+)/(\d+) \((?:après )?([^,)]+)")
PILLAR_LEVEL = 20       # rang Pillar : Renommée 20 (avec la quête, l'événement et le total de crédits)


def bilan(log_dir: Path, today: dt.date | None = None, days: int = 7) -> str:
    """Gains par jour (heure locale), lus dans les journaux : jobs terminés, CR, REP, Renommée, jobs perdus,
    dernier solde, et d'où vient la Renommée (jobs, daily, mercs, ou hors du bot entre deux passages)."""
    today = today or dt.date.today()
    first_day = (today - dt.timedelta(days=days - 1)).isoformat()
    per_day: dict[str, dict] = {}
    types: dict[str, int] = {}
    sources: dict[str, int] = {}
    balance = None
    renown: list[tuple[dt.datetime, tuple[int, int, int]]] = []

    def day_of(d: str) -> dict:
        return per_day.setdefault(d, {"jobs": 0, "cr": 0, "rep": 0, "lost": 0, "renown": None})

    for path in sorted(log_dir.glob("cor3_*.log")):
        lost_seen: set[str] = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := JOB_DONE.match(line):
                day = day_of(m.group(1))
                day["jobs"] += 1
                day["cr"] += int(m.group(3))
                day["rep"] += int(m.group(4))
                if m.group(1) >= first_day:
                    types[m.group(2)] = types.get(m.group(2), 0) + 1
            elif (m := JOB_LOST.match(line)) and "WARNING" in line:
                key = line.split("en échec")[0][20:]            # un même job revient dans le résumé : une fois
                if key not in lost_seen:
                    lost_seen.add(key)
                    day_of(m.group(1))["lost"] += 1
            elif m := BALANCE.match(line):
                balance = (m.group(1), int(m.group(2)))
            elif m := RENOWN_LINE.match(line):
                now = (int(m.group(3)), int(m.group(4)), int(m.group(5)))
                day = day_of(m.group(2))
                day["renown"] = day["renown"] or 0
                if renown and (gain := renown_gain(renown[-1][1], now)):
                    day["renown"] += gain
                    if m.group(2) >= first_day:
                        source = "hors du bot" if m.group(6) == "ouverture" else m.group(6)
                        sources[source] = sources.get(source, 0) + gain
                renown.append((dt.datetime.fromisoformat(m.group(1)), now))

    def num(n: int, width: int) -> str:
        return f"{n:,}".replace(",", " ").rjust(width)

    def row(label: str, day: dict) -> str:
        lost = f"  ({day['lost']} perdu(s))" if day["lost"] else ""
        ren = "-" if day["renown"] is None else num(day["renown"], 0)
        return (f"  {label:<12} {day['jobs']:>3} jobs  {num(day['cr'], 8)} CR  {num(day['rep'], 7)} REP"
                f"  {ren:>5} Renommée{lost}")

    out = ["Bilan des jobs (d'après logs/cor3_*.log)", ""]
    week = {"jobs": 0, "cr": 0, "rep": 0, "lost": 0, "renown": None}
    for i in range(days):
        d = (today - dt.timedelta(days=i)).isoformat()
        day = per_day.get(d, {"jobs": 0, "cr": 0, "rep": 0, "lost": 0, "renown": None})
        for k in ("jobs", "cr", "rep", "lost"):
            week[k] += day[k]
        if day["renown"] is not None:
            week["renown"] = (week["renown"] or 0) + day["renown"]
        out.append(row("aujourd'hui" if i == 0 else "hier" if i == 1 else d, day))
    out.append(row(f"{days} jours", week))
    if types:
        out += ["", f"  Par type ({days} jours) : "
                + ", ".join(f"{k} {v}" for k, v in sorted(types.items(), key=lambda kv: -kv[1]))]
    out += ["", f"  Dernier solde vu : {num(balance[1], 0)} CR le {balance[0]}" if balance
            else "  Dernier solde vu : pas encore relevé (au prochain passage)"]
    if not renown:
        out.append("  Renommée : pas encore relevée (au prochain passage)")
        return "\n".join(out)
    when, (level, points, step) = renown[-1]
    out.append(f"  Renommée : niveau {level}, {points}/{step} le {when:%Y-%m-%d %H:%M}")
    if sources:
        out.append(f"  Renommée gagnée ({days} jours) : "
                   + ", ".join(f"{k} +{v}" for k, v in sorted(sources.items(), key=lambda kv: -kv[1])))
    if level < PILLAR_LEVEL:
        # Hypothèse (paliers vus : 800 au niveau 16, 900 au 17) : chaque palier grandit de 100.
        left = step - points + sum(step + 100 * k for k in range(1, PILLAR_LEVEL - level))
        line = f"  Niveau {PILLAR_LEVEL} : encore ≈ {num(left, 0)} points (si chaque palier grandit de 100)"
        recent = [r for r in renown if r[0].date().isoformat() >= first_day]
        elapsed = (when - recent[0][0]).total_seconds() / 86400 if recent else 0
        gained = sum(renown_gain(a, b) or 0 for (_, a), (_, b) in zip(recent, recent[1:]))
        if elapsed >= 0.5 and gained:
            rate = gained / elapsed
            line += f", soit ≈ {left / rate:.0f} jours à {rate:.0f} points par jour"
        out.append(line)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Automatisation quotidienne de cor3.gg")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--login", action="store_true", help="ouvrir le profil dédié pour se connecter")
    ap.add_argument("--dry-run", action="store_true", help="simulation : aucune validation")
    ap.add_argument("--only", action="append", choices=list(SECTION), help="limiter à une partie")
    ap.add_argument("--auto", action="store_true",
                    help="passage du quart d'heure : ne lance le navigateur que si quelque chose est dû")
    ap.add_argument("--headless", action="store_true", help="navigateur invisible")
    ap.add_argument("--test-notify", action="store_true", help="tester les alertes")
    ap.add_argument("--bilan", action="store_true", help="gains par jour (CR, REP, Renommée), sans ouvrir Chrome")
    ap.add_argument("--url", default=None, help=argparse.SUPPRESS)      # maquette de test
    args = ap.parse_args(argv)
    if args.url:
        global GAME_URL
        GAME_URL = args.url

    cfg = load_config(Path(args.config))
    gen = cfg["general"]
    log_dir = Path(gen.get("log_dir") or ROOT / "logs")
    if not log_dir.is_absolute():
        log_dir = ROOT / log_dir
    gen["log_dir"] = str(log_dir)
    if args.bilan:
        print(bilan(log_dir))
        return 0
    log = setup_logging(log_dir)
    notifier = Notifier(cfg["notify"], log)
    if args.test_notify:
        notifier.send("cor3_bot : test", "Si tu lis ceci, les alertes fonctionnent.", failure=True)
        return 0

    state_path = Path(gen.get("state_file") or "state.json")
    state = State(state_path if state_path.is_absolute() else ROOT / state_path)
    dry_run = args.dry_run or bool(gen.get("dry_run", False))
    lock = ROOT / "cor3_bot.lock"
    busy = lock_busy(lock)
    if args.auto and not args.login:
        parts, why = auto_decision(cfg, state, utc_now(), user_idle_seconds(), args.only)
        if not parts:
            (log.debug if why == "rien à faire" else log.info)("Mode auto : %s", why)
            warn_daily_if_late(cfg, state, notifier, why, utc_now())
            state.save()
            return 0
        if busy:
            log.info("Mode auto : une exécution est déjà en cours, je passe mon tour (%s)", why)
            return 0
        log.info("Mode auto : %s", why)
    else:
        parts = args.only or [p for p in SECTION if cfg[SECTION[p]].get("enabled", p in DEFAULT_ON)]
        if busy and not args.login:            # ex. passage horaire qui surveille une expédition
            log.info("Une autre exécution est en cours : j'attends qu'elle finisse (50 min max).")
            deadline = time.time() + 50 * 60
            while busy and time.time() < deadline:
                time.sleep(30)
                busy = lock_busy(lock)
            if busy:
                log.error("Une autre exécution est toujours en cours (%s). Supprime ce fichier si ce n'est pas le cas.", lock)
                notifier.send("cor3 : passage annulé", "Une autre exécution du bot ne s'est pas terminée.", failure=True)
                return 2
    if not args.login:
        lock.write_text(json.dumps({"pid": os.getpid(), "started": dt.datetime.now().isoformat(timespec="seconds"),
                                    "auto": args.auto}), encoding="utf-8")

    started = time.time()
    results: dict[str, tuple[str, str]] = {}
    exit_code = 0
    if not args.login:
        log.info("=== cor3_bot démarre (%s%s%s) ===", ", ".join(parts),
                 ", SIMULATION" if dry_run else "", ", auto" if args.auto else "")
    try:
        with sync_playwright() as pw:
            profile = Path(gen.get("profile_dir") or ROOT / "chrome-profile").expanduser()
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel=gen.get("channel", "chrome") or None,
                headless=args.headless or bool(gen.get("headless", False)),
                viewport={"width": 1600, "height": 900},
                args=["--mute-audio"],
            )
            ctx.set_default_timeout(20000)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if args.login:
                page.goto(GAME_URL)
                log.info("Connecte-toi à cor3.gg dans cette fenêtre, puis ferme-la.")
                page.wait_for_event("close", timeout=0)
                try:
                    ctx.close()
                except PWError:
                    pass
                return 0
            events = GameEvents(log)
            events.attach(page)
            bot = Bot(page, cfg, log, events, dry_run, state)
            bot.auto_mode = args.auto
            try:
                bot.open_game()
                bot.note_renown("ouverture")
                for part in parts:
                    try:
                        results[part] = ("OK", getattr(bot, f"run_{part}")())
                        log.info("[%s] OK : %s", part, results[part][1])
                    except (SessionLost, LoginRequired):
                        raise
                    except Exception as exc:
                        exit_code = 1
                        results[part] = ("ÉCHEC", f"{type(exc).__name__}: {exc}")
                        log.error("[%s] ÉCHEC : %s\n%s", part, exc, traceback.format_exc())
                        bot.snapshot(f"echec_{part}")
                        try:
                            bot.close_all_windows()
                        except Exception:
                            pass
                    bot.note_renown(f"après {part}")
            except (SessionLost, LoginRequired) as exc:
                exit_code = 2
                results["session"] = ("ÉCHEC", str(exc))
                log.error("Arrêt : %s", exc)
            finally:
                ctx.close()
    except Exception as exc:
        exit_code = 2
        results["navigateur"] = ("ÉCHEC", str(exc))
        log.error("Erreur fatale : %s\n%s", exc, traceback.format_exc())
    finally:
        if not args.login:
            lock.unlink(missing_ok=True)
            try:
                state.data["last_run"] = {"date": dt.datetime.now().isoformat(timespec="seconds"),
                                          "code": exit_code, "auto": args.auto, "parts": parts}
                state.save()
            except Exception as exc:
                log.warning("state.json non enregistré : %s", exc)

    duration = round(time.time() - started)
    summary = " | ".join(f"{k}: {s} ({m})" for k, (s, m) in results.items())
    log.info("=== Fin en %d s, code %d : %s ===", duration, exit_code, summary)
    with (log_dir / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": dt.datetime.now().isoformat(timespec="seconds"), "code": exit_code,
                             "dry_run": dry_run, "auto": args.auto, "duration_s": duration,
                             "results": results}, ensure_ascii=False) + "\n")
    daily_failed = results.get("daily", ("OK",))[0] != "OK" or "session" in results or "navigateur" in results
    if daily_failed and "daily" in parts:
        notifier.send("cor3 : la daily a échoué", summary, failure=True)
    elif exit_code:
        notifier.send("cor3 : exécution incomplète", summary, failure=True)
    else:
        notifier.send("cor3 : OK", summary, failure=False)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
