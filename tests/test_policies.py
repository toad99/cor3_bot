import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cor3_bot  # noqa: E402


def make_bot(jobs=None, mercs=None, dry_run=False):
    cfg = {"general": {}, "daily": {}, "jobs": jobs or {}, "mercenaries": mercs or {}, "notify": {}}
    return cor3_bot.Bot(None, cfg, logging.getLogger("t"), cor3_bot.GameEvents(logging.getLogger("t")), dry_run)


def job(jtype, deposit=0, penalty=50, server="RM7-E1L5"):
    return {"name": jtype, "jobType": jtype, "deposit": deposit, "reputationPenalty": penalty,
            "relatedServers": [{"serverName": server}] if server else []}


def test_default_policy_is_non_destructive():
    bot = make_bot()
    assert bot.job_refusal_reason(job("DecryptFile", server=None)) is None
    assert bot.job_refusal_reason(job("DownloadLog")) is None
    assert "suppression" in bot.job_refusal_reason(job("DeleteIps"))
    assert "suppression" in bot.job_refusal_reason(job("DeleteLog"))
    assert "ajout" in bot.job_refusal_reason(job("AddIps"))
    assert "dépôt" in bot.job_refusal_reason(job("DecryptFile", deposit=126, server=None))


def test_policy_opt_in():
    bot = make_bot({"allow_deletions": True, "allow_ip_add": True, "max_deposit_cr": 200,
                    "max_rep_penalty": 60, "skip_servers": ["RM7-N2ECP"]})
    assert bot.job_refusal_reason(job("DeleteIps")) is None
    assert bot.job_refusal_reason(job("AddIps")) is None
    assert bot.job_refusal_reason(job("DecryptFile", deposit=126, server=None)) is None
    assert "pénalité" in bot.job_refusal_reason(job("DownloadLog", penalty=82))
    assert bot.job_refusal_reason(job("DownloadLog", server="RM7-N2ECP")) == "serveur exclu"


def test_find_target_uses_sequence_for_duplicate_names():
    logs = [
        {"seq": 4, "message": "Backup job finished successfully", "sizeMb": 37.7, "source": "generated"},
        {"seq": 24, "message": "Disk I/O spike detected on node node-2", "sizeMb": 3.52, "source": "job"},
        {"seq": 9, "message": "Backup job finished successfully", "sizeMb": 3.86, "source": "job"},
    ]
    find = cor3_bot.Bot._find_target
    assert find(logs, {"logSeqs": [24], "logNames": ["Disk I/O spike detected on node node-2"]}, "logs") == 1
    assert find(logs, {"logNames": ["Backup job finished successfully"]}, "logs") == 2
    with pytest.raises(cor3_bot.StepFailed):
        find(logs, {"logNames": ["absent"]}, "logs")


def test_event_policies():
    msg = {"decisionOptions": [
        {"id": "option_1", "label": "Break through - fast and loud", "riskModifier": 10, "lootModifier": 30},
        {"id": "option_2", "label": "Find a way around - quiet and slow", "riskModifier": 5, "lootModifier": 10},
    ]}
    assert make_bot(mercs={"event_policy": "prudent"}).choose_option(msg)["id"] == "option_2"
    assert make_bot(mercs={"event_policy": "gourmand", "max_risk_modifier": 10}).choose_option(msg)["id"] == "option_1"
    assert make_bot(mercs={"event_policy": "gourmand", "max_risk_modifier": 5}).choose_option(msg)["id"] == "option_2"
    assert make_bot(mercs={"event_policy": "auto"}).choose_option(msg) is None
    guide = {"decisionOptions": [{"id": "option_1", "label": "Agree", "riskModifier": 10, "lootModifier": 20},
                                 {"id": "option_2", "label": "Refuse - working alone", "riskModifier": -5, "lootModifier": 0}]}
    assert make_bot().choose_option(guide)["label"] == "Refuse - working alone"
