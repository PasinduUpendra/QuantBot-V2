#!/bin/bash
# ================================================
# HYDRA TRADING SYSTEM - QUICK START
# ================================================

echo "╔══════════════════════════════════════════╗"
echo "║    HYDRA TRADING SYSTEM v2.0             ║"
echo "║    'Survive. Compound. Dominate.'        ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Navigate to project directory
cd "$(dirname "$0")"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Install Python 3.9+"
    exit 1
fi

echo "[1/3] Installing dependencies..."
pip3 install -r requirements.txt -q 2>/dev/null

echo "[2/3] Running health check..."
python3 health_check.py --no-backtest

echo "[3/3] Ready to launch!"
echo ""
echo "Commands:"
echo "  Paper trading:  python3 main.py"
echo "  Health check:   python3 health_check.py"
echo "  Live trading:   Edit .env → PAPER_TRADE=false → python3 main.py"
echo ""
echo "To go LIVE:"
echo "  1. Run health check first"
echo "  2. Set PAPER_TRADE=false in .env"
echo "  3. Verify your balance: python3 -c 'from core.exchange import BinanceConnector; b=BinanceConnector(); print(b.get_account_balance())'"
echo "  4. Start: python3 main.py"
echo ""
