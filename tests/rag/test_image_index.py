"""Image index tests. Fake CLIP (colour-based) + in-memory SQLite: no model download, no Postgres.
Needs `aiosqlite` (dev only):  pip install aiosqlite"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("aiosqlite")
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import backend.models.camera as camera_model                                  # noqa: E402,F401
from backend.agent.tools import image_tools                                   # noqa: E402
from backend.core.config import settings                                      # noqa: E402
from backend.core.database import Base                                        # noqa: E402
from backend.models.alert import AlertLog                                     # noqa: E402
from backend.rag import image_index                                           # noqa: E402

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)
WORDS = {"red": RED, "green": GREEN, "blue": BLUE}


class FakeClip:
    """Image -> mean-RGB direction; text -> direction of the colour words it mentions."""
    def encode(self, items, batch_size=None, normalize_embeddings=True):
        out = []
        for it in items:
            if isinstance(it, str):
                v = np.full(3, 0.05)
                for w, c in WORDS.items():
                    if w in it.lower():
                        v = v + np.array(c, float)
            else:
                v = np.asarray(it.convert("RGB").resize((8, 8))).reshape(-1, 3).mean(0) + 0.05
            out.append(v / np.linalg.norm(v))
        return np.array(out)


@pytest.fixture
def env(tmp_path, monkeypatch):
    media = tmp_path / "media"
    (media / "snapshots").mkdir(parents=True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(media))
    monkeypatch.setattr(image_index, "INDEX_DIR", tmp_path / "rag" / "index")
    monkeypatch.setattr(image_index, "_col", None)
    monkeypatch.setattr(image_index, "_last_catchup", 0.0)
    image_index._bad.clear()
    fake = FakeClip()
    monkeypatch.setattr(image_index, "_clip", lambda: fake)

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    sess = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(image_index, "AsyncSessionLocal", sess)

    async def init():
        async with eng.begin() as c:
            await c.run_sync(lambda cn: Base.metadata.create_all(
                cn, tables=[camera_model.CameraDevice.__table__, AlertLog.__table__]))
    asyncio.run(init())

    class E:
        pass
    e = E(); e.media, e.sess = media, sess
    return e


def _img(env, name, color, size=(32, 32)):
    Image.new("RGB", size, color).save(env.media / "snapshots" / name, "JPEG")
    return f"snapshots/{name}"


async def _alert(env, snapshot, ts, cam=1, unknown=False):
    async with env.sess() as db:
        a = AlertLog(violation_type="Unknown Face", snapshot=snapshot, timestamp=ts,
                     unknown_person=unknown, module="attendance")
        a.camera_id = cam
        db.add(a)
        await db.commit()
        return a.id


T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def test_search_ranks_by_content_and_applies_filters(env):
    async def go():
        ids = {}
        for i, (n, c) in enumerate([("red", RED), ("green", GREEN), ("blue", BLUE)]):
            ids[n] = await _alert(env, _img(env, f"{n}.jpg", c), T0 + timedelta(days=i),
                                  cam=i + 1, unknown=(n != "green"))
        assert await image_index.index_new(100) == 3

        r = await image_tools.search_snapshots({"query": "person wearing red"})
        assert r["matches"][0]["alert_id"] == ids["red"] and r["searched"] == 3

        # camera + unknown filters combine with AND
        r = await image_tools.search_snapshots({"query": "red", "unknown_only": True, "camera_id": 2})
        assert r["found"] == 0                       # camera 2 is the (known) green one
        # Karachi calendar day: 2026-10-06 12:00 UTC = 17:00 local, only the green alert
        r = await image_tools.search_snapshots(
            {"query": "red", "date_from": "2026-10-06", "date_to": "2026-10-06"})
        assert [m["alert_id"] for m in r["matches"]] == [ids["green"]]
        assert r["matches"][0]["time"].endswith("17:00")
    asyncio.run(go())


def test_windows_backslash_paths_are_indexed_and_served_with_slashes(env):
    async def go():
        rel = _img(env, "a_1.jpg", RED).replace("/", "\\")        # as stored by os.path.join on Windows
        await _alert(env, rel, T0)
        assert await image_index.index_new(100) == 1
        r = await image_tools.search_snapshots({"query": "red"})
        assert r["matches"][0]["snapshot"] == "/media/snapshots/a_1.jpg"
    asyncio.run(go())


def test_burst_larger_than_limit_is_caught_up_on_next_pass(env):
    async def go():
        for i in range(25):
            await _alert(env, _img(env, f"s{i}.jpg", RED, (8, 8)), T0 + timedelta(seconds=i))
        assert await image_index.index_new(limit=10) == 10
        assert await image_index.index_new(limit=10) == 10
        assert await image_index.index_new(limit=10) == 5
        assert await image_index.index_new(limit=10) == 0
        assert image_index.count() == 25
    asyncio.run(go())


def test_backfill_drains_everything_and_skips_corrupt_files(env):
    async def go():
        for i in range(7):
            await _alert(env, _img(env, f"ok{i}.jpg", GREEN, (8, 8)), T0 + timedelta(seconds=i))
        (env.media / "snapshots" / "bad.jpg").write_bytes(b"not a jpeg")
        await _alert(env, "snapshots/bad.jpg", T0 + timedelta(seconds=99))   # newest, corrupt
        assert await image_index.backfill(chunk=3) == 7
        assert image_index.count() == 7
    asyncio.run(go())


def test_deleted_snapshot_is_not_returned(env):
    async def go():
        rel = _img(env, "gone.jpg", RED)
        await _alert(env, rel, T0)
        await image_index.index_new(100)
        (env.media / "snapshots" / "gone.jpg").unlink()
        r = await image_tools.search_snapshots({"query": "red"})
        assert r["found"] == 0 and r["searched"] == 1
    asyncio.run(go())


def test_similarity_floor(env):
    async def go():
        await _alert(env, _img(env, "b.jpg", BLUE), T0)
        await image_index.index_new(100)
        assert await image_index.search("red", min_score=0.0)             # returned without floor
        assert await image_index.search("red", min_score=0.9) == []       # filtered by floor
    asyncio.run(go())