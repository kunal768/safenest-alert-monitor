# Stage 1: build the React UI
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: FastAPI backend that also serves the built UI
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY ai /app/ai
COPY simulator /app/simulator
COPY --from=web /web/dist /app/web/dist
ENV PYTHONPATH=/app/backend:/app
ENV MODEL_ADAPTER=mock
ENV ENABLE_CLOUD_DEPLOY=false
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
