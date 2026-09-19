"""Player identity for StatsBomb players.

StatsBomb gives every player a stable numeric id, so identity is exact and needs no
name matching. Each StatsBomb player gets a row in `players` keyed
`statsbomb:<id>`, with that id also stored in `players.statsbomb_id`.

StatsBomb players live in their own id space for now. They are deliberately NOT linked
to Understat players (`understat:<id>`): we hold no Understat data for 2015/16, and a
name-only link across ten seasons would be a guess. When cross-source linking is
added, it should set `statsbomb_id` on the existing row rather than create a new one,
which is why `resolve_player` looks the id up before creating anything.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.models.schema import Player

SOURCE = "statsbomb"


def statsbomb_player_id(statsbomb_id: int) -> str:
    """Our player_id for a StatsBomb player (`statsbomb:<id>`)."""
    return f"{SOURCE}:{statsbomb_id}"


def resolve_player(session: Session, statsbomb_id: int, name: str) -> str:
    """Return our `player_id` for a StatsBomb player, creating the `players` row if needed.

    An existing row already carrying this `statsbomb_id` wins (so a future cross-source
    link is respected). Otherwise a `statsbomb:<id>` row is upserted.
    """
    existing = session.scalar(
        select(Player.player_id).where(Player.statsbomb_id == str(statsbomb_id))
    )
    if existing:
        return existing

    player_id = statsbomb_player_id(statsbomb_id)
    session.execute(
        sqlite_insert(Player)
        .values(player_id=player_id, canonical_name=name, statsbomb_id=str(statsbomb_id))
        .on_conflict_do_update(
            index_elements=["player_id"],
            set_={"canonical_name": name, "statsbomb_id": str(statsbomb_id)},
        )
    )
    return player_id


def resolve_players(session: Session, names_by_statsbomb_id: dict[int, str]) -> dict[int, str]:
    """Resolve many StatsBomb players at once: {statsbomb id: our player_id}."""
    return {
        sb_id: resolve_player(session, sb_id, name) for sb_id, name in names_by_statsbomb_id.items()
    }
