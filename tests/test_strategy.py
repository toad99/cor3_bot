"""Politique de signature des jobs : ordre de passage, fusible, ICE WALL bloqué, retour sur le marché."""
import datetime as dt
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cor3_bot  # noqa: E402

ALL_TYPES = ["DecryptFile", "DownloadLog", "DownloadFile", "DecryptDownloadedFile", "UploadFile"]


def job(job_id, job_type, reward, server=None, access="GetServerAccess", deposit=0):
    items = [{"type": job_type, "label": job_type, "isPrimary": True}]
    if server:
        items.insert(0, {"type": access, "label": access, "isPrimary": False})
    return {"id": job_id, "name": job_id, "jobType": job_type, "rewardCredits": reward, "deposit": deposit,
            "reputationPenalty": 0, "relatedServers": [{"serverName": server}] if server else [],
            "conditions": {"items": items}}


JOBS = [job("decrypt-1", "DecryptFile", 1740), job("decrypt-2", "DecryptFile", 838, deposit=126),
        job("log-hack", "DownloadLog", 1600, server="RM7-B"),          # serveur jamais piraté
        job("log-ok", "DownloadLog", 856, server="RM7-A", deposit=129),
        job("data-ok", "DownloadFile", 1445, server="RM7-A", deposit=506)]


def make_bot(tmp_path, fail=None):
    cfg = {"general": {}, "daily": {}, "markets": {}, "mercenaries": {}, "notify": {}, "auto": {},
           "jobs": {"types": ALL_TYPES, "max_deposit_cr": 600, "max_rep_penalty": 300, "max_jobs_per_run": 20,
                    "sign_without_access": True}}
    log = logging.getLogger("t")
    state = cor3_bot.State(tmp_path / "state.json")
    bot = cor3_bot.Bot(None, cfg, log, cor3_bot.GameEvents(log), False, state)
    bot.events._merge_map({"kind": "snapshot", "state": {"servers": [
        {"id": "a", "serverName": "RM7-A", "accessType": "regular", "canSetEndpoint": True},
        {"id": "b", "serverName": "RM7-B", "accessType": "none", "canSetEndpoint": True}]}})
    signed, performed = [], []

    def perform(j):
        performed.append(j["id"])
        if fail and j["id"] in fail:
            raise fail[j["id"]]

    for name in ("ensure_connected", "close_all_windows", "cleanup", "complete_job", "snapshot"):
        setattr(bot, name, lambda *a, **k: None)
    bot.open_market = lambda name=None: None
    bot.visit_market = lambda name, notes, failed: ({"jobs": [dict(j) for j in JOBS]} if name == "Home Server"
                                                    else {"jobs": []})
    bot.sign_job = lambda market, j: signed.append(j["id"]) or 0
    bot.load_jobs = lambda *a, **k: {"recentJobs": []}
    bot.perform_job = perform
    return bot, state, signed


def test_sure_jobs_first_then_decrypts_then_hacks(tmp_path):
    bot, _, signed = make_bot(tmp_path)
    assert bot.run_jobs().startswith("5 job(s) terminé(s)")
    assert signed == ["data-ok", "log-ok", "decrypt-1", "decrypt-2", "log-hack"]


def test_fuse_stops_a_failing_type_for_the_run(tmp_path):
    """Un déchiffrement raté (enchaînement cassé, fichier trop dur) : pas d'autre déchiffrement ce passage,
    les autres types continuent, et le marché est revu au passage suivant pour ce qui reste."""
    bot, state, signed = make_bot(tmp_path, fail={"decrypt-1": cor3_bot.StepFailed("pas de bouton Déchiffrer")})
    with pytest.raises(cor3_bot.StepFailed) as failure:
        bot.run_jobs()
    assert signed == ["data-ok", "log-ok", "decrypt-1", "log-hack"]
    assert "remis au passage" in str(failure.value)
    retry = cor3_bot.parse_time(state.data["markets"]["Home Server"]["retry_at"])
    now = cor3_bot.utc_now()
    assert now < retry <= now + cor3_bot.JOB_RETRY + dt.timedelta(seconds=5)
    assert "decrypt-1" in state.pending_jobs()                       # le job signé sera repris


def test_ice_wall_lock_stops_decrypts_and_hacks(tmp_path):
    until = cor3_bot.utc_now() + dt.timedelta(minutes=40)
    bot, state, signed = make_bot(tmp_path, fail={"decrypt-1": cor3_bot.IceWallLocked("verrouillé", until)})
    with pytest.raises(cor3_bot.StepFailed):
        bot.run_jobs()
    assert signed == ["data-ok", "log-ok", "decrypt-1"]
    assert cor3_bot.parse_time(state.data["markets"]["Home Server"]["retry_at"]) == until.replace(microsecond=0)
    assert state.market_due("Home Server", until + dt.timedelta(seconds=1))
    assert not state.market_due("Home Server", until - dt.timedelta(minutes=1))


