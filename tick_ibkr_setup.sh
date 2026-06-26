#!/bin/bash
# tick_ibkr_setup.sh — IB Gateway Linux Setup Script
# ====================================================
# Installs IB Gateway + IBC (auto-login) on Ubuntu/Debian VPS.
# Run ONCE after creating your IBKR paper account.
#
# Usage:
#   chmod +x tick_ibkr_setup.sh
#   ./tick_ibkr_setup.sh
#
# Then configure credentials:
#   nano /opt/fortress/ibkr/config/ibc/config.ini
#   (set IbLoginId and IbPassword to your IBKR paper account credentials)
#
# Then start:
#   systemctl start fortress-ibkr-gateway
#   systemctl start fortress-ibkr

set -e

IBKR_DIR="/opt/fortress/ibkr"
IBC_VERSION="3.18.0"
GW_VERSION="10.38"

echo "=== Fortress IBKR Gateway Setup ==="
echo ""

# Install Java (required for IB Gateway)
echo "[1/5] Installing Java 11..."
apt-get update -qq
apt-get install -y openjdk-11-jre xvfb wget unzip 2>/dev/null | tail -3
java -version

# Create directories
echo "[2/5] Creating directories..."
mkdir -p $IBKR_DIR/{gateway,ibc,config/ibc,logs}

# Download IB Gateway (stable version)
echo "[3/5] Downloading IB Gateway $GW_VERSION..."
cd $IBKR_DIR/gateway
if [ ! -f "ibgateway-stable-standalone-linux-x64.sh" ]; then
    wget -q "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
    chmod +x ibgateway-stable-standalone-linux-x64.sh
    echo "  Downloaded IB Gateway installer"
else
    echo "  Already downloaded"
fi

# Install IB Gateway (unattended)
if [ ! -d "$IBKR_DIR/gateway/ibgateway" ]; then
    echo "  Installing IB Gateway (headless)..."
    echo "n" | xvfb-run ./ibgateway-stable-standalone-linux-x64.sh -q \
        -dir "$IBKR_DIR/gateway/ibgateway" 2>/dev/null || true
    echo "  IB Gateway installed"
fi

# Download IBC (auto-login controller)
echo "[4/5] Downloading IBC $IBC_VERSION..."
cd $IBKR_DIR/ibc
if [ ! -f "IBCLinux-$IBC_VERSION.zip" ]; then
    wget -q "https://github.com/IbcAlpha/IBC/releases/download/$IBC_VERSION/IBCLinux-$IBC_VERSION.zip"
    unzip -q "IBCLinux-$IBC_VERSION.zip" -d .
    chmod +x *.sh
    echo "  IBC downloaded"
else
    echo "  Already downloaded"
fi

# Write IBC config
echo "[5/5] Writing IBC config..."
cat > $IBKR_DIR/config/ibc/config.ini << 'CONFIG'
# IBC Configuration for IB Gateway
# Edit IbLoginId and IbPassword with your IBKR paper account credentials

[IBController]
FIX=no

[TWS]
IbLoginId=YOUR_IBKR_USERNAME
IbPassword=YOUR_IBKR_PASSWORD
TradingMode=paper
IbDir=/opt/fortress/ibkr/gateway/ibgateway
StoreSettingsOnServer=no
MinimizeMainWindow=no
ExistingSessionDetectedAction=primaryOverride
AcceptIncomingConnectionAction=accept
ShowAllTrades=no
ForceTwsApiPort=7497
ReadOnlyApi=no

LogToConsole=no
IbAutoClosedown=yes
ClosedownAt=Saturday 00:00
AllowBlindTrading=yes
CONFIG

# Write systemd service for IBC/IB Gateway
cat > /etc/systemd/system/fortress-ibkr-gateway.service << 'SVC'
[Unit]
Description=Fortress IB Gateway (via IBC auto-login)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fortress/ibkr/ibc
Environment=DISPLAY=:1
ExecStartPre=/usr/bin/Xvfb :1 -screen 0 1024x768x24 &
ExecStart=/opt/fortress/ibkr/ibc/gatewaystart.sh \
    /opt/fortress/ibkr/config/ibc/config.ini \
    /opt/fortress/ibkr/gateway/ibgateway \
    /opt/fortress/ibkr/logs
Restart=always
RestartSec=60
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload

echo ""
echo "=== Setup Complete ==="
echo ""
echo "NEXT STEPS:"
echo "  1. Edit credentials:"
echo "     nano /opt/fortress/ibkr/config/ibc/config.ini"
echo "     (set IbLoginId=YOUR_USERNAME and IbPassword=YOUR_PASSWORD)"
echo ""
echo "  2. Add to .env:"
echo "     echo 'IBKR_MODE=paper' >> /opt/fortress/.env"
echo "     echo 'IBKR_HOST=127.0.0.1' >> /opt/fortress/.env"
echo "     echo 'IBKR_PORT=7497' >> /opt/fortress/.env"
echo "     echo 'IBKR_CLIENT_ID=10' >> /opt/fortress/.env"
echo ""
echo "  3. Start IB Gateway:"
echo "     systemctl enable --now fortress-ibkr-gateway"
echo "     systemctl enable --now fortress-ibkr"
echo ""
echo "  4. Subscribe to CME + COMEX market data in IBKR account:"
echo "     Account → Settings → Market Data Subscriptions"
echo "     Add: CME Non-Professional, COMEX Non-Professional"
echo "     Cost: ~\$10/month each"
