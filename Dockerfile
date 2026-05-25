FROM python:3.12-slim

# Tizim paketlari: FFmpeg (video/audio), curl (health check)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Avval faqat requirements — Docker layer cache uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Qolgan kod
COPY . .

# Ishlash papkalarini yaratish
RUN mkdir -p downloads logs

# Python optimizatsiyalari
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Tashkent

# yt-dlp GitHub master'dan o'rnatiladi — PyPI cache'ni chetlab o'tadi.
# GitHub master har kuni yangilanadi, PyPI versiyasidan 2-4 hafta ilgari.
CMD ["sh", "-c", "\
  pip install --upgrade --quiet --no-cache-dir \
    'yt-dlp @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz' \
  && echo '✅ yt-dlp (master) yangilandi' \
  && python bot.py"]
