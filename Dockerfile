# ── Build stage 1: frontend build ──
FROM node:22-slim AS frontend
WORKDIR /src
COPY admin/frontend/package.json admin/frontend/package-lock.json ./
RUN npm ci
COPY admin/frontend/ ./
RUN npm run build

# ── Build stage 2: install Python dependencies ──
FROM python:3.12-slim AS builder

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --target=/deps -r /tmp/requirements.txt

# ── Runtime stage: minimal image ──
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor curl libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Dependencies from build stage
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/
COPY --from=frontend /src/dist/ /opt/quant/admin/frontend/dist/

# Application code (read-only after build)
COPY . /opt/quant/

# Writable directories (will be volume-mounted in production)
RUN mkdir -p /var/log/quant/{collector,admin,live} \
    /var/quant/state /var/quant/experiments /var/data

# Supervisor configuration
COPY docker/supervisord.conf /etc/supervisor/conf.d/quant.conf

ENV QUANT_HOME=/opt/quant \
    PYTHONPATH=/opt/quant \
    QUANT_ENV=prod

EXPOSE 8091 5000
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
