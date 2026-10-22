"""Tests d'intégration Playwright contre une maquette locale du jeu.

La maquette reproduit les attributs et classes relevés sur cor3.gg (octobre 2026) :
elle vérifie que les sélecteurs, scripts injectés et enchaînements du bot tiennent
debout, sans toucher au vrai site. Lancement : python -m pytest tests -q
"""
import asyncio
import datetime as dt
import http.server
import json
import logging
import os
import sys
import threading
import urllib.parse
from pathlib import Path

import msgpack
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cor3_bot  # noqa: E402

MOCK_DIR = Path(__file__).parent / "mock_game"
SIGNAL_ID = "019aea65-b0c2-70e9-bbbc-8caed416854c"
LOG_ID = "019aaaaa-0000-7000-8000-00000000log1"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(MOCK_DIR), **kw)

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/user-daily-claim":
            q = urllib.parse.parse_qs(url.query)
            game = q.get("game", ["signal"])[0]
            self._json(200, {"currentGameId": SIGNAL_ID if game == "signal" else LOG_ID,
                             "hasClaimedToday": False, "currentStreak": 1,
                             "difficulty": q.get("difficulty", ["easy"])[0],
                             "nextTaskTime": "2026-10-22T00:00:00.000Z"})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/user-daily-claim"):
            self._json(201, {"hasClaimedToday": True, "currentStreak": 2, "nextTaskTime": "2026-10-22T00:00:00.000Z"})
        else:
            self._json(404, {})


@pytest.fixture(scope="module")
def servers():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    import websockets
    loop = asyncio.new_event_loop()
    connected = msgpack.packb({"type": 2, "nsp": "/", "data": ["connected", {
        "message": "Connection established", "frontendVersion": "v1.25.51"}]})
    state = {}

    async def handler(ws):
        await ws.send(connected)
        async for msg in ws:          # la page envoie {room, action, data} en JSON : on le renvoie en msgpack
            if not isinstance(msg, str):
                continue
            try:
                req = json.loads(msg)
            except ValueError:
                continue
            await ws.send(msgpack.packb({"type": 2, "nsp": "/", "data": [
                req["room"], {"event": {"name": req["room"], "action": req["action"]}, "data": req.get("data")}]}))

    async def start():
        server = await websockets.serve(handler, "127.0.0.1", 0)
        state["port"] = server.sockets[0].getsockname()[1]
        state["server"] = server

    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(start(), loop).result(10)
    yield httpd.server_address[1], state["port"]
    httpd.shutdown()
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture()
def make_bot(servers, tmp_path):
    from playwright.sync_api import sync_playwright
    http_port, ws_port = servers
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    log = logging.getLogger("cor3-test")
    log.setLevel(logging.DEBUG)

    def _make(game="signal", difficulty="easy", dry_run=False, extra="", jobs=None, state=None):
        cor3_bot.GAME_URL = (f"http://127.0.0.1:{http_port}/index.html?game={game}"
                             f"&difficulty={difficulty}&ws={ws_port}{extra}")
        cfg = {"general": {"log_dir": str(tmp_path)}, "daily": {}, "jobs": jobs or {}, "markets": {},
               "mercenaries": {}, "notify": {}, "auto": {}}
        events = cor3_bot.GameEvents(log)
        events.attach(page)
        bot = cor3_bot.Bot(page, cfg, log, events, dry_run, state)
        bot.open_game()
        return bot

    yield _make
    browser.close()
    pw.stop()


@pytest.mark.parametrize("enc", ["morse", "binary"])
@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_signal_decode(make_bot, enc, difficulty):
    bot = make_bot("signal", difficulty, extra=f"&enc={enc}")
    result = bot.run_daily()
    assert "Signal Decode réussi" in result
    mock = bot.page.evaluate("window.__mock") if bot.page.locator(cor3_bot.WINDOW).count() else None
    assert mock is None or mock["mismatches"] == 0


def test_signal_decode_dry_run_does_not_submit(make_bot):
    bot = make_bot("signal", "medium", dry_run=True)
    result = bot.run_daily()
    assert result.startswith("simulation")
    assert bot.page.evaluate("window.__mock.claims") == 0


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
def test_log_integrity(make_bot, difficulty):
    bot = make_bot("logs", difficulty)
    result = bot.run_daily()
    assert result == "System Log Integrity réussi"
    assert bot.page.evaluate("window.__mock.claims") == 1
    assert bot.page.evaluate("window.__mock.wrongModule") == 0      # pas la carte Tower Defense


