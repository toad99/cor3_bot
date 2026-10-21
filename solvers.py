"""Solveurs purs (sans navigateur) pour les mini-jeux de cor3.gg.

Tout ce fichier est testable hors ligne : `python -m pytest tests`.
Les règles viennent du code client de cor3.gg (frontend v1.25.51, octobre 2026).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# System Log Integrity
# --------------------------------------------------------------------------
VALID_TYPES = ("AUTH", "TEMP-SYNC", "SCAN", "ROUTE-CHECK", "RADIO-TEST", "PING", "SYNC")
VALID_STATUSES = ("OK", "WARN", "ERROR")

# Ordre des 6 boutons sous « Fix Error » (identique dans le code client et le guide).
ERROR_TYPES = (
    "invalid_time",
    "invalid_event_type",
    "missing_sector",
    "missing_status",
    "invalid_sector",
    "invalid_status",
)
# Libellés affichés (le mini-jeu reste en anglais même si l'OS est en français).
ERROR_LABELS = {
    "invalid_time": "Time format is incorrect",
    "invalid_event_type": "Event type is incorrect",
    "missing_sector": "Missing /sector parameter",
    "missing_status": "Missing /status parameter",
    "invalid_sector": "/sector parameter is incorrect",
    "invalid_status": "/status parameter is incorrect",
}

_LINE_RE = re.compile(r"^\[(?P<time>[^\]]*)\]\s*(?P<type>\S*)(?P<rest>.*)$")
_TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})")
_PARAM_RE = re.compile(r"/(?P<key>[A-Za-z]+)=(?P<val>\S*)")


def analyse_log_line(line: str) -> set[str]:
    """Renvoie l'ensemble des violations d'une ligne (ensemble vide = ligne valide).

    Format attendu : [HH:MM:SS] <TYPE> /sector=<1-256> /status=<OK|WARN|ERROR>
    Le jeu exige de cocher EXACTEMENT toutes les violations d'une ligne.
    """
    errors: set[str] = set()
    m = _LINE_RE.match(line.strip())
    if not m:
        return {"invalid_time"}

    tm = _TIME_RE.fullmatch(m.group("time").strip())
    if not tm or int(tm.group(1)) > 23 or int(tm.group(2)) > 59 or int(tm.group(3)) > 59:
        errors.add("invalid_time")

    if m.group("type") not in VALID_TYPES:
        errors.add("invalid_event_type")

    params: dict[str, str] = {}
    for pm in _PARAM_RE.finditer(m.group("rest")):
        params.setdefault(pm.group("key").lower(), pm.group("val"))

    if "sector" not in params:
        errors.add("missing_sector")
    else:
        value = params["sector"]
        if not value.isdigit() or not 1 <= int(value) <= 256:
            errors.add("invalid_sector")

    if "status" not in params:
        errors.add("missing_status")
    elif params["status"] not in VALID_STATUSES:
        errors.add("invalid_status")

    return errors


def pick_invalid_logs(lines: list[str], expected: int | None = None) -> dict[int, set[str]]:
    """Index -> violations, pour les lignes invalides. Lève ValueError si le nombre
    de lignes invalides ne correspond pas à celui demandé par le jeu."""
    found = {i: errs for i, line in enumerate(lines) if (errs := analyse_log_line(line))}
    if expected is not None and len(found) != expected:
        raise ValueError(
            f"{len(found)} ligne(s) invalide(s) trouvée(s), le jeu en attend {expected} : "
            + "; ".join(f"#{i} {lines[i]!r} {sorted(e)}" for i, e in found.items())
        )
    return found


# --------------------------------------------------------------------------
# Signal Decode
# --------------------------------------------------------------------------
MORSE_DIGITS = {
    "LLLLL": "0", "SLLLL": "1", "SSLLL": "2", "SSSLL": "3", "SSSSL": "4",
    "SSSSS": "5", "LSSSS": "6", "LLSSS": "7", "LLLSS": "8", "LLLLS": "9",
}
BINARY_DIGITS = {format(d, "04b").replace("0", "S").replace("1", "L"): str(d) for d in range(10)}
# Longueur du code selon la difficulté (easy/medium/hard) dans le code client.
CODE_LENGTH_BY_DIFFICULTY = {"easy": 3, "medium": 5, "hard": 10}


class SignalError(ValueError):
    """Signal impossible à décoder sans ambiguïté."""


def _decode(pulses: str, size: int, table: dict[str, str]) -> str | None:
    if not pulses or len(pulses) % size:
        return None
    chunks = [pulses[i:i + size] for i in range(0, len(pulses), size)]
    if any(c not in table for c in chunks):
        return None
    return "".join(table[c] for c in chunks)


def decode_signal(
    pulses: str,
    code_length: int | None = None,
    morse_table: dict[str, str] | None = None,
    binary_table: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Décode une suite d'impulsions 'S'/'L'. Renvoie (encodage, code).

    Attention : 20 et 40 impulsions sont multiples de 4 ET de 5. Dans la version
    actuelle du jeu ce sont toujours du binaire (codes de 5 et 10 chiffres),
    mais on tranche ici avec la validité des segments puis la longueur attendue.
    """
    pulses = pulses.strip().upper()
    if set(pulses) - {"S", "L"}:
        raise SignalError(f"caractères inattendus dans {pulses!r}")

    candidates = []
    morse = _decode(pulses, 5, morse_table or MORSE_DIGITS)
    if morse is not None:
        candidates.append(("morse", morse))
    binary = _decode(pulses, 4, binary_table or BINARY_DIGITS)
    if binary is not None:
        candidates.append(("binary", binary))

    if code_length:
        candidates = [c for c in candidates if len(c[1]) == code_length]
    if len(candidates) > 1:
        known = set(CODE_LENGTH_BY_DIFFICULTY.values())
        candidates = [c for c in candidates if len(c[1]) in known] or candidates
    if len(candidates) != 1:
        raise SignalError(f"{len(pulses)} impulsions, candidats : {candidates or 'aucun'}")
    return candidates[0]


