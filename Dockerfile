FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies: ffmpeg for stream merging/MP3 conversion, curl, certs, and fonts for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt && \
    pip install --upgrade yt-dlp

COPY assets /app/assets
COPY bot /app/bot
COPY data /app/data

# Generate welcome_banner.png in brand palette using Pillow during image build
RUN python /app/assets/generate_banner.py

CMD ["python", "-m", "bot.main"]
