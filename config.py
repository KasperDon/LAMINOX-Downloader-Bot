import os
from dotenv import load_dotenv

load_dotenv()

# ── Bot ───────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# ── Admin ─────────────────────────────────────────────────
ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

# ── Kanal ─────────────────────────────────────────────────
CHANNEL_USERNAME: str = os.getenv("CHANNEL_USERNAME", "@laminox")
CHANNEL_ID: str = CHANNEL_USERNAME
CHANNEL_URL: str = "https://t.me/" + CHANNEL_USERNAME.lstrip("@")

# ── Yuklab olish ──────────────────────────────────────────
DOWNLOAD_PATH: str = "./downloads"
MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB
COOLDOWN_SECONDS: int = int(os.getenv("COOLDOWN_SECONDS", "15"))

# ── Watermark ─────────────────────────────────────────────
# WATERMARK_ENABLED=true  →  barcha videohlarga watermark qo'shiladi
# WATERMARK_ENABLED=false →  watermark o'chirilgan
WATERMARK_ENABLED: bool = os.getenv("WATERMARK_ENABLED", "true").lower() in ("1", "true", "yes")
WATERMARK_TEXT: str = os.getenv("WATERMARK_TEXT", "@laminox")

# ── FFmpeg siqish ─────────────────────────────────────────
# CRF: 18 = yuqori sifat (katta hajm), 28 = past sifat (kichik hajm)
VIDEO_CRF: int = int(os.getenv("VIDEO_CRF", "28"))
VIDEO_AUDIO_BITRATE: str = os.getenv("VIDEO_AUDIO_BITRATE", "128k")
AUDIO_BITRATE: str = os.getenv("AUDIO_BITRATE", "128k")

# ── Ma'lumotlar bazasi ────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "database.db")
