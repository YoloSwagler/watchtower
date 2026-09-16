from pathlib import Path

from watchtower.storage.bookmarks import BookmarkStore


async def test_new_store_starts_empty(tmp_path: Path):
    store = BookmarkStore(tmp_path / "bookmarks.json")
    assert await store.list() == []


async def test_create_returns_and_persists_a_bookmark(tmp_path: Path):
    store = BookmarkStore(tmp_path / "bookmarks.json")
    bookmark = await store.create(name="NOAA Weather", frequency_mhz=162.475, mode="nfm", squelch=5, note="channel 1")

    assert bookmark.name == "NOAA Weather"
    assert bookmark.frequency_mhz == 162.475
    assert bookmark.mode == "nfm"
    assert bookmark.gain_db is None
    assert bookmark.squelch == 5
    assert bookmark.id

    listed = await store.list()
    assert len(listed) == 1
    assert listed[0] == bookmark


async def test_persistence_survives_a_new_store_instance(tmp_path: Path):
    path = tmp_path / "bookmarks.json"
    store1 = BookmarkStore(path)
    await store1.create(name="Local FM", frequency_mhz=101.5, mode="wfm", gain_db=25.0)

    store2 = BookmarkStore(path)
    listed = await store2.list()
    assert len(listed) == 1
    assert listed[0].name == "Local FM"
    assert listed[0].gain_db == 25.0
    assert path.exists()


async def test_update_changes_only_given_fields(tmp_path: Path):
    store = BookmarkStore(tmp_path / "bookmarks.json")
    bookmark = await store.create(name="Unknown Signal", frequency_mhz=433.0, mode="nfm")

    updated = await store.update(bookmark.id, name="Repeater", note="found near the tower")

    assert updated.name == "Repeater"
    assert updated.note == "found near the tower"
    assert updated.frequency_mhz == 433.0  # untouched
    assert updated.updated_at >= bookmark.updated_at
    assert updated.created_at == bookmark.created_at


async def test_update_unknown_id_returns_none(tmp_path: Path):
    store = BookmarkStore(tmp_path / "bookmarks.json")
    assert await store.update("does-not-exist", name="x") is None


async def test_delete_removes_bookmark_and_reports_success(tmp_path: Path):
    store = BookmarkStore(tmp_path / "bookmarks.json")
    bookmark = await store.create(name="Repeater", frequency_mhz=146.94, mode="nfm")

    assert await store.delete(bookmark.id) is True
    assert await store.list() == []
    assert await store.delete(bookmark.id) is False  # already gone


async def test_multiple_bookmarks_independent(tmp_path: Path):
    store = BookmarkStore(tmp_path / "bookmarks.json")
    a = await store.create(name="A", frequency_mhz=100.0, mode="nfm")
    b = await store.create(name="B", frequency_mhz=200.0, mode="wfm")

    await store.delete(a.id)
    listed = await store.list()

    assert len(listed) == 1
    assert listed[0].id == b.id


async def test_corrupt_file_is_treated_as_empty_not_a_crash(tmp_path: Path):
    path = tmp_path / "bookmarks.json"
    path.write_text("not valid json{{{")
    store = BookmarkStore(path)
    assert await store.list() == []

    # a subsequent write should still succeed and overwrite the corrupt file
    bookmark = await store.create(name="A", frequency_mhz=100.0, mode="nfm")
    assert await store.list() == [bookmark]
