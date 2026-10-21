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
    return cor3_bot.Bot(None, config, log, cor3_bot.GameEvents(log), False)


def test_hack_presets_by_server_type():
    bot = make_bot({"hack_presets": {"SOYUZ": "hack soyuz", "USOL public": "hack t4", "default": "hack"}})
    assert bot.hack_preset_for("SOYUZ public") == "hack soyuz"
    assert bot.hack_preset_for("USOL public") == "hack t4"
    assert bot.hack_preset_for("CEDRT private") == "hack"
    assert bot.hack_preset_for(None) == "hack"
    assert make_bot().hack_preset_for("SOYUZ public") is None


def test_job_access_condition_and_allow_hack():
    acquisition = {"name": "Log Download", "jobType": "DownloadLog", "relatedServers": [{"serverName": "RM7-W3L1"}],
                   "conditions": {"items": [{"type": "AccessAcquisition", "details": {"logSeqs": [3]}}]}}
    grant = {**acquisition, "conditions": {"items": [{"type": "AccessGrant"}]}}
    assert cor3_bot.Bot.job_access(acquisition) == "acquisition"
    assert cor3_bot.Bot.job_access(grant) == "grant"
    assert make_bot().job_refusal_reason(acquisition) is None
    assert "allow_hack" in make_bot({"allow_hack": False}).job_refusal_reason(acquisition)
    assert make_bot({"allow_hack": False}).job_refusal_reason(grant) is None


def test_server_in_maintenance_from_map_events():
    bot = make_bot()
    bot.events._merge_map({"kind": "snapshot", "state": {"servers": [
        {"id": "a", "serverName": "RM7-E1L5", "isInMaintenance": False},
        {"id": "b", "serverName": "SRM7-M", "isEndpoint": True}]}})
    job = {"name": "Log Download", "jobType": "DownloadLog", "relatedServers": [{"serverName": "RM7-E1L5"}]}
    assert bot.job_refusal_reason(job) is None
    bot.events._merge_map({"kind": "delta", "state": {"servers": [
        {"id": "a", "isInMaintenance": True, "maintenanceEndsAt": "2026-10-21T10:00:00.000Z"}]}})
    assert "maintenance" in bot.job_refusal_reason(job)
    assert bot.events.endpoint() == "SRM7-M"
