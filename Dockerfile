FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV LIT_AGENT_HOST=0.0.0.0
ENV LIT_AGENT_PORT=8765

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3)"

CMD ["python3", "scripts/run_capture_server.py"]
