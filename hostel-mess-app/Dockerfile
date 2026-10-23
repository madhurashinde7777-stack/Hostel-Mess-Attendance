# MessMate - production image used by Railway.
# Stage 1 builds the React pages; stage 2 runs the Flask app with gunicorn.

# ---- 1. build the frontend ----
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- 2. run the backend ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    PORT=8080
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=frontend /app/frontend/dist ./frontend/dist
EXPOSE 8080
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