def test_ice_wall_single_anchor_clicks(make_bot):
    bot = make_bot("signal")
    bot.page.evaluate("openIce()")
    bot.solve_ice_wall(timeout_s=60)
    mock = bot.page.evaluate("window.__mock")
    assert mock["iceDone"] == 3, mock["logs"]
    assert mock["mismatches"] == 0, mock["logs"]


def test_ws_decoding_and_session_loss(make_bot):
    bot = make_bot("signal")
    assert bot.events.frontend_version == "v1.25.51"
    bot.events._on_frame("in", msgpack.packb({"type": 2, "nsp": "/", "data": ["session-replaced", {"message": "x"}]}))
    with pytest.raises(cor3_bot.SessionLost):
        bot.check_session()


def test_ws_frames_are_logged_without_their_content(caplog):
    """Journal DEBUG : le nom de chaque trame (salle:action), pour diagnostiquer une nouvelle version du jeu,
    mais jamais son contenu."""
    log = logging.getLogger("cor3-test")
    caplog.set_level(logging.DEBUG, logger="cor3-test")
    events = cor3_bot.GameEvents(log)
    jwt = "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2"
    events._on_frame("out", msgpack.packb({"type": 2, "nsp": "/", "data": [
        "expeditions", {"event": {"name": "expeditions", "action": "get.active"}, "data": {"token": jwt}}]}))
    events._on_frame("in", msgpack.packb({"type": 2, "nsp": "/", "data": [
        "connected", {"message": "Connection established", "frontendVersion": "v1.25.71"}]}))
    assert "WS → expeditions:get.active" in caplog.text
    assert "WS ← connected" in caplog.text
    assert jwt not in caplog.text and "v1.25.71" not in caplog.text


# ---------------------------------------------------------------------------
# Carte du réseau, 4 marchés, maintenance, connexion SAI et piratage
# ---------------------------------------------------------------------------
def mock(bot):
    return bot.page.evaluate("window.__mock")


