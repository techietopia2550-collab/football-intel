"""2015/16 market values from the local dcaribou Transfermarkt dataset (Kaggle
`davidcariboo/player-scores`, CC0), matched to the profiled StatsBomb players.

Reads only the CSVs in `settings.cache_dir / "transfermarkt"`: no network, no scraping. Steps:

1. **Snapshot** (`select_snapshot`): each Transfermarkt player's latest valuation dated inside
   `SNAPSHOT_START`..`SNAPSHOT_END` (both inclusive). None in the window -> no value.
2. **Candidates** (`season_candidates`): Transfermarkt players with an appearance for a club in a
   2015/16 league game of the four leagues, with that club mapped to our canonical team name.
3. **Match** (`match_players`): each profiled player against the candidates of his own club(s)
   only, so a name is never matched across a club conflict. Stages, first hit wins: exact name,
   normalized name, fuzzy (token containment or close spelling). More than one candidate at a
   stage -> unmatched ("ambiguous"), never a guess. A Transfermarkt player claimed twice keeps
   the stronger match; the other is unmatched.

Writes `player_market_values` (one row per profiled player, upsert on player_id + season) and
`data/processed/unmatched_market_values.csv`. See ANALYTICS_DECISIONS.md section 25.

    python -m src.transform.market_values
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import difflib
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.ingest.finance import EXCHANGE_RATES_TO_GBP
from src.ingest.team_identity import is_known_team, normalize_team_name
from src.models.market_value import MarketValueRecord
from src.models.schema import PlayerMarketValue, get_engine, init_db
from src.transform.normalize import DEFAULT_SEASON, season_table_name
from src.utils.config import settings
from src.utils.logging import logger

TM_DIR: Path = settings.cache_dir / "transfermarkt"
UNMATCHED_CSV: Path = settings.processed_dir / "unmatched_market_values.csv"
SOURCE = "transfermarkt (dcaribou/transfermarkt-datasets, Kaggle davidcariboo/player-scores, CC0)"

SNAPSHOT_START = pd.Timestamp("2015-10-01")
SNAPSHOT_END = pd.Timestamp("2016-03-01")
TM_SEASON = 2015  # Transfermarkt's season code for 2015/16
TM_LEAGUES: dict[str, str] = {
    "GB1": "ENG-Premier League", "ES1": "ESP-La Liga", "IT1": "ITA-Serie A", "FR1": "FRA-Ligue 1",
}  # fmt: skip

FUZZY_MIN_RATIO = 0.85  # difflib ratio of normalized names needed for a fuzzy match
FUZZY_MIN_MARGIN = 0.10  # and the lead it needs over the next candidate
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
# Letters NFKD does not decompose into a base letter plus accent.
_SPECIAL = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i",
                          "þ": "th", "ð": "d"})  # fmt: skip
_STAGE_RANK = {"exact": 0, "normalized": 1, "fuzzy": 2}


# ---- pure functions ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Lowercase, accents removed, apostrophes and dots dropped, hyphens and other punctuation
    as spaces, generational suffixes (Jr., Sr., II...) removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKD", name.casefold().translate(_SPECIAL))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"['’`´.]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(t for t in text.split() if t not in SUFFIXES)


def select_snapshot(
    valuations: pd.DataFrame, start: pd.Timestamp = SNAPSHOT_START, end: pd.Timestamp = SNAPSHOT_END
) -> pd.DataFrame:
    """Each player's latest valuation dated in [start, end]. Players with none are absent.

    `valuations` needs player_id, date, market_value_in_eur, current_club_name. On two
    valuations with the same date the later row in the file wins (the sort is stable)."""
    dates = pd.to_datetime(valuations["date"])
    window = valuations.loc[(dates >= start) & (dates <= end)].copy()
    window["date"] = pd.to_datetime(window["date"])
    window = window.sort_values(["player_id", "date"], kind="stable")
    return window.groupby("player_id", as_index=False).tail(1).reset_index(drop=True)


def season_candidates(
    games: pd.DataFrame, appearances: pd.DataFrame, players: pd.DataFrame
) -> pd.DataFrame:
    """Transfermarkt players who appeared in a 2015/16 league game of the four leagues, one row
    per (player, club), with the club as our canonical team name and every spelling of his name.

    Columns: tm_id, team, league, names (tuple of str). Clubs whose name does not resolve to a
    known team are logged and dropped."""
    g = games.loc[games["competition_id"].isin(TM_LEAGUES) & (games["season"] == TM_SEASON)]
    club_names = pd.concat([
        g[["home_club_id", "home_club_name"]].set_axis(["club_id", "club_name"], axis=1),
        g[["away_club_id", "away_club_name"]].set_axis(["club_id", "club_name"], axis=1),
    ]).drop_duplicates("club_id").set_index("club_id")["club_name"]  # fmt: skip
    unknown = sorted(n for n in club_names if not is_known_team(n))
    if unknown:
        logger.warning("Market values: clubs with no canonical name, dropped: {u}", u=unknown)
    apps = appearances.loc[appearances["game_id"].isin(set(g["game_id"]))]
    apps = apps.merge(g[["game_id", "competition_id"]].rename(columns={"competition_id": "comp"}))
    pairs = apps.groupby(["player_id", "player_club_id", "comp"], as_index=False).agg(
        app_name=("player_name", "first")
    )
    pairs["team"] = pairs["player_club_id"].map(club_names).map(
        lambda n: normalize_team_name(n) if isinstance(n, str) and is_known_team(n) else None
    )
    pairs = pairs.dropna(subset=["team"])
    pl = players.set_index("player_id")
    rows = []
    for r in pairs.itertuples(index=False):
        names = {r.app_name}
        if r.player_id in pl.index:
            p = pl.loc[r.player_id]
            names.add(p["name"])
            if isinstance(p["first_name"], str) and isinstance(p["last_name"], str):
                names.add(f"{p['first_name']} {p['last_name']}")
        names = tuple(sorted(n for n in names if isinstance(n, str) and n.strip()))
        rows.append({"tm_id": int(r.player_id), "team": r.team, "league": TM_LEAGUES[r.comp],
                     "names": names})  # fmt: skip
    return pd.DataFrame(rows, columns=["tm_id", "team", "league", "names"])


def _stage(ours: str, theirs: str) -> tuple[str, float] | None:
    """How one pair of names matches: ('exact'|'normalized'|'fuzzy', score) or None."""
    if ours.casefold().strip() == theirs.casefold().strip():
        return "exact", 1.0
    a, b = normalize_name(ours), normalize_name(theirs)
    if not a or not b:
        return None
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return "normalized", 1.0
    ta, tb = set(a.split()), set(b.split())
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if short <= long_ and max(len(t) for t in short) >= 4:  # "José Giménez" in "José María Giménez"
        return "fuzzy", round(len(short) / len(long_), 3)
    fa, fb = a.split(), b.split()
    if (len(fa) == len(fb) == 2 and fa[1] == fb[1] and min(len(fa[0]), len(fb[0])) >= 3
            and (fa[0].startswith(fb[0]) or fb[0].startswith(fa[0]))):  # "Maxi"/"Maximiliano"
        return "fuzzy", round(min(len(fa[0]), len(fb[0])) / max(len(fa[0]), len(fb[0])), 3)
    ratio = max(  # as written, and with the words sorted ("Ki Sung-Yeung" / "Sung-yueng Ki")
        difflib.SequenceMatcher(None, a, b).ratio(),
        difflib.SequenceMatcher(None, " ".join(sorted(fa)), " ".join(sorted(fb))).ratio(),
    )
    return ("fuzzy", round(ratio, 3)) if ratio >= FUZZY_MIN_RATIO else None


def _best_for(name: str, pool: pd.DataFrame) -> tuple[int, str, float, str] | tuple[None, None, None, str]:
    """(tm_id, stage, score, note) for the single best candidate in `pool`, or Nones and why."""
    hits: dict[int, tuple[str, float]] = {}
    for c in pool.itertuples(index=False):
        for n in c.names:
            s = _stage(name, n)
            if s and (c.tm_id not in hits or (_STAGE_RANK[s[0]], -s[1]) < (
                _STAGE_RANK[hits[c.tm_id][0]], -hits[c.tm_id][1])):  # fmt: skip
                hits[c.tm_id] = s
    if not hits:
        return None, None, None, "no candidate at his club(s) with a close name"
    best_stage = min(_STAGE_RANK[s] for s, _ in hits.values())
    top = {k: v for k, v in hits.items() if _STAGE_RANK[v[0]] == best_stage}
    ranked = sorted(top.items(), key=lambda kv: -kv[1][1])
    if len(ranked) > 1:
        stage = ranked[0][1][0]
        if stage != "fuzzy" or ranked[0][1][1] - ranked[1][1][1] < FUZZY_MIN_MARGIN:
            names = "; ".join(
                f"{pool.loc[pool['tm_id'] == k, 'names'].iloc[0][0]} ({k})" for k, _ in ranked[:4]
            )
            return None, None, None, f"ambiguous ({stage}): {names}"
    tm_id, (stage, score) = ranked[0]
    return tm_id, stage, score, ""


def match_players(ours: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Match profiled players to Transfermarkt ids, within their own club(s) only. Pure.

    `ours` needs player_id, player_name, team, team_split (the "Club 1200 / Club 300" form; may
    be blank for one-club players). Returns player_id, transfermarkt_id (nullable Int64),
    match_method, match_score, match_note."""
    out = []
    for r in ours.itertuples(index=False):
        clubs = {r.team}
        if isinstance(r.team_split, str) and r.team_split:
            clubs |= {re.sub(r"\s+\d+$", "", part.strip()) for part in r.team_split.split(" / ")}
        pool = candidates.loc[candidates["team"].isin(clubs)].drop_duplicates("tm_id")
        tm_id, stage, score, note = _best_for(r.player_name, pool)
        out.append({"player_id": r.player_id, "transfermarkt_id": tm_id,
                    "match_method": stage or "unmatched", "match_score": score,
                    "match_note": note or None})  # fmt: skip
    res = pd.DataFrame(out, columns=["player_id", "transfermarkt_id", "match_method",
                                     "match_score", "match_note"])  # fmt: skip
    res["transfermarkt_id"] = res["transfermarkt_id"].astype("Int64")
    # One Transfermarkt player may back only one profiled player: keep the strongest claim.
    claimed = res.dropna(subset=["transfermarkt_id"]).copy()
    claimed["rank"] = claimed["match_method"].map(_STAGE_RANK)
    claimed = claimed.sort_values(["rank", "match_score"], ascending=[True, False], kind="stable")
    losers = claimed.index[claimed.duplicated("transfermarkt_id")]
    for i in losers:
        res.loc[i, "match_note"] = (
            f"Transfermarkt id {res.loc[i, 'transfermarkt_id']} already matched to another player"
        )
        res.loc[i, ["transfermarkt_id", "match_score"]] = [pd.NA, None]
        res.loc[i, "match_method"] = "unmatched"
    return res


