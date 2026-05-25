import aiosqlite
from datetime import datetime
from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT    DEFAULT '',
                full_name   TEXT    DEFAULT '',
                joined_at   TEXT    NOT NULL,
                last_active TEXT    NOT NULL,
                dl_count    INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                platform    TEXT    NOT NULL,
                media_type  TEXT    NOT NULL,
                url         TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            )
        """)
        await db.commit()


async def upsert_user(user_id: int, username: str, full_name: str) -> None:
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, joined_at, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username    = excluded.username,
                full_name   = excluded.full_name,
                last_active = excluded.last_active
        """, (user_id, username, full_name, now, now))
        await db.commit()


async def increment_downloads(user_id: int) -> None:
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET dl_count = dl_count + 1, last_active = ?
            WHERE user_id = ?
        """, (now, user_id))
        await db.commit()


async def log_download(user_id: int, platform: str, media_type: str, url: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO downloads (user_id, platform, media_type, url, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, platform, media_type, url, datetime.now().isoformat()))
        await db.commit()


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async def scalar(q: str) -> int:
            async with db.execute(q) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

        return {
            "users":     await scalar("SELECT COUNT(*) FROM users"),
            "downloads": await scalar("SELECT COUNT(*) FROM downloads"),
            "youtube":   await scalar("SELECT COUNT(*) FROM downloads WHERE platform='youtube'"),
            "instagram": await scalar("SELECT COUNT(*) FROM downloads WHERE platform='instagram'"),
            "tiktok":    await scalar("SELECT COUNT(*) FROM downloads WHERE platform='tiktok'"),
            "video":     await scalar("SELECT COUNT(*) FROM downloads WHERE media_type='video'"),
            "audio":     await scalar("SELECT COUNT(*) FROM downloads WHERE media_type='audio'"),
        }


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]
