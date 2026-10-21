import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers import (  # noqa: E402
    BINARY_DIGITS, MORSE_DIGITS, Cell, Glyph, SignalError, analyse_log_line,
    decode_signal, pick_invalid_logs, solve_ice_wall,
)


# ---------------- System Log Integrity ----------------
@pytest.mark.parametrize("line", [
    "[21:14:53] SCAN /sector=187 /status=WARN",
    "[05:27:29] PING /sector=227 /status=OK",
    "[00:00:00] TEMP-SYNC /sector=01 /status=ERROR",
    "[23:59:59] ROUTE-CHECK /sector=256 /status=OK",
    "[12:00:00] RADIO-TEST /sector=1 /status=WARN",
    "[07:55:43] SYNC /sector=134 /status=WARN",
    "[01:47:18] AUTH /sector=32 /status=ERROR",
])
def test_valid_lines(line):
    assert analyse_log_line(line) == set()


@pytest.mark.parametrize("line,expected", [
    # exemples du guide
    ("[09:13:59] SCAN /sector=86", {"missing_status"}),
    ("[16:13:53] RADIO-TEST /sector=289 /status=ERROR", {"invalid_sector"}),
    ("[02:67:48] ROUTE-CHECK /sector=289 /status=OK", {"invalid_time", "invalid_sector"}),
    # variantes produites par le générateur du jeu
    ("[24:10:10] PING /sector=10 /status=OK", {"invalid_time"}),
    ("[10:10:65] PING /sector=10 /status=OK", {"invalid_time"}),
    ("[10:10:10] AUHT /sector=10 /status=OK", {"invalid_event_type"}),
    ("[10:10:10] PINGX /sector=10 /status=OK", {"invalid_event_type"}),
    ("[10:10:10] TEMP-SYCN /sector=10 /status=OK", {"invalid_event_type"}),
    ("[10:10:10] SYNC /status=OK", {"missing_sector"}),
    ("[10:10:10] SYNC /sector=300 /status=OKAY", {"invalid_sector", "invalid_status"}),
    ("[10:10:10] SYNC /sector=0 /status=OK", {"invalid_sector"}),
    ("[10:10:10] SYNC", {"missing_sector", "missing_status"}),
    ("[33:61:10] SCANN /status=OKAY", {"invalid_time", "invalid_event_type", "missing_sector", "invalid_status"}),
])
def test_invalid_lines(line, expected):
    assert analyse_log_line(line) == expected


def test_pick_invalid_logs_counts():
    lines = [
        "[21:14:53] SCAN /sector=187 /status=WARN",
        "[09:13:59] SCAN /sector=86",
        "[05:27:29] PING /sector=227 /status=OK",
        "[02:67:48] ROUTE-CHECK /sector=289 /status=OK",
    ]
    assert pick_invalid_logs(lines, expected=2) == {
        1: {"missing_status"}, 3: {"invalid_time", "invalid_sector"},
    }
    with pytest.raises(ValueError):
        pick_invalid_logs(lines, expected=3)


# ---------------- Signal Decode ----------------
def encode(code, table):
    rev = {v: k for k, v in table.items()}
    return "".join(rev[d] for d in code)


def test_tables():
    assert BINARY_DIGITS["SSSS"] == "0" and BINARY_DIGITS["LSSL"] == "9" and len(BINARY_DIGITS) == 10
    assert MORSE_DIGITS["SSSLL"] == "3" and len(MORSE_DIGITS) == 10


def test_signal_of_the_day():
    # signal réellement résolu pendant l'inspection (21/10/2026) : 321
    assert decode_signal("SSSLLSSLLLSLLLL", code_length=3) == ("morse", "321")


@pytest.mark.parametrize("code", ["543", "54318", "0123456789", "999", "00000"])
def test_morse_roundtrip(code):
    assert decode_signal(encode(code, MORSE_DIGITS)) == ("morse", code)


@pytest.mark.parametrize("code", ["785", "78596", "0123456789", "000", "99999"])
def test_binary_roundtrip(code):
    assert decode_signal(encode(code, BINARY_DIGITS)) == ("binary", code)


