FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg for video processing, yt-dlp).
# fonts-liberation / fonts-dejavu-core: libass (filtro `ass=` de subtítulos
# y overlay, services/clip_generator.py) resuelve "Liberation Sans" por
# nombre vía fontconfig — sin el paquete instalado, no hay garantía de que
# esa familia exista en la imagen y el texto puede caer a un fallback
# arbitrario (W11, docs/PLAN_CALIDAD.md §9 Fase 1). La fuente cómic del
# estilo tiktok_viral_v2 (Bangers, worker/fonts/) NO se instala acá —
# se pasa como `fontsdir` al filtro `ass=` en tiempo de render, sin tocar
# fontconfig del sistema.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create directories for media files
RUN mkdir -p downloads clips logs

CMD ["python", "main.py"]