def test_unavailable_server_does_not_blow_the_fuse(tmp_path):
    bot, _, signed = make_bot(tmp_path, fail={"data-ok": cor3_bot.Unavailable("RM7-A en maintenance")})
    with pytest.raises(cor3_bot.StepFailed):
        bot.run_jobs()
    assert signed == ["data-ok", "log-ok", "decrypt-1", "decrypt-2", "log-hack"]


def test_revisit_is_useless_when_the_timer_comes_first(tmp_path):
    state = cor3_bot.State(tmp_path / "state.json")
    now = cor3_bot.utc_now()
    state.set_market_reset("SRM7-M", now + dt.timedelta(minutes=10), now=now)
    state.set_market_revisit("SRM7-M", now + dt.timedelta(minutes=15))
    assert "retry_at" not in state.data["markets"]["SRM7-M"]


def test_find_target_reads_the_files_of_the_job():
    items = [{"id": "f1", "name": "a.ab52p"}, {"id": "f2", "name": "b.xvct"}]
    assert cor3_bot.Bot._find_target(items, {"files": [{"id": "f2", "name": "b.xvct"}]}, "files") == 1
    assert cor3_bot.Bot._find_target(items, {"files": [{"name": "a.ab52p"}]}, "files") == 0


def test_xpath_literal():
    assert cor3_bot.xpath_literal("log_1.ab52p") == "'log_1.ab52p'"
    assert cor3_bot.xpath_literal("it's") == '"it\'s"'
    assert cor3_bot.xpath_literal("a'b\"c") == "concat('a', \"'\", 'b\"c')"


def test_decrypt_and_extract_target_found_by_name_without_extension():
    """Le job annonce « db_6531.txt », le nœud a « db_6531.bak » ; l'id « j_… » n'est pas celui du fichier."""
    items = [{"id": "f1", "name": "db_9736.dat"}, {"id": "f2", "name": "db_6531.bak"}, {"id": "f3", "name": "x.enc"}]
    details = {"extensions": [{"ext": ".ab52p", "encryptionLevel": [14, 26]}],
               "files": [{"id": "j_01a16168", "name": "db_6531.txt", "isDecrypted": False}]}
    assert cor3_bot.Bot._find_target(items, details, "files") == 1
    with pytest.raises(cor3_bot.StepFailed):         # deux fichiers du même nom : on ne devine pas
        cor3_bot.Bot._find_target(items + [{"id": "f4", "name": "db_6531.log"}], details, "files")


def test_hopeless_decrypt_is_lost_not_retried_and_keeps_the_fuse(tmp_path):
    bot, state, signed = make_bot(tmp_path, fail={"decrypt-1": cor3_bot.Hopeless("module trop faible")})
    with pytest.raises(cor3_bot.StepFailed):
        bot.run_jobs()
    assert signed == ["data-ok", "log-ok", "decrypt-1", "decrypt-2", "log-hack"]   # le fusible ne saute pas
    assert "decrypt-1" not in state.pending_jobs() and state.job_lost("decrypt-1")


def test_unreadable_extension_is_refused_before_signing(tmp_path):
    """23/10 : .xvct signé, mais « big decrypt » ne le lit pas (pas de bouton Déchiffrer). Les contrats .xvct
    suivants sont refusés, tant que la config envoie .xvct vers ce préréglage."""
    bot, state, _ = make_bot(tmp_path)
    xvct = job("dec-xvct", "DecryptFile", 1500)
    xvct["conditions"] = {"items": [{"type": "DecryptFile", "details": {
        "extensions": [{"ext": ".xvct", "encryptionLevel": [22, 27]}], "encryptionLevel": [22, 27]}}]}
    bot.cfg["jobs"]["decrypt_presets"] = {"eb52x": "decrypt", "default": "big decrypt"}
    assert bot.decrypt_problem(xvct) is None
    state.note_unreadable("xvct", "big decrypt")
    assert "illisible" in bot.job_refusal_reason(xvct)
    bot.cfg["jobs"]["decrypt_presets"]["xvct"] = "lyapun"           # nouveau module, nouveau préréglage
    assert bot.decrypt_problem(xvct) is None


def test_over_defended_server_is_refused_before_signing(tmp_path):
    """22/10 : SRM7-M défense 24, meilleur outil 19 : le contrat ne doit pas être signé."""
    bot, state, _ = make_bot(tmp_path)
    acquisition = job("log-srm7", "DownloadLog", 2011, server="RM7-B")
    bot.events.servers["b"]["serverDefenceRate"] = 24
    assert bot.access_problem(acquisition) is None                   # puissance encore inconnue : on tente
    state.note_hack_power(19)
    assert "plus forte que ton meilleur outil" in bot.access_problem(acquisition)
    state.note_hack_power(17)                                        # on garde le maximum
    assert state.data["hack_power"] == 19
    bot.events.servers["b"]["serverDefenceRate"] = 15
    assert bot.access_problem(acquisition) is None
