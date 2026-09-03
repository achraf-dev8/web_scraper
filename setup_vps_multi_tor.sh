#!/bin/bash
# ==============================================================================
# Multi-Tor Instance Setup Script for Linux VPS / WSL (Ubuntu / Debian)
# Sets up 4 parallel, native Tor SOCKS & Control instances
# ==============================================================================

echo "[+] Updating package lists and installing Tor & Python dependencies..."
sudo apt update && sudo apt install -y tor python3-stem python3-pip python3-venv curl netcat-openbsd

echo "[+] Terminating default single-instance Tor service if running..."
sudo systemctl stop tor 2>/dev/null || true
sudo mkdir -p /etc/tor

echo "[+] Configuring 4 Multi-Tor Instances on ports 9050, 9052, 9054, 9056..."

for i in 1 2 3 4; do
    SOCKS_PORT=$((9048 + i * 2))   # 9050, 9052, 9054, 9056
    CTRL_PORT=$((9049 + i * 2))    # 9051, 9053, 9055, 9057
    DATA_DIR="/var/lib/tor_inst_$i"

    echo "  -> Setting up Instance #$i: SOCKS Port $SOCKS_PORT | Control Port $CTRL_PORT"

    sudo mkdir -p "$DATA_DIR"
    sudo chmod 700 "$DATA_DIR"
    sudo chown -R $(id -u):$(id -g) "$DATA_DIR" 2>/dev/null || true

    cat <<EOF | sudo tee /etc/tor/torrc_inst_$i > /dev/null
SocksPort 127.0.0.1:$SOCKS_PORT
ControlPort 127.0.0.1:$CTRL_PORT
DataDirectory $DATA_DIR
CookieAuthentication 0
EOF

    # Kill old instance process if running
    pkill -f "torrc_inst_$i" 2>/dev/null || true

    # Launch native headless Tor instance
    sudo tor -f /etc/tor/torrc_inst_$i --RunAsDaemon 1
done

echo "[+] Waiting 5 seconds for Tor circuits to initialize..."
sleep 5

echo "[+] Testing active SOCKS ports..."
for i in 1 2 3 4; do
    SOCKS_PORT=$((9048 + i * 2))
    if nc -z 127.0.0.1 $SOCKS_PORT 2>/dev/null || timeout 1 bash -c "</dev/tcp/127.0.0.1/$SOCKS_PORT" 2>/dev/null; then
        echo "  [SUCCESS] SOCKS Port $SOCKS_PORT is ACTIVE and ready!"
    else
        echo "  [WARN] SOCKS Port $SOCKS_PORT failed to start."
    fi
done

echo ""
echo "[READY] Multi-Tor environment setup complete! You can now run:"
echo "        python3 tor_scraper.py"
