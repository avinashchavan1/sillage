FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY scripts scripts
# Data (SQLite + generated API key) lives on a volume
ENV SILLAGE_DB=/data/sillage.db \
    SILLAGE_BOOTSTRAP=off
VOLUME /data
EXPOSE 8000
# Hosts like Render pass the port in $PORT
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
