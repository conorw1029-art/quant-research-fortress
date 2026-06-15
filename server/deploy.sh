#!/bin/bash
# ============================================================
# Fortress Trading System — Server Deploy Script
# Run once as root on a fresh Ubuntu server:
#   bash deploy.sh
# ============================================================
set -e

REPO="https://github.com/conorw1029-art/quant-research-fortress.git"
INSTALL_DIR="/opt/fortress"
USER="fortress"

echo ""
echo "  Fortress Trading System — Server Setup"
echo "============================================"
echo ""

# ── 1. System packages ─────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

# ── 2. Create system user ──────────────────────────────────
echo "[2/7] Creating fortress user..."
id -u $USER &>/dev/null || useradd -r -m -d $INSTALL_DIR -s /bin/bash $USER

# ── 3. Clone repo ──────────────────────────────────────────
echo "[3/7] Cloning repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Repo already exists — pulling latest..."
    cd $INSTALL_DIR && git pull
else
    git clone $REPO $INSTALL_DIR
fi
chown -R $USER:$USER $INSTALL_DIR

# ── 4. Python venv + dependencies ─────────────────────────
echo "[4/7] Installing Python dependencies..."
cd $INSTALL_DIR
sudo -u $USER python3 -m venv venv
sudo -u $USER venv/bin/pip install --quiet --upgrade pip
sudo -u $USER venv/bin/pip install --quiet -r requirements.txt

# ── 5. Create required directories ────────────────────────
echo "[5/7] Creating data directories..."
sudo -u $USER mkdir -p \
    01_data/tick_bars/live \
    06_live_trading/logs \
    06_live_trading/state

# ── 6. Environment file ────────────────────────────────────
echo "[6/7] Setting up environment..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp $INSTALL_DIR/server/.env.template $INSTALL_DIR/.env
    chown $USER:$USER $INSTALL_DIR/.env
    chmod 600 $INSTALL_DIR/.env
    echo ""
    echo "  .env created from template. Telegram is pre-configured."
    echo "  Add Tradovate credentials later: nano $INSTALL_DIR/.env"
fi

# ── 7. Systemd services ────────────────────────────────────
echo "[7/7] Installing and starting systemd services..."
cp $INSTALL_DIR/server/fortress-yfinance.service  /etc/systemd/system/
cp $INSTALL_DIR/server/fortress-executor.service  /etc/systemd/system/
cp $INSTALL_DIR/server/fortress-barreader.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable fortress-yfinance fortress-executor fortress-barreader

# Start yfinance first — let it download initial data before executor starts
systemctl start fortress-yfinance
echo ""
echo "  Downloading initial bar data (GC/SI/ES/NQ)..."
echo "  Waiting 45 seconds..."
sleep 45

systemctl start fortress-executor
systemctl start fortress-barreader

echo ""
echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Services running:"
systemctl is-active fortress-yfinance  && echo "    fortress-yfinance  ✓ running" || echo "    fortress-yfinance  ✗ FAILED"
systemctl is-active fortress-executor  && echo "    fortress-executor  ✓ running" || echo "    fortress-executor  ✗ FAILED"
systemctl is-active fortress-barreader && echo "    fortress-barreader ✓ running" || echo "    fortress-barreader ✗ FAILED"
echo ""
echo "  Useful commands:"
echo "    journalctl -u fortress-executor -f     # live executor log"
echo "    journalctl -u fortress-yfinance -f     # data updater log"
echo "    systemctl restart fortress-executor    # restart executor"
echo "    git -C $INSTALL_DIR pull && systemctl restart fortress-executor fortress-yfinance  # update + restart"
echo ""
echo "  Check your Telegram — startup message should arrive shortly."
echo ""
