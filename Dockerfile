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

# yt-dlp ni har deploy vaqtida yangilaymiz (YouTube tez-tez o'zgaradi)
# Bu qadam COPY . . dan keyin keladi — har yangi commit'da ishga tushadi
RUN pip install --no-cache-dir --upgrade yt-dlp

# Ishlash papkalarini yaratish
RUN mkdir -p downloads logs

# Python optimizatsiyalari
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Tashkent

CMD ["python", "bot.py"]
