"""Formation templates for the Tactical Composer: 11 slots, each one of the 8 position groups.

Coordinates are StatsBomb yards (x 0-120 towards the opponent's goal, y 0-80 from the team's left
touchline) and only place the slot on a drawn pitch; they play no part in picking players.

3-5-2 and 5-3-2 have the same slot list: two FB slots used as wing-backs. The data has no separate
wing-back group, so both formations pick from the same full-backs; they differ only in where the
wing-backs are drawn (midfield line in 3-5-2, back line in 5-3-2). ANALYTICS_DECISIONS.md
section 29.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.analytics.metrics import POSITION_GROUPS


class Slot(BaseModel):
    """One position in a formation. `label` is what the pitch shows (e.g. "LB", "RW")."""

    group: str
    label: str
    x: float
    y: float


class Formation(BaseModel):
    name: str
    slots: list[Slot]
    note: str = ""


def _s(group: str, label: str, x: float, y: float) -> Slot:
    return Slot(group=group, label=label, x=x, y=y)


_GK = _s("GK", "GK", 6, 40)
_WB_MID = "The two FB slots are wing-backs, drawn in midfield."
_WB_BACK = "The two FB slots are wing-backs, drawn on the back line."
FORMATIONS: dict[str, Formation] = {
    "4-3-3": Formation(name="4-3-3", slots=[
        _GK, _s("FB", "LB", 28, 10), _s("CB", "CB", 22, 30), _s("CB", "CB", 22, 50),
        _s("FB", "RB", 28, 70), _s("DM", "DM", 42, 40), _s("CM", "CM", 58, 25),
        _s("CM", "CM", 58, 55), _s("W", "LW", 88, 12), _s("W", "RW", 88, 68),
        _s("ST", "ST", 100, 40),
    ]),
    "4-2-3-1": Formation(name="4-2-3-1", slots=[
        _GK, _s("FB", "LB", 28, 10), _s("CB", "CB", 22, 30), _s("CB", "CB", 22, 50),
        _s("FB", "RB", 28, 70), _s("DM", "DM", 45, 28), _s("DM", "DM", 45, 52),
        _s("AM", "AM", 74, 40), _s("W", "LW", 80, 12), _s("W", "RW", 80, 68),
        _s("ST", "ST", 102, 40),
    ]),
    "4-4-2": Formation(name="4-4-2", slots=[
        _GK, _s("FB", "LB", 28, 10), _s("CB", "CB", 22, 30), _s("CB", "CB", 22, 50),
        _s("FB", "RB", 28, 70), _s("CM", "CM", 55, 30), _s("CM", "CM", 55, 50),
        _s("W", "LM", 62, 10), _s("W", "RM", 62, 70), _s("ST", "ST", 98, 30),
        _s("ST", "ST", 98, 50),
    ]),
    "3-5-2": Formation(name="3-5-2", note=_WB_MID, slots=[
        _GK, _s("CB", "CB", 22, 20), _s("CB", "CB", 20, 40), _s("CB", "CB", 22, 60),
        _s("FB", "LWB", 58, 8), _s("FB", "RWB", 58, 72), _s("DM", "DM", 42, 40),
        _s("CM", "CM", 60, 27), _s("CM", "CM", 60, 53), _s("ST", "ST", 98, 30),
        _s("ST", "ST", 98, 50),
    ]),
    "5-3-2": Formation(name="5-3-2", note=_WB_BACK, slots=[
        _GK, _s("CB", "CB", 20, 22), _s("CB", "CB", 18, 40), _s("CB", "CB", 20, 58),
        _s("FB", "LWB", 32, 6), _s("FB", "RWB", 32, 74), _s("DM", "DM", 45, 40),
        _s("CM", "CM", 60, 25), _s("CM", "CM", 60, 55), _s("ST", "ST", 96, 30),
        _s("ST", "ST", 96, 50),
    ]),
}  # fmt: skip

for _f in FORMATIONS.values():  # a malformed template is a programming error: fail at import
    assert len(_f.slots) == 11 and all(s.group in POSITION_GROUPS for s in _f.slots), _f.name
    assert sum(s.group == "GK" for s in _f.slots) == 1, _f.name


def get_formation(name: str) -> Formation:
    if name not in FORMATIONS:
        raise KeyError(f"Unknown formation {name!r}; choose one of {', '.join(FORMATIONS)}")
    return FORMATIONS[name]
