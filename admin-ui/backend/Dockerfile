FROM python:3.12-slim

# Non-root user; backend writes go through bind-mounts with explicit perms.
RUN groupadd -r admin && useradd -r -g admin -u 1000 admin

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY audit.py .
COPY sources/ ./sources/

RUN mkdir -p /app/data

# NOTE: this container intentionally runs as root so it can:
#   (a) write to the bind-mounted .env regardless of host file ownership
#   (b) speak to /var/run/docker.sock for the restart endpoint
# This is a significant privilege. It's justified only because the service
# is profile-gated (only starts with --profile admin), loopback-only
# (127.0.0.1:7070), and mints no outbound sessions. If you expose this
# beyond loopback, add OIDC / a separate privileged helper process that the
# non-root web process talks to via a narrow IPC.
# USER admin   # re-enable once we split privileges properly
# Root is required for docker.sock + .env writes; service is profile-gated
# and loopback-only. Trivy DS-0002 is accepted in /.trivyignore with rationale.
EXPOSE 7070

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:7070/api/health').read()" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7070", "--log-level", "info"]
