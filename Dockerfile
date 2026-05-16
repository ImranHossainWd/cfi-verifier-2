FROM python:3.12-slim

# System tools: Poppler for pdftoppm/pdfinfo, Tesseract for OCR,
# DejaVu fonts so the verifier's PIL annotations render correctly.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-eng \
        fonts-dejavu-core \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Verifier code + configs + cached vision OCR for the 4 sample packets
COPY sqr_verifier_v2 ./sqr_verifier_v2
# Web app
COPY app ./app

# Job state + uploads land here; mounted as a volume in compose,
# attached as a disk on Render.
RUN mkdir -p /app/data/jobs

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# Render passes $PORT; default to 8000 locally
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
