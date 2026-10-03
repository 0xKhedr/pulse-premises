# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# DB_FILE keeps the database on the mounted volume by default. The app's own
# default is a relative path, which inside a container would land in the
# ephemeral writable layer and be lost on every container recreation.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DB_FILE=/app/data/smarthome.db \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_RETRIES=10 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    ca-certificates \
    openssl \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies.
#
# The optional managed-network CA is installed in this same layer rather than
# a preceding one. BuildKit does not include secret *contents* in a layer's
# cache key, so a separate CA step built once without the secret caches as a
# no-op and is then silently reused by later builds that do pass it, leaving
# pip to fail on certificate verification. Sharing a layer with pip install
# ties the two together: the layer can only be cached in a state where the
# dependencies actually installed.
#
# The certificate lands in the image (so outbound HTTPS also works at
# runtime) but is never copied into the repository.
COPY requirements.txt .
RUN --mount=type=secret,id=sbms_local_ca,target=/tmp/sbms-local-ca.cer,required=false \
    if [ -s /tmp/sbms-local-ca.cer ]; then \
        openssl x509 -inform DER -in /tmp/sbms-local-ca.cer \
            -out /usr/local/share/ca-certificates/sbms-local-ca.crt && \
        update-ca-certificates; \
    fi && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/ app/
COPY templates/ templates/
COPY static/ static/
COPY windows/ windows/
COPY linux/ linux/
COPY firmware/ firmware/
COPY tools/ tools/

# Create an unprivileged user and pre-create the directories it needs to
# write to. The named volume mounted at /app/data inherits ownership from
# this image directory the first time it is attached, so both directories
# must exist and be chowned here, before the volume/bind-mount attach.
RUN groupadd --system --gid 1000 sbms \
    && useradd --system --uid 1000 --gid sbms --home-dir /app --shell /usr/sbin/nologin sbms \
    && mkdir -p /app/data /app/uploads \
    && chown -R sbms:sbms /app/data /app/uploads

USER sbms

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/healthz || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
