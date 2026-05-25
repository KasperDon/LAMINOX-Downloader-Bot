# 🎬 LAMINOX Downloader Bot

Production-ready Telegram media downloader bot — YouTube, Instagram, TikTok uchun.

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-blue)](https://aiogram.dev)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-green)](https://github.com/yt-dlp/yt-dlp)
[![Railway](https://img.shields.io/badge/Deploy-Railway-purple)](https://railway.app)

---

## ✨ Imkoniyatlar

| Funksiya | Tavsif |
|----------|--------|
| 🔴 YouTube | Video, Shorts — MP4 yuklab olish |
| 📸 Instagram | Reels, Post — MP4 yuklab olish |
| 🎵 TikTok | Video — MP4 yuklab olish |
| 🎧 MP3 | Har qanday platformadan audio (128 kbps) |
| 📐 Auto siqish | 720p → 480p → 360p cascading fallback |
| 🎨 Watermark | FFmpeg drawtext — sozlanadi |
| 🔒 Obuna | Kanalga obuna tekshiruvi |
| 🛡 Anti-spam | Cooldown tizimi |
| 📊 Admin panel | Statistika, broadcast |
| 🍪 Cookies | YouTube bot-himoyasidan o'tish |

---

## 🏗 Fayl strukturasi

```
telegram-media-bot/
├── bot.py                 — Asosiy kirish nuqtasi
├── config.py              — Barcha sozlamalar (.env dan)
├── handlers/
│   ├── __init__.py
│   ├── start.py           — /start, obuna tekshiruvi
│   ├── download.py        — Video/audio FSM + cascading quality
│   └── admin.py           — /admin, /stats, /broadcast
├── keyboards/
│   └── inline.py          — Barcha inline klaviaturalar
├── utils/
│   ├── checker.py         — Obuna tekshiruvi
│   ├── database.py        — SQLite (aiosqlite)
│   ├── downloader.py      — yt-dlp + anti-bot
│   ├── helpers.py         — Yordamchi funksiyalar
│   ├── notifications.py   — Admin xabarnomalar
│   └── watermark.py       — FFmpeg siqish + watermark
├── cookies.txt            — YouTube cookies (Git'ga yuklanmaydi!)
├── .env                   — Maxfiy sozlamalar (Git'ga yuklanmaydi!)
├── .env.example           — Sozlamalar namunasi
├── .gitignore
├── Dockerfile
├── railway.json
└── requirements.txt
```

---

## ⚙️ Sozlash

### 1. Repozitoriyni klonlash

```bash
git clone https://github.com/KasperDon/LAMINOX-Downloader-Bot.git
cd LAMINOX-Downloader-Bot
```

### 2. `.env` faylini yaratish

```bash
cp .env.example .env
```

`.env` faylini oching va to'ldiring:

```env
# Majburiy
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321

# Kanal (@username yoki -100xxxxxxx ID)
CHANNEL_USERNAME=@laminox

# Ixtiyoriy
COOLDOWN_SECONDS=15
WATERMARK_ENABLED=true
WATERMARK_TEXT=@laminox
VIDEO_CRF=28
VIDEO_AUDIO_BITRATE=128k
AUDIO_BITRATE=128k

# YouTube cookies
YOUTUBE_COOKIES_ENABLED=true
COOKIES_PATH=cookies.txt

# Proxy (ixtiyoriy — bo'sh qoldirilsa proxy ishlatilmaydi)
# Formatlar: http://host:port  |  socks5://user:pass@host:port
PROXY_URL=

# Database
DB_PATH=database.db
```

### 3. Talablar

- Python 3.12+
- FFmpeg (`sudo apt install ffmpeg`)
- Bot kanalda admin bo'lishi kerak (obuna tekshiruvi uchun)

### 4. Virtual muhit va ishga tushirish

```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
# yoki: venv\Scripts\activate  (Windows)

pip install -r requirements.txt
python bot.py
```

---

## 🍪 YouTube Cookies (Anti-Bot)

YouTube "Sign in to confirm you're not a bot" xatoligini hal qilish uchun.

### Cookies.txt qanday olish?

#### Chrome Extension orqali (tavsiya etiladi):
1. Chrome'da **[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** extension'ini o'rnating
2. **YouTube.com**'ga kiring (Google akkauntingiz bilan)
3. Extension ikonkasiga bosing → **Export** → **cookies.txt**
4. Faylni loyiha papkasiga joylashtiring:
   ```
   telegram-media-bot/
   └── cookies.txt   ← shu yerga
   ```

#### Firefox orqali:
1. **[cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)** addon'ini o'rnating
2. YouTube'ga kiring
3. Extension orqali eksport qiling

### ⚠️ Muhim qoidalar:
- `cookies.txt` ni **hech qachon GitHub'ga push qilmang** (`.gitignore`da bloklanган)
- Cookies **30 kun** da bir yangilanishi kerak
- Bitta akkauntdan ko'p so'rov yuborilsa, YouTube bloklashi mumkin

---

## 🚀 Railway Deploy

### 1-qadam: GitHub'ga ulash

1. [Railway.app](https://railway.app) ga kiring
2. **New Project** → **Deploy from GitHub repo**
3. `LAMINOX-Downloader-Bot` repozitoriyasini tanlang

### 2-qadam: Variables qo'shish

Railway dashboard → **Variables** bo'limiga o'ting va quyidagilarni qo'shing:

```
BOT_TOKEN        = 7xxxxxxxxxx:AAF...
ADMIN_IDS        = 123456789
CHANNEL_USERNAME = @laminox
COOLDOWN_SECONDS = 15
WATERMARK_ENABLED = true
WATERMARK_TEXT   = @laminox
YOUTUBE_COOKIES_ENABLED = true
COOKIES_PATH     = /app/cookies.txt
VIDEO_CRF        = 28
DB_PATH          = database.db

# Ixtiyoriy — proxy (bo'sh qoldirilsa ishlatilmaydi)
# Formatlar: http://host:port  |  socks5://user:pass@host:port
PROXY_URL        =
```

### 3-qadam: Cookies.txt ni Railway'ga yuklash

Railway'da persistent fayl saqlash uchun ikki usul mavjud:

#### Usul A — Railway Volume (tavsiya):
1. Railway dashboard → **Volumes** → **Add Volume**
2. Mount path: `/app/data`
3. `COOKIES_PATH = /app/data/cookies.txt` deb o'zgartiring
4. SSH yoki Railway CLI orqali `cookies.txt` ni yuklab qo'ying:
   ```bash
   railway run -- cp /local/cookies.txt /app/data/cookies.txt
   ```

#### Usul B — Base64 Environment Variable:
1. `cookies.txt` ni base64'ga o'zgartiring:
   ```bash
   base64 -w 0 cookies.txt
   ```
2. Natijani `COOKIES_B64` nomli env variable sifatida qo'shing
3. `bot.py` ga quyidagi kodni qo'shing (startup_checks ichida):
   ```python
   import base64
   b64 = os.getenv("COOKIES_B64")
   if b64:
       with open("cookies.txt", "wb") as f:
           f.write(base64.b64decode(b64))
   ```

#### Usul C — Startup Script:
Maxsus `startup.sh` yozing va Railway CMD sifatida ishga tushiring.

### 4-qadam: Deploy

Railway avtomatik build qiladi. Loglarni tekshiring:
```
✅ YouTube cookies topildi: /app/cookies.txt
✅ Database tayyor
🤖 Bot ishga tushdi: @YourBotName
```

---

## 🖥 VPS Deploy (systemd)

```bash
# Bot fayllarini nusxa ko'chirish
git clone https://github.com/KasperDon/LAMINOX-Downloader-Bot.git /opt/laminox-bot
cd /opt/laminox-bot

# Muhit
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# .env fayli
cp .env.example .env
nano .env  # to'ldiring

# cookies.txt ni joylashtiring
cp ~/cookies.txt /opt/laminox-bot/cookies.txt

# Systemd service
sudo tee /etc/systemd/system/laminox-bot.service << EOF
[Unit]
Description=LAMINOX Downloader Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/laminox-bot
ExecStart=/opt/laminox-bot/venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now laminox-bot
sudo systemctl status laminox-bot

# Loglarni kuzatish
journalctl -u laminox-bot -f
```

---

## 👨‍💼 Admin buyruqlari

| Buyruq | Tavsif |
|--------|--------|
| `/admin` | Admin panelni ochish |
| `/stats` | To'liq statistika |
| `/broadcast` | Barcha foydalanuvchilarga xabar |

**Admin bo'lish uchun:** `ADMIN_IDS` ga Telegram user ID'ingizni qo'shing.  
ID'ingizni [@userinfobot](https://t.me/userinfobot) orqali bilib oling.

---

## 🔧 Texnologiyalar

| Texnologiya | Versiya | Maqsad |
|------------|---------|--------|
| Python | 3.12 | Asosiy til |
| Aiogram | 3.13.1 | Telegram Bot framework |
| yt-dlp | latest | Media yuklab olish |
| FFmpeg | system | Video siqish + watermark |
| aiosqlite | 0.20.0 | Asinxron SQLite |
| aiohttp | 3.10.11 | HTTP client |

---

## 🐛 Muammolar va yechimlar

| Muammo | Sabab | Yechim |
|--------|-------|--------|
| "Sign in to confirm you're not a bot" | YouTube bot taniqladi | cookies.txt qo'shing |
| TelegramConflictError | 2 ta bot instance ishlayapti | Faqat bitta deploy bo'lsin |
| "Failed to extract player response" | yt-dlp eskirgan | Docker restart (auto-update bo'ladi) |
| Fayl 50MB dan katta | Video uzun | Bot avtomatik 480p/360p'ga tushadi |
| Obuna tekshiruvi ishlamaydi | Bot kanal admin emas | Botni kanalga admin qiling |

---

## 📄 Litsenziya

MIT © 2025 LAMINOX
