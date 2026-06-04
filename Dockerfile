# Pinned to the python:3.12-slim registry manifest digest resolved 2026-06-04.
# Bump deliberately with:
#   docker pull python:3.12-slim && docker inspect python:3.12-slim --format '{{index .RepoDigests 0}}'
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

# Non-root user — uid/gid 1000 matches the cargo user on the NAS.
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Install deps as root, then drop privileges
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app src/ ./src/
COPY --chown=app:app pyproject.toml ./

# Persistent data lives outside the image
RUN mkdir -p /data && chown app:app /data
ENV OPTIMIZER_DATA_DIR=/data
ENV PYTHONPATH=/app/src

USER app
EXPOSE 5004

# Default: dashboard via gunicorn. The scheduler container uses a different
# command via docker-compose (see compose file).
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5004", "--access-logfile", "-", "optimizer.web.dashboard:create_app()"]