def test_twenty_pulses_prefers_binary_with_known_lengths():
    # 20 impulsions : 5 chiffres binaires (medium) plutôt que 4 chiffres morse
    pulses = encode("55555", BINARY_DIGITS)  # 'SLSL' x5 -> pas un morse valide de toute façon
    assert decode_signal(pulses) == ("binary", "55555")
    # cas ambigu construit : valide en morse ET en binaire
    amb = encode("0000", MORSE_DIGITS)  # 'LLLLL' x4 = 20 'L' -> binaire 'LLLL' = 15 invalide
    assert decode_signal(amb) == ("morse", "0000")
    assert decode_signal(encode("33333", BINARY_DIGITS), code_length=5) == ("binary", "33333")


def test_signal_errors():
    with pytest.raises(SignalError):
        decode_signal("SSL")           # ni multiple de 4 ni de 5
    with pytest.raises(SignalError):
        decode_signal("LLLL" * 3)      # binaire 15 invalide, pas multiple de 5
    with pytest.raises(SignalError):
        decode_signal("SSSLLSSLLLSLLLL", code_length=5)


# ---------------- ICE WALL ----------------
def test_ice_wall_finds_group():
    A, B, C, D = "a", "b", "c", "d"
    targets = [Glyph(A, 31.5, 0, False), Glyph(B, 94.5, 0, False), Glyph(C, 63, 54, False)]
    cells = [
        Cell(A, 100, 100, False, index=0), Cell(D, 131.5, 100, True, index=1),
        Cell(B, 163, 100, False, index=2), Cell(C, 131.5, 154, False, index=3),
        Cell(A, 300, 100, False, index=4), Cell(B, 363, 100, False, index=5),  # pas de C dessous
        Cell(A, 163, 100, True, index=6),  # mauvaise orientation, même position que B
    ]
    sols = solve_ice_wall(targets, cells)
    assert [[c.index for c in s] for s in sols] == [[0, 2, 3]]


def test_ice_wall_no_solution():
    targets = [Glyph("a", 0, 0, False), Glyph("b", 63, 0, False)]
    cells = [Cell("a", 0, 0, False, index=0), Cell("b", 70, 0, False, index=1)]
    assert solve_ice_wall(targets, cells) == []


def test_ice_anchor_is_bottom_center_up():
    from solvers import ice_anchor_index
    # motif du jour : deux glyphes en haut, un en bas au centre -> l'ancre est celui du bas
    targets = [Glyph("a", 31.5, 0, False), Glyph("b", 94.5, 0, False), Glyph("c", 63, 54, False)]
    assert ice_anchor_index(targets) == 2
    # rangée du bas avec un triangle pointe en bas (y + hauteur) et deux pointe en haut
    targets = [Glyph("a", 63, 0, False), Glyph("b", 31.5, 54, False), Glyph("c", 63, 108, True),
               Glyph("d", 94.5, 54, False)]
    assert ice_anchor_index(targets) in (1, 3)
    # un seul triangle, pointe en bas, en bas : il devient l'ancre
    targets = [Glyph("a", 31.5, 0, False), Glyph("b", 63, 108, True)]
    assert ice_anchor_index(targets) == 1



def test_ice_partial_deduction():
    from solvers import solve_ice_wall_partial
    # 1 rangée de 5 triangles alternés (haut, bas, haut, bas, haut) ; motif = 2 triangles pointe en haut
    targets = [Glyph("a", 0, 0, False), Glyph("b", 63, 0, False)]
    cells = [Cell("a", 0, 0, False, index=0), Cell("x", 31.5, 54, True, index=1),
             Cell("", 63, 0, False, index=2, hidden=True), Cell("y", 94.5, 54, True, index=3),
             Cell("c", 126, 0, False, index=4)]
    group, why = solve_ice_wall_partial(targets, cells)
    # positions possibles : (0,2) cohérente (2 caché), (2,4) contredite (4 = 'c' au lieu de 'b')
    assert why == "deduced" and [c.index for c in group] == [0, 2]
    # si la cellule 4 était cachée aussi, deux positions restent possibles : on attend
    cells[4] = Cell("", 126, 0, False, index=4, hidden=True)
    group, why = solve_ice_wall_partial(targets, cells)
    assert group is None and why == "2 candidates"
    # tout est visible et correct : réponse directe
    cells[2] = Cell("b", 63, 0, False, index=2)
    group, why = solve_ice_wall_partial(targets, cells)
    assert why == "visible" and [c.index for c in group] == [0, 2]
