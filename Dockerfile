FROM python:3.12-slim

# Node.js 20 LTS + git (bgutil server uchun)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        nodejs \
        git \
    && rm -rf /var/lib/apt/lists/*

# bgutil-ytdlp-pot-provider server qurish (YouTube PO token generator)
# Bu server yt-dlp so'rovida real PO token hosil qilib beradi.
# Port 4416 da ishlaydi — bot.py undan foydalanadi.
RUN git clone --depth=1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil \
    && cd /bgutil && npm ci && npx tsc \
    && echo '✅ bgutil server qurildi'

WORKDIR /app

# Avval faqat requirements — Docker layer cache uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp bgutil plugin (Python tomoni — server bilan gaplashadi)
RUN pip install bgutil-ytdlp-pot-provider

# Qolgan kod
COPY . .

# Ishlash papkalarini yaratish
RUN mkdir -p downloads logs

# Python optimizatsiyalari
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Tashkent

# Ishga tushirish tartibi:
# 1. yt-dlp + bgutil plugin yangilash
# 2. bgutil Node.js server background'da ishga tushirish (port 4416)
# 3. 3 soniya kutish (server tayyor bo'lsin)
# 4. bot.py ishga tushirish
CMD ["sh", "-c", "\
  pip install --upgrade --quiet --no-cache-dir \
    'yt-dlp @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz' \
    bgutil-ytdlp-pot-provider \
  && echo '✅ yt-dlp (master) + bgutil yangilandi' \
  && node /bgutil/build/main.js & \
  sleep 3 \
  && echo '✅ bgutil PO token server port 4416 da ishga tushdi' \
  && python bot.py"]
