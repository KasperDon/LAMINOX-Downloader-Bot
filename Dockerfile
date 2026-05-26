FROM python:3.12-slim

# 1. Avval curl o'rnatamiz (NodeSource setup uchun kerak)
# 2. NodeSource orqali Node.js 20 LTS qo'shamiz (bgutil uchun)
# 3. Qolgan paketlar: ffmpeg, git
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        nodejs \
        git \
    && rm -rf /var/lib/apt/lists/*

# bgutil-ytdlp-pot-provider server qurish
# YouTube 2024-yildan PO token talab qiladi — bu server uni hosil qiladi.
# Port 4416 da ishlaydi. bot.py ishga tushishdan oldin start bo'ladi.
RUN git clone --depth=1 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil \
    && cd /bgutil && npm install && npx tsc \
    && echo '✅ bgutil Node.js server qurildi'

WORKDIR /app

# Avval faqat requirements — Docker layer cache uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# bgutil-ytdlp-pot-provider Python plugin (yt-dlp bilan gaplashadi)
RUN pip install bgutil-ytdlp-pot-provider

# Qolgan kod
COPY . .

# Ishlash papkalarini yaratish
RUN mkdir -p downloads logs

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Tashkent

# Ishga tushirish:
# 1. yt-dlp master + bgutil plugin yangilash
# 2. bgutil Node.js server background'da ishga tushirish (port 4416)
# 3. 3s kutish (server ready bo'lsin)
# 4. bot.py ishga tushirish
CMD ["sh", "-c", "\
  pip install --upgrade --quiet --no-cache-dir \
    'yt-dlp @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz' \
    bgutil-ytdlp-pot-provider \
  && echo '✅ yt-dlp master + bgutil yangilandi' \
  && node /bgutil/build/main.js & \
  sleep 3 \
  && echo '✅ bgutil PO token server port 4416 da tayyor' \
  && python bot.py"]
