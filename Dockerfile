# ── Python build stage ──
FROM python:3.12-slim AS python-builder

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --target=/deps -r /tmp/requirements.txt

# ── Frontend build stage ──
FROM node:22-slim AS frontend-builder

WORKDIR /app
COPY admin/frontend/package.json admin/frontend/package-lock.json ./
RUN npm ci

COPY admin/frontend/ ./
RUN npm run build

# ── Runtime stage ──
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor curl libgomp1 cron && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies from build stage
COPY --from=python-builder /deps /usr/local/lib/python3.12/site-packages/

# Application code
COPY . /opt/quant/

# Frontend dist from build stage (overwrites any git-tracked dist)
COPY --from=frontend-builder /app/dist/ /opt/quant/admin/frontend/dist/

# Writable directories (will be volume-mounted in production)
RUN mkdir -p /var/log/quant/{collector,admin,live} \
    /var/quant/state /var/quant/experiments /var/data && \
    ln -s /opt/quant /opt/quant-prod

# Supervisor configuration
COPY docker/supervisord.conf /etc/supervisor/conf.d/quant.conf

ENV QUANT_HOME=/opt/quant \
    PYTHONPATH=/opt/quant \
    QUANT_ENV=prod

EXPOSE 8091 5000
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
