"""Write the upload file the site accepts back.

- Entries file in -> edited entries file out (same metadata columns, slot header as the
  site wrote it, one player per slot, only entries whose lineup changed unless asked).
- Salary/player list in -> blank-template upload: slot header row then one row per lineup.

DK accepts the bare ``ID`` or its own "Name (ID)" text (its entries file, instruction 4: "Use data
from the Name+ID column or the ID column; you cannot use just the player's name"), so DK cells are
written as "Name (ID)": readable, and still exactly what DK prints. FD's slot cells take the bare
``Id`` ("123-456"; whether FD accepts "Id:Name" is unverified), so FD uploads keep IDs and a
``*_readable.csv`` copy with names is written next to them. Lock checks always compare IDs.
"""

from __future__ import annotations

import csv
import os
import io
import re
from pathlib import Path

from dfs.models import Contest, Lineup, Site


class UploadError(ValueError):
    pass


def _ids(contest: Contest, lineup: Lineup) -> list[str]:
    if [s.slot for s in lineup.slots] != [s.name for s in contest.rules.slots]:
        raise UploadError("lineup slots do not match the contest's slot order")
    return [s.site_id for s in lineup.slots]


def name_cell(contest: Contest, site_id: str) -> str:
    """"Name (ID)" for a site ID: the site's own Name + ID text when the file has it."""
    for c in contest.candidates:
        if c.site_id == site_id:
            if c.display and c.display.strip().endswith(f"({site_id})"):
                return c.display.strip()
            p = contest.players.get(c.player_key)
            return f"{p.name if p else c.player_key} ({site_id})"
    return site_id


def cell_id(cell: str) -> str:
    """The site ID in an upload cell: "Name (ID)", "Name (ID) (LOCKED)" or a bare ID."""
    m = re.search(r"\((\d+)\)(?:\s*\(LOCKED\))?\s*$", cell or "")
    return m.group(1) if m else (cell or "").strip()


def _cells(contest: Contest, ids: list[str], names: bool) -> list[str]:
    return [name_cell(contest, i) if names and i else i for i in ids]


def upload_rows(contest: Contest, lineups: list[Lineup], entry_ids: list[str] | None = None,
                only_changed: bool = True, started_ids: set[str] | None = None,
                names: bool | None = None) -> list[list[str]]:
    """Rows for the upload CSV. With entries, lineup i goes to entry_ids[i] (default: file order).
    ``started_ids``: players whose games have started; their cells must not change and they cannot be added.
    ``names``: write "Name (ID)" cells (default: on for DraftKings, which accepts them)."""
    live = started_ids or set()
    names = contest.rules.site == Site.DRAFTKINGS if names is None else names
    if not contest.entries:
        return [list(contest.slot_headers)] + [_cells(contest, _ids(contest, lu), names) for lu in lineups]

    by_id = {e.entry_id: e for e in contest.entries}
    ids = entry_ids or [e.entry_id for e in contest.entries][:len(lineups)]
    if len(ids) < len(lineups):
        raise UploadError(f"{len(lineups)} lineups but only {len(ids)} entries in the file")
    if contest.rules.site == Site.DRAFTKINGS:
        head = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
    else:
        head = ["entry_id", "contest_id", "contest_name", "entry_fee"]
    rows = [head + list(contest.slot_headers)]
    for eid, lu in zip(ids, lineups):
        e = by_id.get(eid)
        if e is None:
            raise UploadError(f"entry {eid} is not in the entries file")
        new = _ids(contest, lu)
        for i, (was, locked) in enumerate(zip(e.current, e.locked)):
            if (locked or was in live) and was and was != new[i]:
                raise UploadError(f"entry {eid}: slot {i + 1} is locked to {was} (game started); lineup has {new[i]}")
            if new[i] in live and new[i] != was:
                raise UploadError(f"entry {eid}: slot {i + 1} adds {new[i]}, whose game has started")
        if only_changed and new == list(e.current):
            continue
        meta = [e.entry_id, e.contest_name, e.contest_id, e.entry_fee or ""] if contest.rules.site == Site.DRAFTKINGS \
            else [e.entry_id, e.contest_id, e.contest_name, e.entry_fee or ""]
        rows.append(meta + _cells(contest, new, names))
    return rows


def write_upload(contest: Contest, lineups: list[Lineup], path: str | Path, **kw) -> Path:
    """Validate first, then replace the file atomically: a failed check never leaves a broken upload."""
    path = Path(path)
    rows = upload_rows(contest, lineups, **kw)
    _atomic_csv(path, rows)
    if contest.rules.site != Site.DRAFTKINGS and not kw.get("names"):  # FD cells stay IDs: add a copy to read
        _atomic_csv(path.with_name(f"{path.stem}_readable.csv"), upload_rows(contest, lineups, **kw, names=True))
    return path


def _atomic_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    os.replace(tmp, path)


def upload_text(contest: Contest, lineups: list[Lineup], **kw) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerows(upload_rows(contest, lineups, **kw))
    return buf.getvalue()
