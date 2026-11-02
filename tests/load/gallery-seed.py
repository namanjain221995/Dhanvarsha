"""Fills the isolated load-test copy with a gallery of a realistic size, so reading it is really tested.

Run by tests/load/gallery-run.sh only. It refuses a database that is not the load-test copy's.
"""

import asyncio
import io
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import numpy as np
from PIL import Image

PEOPLE, UNNAMED, FILES, FACES = (int(value) for value in sys.argv[1:5])


async def main() -> None:
    assert os.environ["APP_BASE_URL"].endswith(":3190"), "this seeds only the load-test copy"
    url = os.environ["DATABASE_MIGRATION_URL"].replace("postgresql+asyncpg", "postgresql")
    db = await asyncpg.connect(url)
    programme = await db.fetchval("SELECT id FROM programmes WHERE is_active LIMIT 1")
    root = Path(os.environ["MEDIA_ROOT"])
    picture = io.BytesIO()
    Image.new("RGB", (640, 480), (40, 60, 120)).save(picture, format="WEBP", quality=80)
    key = f"{uuid.uuid4().hex}.webp"
    (root / key).write_bytes(picture.getvalue())
    rng = np.random.default_rng(7)
    people = [uuid.uuid4() for _ in range(PEOPLE + UNNAMED)]

    def embedding() -> bytes:
        vector = rng.normal(size=128).astype(np.float32)
        return (vector / np.linalg.norm(vector)).tobytes()

    await db.copy_records_to_table(
        "gallery_people",
        columns=["id", "programme_id", "name", "embedding", "aligned", "avatar_key", "enrolled_at"],
        records=[
            (pid, programme, f"Seed Person {index}", embedding(), b"x", key, None)
            if index < PEOPLE
            else (pid, programme, "", None, None, key, None)
            for index, pid in enumerate(people)
        ],
    )
    await db.execute("UPDATE gallery_people SET enrolled_at = now() WHERE name <> ''")
    files = [uuid.uuid4() for _ in range(FILES)]
    await db.copy_records_to_table(
        "gallery_media",
        columns=["id", "programme_id", "kind", "status", "storage_key", "display_key", "thumb_key", "content_type", "byte_size", "sha256", "width", "height", "original_name", "analysis"],
        records=[
            (mid, programme, "image", "ready", key, key, key, "image/webp", 3_500_000, uuid.uuid4().hex, 640, 480, f"seed-{index}.jpg", '{"frames_read": 1}')
            for index, mid in enumerate(files)
        ],
    )
    seen: set[uuid.UUID] = set()
    records = []
    for index in range(FACES):
        person = people[int(rng.integers(len(people)))]
        records.append((uuid.uuid4(), files[index % FILES], person, embedding(), b"x", key, 0.5, "new", person not in seen, "{}"))
        seen.add(person)
    await db.copy_records_to_table(
        "gallery_faces",
        columns=["id", "media_id", "person_id", "embedding", "aligned", "crop_key", "quality", "matched_by", "is_reference", "analysis"],
        records=records,
    )
    await db.execute("ANALYZE gallery_people; ANALYZE gallery_media; ANALYZE gallery_faces")
    await db.close()
    print(f"seeded {PEOPLE} named, {UNNAMED} unnamed, {FILES} files, {FACES} faces")


asyncio.run(main())
