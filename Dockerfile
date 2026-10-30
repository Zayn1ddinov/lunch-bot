FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# база на постоянном диске (на Railway сюда монтируется volume)
ENV DB_PATH=/data/lunch.db
RUN mkdir -p /data
CMD ["python", "-u", "bot.py"]
