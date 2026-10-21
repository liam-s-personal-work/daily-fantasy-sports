"""Write the upload file the site accepts back.

- Entries file in -> edited entries file out (same metadata columns, slot header as the
  site wrote it, one player ID per slot, only entries whose lineup changed unless asked).
- Salary/player list in -> blank-template upload: slot header row then one row of IDs per lineup.

DK accepts the bare ``ID`` (or "Name (ID)"); FD accepts the ``Id`` ("123-456"). We write IDs.
"""

from __future__ import annotations

import csv
import os
import io
from pathlib import Path

from dfs.models import Contest, Lineup, Site


class UploadError(ValueError):
    pass


def _ids(contest: Contest, lineup: Lineup) -> list[str]:
    if [s.slot for s in lineup.slots] != [s.name for s in contest.rules.slots]:
        raise UploadError("lineup slots do not match the contest's slot order")
    return [s.site_id for s in lineup.slots]


def upload_rows(contest: Contest, lineups: list[Lineup], entry_ids: list[str] | None = None,
                only_changed: bool = True, started_ids: set[str] | None = None) -> list[list[str]]:
    """Rows for the upload CSV. With entries, lineup i goes to entry_ids[i] (default: file order).
    ``started_ids``: players whose games have started; their cells must not change and they cannot be added."""
    live = started_ids or set()
    if not contest.entries:
        return [list(contest.slot_headers)] + [_ids(contest, lu) for lu in lineups]

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
        rows.append(meta + new)
    return rows


def write_upload(contest: Contest, lineups: list[Lineup], path: str | Path, **kw) -> Path:
    """Validate first, then replace the file atomically: a failed check never leaves a broken upload."""
    path = Path(path)
    rows = upload_rows(contest, lineups, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    os.replace(tmp, path)
    return path


def upload_text(contest: Contest, lineups: list[Lineup], **kw) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerows(upload_rows(contest, lineups, **kw))
    return buf.getvalue()