def build_records(
    ours: pd.DataFrame, matches: pd.DataFrame, snapshot: pd.DataFrame, tm_names: pd.Series,
    season: str = DEFAULT_SEASON,
) -> list[MarketValueRecord]:
    """One record per profiled player. Unmatched, or matched with no valuation in the window ->
    every value field None. Pure."""
    rate = EXCHANGE_RATES_TO_GBP[season]["EUR"]
    snap = snapshot.set_index("player_id")
    m = ours.merge(matches, on="player_id", how="left")
    records = []
    for r in m.itertuples(index=False):
        tm_id = None if pd.isna(r.transfermarkt_id) else int(r.transfermarkt_id)
        v = snap.loc[tm_id] if tm_id is not None and tm_id in snap.index else None
        note = r.match_note if isinstance(r.match_note, str) else None
        if tm_id is not None and v is None:
            note = "matched, but no valuation between 2015-10-01 and 2016-03-01"
        eur = None if v is None else float(v["market_value_in_eur"])
        records.append(MarketValueRecord(
            player_id=r.player_id, player_name=r.player_name, season=season, league=r.league,
            team=r.team, transfermarkt_id=tm_id,
            transfermarkt_name=None if tm_id is None else tm_names.get(tm_id),
            match_method=r.match_method,
            match_score=None if pd.isna(r.match_score) else float(r.match_score),
            match_note=note,
            valuation_date=None if v is None else pd.Timestamp(v["date"]).date(),
            valuation_club=None if v is None else v["current_club_name"],
            market_value_eur=eur,
            market_value_gbp=None if eur is None else round(eur * rate, 2),
            exchange_rate_used=None if eur is None else rate,
            source=SOURCE,
        ))  # fmt: skip
    return records


