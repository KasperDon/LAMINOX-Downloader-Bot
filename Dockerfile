FROM python:3.12-slim

# Node.js 20 LTS (bgutil PO token generator uchun zarur)
# + FFmpeg (video/audio), curl (health check)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Avval faqat requirements — Docker layer cache uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# bgutil-ytdlp-pot-provider: YouTube Proof of Origin (PO) token generatori.
# YouTube 2024-yildan yangi videolar uchun PO token talab qiladi.
# Bu plugin yt-dlp ga avtomatik yuklanadi — har bir so'rovda token hosil qiladi.
# Node.js 20 kerak (yuqorida o'rnatildi).
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

# yt-dlp GitHub master + bgutil har safar yangilanadi
CMD ["sh", "-c", "\
  pip install --upgrade --quiet --no-cache-dir \
    'yt-dlp @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz' \
    bgutil-ytdlp-pot-provider \
  && echo '✅ yt-dlp (master) + bgutil PO token yangilandi' \
  && python bot.py"]
