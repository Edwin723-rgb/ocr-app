# SCI OCR — imagen para servidor web (FastAPI + Tesseract)
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

WORKDIR /app/backend

ENV PYTHONUNBUFFERED=1 \
    TESSERACT_CMD=/usr/bin/tesseract \
    OCR_HOST=0.0.0.0 \
    OCR_PORT=8000 \
    OCR_RELOAD=0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/config', timeout=5)"

CMD ["python", "main.py"]
