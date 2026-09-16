"""JSON-file-backed store for saved frequency bookmarks.

No SQLite, no ORM — a small local JSON file (see ARCHITECTURE.md, "Saved
frequencies (bookmarks)"). Writes are atomic (write to a temp file in the
same directory, then os.replace) since this runs on a Pi's SD card in the
field, where a mid-write power loss is a real if rare risk. Reads/writes
are serialized by one asyncio.Lock — there is exactly one browser tab/
operator, so this only prevents two near-simultaneous requests from
interleaving a read-modify-write, not solving a real concurrency problem.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from watchtower.logging_setup import get_logger

logger = get_logger("storage.bookmarks")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Bookmark:
    id: str
    name: str
    frequency_mhz: float
    mode: str
    gain_db: float | None = None  # None = Auto
    squelch: int = 0
    note: str = ""
    created_at: str = ""
    updated_at: str = ""


class BookmarkStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def list(self) -> list[Bookmark]:
        async with self._lock:
            return self._load()

    async def create(
        self,
        name: str,
        frequency_mhz: float,
        mode: str,
        gain_db: float | None = None,
        squelch: int = 0,
        note: str = "",
    ) -> Bookmark:
        async with self._lock:
            bookmarks = self._load()
            now = _now_iso()
            bookmark = Bookmark(
                id=uuid.uuid4().hex[:12],
                name=name,
                frequency_mhz=frequency_mhz,
                mode=mode,
                gain_db=gain_db,
                squelch=squelch,
                note=note,
                created_at=now,
                updated_at=now,
            )
            bookmarks.append(bookmark)
            self._save(bookmarks)
            return bookmark

    async def update(self, bookmark_id: str, **fields) -> Bookmark | None:
        async with self._lock:
            bookmarks = self._load()
            for i, existing in enumerate(bookmarks):
                if existing.id == bookmark_id:
                    updated = replace(existing, **fields, updated_at=_now_iso())
                    bookmarks[i] = updated
                    self._save(bookmarks)
                    return updated
            return None

    async def delete(self, bookmark_id: str) -> bool:
        async with self._lock:
            bookmarks = self._load()
            remaining = [b for b in bookmarks if b.id != bookmark_id]
            if len(remaining) == len(bookmarks):
                return False
            self._save(remaining)
            return True

    def _load(self) -> list[Bookmark]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("bookmarks file unreadable (%s); treating as empty", e)
            return []
        entries = raw.get("bookmarks", []) if isinstance(raw, dict) else []
        bookmarks: list[Bookmark] = []
        for entry in entries:
            try:
                bookmarks.append(Bookmark(**entry))
            except TypeError:
                logger.warning("skipping malformed bookmark entry: %r", entry)
        return bookmarks

    def _save(self, bookmarks: list[Bookmark]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bookmarks": [asdict(b) for b in bookmarks]}
        fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), prefix=".bookmarks-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
