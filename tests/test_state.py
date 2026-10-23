"""Mémoire entre deux passages (state.json), mode --auto, verrou, petites règles des marchés."""
import datetime as dt
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cor3_bot  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def cfg(**sections):
    base = {"general": {}, "daily": {"enabled": True}, "jobs": {"enabled": True}, "markets": {},
            "mercenaries": {"enabled": True}, "notify": {}, "auto": {}}
    for key, value in sections.items():
        base[key] = {**base[key], **value}
    return base


def test_parse_time_accepts_game_formats():
    expected = dt.datetime(2026, 10, 21, 9, 38, 18, 462000, tzinfo=UTC)
    assert cor3_bot.parse_time("2026-10-21T09:38:18.462Z") == expected
    assert cor3_bot.parse_time(expected) == expected
    assert cor3_bot.parse_time(int(expected.timestamp() * 1000)) == expected
    assert cor3_bot.parse_time(None) is None and cor3_bot.parse_time("bientôt") is None


def test_market_timers(tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    assert state.market_due("SRM7-M", NOW)                      # jamais vu : à visiter
    state.set_market_reset("SRM7-M", "2026-10-21T19:00:00.000Z", now=NOW)
    assert not state.market_due("SRM7-M", NOW)
    assert state.market_due("SRM7-M", NOW + dt.timedelta(hours=10))
    state.set_market_reset("URM7-M", "2026-10-21T08:00:00.000Z", now=NOW)   # échu mais pas relancé
    assert not state.market_due("URM7-M", NOW + dt.timedelta(minutes=10))
    assert state.market_due("URM7-M", NOW + dt.timedelta(minutes=31))
    state.set_market_retry("D4RK RM7MI", NOW + dt.timedelta(hours=1), "maintenance", now=NOW)
    assert not state.market_due("D4RK RM7MI", NOW) and state.market_due("D4RK RM7MI", NOW + dt.timedelta(hours=1))
    state.set_market_reset("Home Server", None, now=NOW)        # pas de minuteur : revoir dans 6 h
    assert not state.market_due("Home Server", NOW + dt.timedelta(hours=5))
    assert state.market_due("Home Server", NOW + dt.timedelta(hours=6))
    state.save()
    again = cor3_bot.State(tmp_path / "state.json")
    assert again.data["markets"]["D4RK RM7MI"]["problem"] == "maintenance"
    assert not again.market_due("SRM7-M", NOW)


def test_daily_uses_utc_day(tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    late = dt.datetime(2026, 10, 21, 23, 30, tzinfo=UTC)          # 01:30 à Paris le 22
    state.mark_daily_done(late)
    assert state.daily_done_today(late)
    assert not state.daily_done_today(late + dt.timedelta(hours=1))


def test_due_and_auto_decision(tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    for name in cor3_bot.DEFAULT_MARKETS:
        state.set_market_reset(name, NOW + dt.timedelta(hours=4), now=NOW)
    config = cfg()
    assert set(state.due(config, NOW)) == {"daily"}
    state.mark_daily_done(NOW)
    assert state.due(config, NOW) == {}
    assert cor3_bot.auto_decision(config, state, NOW, idle_s=None) == ([], "rien à faire")
    later = NOW + dt.timedelta(hours=5)
    parts, why = cor3_bot.auto_decision(config, state, later, idle_s=3600)
    assert parts == ["jobs"] and "SRM7-M" in why
    assert cor3_bot.auto_decision(cfg(jobs={"enabled": False}), state, later, idle_s=None)[0] == ["markets"]
    parts, why = cor3_bot.auto_decision(config, state, later, idle_s=60)          # tu joues : on attend
    assert parts == [] and "PC utilisé" in why
    assert cor3_bot.auto_decision(cfg(auto={"skip_if_user_active_min": 0}), state, later, idle_s=60)[0] == ["jobs"]
    state.set_expedition(True)
    assert cor3_bot.auto_decision(config, state, NOW, idle_s=None)[0] == ["mercs"]
    assert cor3_bot.auto_decision(config, state, later, idle_s=None, only=["mercs"])[0] == ["mercs"]


def test_signed_job_brings_its_market_back(tmp_path):
    """Job signé pas terminé : --auto revient sur son marché à l'heure de reprise, sans attendre le minuteur."""
    state = cor3_bot.State(tmp_path / "state.json")
    for name in cor3_bot.DEFAULT_MARKETS:
        state.set_market_reset(name, NOW + dt.timedelta(hours=6), now=NOW)
    state.mark_daily_done(NOW)
    state.set_job_pending("job-1", "D4RK RM7MI", "Log Download", "RM7-E1L5 en maintenance",
                          NOW + dt.timedelta(hours=1), now=NOW)
    assert not state.market_due("D4RK RM7MI", NOW + dt.timedelta(minutes=59))
    assert state.market_due("D4RK RM7MI", NOW + dt.timedelta(hours=1))
    assert not state.market_due("SRM7-M", NOW + dt.timedelta(hours=1))
    parts, why = cor3_bot.auto_decision(cfg(), state, NOW + dt.timedelta(hours=1), idle_s=None)
    assert parts == ["jobs"] and "Log Download (D4RK RM7MI)" in why and "minuteur" not in why
    state.save()
    again = cor3_bot.State(tmp_path / "state.json")
    assert again.pending_jobs()["job-1"]["problem"] == "RM7-E1L5 en maintenance"
    again.prune_pending("D4RK RM7MI", {"job-1"})                # toujours signé : on le garde
    assert again.market_due("D4RK RM7MI", NOW + dt.timedelta(hours=1))
    again.prune_pending("D4RK RM7MI", set())                    # plus signé : terminé à la main ou expiré
    assert not again.market_due("D4RK RM7MI", NOW + dt.timedelta(hours=1))
    # échec à hh:15:34 (comme le passage de 02:15) : repris par la tâche horaire de hh:45, pas une heure après
    failed_at, next_run = NOW + dt.timedelta(minutes=15, seconds=34), NOW + dt.timedelta(minutes=45, seconds=2)
    again.set_job_pending("job-2", "URM7-M", "Data Download", "échec", now=failed_at)
    assert not again.market_due("URM7-M", failed_at + dt.timedelta(minutes=14))
    assert again.market_due("URM7-M", next_run)
    again.clear_job_pending("job-2")
    assert not again.market_due("URM7-M", next_run)
    # le marché lui-même en maintenance prime sur le job à reprendre (pas de passage inutile)
    state.set_market_retry("D4RK RM7MI", NOW + dt.timedelta(hours=2), "D4RK RM7MI en maintenance", now=NOW)
    assert not state.market_due("D4RK RM7MI", NOW + dt.timedelta(hours=1))
    assert state.market_due("D4RK RM7MI", NOW + dt.timedelta(hours=2))


def test_in_hours():
    at = lambda h: dt.datetime(2026, 10, 21, h, 30)  # noqa: E731
    assert cor3_bot.in_hours([0, 24], at(3))
    assert cor3_bot.in_hours([8, 23], at(8)) and not cor3_bot.in_hours([8, 23], at(23))
    assert cor3_bot.in_hours([22, 6], at(23)) and cor3_bot.in_hours([22, 6], at(2))
    assert not cor3_bot.in_hours([22, 6], at(12))


def test_daily_warning_once_per_day(tmp_path):
    sent = []

    class FakeNotifier:
        def send(self, title, message, failure=True):
            sent.append(title)

    state = cor3_bot.State(tmp_path / "state.json")
    evening = dt.datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
    cor3_bot.warn_daily_if_late(cfg(), state, FakeNotifier(), "PC utilisé", NOW)       # trop tôt
    cor3_bot.warn_daily_if_late(cfg(), state, FakeNotifier(), "PC utilisé", evening)
    cor3_bot.warn_daily_if_late(cfg(), state, FakeNotifier(), "PC utilisé", evening + dt.timedelta(minutes=30))
    assert sent == ["cor3 : daily pas encore faite"]


def test_lock_ignores_dead_process(tmp_path):
    lock = tmp_path / "cor3_bot.lock"
    assert not cor3_bot.lock_busy(lock)
    lock.write_text(json.dumps({"pid": os.getpid()}))
    assert cor3_bot.lock_busy(lock)
    lock.write_text(json.dumps({"pid": 2 ** 22 + 12345}))        # processus tué : verrou périmé
    assert not cor3_bot.lock_busy(lock)
    lock.write_text("2026-10-21 02:15:00")                        # ancien format : on se fie à l'âge
    assert cor3_bot.lock_busy(lock)


def make_bot(jobs=None):
    config = cfg(jobs=jobs or {})
    log = logging.getLogger("t")
    bot = cor3_bot.Bot(None, config, log, cor3_bot.GameEvents(log), False)
    bot.events._merge_map({"kind": "snapshot", "state": {"servers": [{"id": "w3l1", "serverName": "RM7-W3L1"}]}})
    return bot


def test_hack_presets_by_server_type():
    bot = make_bot({"hack_presets": {"SOYUZ": "hack soyuz", "USOL public": "hack t4", "default": "hack"}})
    assert bot.hack_preset_for("SOYUZ public") == "hack soyuz"
    assert bot.hack_preset_for("USOL public") == "hack t4"
    assert bot.hack_preset_for("CEDRT private") == "hack"
    assert bot.hack_preset_for(None) == "hack"
    assert make_bot().hack_preset_for("SOYUZ public") is None


def test_job_access_condition_and_allow_hack():
    """Conditions d'accès relevées en v1.25.71 (GetServerAccess / GrantServerAccess)."""
    acquisition = {"name": "Log Download", "jobType": "DownloadLog", "relatedServers": [{"serverName": "RM7-W3L1"}],
                   "conditions": {"items": [
                       {"type": "GetServerAccess", "label": "Access Acquisition", "isPrimary": False, "details": None},
                       {"type": "DownloadLog", "label": "Log Download", "isPrimary": True, "details": {"logSeqs": [3]}}]}}
    grant = {**acquisition, "conditions": {"items": [{"type": "GrantServerAccess", "label": "Access Grant"}]}}
    assert cor3_bot.Bot.job_access(acquisition) == "acquisition"
    assert cor3_bot.Bot.job_access(grant) == "grant"
    assert cor3_bot.Bot.job_access({"conditions": {"items": [{"type": "AccessAcquisition"}]}}) == "acquisition"
    assert cor3_bot.Bot.job_details(acquisition) == {"logSeqs": [3]}       # la condition d'accès n'a pas de détails
    bot = make_bot()
    bot.events.servers["w3l1"]["accessType"] = "regular"                  # serveur déjà piraté
    assert bot.job_refusal_reason(acquisition) is None
    assert "allow_hack" in make_bot({"allow_hack": False}).job_refusal_reason(acquisition)
    assert make_bot({"allow_hack": False}).job_refusal_reason(grant) is None


def test_server_in_maintenance_from_map_events():
    bot = make_bot()
    bot.events._merge_map({"kind": "snapshot", "state": {"servers": [
        {"id": "a", "serverName": "RM7-E1L5", "isInMaintenance": False},
        {"id": "b", "serverName": "SRM7-M", "isEndpoint": True}]}})
    job = {"name": "Log Download", "jobType": "DownloadLog", "relatedServers": [{"serverName": "RM7-E1L5"}],
           "conditions": {"items": [{"type": "GrantServerAccess", "label": "Access Grant", "isPrimary": False}]}}
    assert bot.job_refusal_reason(job) is None
    bot.events._merge_map({"kind": "delta", "state": {"servers": [
        {"id": "a", "isInMaintenance": True, "maintenanceEndsAt": "2026-10-21T10:00:00.000Z"}]}})
    assert "maintenance" in bot.job_refusal_reason(job)
    assert bot.events.endpoint() == "SRM7-M"


def test_bilan_reads_the_daily_logs(tmp_path):
    (tmp_path / "cor3_2026-10-22.log").write_text("\n".join([
        "2026-10-22 03:25:21 INFO    Job terminé : Log Download sur Home Server (+1445 CR, +261 REP)",
        "2026-10-22 04:12:22 INFO    Job terminé : File Decryption sur Home Server (+838 CR, +151 REP)",
        "2026-10-22 04:19:20 WARNING Job File Decryption (SRM7-M) en échec : pas de bouton Déchiffrer : job perdu",
        "2026-10-22 04:20:43 ERROR   [jobs] ÉCHEC : ... File Decryption (SRM7-M) : job perdu",
        "2026-10-22 05:00:00 INFO    Solde : 39540 CR"]), encoding="utf-8")
    (tmp_path / "cor3_2026-10-23.log").write_text(
        "2026-10-23 02:16:00 INFO    Job terminé : IP Cleanup sur Home Server (+1274 CR, +230 REP)\n", encoding="utf-8")
    text = cor3_bot.bilan(tmp_path, today=dt.date(2026, 10, 23))
    assert "aujourd'hui    1 jobs     1 274 CR      230 REP      - Renommée" in text
    assert "hier           2 jobs     2 283 CR      412 REP      - Renommée  (1 perdu(s))" in text
    assert "7 jours        3 jobs     3 557 CR      642 REP      - Renommée  (1 perdu(s))" in text
    assert "Dernier solde vu : 39 540 CR le 2026-10-22 05:00:00" in text
    assert "Renommée : pas encore relevée" in text


def test_bilan_follows_the_renown(tmp_path):
    """Renommée lue à l'ouverture et après chaque partie : gains par jour, par source, et niveau 20 estimé."""
    (tmp_path / "cor3_2026-10-22.log").write_text("\n".join([
        "2026-10-22 02:15:10 INFO    Renommée : niveau 16, 694/800 (ouverture)",
        "2026-10-22 02:15:30 INFO    Renommée : niveau 16, 694/800 (après daily)",
        "2026-10-22 05:20:00 INFO    Renommée : niveau 16, 790/800 (après jobs, +96)",
        "2026-10-22 11:45:00 INFO    Renommée : niveau 16, 795/800 (ouverture, +5)"]), encoding="utf-8")
    (tmp_path / "cor3_2026-10-23.log").write_text("\n".join([
        "2026-10-23 02:15:00 INFO    Renommée : niveau 17, 50/900 (après jobs, +55)",
        "2026-10-23 02:16:00 INFO    Renommée : niveau 17, 56/900 (après mercs, +6)"]), encoding="utf-8")
    text = cor3_bot.bilan(tmp_path, today=dt.date(2026, 10, 23))
    assert "aujourd'hui    0 jobs         0 CR        0 REP     61 Renommée" in text
    assert "hier           0 jobs         0 CR        0 REP    101 Renommée" in text
    assert "7 jours        0 jobs         0 CR        0 REP    162 Renommée" in text
    assert "Renommée : niveau 17, 56/900 le 2026-10-23 02:16" in text
    assert "Renommée gagnée (7 jours) : jobs +151, mercs +6, hors du bot +5" in text
    # 844 pour finir le 17, puis 1 000 et 1 100 : 2 944 ; 162 points en un jour
    assert "Niveau 20 : encore ≈ 2 944 points (si chaque palier grandit de 100), soit ≈ 18 jours" in text


def test_renown_gain_across_a_level():
    assert cor3_bot.renown_gain((17, 100, 900), (17, 133, 900)) == 33
    assert cor3_bot.renown_gain((16, 769, 800), (17, 5, 900)) == 36
    assert cor3_bot.renown_gain((16, 769, 800), (18, 5, 1000)) is None


def test_ws_dump_refuses_while_the_bot_runs(tmp_path, monkeypatch):
    """ws_dump ouvre le même profil Chrome : jamais pendant un passage du bot (verrou cor3_bot.lock)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import ws_dump
    monkeypatch.setattr(cor3_bot, "ROOT", tmp_path)
    (tmp_path / "config.toml").write_text(f'[general]\nlog_dir = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    lock = tmp_path / "cor3_bot.lock"
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert ws_dump.main(["--config", str(tmp_path / "config.toml")]) == 2
    assert lock.exists()                                    # le verrou du bot n'est pas touché
