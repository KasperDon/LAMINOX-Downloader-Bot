import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

# CHANNEL_USERNAME — masalan @laminox
CHANNEL_USERNAME: str = os.getenv("CHANNEL_USERNAME", "@laminox")
# get_chat_member uchun to'g'ridan-to'g'ri username ishlatiladi
CHANNEL_ID: str = CHANNEL_USERNAME
# Subscription tugmasi uchun URL avtomatik hosil qilinadi
CHANNEL_URL: str = "https://t.me/" + CHANNEL_USERNAME.lstrip("@")

DOWNLOAD_PATH: str = "./downloads"
MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB

COOLDOWN_SECONDS: int = int(os.getenv("COOLDOWN_SECONDS", "15"))

DB_PATH: str = os.getenv("DB_PATH", "database.db")
