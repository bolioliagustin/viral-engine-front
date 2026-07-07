FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg for video processing, yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create directories for media files
RUN mkdir -p downloads clips logs

CMD ["python", "main.py"]
