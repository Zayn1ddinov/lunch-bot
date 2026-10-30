FROM python:3.12-slim

# tzdata — чтобы время в логах было местным (TZ задаётся в docker-compose.yml)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/lunch.db \
    LOG_DIR=

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./

# база и резервные копии — в /data, монтируется с хоста
VOLUME /data
CMD ["python", "bot.py"]
