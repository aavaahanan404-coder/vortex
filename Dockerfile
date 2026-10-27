# Hosted demo: the seeded demo API and the Next.js web app in one container (one public port).
# Build: docker build -t vortex .      Run: docker run -p 3000:3000 -e PORT=3000 vortex
# Demo data is created at BUILD time, so a restart is quick and always returns to the same starting state.

# ---- web app
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build && npm prune --omit=dev

# ---- runtime
FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SIH_HOSTED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libglib2.0-0 bash \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements-hosted.txt ./
RUN pip install --no-cache-dir -r requirements-hosted.txt
COPY contracts.py conftest.py ./
COPY api ./api
COPY modules ./modules
COPY config ./config
COPY harness ./harness
COPY worker ./worker
COPY demo_assets ./demo_assets
COPY corpus_v2 ./corpus_v2
COPY --from=web /web ./frontend
COPY start_hosted.sh ./
RUN useradd -m app && python -m harness.demo --seed-only && chown -R app /app && chmod +x start_hosted.sh
USER app
EXPOSE 3000
CMD ["./start_hosted.sh"]
