"""Outils d'inspection : masquage des secrets avant écriture."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ws_dump", ROOT / "tools" / "ws_dump.py")
ws_dump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws_dump)


def test_scrub_masks_secrets_but_keeps_game_data():
    jwt = "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2"
    data = {"accessToken": "abc", "password": "x", "auth": {"a": 1}, "authorization": "Bearer x",
            "author": "Chuck", "message": f"voici {jwt}", "items": [{"refresh_token": "r", "seq": 9}],
            "raw": b"\x00\x01\x02"}
    assert ws_dump.scrub(data) == {
        "accessToken": "<masqué>", "password": "<masqué>", "auth": "<masqué>", "authorization": "<masqué>",
        "author": "Chuck", "message": "voici <jwt masqué>", "items": [{"refresh_token": "<masqué>", "seq": 9}],
        "raw": "<3 octets>"}
