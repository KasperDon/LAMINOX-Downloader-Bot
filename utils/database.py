"""
utils/database.py
─────────────────
aiosqlite asosidagi barcha DB operatsiyalari.

Jadvallar:
  users     — foydalanuvchilar
  downloads — har bir yuklab olish logi
"""

import aiosqlite
from datetime import datetime
from config import DB_PATH


# ──────────────────────────────────────────────────────────
# Init
# ──────────────────────────────────────────────────────────

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
        # Tezlik uchun indekslar
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dl_user ON downloads(user_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_dl_date ON downloads(created_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active)"
        )
        await db.commit()


# ──────────────────────────────────────────────────────────
# Foydalanuvchi
# ──────────────────────────────────────────────────────────

async def is_new_user(user_id: int) -> bool:
    """
    Foydalanuvchi avval botni ishlatmaganini tekshiradi.
    True  → birinchi marta (yangi)
    False → allaqachon bazada bor
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is None


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


async def log_download(
    user_id: int, platform: str, media_type: str, url: str
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO downloads (user_id, platform, media_type, url, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, platform, media_type, url, datetime.now().isoformat()))
        await db.commit()


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return [r[0] for r in await cur.fetchall()]


# ──────────────────────────────────────────────────────────
# Statistika — asosiy (admin panel uchun)
# ──────────────────────────────────────────────────────────

async def get_stats() -> dict:
    """Admin panel kartasidagi qisqa statistika."""
    async with aiosqlite.connect(DB_PATH) as db:

        async def scalar(q: str, params: tuple = ()) -> int:
            async with db.execute(q, params) as cur:
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


# ──────────────────────────────────────────────────────────
# Statistika — kengaytirilgan (/stats buyrug'i uchun)
# ──────────────────────────────────────────────────────────

async def get_full_stats() -> dict:
    """
    To'liq statistika:
      - jami + bugungi ko'rsatkichlar
      - oxirgi 10 ta yuklab olish
      - eng faol 10 ta foydalanuvchi
    """
    today = datetime.now().date().isoformat()  # "2026-05-25"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async def scalar(q: str, params: tuple = ()) -> int:
            async with db.execute(q, params) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

        # ── Jami ──────────────────────────────────────────
        total_users     = await scalar("SELECT COUNT(*) FROM users")
        total_downloads = await scalar("SELECT COUNT(*) FROM downloads")
        total_video     = await scalar("SELECT COUNT(*) FROM downloads WHERE media_type='video'")
        total_audio     = await scalar("SELECT COUNT(*) FROM downloads WHERE media_type='audio'")
        total_youtube   = await scalar("SELECT COUNT(*) FROM downloads WHERE platform='youtube'")
        total_instagram = await scalar("SELECT COUNT(*) FROM downloads WHERE platform='instagram'")
        total_tiktok    = await scalar("SELECT COUNT(*) FROM downloads WHERE platform='tiktok'")

        # ── Bugun ─────────────────────────────────────────
        today_users = await scalar(
            "SELECT COUNT(*) FROM users WHERE substr(joined_at, 1, 10) = ?",
            (today,)
        )
        today_active = await scalar(
            "SELECT COUNT(*) FROM users WHERE substr(last_active, 1, 10) = ?",
            (today,)
        )
        today_downloads = await scalar(
            "SELECT COUNT(*) FROM downloads WHERE substr(created_at, 1, 10) = ?",
            (today,)
        )
        today_video = await scalar(
            "SELECT COUNT(*) FROM downloads "
            "WHERE media_type='video' AND substr(created_at, 1, 10) = ?",
            (today,)
        )
        today_audio = await scalar(
            "SELECT COUNT(*) FROM downloads "
            "WHERE media_type='audio' AND substr(created_at, 1, 10) = ?",
            (today,)
        )

        # ── Oxirgi 10 ta yuklab olish ─────────────────────
        async with db.execute("""
            SELECT
                d.platform,
                d.media_type,
                substr(d.created_at, 1, 16) AS dl_time,
                COALESCE(NULLIF(u.username,''), u.full_name, 'Noma''lum') AS display_name
            FROM downloads d
            LEFT JOIN users u ON d.user_id = u.user_id
            ORDER BY d.id DESC
            LIMIT 10
        """) as cur:
            last_10 = [dict(r) for r in await cur.fetchall()]

        # ── Eng faol 10 ta foydalanuvchi ─────────────────
        async with db.execute("""
            SELECT
                COALESCE(NULLIF(username,''), full_name, 'Noma''lum') AS display_name,
                dl_count
            FROM users
            WHERE dl_count > 0
            ORDER BY dl_count DESC
            LIMIT 10
        """) as cur:
            top_10 = [dict(r) for r in await cur.fetchall()]

    return {
        # Jami
        "users":          total_users,
        "downloads":      total_downloads,
        "video":          total_video,
        "audio":          total_audio,
        "youtube":        total_youtube,
        "instagram":      total_instagram,
        "tiktok":         total_tiktok,
        # Bugun
        "today_users":    today_users,
        "today_active":   today_active,
        "today_downloads": today_downloads,
        "today_video":    today_video,
        "today_audio":    today_audio,
        # Ro'yxatlar
        "last_10":        last_10,
        "top_10":         top_10,
    }
