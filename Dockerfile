# ── Build stage: install Python dependencies ──
FROM python:3.12-slim AS builder

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --target=/deps -r /tmp/requirements.txt

# ── Runtime stage: minimal image ──
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor curl libgomp1 cron && \
    rm -rf /var/lib/apt/lists/*

# Dependencies from build stage
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/
# Application code (read-only after build)
COPY . /opt/quant/

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
