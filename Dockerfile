FROM python:3.12-slim

# Tizim paketlari: FFmpeg (audio ajratish) + curl (health check)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Avval faqat requirements — layer cache uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Qolgan kod
COPY . .

# Ishlash papkalari
RUN mkdir -p downloads logs

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
