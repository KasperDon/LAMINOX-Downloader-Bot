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

# yt-dlp YouTube tez-tez o'zgaradi — har container ishga tushganda yangilaymiz.
# Build vaqtida emas, RUNTIME'da yangilanadi → har doim eng yangi versiya.
CMD ["sh", "-c", "pip install --upgrade --quiet yt-dlp && echo '✅ yt-dlp yangilandi' && python bot.py"]