# ---- I/O ----------------------------------------------------------------------------------------


def read_transfermarkt(tm_dir: Path = TM_DIR) -> dict[str, pd.DataFrame]:
    """The five CSVs this module needs. Raises FileNotFoundError naming any that is missing."""
    cols = {
        "player_valuations": ["player_id", "date", "market_value_in_eur", "current_club_name"],
        "players": ["player_id", "name", "first_name", "last_name"],
        "games": ["game_id", "competition_id", "season", "home_club_id", "away_club_id",
                  "home_club_name", "away_club_name"],
        "appearances": ["game_id", "player_id", "player_club_id", "player_name"],
    }  # fmt: skip
    missing = [f"{n}.csv" for n in cols if not (tm_dir / f"{n}.csv").is_file()]
    if missing:
        raise FileNotFoundError(f"Missing in {tm_dir}: {', '.join(missing)}")
    return {n: pd.read_csv(tm_dir / f"{n}.csv", usecols=c, encoding="utf-8") for n, c in cols.items()}


def load_profiled(engine: Engine, season: str = DEFAULT_SEASON) -> pd.DataFrame:
    table = season_table_name(season)
    return pd.read_sql(
        f"SELECT player_id, player_name, team, league, team_split, minutes_total FROM {table}",
        engine,
    )


