#!/bin/bash
set -e
exec > /var/log/startup.log 2>&1

echo "=== Quant VM Startup: $(date) ==="

# =============================================================================
# 1. System packages
# =============================================================================
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ubuntu-mate-desktop xrdp \
    python3 python3-pip python3-venv \
    git curl unzip wget \
    chromium-browser nginx \
    cron

# =============================================================================
# 2. Create quant user with password
# =============================================================================
USERNAME="quant"
USER_PASS="Quant@2026!"
if ! id "$USERNAME" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo "$USERNAME"
    echo "${USERNAME}:${USER_PASS}" | chpasswd
fi

# Configure xRDP for MATE
echo "mate-session" > /home/${USERNAME}/.xsession
chown ${USERNAME}:${USERNAME} /home/${USERNAME}/.xsession

# =============================================================================
# 3. Python collector dependencies
# =============================================================================
pip3 install --no-cache-dir \
    futu-api pandas pyarrow google-cloud-storage google-cloud-bigquery \
    yfinance alpaca-py ccxt

# =============================================================================
# 4. OpenD CLI binary
# =============================================================================
OPEND_URL="https://www.futunn.com/download/fetch-lasted-link?name=opend-ubuntu"
curl -L -o /tmp/opend.tar.gz "$OPEND_URL"
mkdir -p /tmp/opend_extract /opt/opend
tar xzf /tmp/opend.tar.gz -C /tmp/opend_extract
OPEND_DIR=$(find /tmp/opend_extract -name "FutuOpenD" -type f | head -1 | xargs dirname)
cp -r "${OPEND_DIR}"/* /opt/opend/
chmod +x /opt/opend/FutuOpenD
rm -rf /tmp/opend.tar.gz /tmp/opend_extract

# =============================================================================
# 5. Clone quant repo
# =============================================================================
if [ ! -d /opt/quant ]; then
    git clone https://github.com/Roach724/quant.git /opt/quant || \
    git clone git@github.com:Roach724/quant.git /opt/quant
fi
chown -R ${USERNAME}:${USERNAME} /opt/quant

# =============================================================================
# 6. Cron jobs for collectors and BQ loaders
# =============================================================================
cat > /opt/cron_jobs << 'CRON'
# Futu US/HK Stock 5m — every 30min during HK trading hours (UTC)
*/30 1-10 * * 1-5 quant cd /opt/quant && python3 -m collectors.main \
    GCS_BUCKET=deductive-notch-495015-c2-quant-data \
    COLLECTOR_SOURCE=futu_stock FREQUENCY=5m LOOKBACK_MINUTES=120 \
    OPEND_HOST=127.0.0.1 OPEND_PORT=11111 >> /var/log/collector_5m.log 2>&1

# Futu US/HK Stock 1d — daily after HK close (9:30am HKT = 1:30am UTC)
30 1 * * 1-5 quant cd /opt/quant && python3 -m collectors.main \
    GCS_BUCKET=deductive-notch-495015-c2-quant-data \
    COLLECTOR_SOURCE=futu_stock FREQUENCY=1d LOOKBACK_MINUTES=1440 \
    OPEND_HOST=127.0.0.1 OPEND_PORT=11111 >> /var/log/collector_1d.log 2>&1

# BQ Loaders
0 6 * * 1-5  quant cd /opt/quant && python3 -m bigquery_loader.main \
    GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 \
    MARKET=us FREQUENCY=5m TABLE=us_bars_5m >> /var/log/bq_loader.log 2>&1

0 6 * * 1-5  quant cd /opt/quant && python3 -m bigquery_loader.main \
    GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 \
    MARKET=us FREQUENCY=1d TABLE=us_bars_1d >> /var/log/bq_loader.log 2>&1

30 9 * * 1-5 quant cd /opt/quant && python3 -m bigquery_loader.main \
    GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 \
    MARKET=hk FREQUENCY=5m TABLE=hk_bars_5m >> /var/log/bq_loader.log 2>&1

30 9 * * 1-5 quant cd /opt/quant && python3 -m bigquery_loader.main \
    GCS_BUCKET=deductive-notch-495015-c2-quant-data GCP_PROJECT=deductive-notch-495015-c2 \
    MARKET=hk FREQUENCY=1d TABLE=hk_bars_1d >> /var/log/bq_loader.log 2>&1
CRON
crontab -u ${USERNAME} /opt/cron_jobs

# =============================================================================
# 7. Set up log rotation
# =============================================================================
cat > /etc/logrotate.d/quant << 'LOGROT'
/var/log/collector_5m.log
/var/log/collector_1d.log
/var/log/bq_loader.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
LOGROT

echo "=== Startup complete: $(date) ==="
