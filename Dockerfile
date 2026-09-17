FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi>=0.100.0 \
    uvicorn>=0.22.0 \
    google-cloud-storage>=2.16.0 \
    sqlalchemy>=2.0.0 \
    asyncpg>=0.28.0

COPY license_server.py create_tables.py ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn license_server:app --host 0.0.0.0 --port ${PORT:-8080}"]