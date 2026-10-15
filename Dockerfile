FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY package*.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY public ./public
COPY tools/pwa.mjs ./tools/pwa.mjs
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TIMETABLE_DB=/app/data/timetable.db TIMETABLE_REQUIRE_AUTH=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 atlas && useradd --uid 10001 --gid atlas --no-create-home atlas
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY backend ./backend
COPY tools ./tools
COPY --from=frontend /build/dist ./dist
RUN mkdir -p /app/data /app/backups && chown atlas:atlas /app/data /app/backups
USER atlas
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready',timeout=3)"
CMD ["python", "-m", "tools.serve"]
