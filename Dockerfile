FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV STATE_DIR=/app/data
RUN mkdir -p /app/data

CMD ["sh", "start.sh"]
