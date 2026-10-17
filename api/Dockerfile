FROM python:3.11-slim

WORKDIR /app

# System deps for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Python deps (CPU-only onnxruntime in Docker — GPU handled by compose override)
COPY requirements.txt .
RUN pip install --no-cache-dir \
        fastapi uvicorn[standard] websockets pydantic>=2.0 \
        aiosqlite python-multipart python-dotenv \
        opencv-python-headless supervision numpy \
        onnxruntime   # CPU fallback — override with onnxruntime-gpu in compose

# Copy source
COPY core/ ./core/
COPY api/  ./api/

# Data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
