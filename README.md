# 🎬 MediaLoader Pro Bot

Telegram media downloader bot — YouTube, Instagram, TikTok uchun.

## Imkoniyatlar

- 🔴 YouTube video / Shorts (MP4)
- 📸 Instagram Reels / Post (MP4)
- 🎵 TikTok video (MP4)
- 🎧 MP3 audio (har qanday platformadan)
- ✅ Kanalga obuna tekshiruvi
- 🛡 Anti-spam cooldown
- 📊 Admin panel + broadcast
- 🪵 Logging tizimi

## Texnologiyalar

- Python 3.12
- Aiogram 3.x
- yt-dlp
- FFmpeg
- aiosqlite
- asyncio

---

## O'rnatish

### 1. Talablar

- Python 3.12+
- FFmpeg (`sudo apt install ffmpeg` yoki Windows uchun https://ffmpeg.org/download.html)

### 2. Loyihani yuklab olish

```bash
git clone <repo-url>
cd telegram-media-bot
```

### 3. Virtual muhit va paketlar

```bash
python -m venv venv
# Linux/Mac:
source venv/bin/activate
# Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

### 4. Konfiguratsiya

```bash
cp .env.example .env
```

`.env` faylini oching va to'ldiring:

```env
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789
CHANNEL_ID=@laminox
CHANNEL_URL=https://t.me/laminox
COOLDOWN_SECONDS=15
DB_PATH=database.db
```

**Bot tokenini** @BotFather orqali oling.  
**Admin ID** — Telegram user ID'ingiz (@userinfobot orqali bilib olishingiz mumkin).  
**Bot kanal admini bo'lishi shart** — aks holda obuna tekshiruvi ishlamaydi.

### 5. Ishga tushirish

```bash
python bot.py
```

---

## Docker orqali ishga tushirish

```bash
# Image qurish va ishga tushirish
docker-compose up -d

# Loglarni ko'rish
docker-compose logs -f

# To'xtatish
docker-compose down
```

---

## Railway Deploy

1. Railway.app'ga kiring
2. "New Project" → "Deploy from GitHub"
3. Repo'ni tanlang
4. "Variables" bo'limida `.env` qiymatlarini kiriting
5. Deploy tugmasini bosing

---

## VPS Deploy (systemd)

```bash
sudo nano /etc/systemd/system/mediabot.service
```

```ini
[Unit]
Description=MediaLoader Pro Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/telegram-media-bot
ExecStart=/home/ubuntu/telegram-media-bot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable mediabot
sudo systemctl start mediabot
sudo systemctl status mediabot
```

---

## Admin buyruqlari

| Buyruq | Tavsif |
|--------|--------|
| `/admin` | Admin panelni ochish |
| Broadcast | Barcha foydalanuvchilarga xabar yuborish |

---

## Fayl strukturasi

```
telegram-media-bot/
├── bot.py              — Asosiy kirish nuqtasi
├── config.py           — Konfiguratsiya
├── handlers/
│   ├── start.py        — /start, obuna tekshiruvi, yordam
│   ├── download.py     — Video/audio yuklash (FSM)
│   └── admin.py        — Admin panel, broadcast
├── keyboards/
│   └── inline.py       — Barcha inline klaviaturalar
├── utils/
│   ├── database.py     — SQLite asinxron operatsiyalar
│   ├── downloader.py   — yt-dlp integratsiya
│   ├── checker.py      — Obuna tekshiruvi
│   └── helpers.py      — Yordamchi funksiyalar
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── railway.json
```

---

## Litsenziya

MIT