def test_markets_refresh_skips_maintenance(make_bot, tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    bot = make_bot(extra="&maint=SRM7-M", state=state)
    summary = bot.run_markets()
    m = mock(bot)
    assert m["contracts"] == ["m-home", "m-d4rk", "m-urm7"], summary
    assert "SRM7-M en maintenance" in summary
    assert m["endpoint"] == "RM7-S4L1"                      # point de sortie du joueur remis
    for name in ("Home Server", "D4RK RM7MI", "URM7-M"):
        assert not state.market_due(name)
    assert not state.market_due("SRM7-M")                   # on attend la fin de la maintenance
    assert state.market_due("SRM7-M", cor3_bot.utc_now() + dt.timedelta(hours=1, minutes=5))
    assert state.market_due("URM7-M", cor3_bot.utc_now() + dt.timedelta(hours=8, minutes=1))
    assert not state.market_due("URM7-M", cor3_bot.utc_now() + dt.timedelta(hours=7))
    assert bot.run_markets() == "aucun minuteur de marché échu"
    state.save()
    assert cor3_bot.State(tmp_path / "state.json").data["markets"]["SRM7-M"]["problem"]


def test_run_jobs_visits_the_four_markets(make_bot, tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    bot = make_bot(state=state)
    summary = bot.run_jobs()
    assert summary.startswith("0 job(s) terminé(s)")
    assert "Home Server, D4RK RM7MI, SRM7-M, URM7-M" in summary
    assert mock(bot)["contracts"] == ["m-home", "m-d4rk", "m-srm7", "m-urm7"]
    assert mock(bot)["endpoint"] == "RM7-S4L1"
    bot.auto_mode = True                                     # --auto : rien n'est échu
    assert bot.run_jobs() == "aucun minuteur de marché échu"


def test_jobs_reload_when_contracts_tab_is_already_open(make_bot):
    """v1.25.71 : recliquer l'onglet Contrats déjà ouvert ne redemande rien au serveur ; le bot passe par
    l'onglet Marché pour recharger la liste (fin d'un job sans avoir fermé le marché)."""
    bot = make_bot()
    bot.load_jobs(bot.open_market("Home Server"))
    bot.load_jobs(bot.open_market("Home Server"))           # même fenêtre, onglet déjà ouvert
    assert mock(bot)["contracts"] == ["m-home", "m-home"]


@pytest.mark.parametrize("hack", ["auto", "ice"])
def test_open_sai_hacks_again_when_access_is_lost(make_bot, hack):
    bot = make_bot(extra=f"&hack={hack}")
    sai = bot.open_sai("RM7-E1L5")
    assert sai.count() == 1
    m = mock(bot)
    assert m["sai"] == 1 and m["forceHack"] == 0
    assert m["hackClicks"] == 2 and m["lastTool"] == "PORTER-LITE"   # outil le plus puissant
    if hack == "ice":
        assert m["iceDone"] == 3 and m["mismatches"] == 0


def test_open_sai_prefers_task_access(make_bot):
    bot = make_bot(extra="&task=RM7-E1L5")
    bot.open_sai("RM7-E1L5")
    assert mock(bot)["hackClicks"] == 0 and mock(bot)["sai"] == 1


def test_open_sai_refusals(make_bot):
    bot = make_bot(extra="&hack=none")
    with pytest.raises(cor3_bot.StepFailed, match="aucun outil"):
        bot.open_sai("RM7-E1L5")
    bot = make_bot(jobs={"allow_hack": False})
    with pytest.raises(cor3_bot.StepFailed, match="allow_hack"):
        bot.open_sai("RM7-E1L5")
    bot = make_bot(dry_run=True)
    with pytest.raises(cor3_bot.Simulated):
        bot.open_sai("RM7-E1L5")
    assert mock(bot)["hackClicks"] == 0 and mock(bot)["forceHack"] == 0


def test_server_in_maintenance(make_bot):
    bot = make_bot(extra="&maint=RM7-E1L5")
    with pytest.raises(cor3_bot.Unavailable, match="maintenance"):
        bot.open_sai("RM7-E1L5")
    job = {"name": "Log Download", "jobType": "DownloadLog", "relatedServers": [{"serverName": "RM7-E1L5"}]}
    assert "maintenance" in bot.job_refusal_reason(job)


def test_ws_tap_snippet(make_bot):
    """tools/ws_tap.js : crochet WebSocket + décodeur msgpack + masquage, dans un vrai navigateur."""
    bot = make_bot()
    snippet = (Path(__file__).parent.parent / "tools" / "ws_tap.js").read_text(encoding="utf-8")
    assert "installée" in bot.page.evaluate(snippet)
    bot.page.evaluate("openMap()")
    bot.page.wait_for_function("() => window.__cor3ws.last('network-map', 'get.map')")
    assert len(bot.page.evaluate("window.__cor3ws.last('network-map', 'get.map').data.state.servers")) == 6
    jwt = "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2"
    sample = {"a": [1, -5, -200, 70000, 2 ** 40, 1.5, None, True, False, "é" * 40, b"\x00\x01"],
              "b": {"accessToken": jwt, "author": "Chuck"}, "big": list(range(20)),
              "t32": msgpack.Timestamp(1792500000, 0), "t64": msgpack.Timestamp(1792500000, 500_000_000),
              "text": f"jeton {jwt} fin"}
    got = bot.page.evaluate("b => window.__cor3ws.scrub(window.__cor3ws.decode(new Uint8Array(b).buffer))",
                            list(msgpack.packb(sample)))
    assert got["a"] == [1, -5, -200, 70000, 2 ** 40, 1.5, None, True, False, "é" * 40, "<2 octets>"]
    assert got["b"] == {"accessToken": "<masqué>", "author": "Chuck"}
    assert got["big"] == list(range(20))
    assert got["t32"] == "2026-10-20T12:40:00.000Z" and got["t64"] == "2026-10-20T12:40:00.500Z"
    assert got["text"] == "jeton <jwt masqué> fin"


def test_job_on_remote_market_end_to_end(make_bot, tmp_path):
    """D4RK RM7MI : signer un Log Download, se connecter à RM7-E1L5 (accès par tâche), télécharger le bon
    log parmi deux homonymes, revenir au marché distant et terminer le job, puis remettre le point de sortie."""
    bot = make_bot(extra="&jobs=1", state=cor3_bot.State(tmp_path / "state.json"))
    summary = bot.run_jobs()
    m = mock(bot)
    assert summary.startswith("1 job(s) terminé(s) : Log Download (D4RK RM7MI)"), summary
    assert m["downloads"] == [24] and m["completed"] == ["job-1"], m
    assert m["mismatches"] == 0 and m["deletes"] == 0 and m["hackClicks"] == 0
    assert m["endpoint"] == "RM7-S4L1"


def test_mercs_without_expedition(make_bot, tmp_path, caplog):
    """v1.25.71 : la liste (vide) arrive à la connexion, rien à l'ouverture de la fenêtre : on prend cette trame."""
    caplog.set_level(logging.DEBUG, logger="cor3-test")
    state = cor3_bot.State(tmp_path / "state.json")
    state.set_expedition(True)                              # passage précédent : expédition en cours
    bot = make_bot(state=state)
    assert bot.run_mercs() == "aucune expédition en cours"
    assert state.data["expedition_active"] is False         # --auto ne relancera pas mercs pour rien
    assert "lu à l'écran" not in caplog.text                # la trame de la connexion a suffi


def test_active_expedition_known_from_connection(make_bot):
    """v1.25.71 : une expédition en cours n'est annoncée qu'à la connexion (expeditions:get.active)."""
    bot = make_bot(extra="&exp=running")
    assert [m["status"] for m in bot.active_expeditions()] == ["RUNNING"]


def test_mercs_without_any_frame_trusts_the_empty_screen(make_bot):
    """Aucune trame get.active du tout : l'écran « Il n'y a aucune expédition active » fait foi."""
    bot = make_bot(extra="&exp=nopush")
    assert bot.run_mercs() == "aucune expédition en cours"


def test_job_on_unreachable_server_is_not_signed(make_bot):
    """Route coupée vers le serveur du job (un nœud en maintenance) : on ne signe pas."""
    bot = make_bot(extra="&jobs=1&cut=RM7-E1L5")
    summary = bot.run_jobs()
    assert summary.startswith("0 job(s) terminé(s)"), summary
    assert bot.page.evaluate("JOBS['m-d4rk'][0].status") == "AVAILABLE"


def test_job_needing_a_hack_is_not_signed(make_bot):
    """« Access Acquisition » sur un serveur sans accès : il faudrait pirater, on ne signe pas (par défaut)."""
    bot = make_bot(extra="&jobs=1&access=acq")
    summary = bot.run_jobs()
    assert summary.startswith("0 job(s) terminé(s)"), summary
    assert bot.page.evaluate("JOBS['m-d4rk'][0].status") == "AVAILABLE" and mock(bot)["hackClicks"] == 0


def test_job_on_a_server_already_hacked_is_done(make_bot):
    """« Access Acquisition » sur un serveur déjà piraté : on signe, l'accès est réutilisé, rien n'est repiraté."""
    bot = make_bot(extra="&jobs=1&access=acq&held=RM7-E1L5")
    summary = bot.run_jobs()
    assert summary.startswith("1 job(s) terminé(s) : Log Download (D4RK RM7MI)"), summary
    m = mock(bot)
    assert m["completed"] == ["job-1"] and m["downloads"] == [24] and m["hackClicks"] == 1 and m["forceHack"] == 0


def test_job_needing_a_hack_signed_when_allowed(make_bot):
    """sign_without_access = true : on signe quand même, puis on pirate (ici sans mini-jeu)."""
    bot = make_bot(extra="&jobs=1&access=acq", jobs={"sign_without_access": True})
    summary = bot.run_jobs()
    assert summary.startswith("1 job(s) terminé(s) : Log Download (D4RK RM7MI)"), summary
    assert mock(bot)["completed"] == ["job-1"] and mock(bot)["hackClicks"] >= 1


def test_signed_job_blocked_by_maintenance_waits_for_its_end(make_bot, tmp_path):
    """Job déjà signé, serveur en maintenance : on ne le tente pas, on y revient à la fin de la maintenance."""
    state = cor3_bot.State(tmp_path / "state.json")
    bot = make_bot(extra="&jobs=taken&maint=RM7-E1L5", state=state)
    summary = bot.run_jobs()
    assert "laissé de côté" in summary and "maintenance" in summary, summary
    now = cor3_bot.utc_now()
    retry = cor3_bot.parse_time(state.pending_jobs()["job-1"]["retry_at"])
    assert now + dt.timedelta(minutes=55) < retry < now + dt.timedelta(minutes=70)     # fin (1 h) + 2 min
    assert not state.market_due("D4RK RM7MI", now + dt.timedelta(minutes=30))        # minuteur : 6 h
    assert state.market_due("D4RK RM7MI", now + dt.timedelta(minutes=70))
    assert mock(bot)["sai"] == 0 and mock(bot)["hackClicks"] == 0


def test_job_failing_after_signature_is_retried_soon(make_bot, tmp_path):
    """Signé, puis connexion impossible (pas d'accès par tâche, aucun outil) : alerte, et reprise au passage suivant."""
    state = cor3_bot.State(tmp_path / "state.json")
    bot = make_bot(extra="&jobs=1&grant=0&hack=none", state=state)
    with pytest.raises(cor3_bot.StepFailed, match="aucun outil") as failure:
        bot.run_jobs()
    assert "à finir avant" in str(failure.value)                          # l'alerte donne l'échéance
    assert state.pending_jobs()["job-1"]["market"] == "D4RK RM7MI"
    assert cor3_bot.parse_time(state.pending_jobs()["job-1"]["deadline"])
    assert state.market_due("D4RK RM7MI", cor3_bot.utc_now() + dt.timedelta(minutes=31))


def test_next_job_is_found_after_a_failure(make_bot, tmp_path):
    """Un job échoue au terminal (le marché a été fermé) : le suivant, d'un autre type, est quand même signé
    (le marché rouvert s'affiche sur l'onglet Marché, il faut revenir sur Contrats)."""
    bot = make_bot(extra="&jobs=2&grant=0&hack=none", state=cor3_bot.State(tmp_path / "state.json"))
    with pytest.raises(cor3_bot.StepFailed) as failure:
        bot.run_jobs()
    assert "carte introuvable" not in str(failure.value), failure.value
    assert bot.page.evaluate("JOBS['m-d4rk'].map(j => j.status)") == ["TAKEN", "TAKEN"]


def test_game_reopened_on_the_loadout_screen(make_bot):
    """Passage interrompu dans le Loadout : le jeu se rouvre sur cet écran ; le bot le rallume et en sort."""
    bot = make_bot(extra="&screen=loadout")
    assert bot.page.locator('[data-onboarding-300-id="TabBarItem-NETWORK_MAP"]').is_visible()
    assert bot.page.evaluate("localStorage.getItem('loadout-powered')") == "true"


def test_sai_tab_click_without_answer_is_retried(make_bot, tmp_path):
    """Le premier clic sur JOURNAUX n'envoie rien (22/10, IP Cleanup) : le bot passe par Résumé et revient."""
    bot = make_bot(extra="&jobs=1&lazytab=1", state=cor3_bot.State(tmp_path / "state.json"))
    summary = bot.run_jobs()
    assert summary.startswith("1 job(s) terminé(s) : Log Download (D4RK RM7MI)"), summary
    assert mock(bot)["downloads"] == [24]


def test_signed_job_is_finished_and_forgotten(make_bot, tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    state.set_job_pending("job-1", "D4RK RM7MI", "Log Download", "RM7-E1L5 en maintenance")
    bot = make_bot(extra="&jobs=taken", state=state)
    summary = bot.run_jobs()
    assert summary.startswith("1 job(s) terminé(s) : Log Download (D4RK RM7MI)"), summary
    assert state.pending_jobs() == {}


def test_job_dry_run_signs_nothing(make_bot):
    bot = make_bot(extra="&jobs=1", dry_run=True)
    summary = bot.run_jobs()
    m = mock(bot)
    assert summary.startswith("0 job(s) terminé(s)"), summary
    assert m["completed"] == [] and m["downloads"] == []
    assert bot.page.evaluate("JOBS['m-d4rk'][0].status") == "AVAILABLE"
