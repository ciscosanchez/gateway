FROM python:3.12-slim

# Non-root user; backend writes go through bind-mounts with explicit perms.
RUN groupadd -r admin && useradd -r -g admin -u 1000 admin

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY sources/ ./sources/

USER admin
EXPOSE 7070

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:7070/api/health').read()" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7070", "--log-level", "info"]