# --------------------------------------------------------------------------
# ICE WALL Break (mini-jeu de déchiffrement des fichiers .eb52x)
# --------------------------------------------------------------------------
@dataclass
class Glyph:
    sig: str          # signature des tracés SVG du glyphe (hors triangle de contour)
    x: float          # translate() du groupe SVG
    y: float
    flip: bool        # triangle pointe en bas (scale(1,-1))


@dataclass
class Cell(Glyph):
    index: int = 0
    cx: float = 0.0   # centre à l'écran (pixels CSS) pour le clic
    cy: float = 0.0
    extra: dict = field(default_factory=dict)
    hidden: bool = False   # glyphe pas encore révélé (cellule à 2 tracés)


ICE_ROW_HEIGHT = 54.0     # hauteur d'un triangle (translate y d'une rangée à l'autre)
ICE_HALF_WIDTH = 31.5     # demi-largeur d'un triangle


def ice_row_col(g: Glyph) -> tuple[int, int]:
    """Rangée / colonne (en demi-largeurs) d'un glyphe à partir de son translate().
    Les triangles pointe en bas sont dessinés avec translate(x, y + hauteur) scale(1,-1)."""
    y = g.y - (ICE_ROW_HEIGHT if g.flip else 0.0)
    return round(y / ICE_ROW_HEIGHT), round(g.x / ICE_HALF_WIDTH)


def ice_anchor_index(targets: list[Glyph]) -> int:
    """Indice du glyphe « ancre » de la séquence cible.

    Dans le jeu, UN SEUL clic pose tout le motif : la cellule cliquée reçoit le point
    ancre du motif = rangée la plus basse, de préférence un triangle pointe en haut,
    le plus proche du centre de cette rangée (règle lue dans le code client)."""
    rows = [ice_row_col(t) for t in targets]
    bottom = max(r for r, _ in rows)
    in_bottom = [i for i, (r, _) in enumerate(rows) if r == bottom]
    ups = [i for i in in_bottom if not targets[i].flip]
    pool = ups or in_bottom
    mean = sum(rows[i][1] for i in pool) / len(pool)
    return min(pool, key=lambda i: (abs(rows[i][1] - mean), rows[i][1]))


def solve_ice_wall(targets: list[Glyph], cells: list[Cell], tol: float = 1.0) -> list[list[Cell]]:
    """Cherche dans le mur les groupes de cellules qui reproduisent la séquence
    cible : mêmes glyphes, même orientation, mêmes décalages relatifs."""
    if not targets:
        return []
    base = targets[0]
    offsets = [(t, t.x - base.x, t.y - base.y) for t in targets[1:]]
    solutions = []
    for anchor in cells:
        if anchor.sig != base.sig or anchor.flip != base.flip:
            continue
        group = [anchor]
        for target, dx, dy in offsets:
            match = next(
                (c for c in cells
                 if c.sig == target.sig and c.flip == target.flip
                 and abs(c.x - anchor.x - dx) <= tol and abs(c.y - anchor.y - dy) <= tol),
                None,
            )
            if match is None:
                break
            group.append(match)
        else:
            solutions.append(group)
    return solutions


def ice_placements(targets: list[Glyph], cells: list[Cell]) -> list[list[Cell]]:
    """Toutes les positions où le motif tient entièrement dans le mur (sans regarder les glyphes)."""
    if not targets:
        return []
    by_pos = {(round(c.x * 2), round(c.y * 2), c.flip): c for c in cells}
    base = targets[0]
    groups = []
    for anchor in cells:
        if anchor.flip != base.flip:
            continue
        group = [anchor]
        for t in targets[1:]:
            cell = by_pos.get((round((anchor.x + t.x - base.x) * 2), round((anchor.y + t.y - base.y) * 2), t.flip))
            if cell is None:
                break
            group.append(cell)
        else:
            groups.append(group)
    return groups


def solve_ice_wall_partial(targets: list[Glyph], cells: list[Cell]) -> tuple[list[Cell] | None, str]:
    """Version « au fil des révélations » : renvoie (groupe, raison).

    - 'visible' : un placement entièrement révélé correspond au motif ;
    - 'deduced' : tous les autres placements sont contredits par des glyphes déjà visibles,
      il ne reste qu'une position possible (le motif existe forcément dans le mur) ;
    - (None, 'n candidates') : pas encore assez d'informations, il faut attendre.
    On ne devine jamais entre plusieurs positions : un mauvais placement coûte 20 s."""
    complete, possible = [], []
    for group in ice_placements(targets, cells):
        consistent, revealed = True, True
        for target, cell in zip(targets, group):
            if cell.hidden:
                revealed = False
            elif cell.sig != target.sig:
                consistent = False
                break
        if consistent:
            (complete if revealed else possible).append(group)
    if complete:
        return complete[0], "visible"
    if len(possible) == 1:
        return possible[0], "deduced"
    return None, f"{len(possible)} candidates"