def upsert_records(engine: Engine, records: list[MarketValueRecord]) -> int:
    """Insert or replace by (player_id, season)."""
    if not records:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [{**r.model_dump(), "updated_at": now} for r in records]
    with engine.begin() as conn:
        for i in range(0, len(rows), 200):  # SQLite caps bound parameters per statement
            stmt = sqlite_insert(PlayerMarketValue).values(rows[i : i + 200])
            stmt = stmt.on_conflict_do_update(
                index_elements=["player_id", "season"],
                set_={c: stmt.excluded[c] for c in rows[0] if c not in ("player_id", "season")},
            )
            conn.execute(stmt)
    return len(rows)


def run(engine: Engine | None = None, tm_dir: Path = TM_DIR, season: str = DEFAULT_SEASON) -> pd.DataFrame:
    """Snapshot, match, store, write the unmatched CSV. Returns the stored rows as a frame."""
    engine = init_db(engine or get_engine())
    tm = read_transfermarkt(tm_dir)
    snapshot = select_snapshot(tm["player_valuations"])
    candidates = season_candidates(tm["games"], tm["appearances"], tm["players"])
    ours = load_profiled(engine, season)
    matches = match_players(ours, candidates)
    tm_names = tm["players"].set_index("player_id")["name"]
    records = build_records(ours, matches, snapshot, tm_names, season)
    n = upsert_records(engine, records)
    out = pd.DataFrame([r.model_dump() for r in records]).merge(
        ours[["player_id", "minutes_total"]], on="player_id"
    )
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    unmatched = out.loc[out["match_method"] == "unmatched"].sort_values(
        "minutes_total", ascending=False
    )
    unmatched[["player_id", "player_name", "team", "league", "minutes_total", "match_note"]].to_csv(
        UNMATCHED_CSV, index=False, encoding="utf-8"
    )
    logger.info("Market values: {n} rows written, {u} unmatched -> {p}",
                n=n, u=len(unmatched), p=UNMATCHED_CSV)  # fmt: skip
    return out


def _main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    out = run()
    matched = out["match_method"] != "unmatched"
    valued = out["market_value_eur"].notna()
    print(f"Profiled players: {len(out)}; matched {matched.sum()} ({matched.mean():.1%}); "
          f"with a value {valued.sum()} ({valued.mean():.1%})")  # fmt: skip
    print(out.groupby("league").agg(
        players=("player_id", "size"), matched=("transfermarkt_id", lambda s: s.notna().sum()),
        valued=("market_value_eur", lambda s: s.notna().sum()),
    ).to_string())  # fmt: skip
    print(out["match_method"].value_counts().to_string())


if __name__ == "__main__":
    _main()

