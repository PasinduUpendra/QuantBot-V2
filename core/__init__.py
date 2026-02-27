"""
HYDRA Trading System - Configuration Module
All system-wide constants and settings.
"""
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / '.env')


# ============================================================
# API KEYS
# ============================================================
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY', '')
BINANCE_SECRET_KEY = os.getenv('BINANCE_SECRET_KEY', '')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

# ============================================================
# RISK MANAGEMENT PARAMETERS
# ============================================================
MAX_DRAWDOWN_PCT = float(os.getenv('MAX_DRAWDOWN_PCT', 15))
DAILY_LOSS_LIMIT_PCT = float(os.getenv('DAILY_LOSS_LIMIT_PCT', 5))
MAX_POSITION_PCT = float(os.getenv('MAX_POSITION_PCT', 20))
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv('MAX_TOTAL_EXPOSURE_PCT', 80))
RISK_PER_TRADE_PCT = float(os.getenv('RISK_PER_TRADE_PCT', 1.5))

# ============================================================
# STRATEGY CAPITAL ALLOCATION (% of total)
# ============================================================
GRID_ALLOCATION = float(os.getenv('GRID_ALLOCATION', 30)) / 100
MEAN_REVERSION_ALLOCATION = float(os.getenv('MEAN_REVERSION_ALLOCATION', 25)) / 100
MOMENTUM_ALLOCATION = float(os.getenv('MOMENTUM_ALLOCATION', 25)) / 100
FUNDING_ARB_ALLOCATION = float(os.getenv('FUNDING_ARB_ALLOCATION', 20)) / 100

# ============================================================
# TRADING PAIRS
# ============================================================
PRIMARY_PAIRS = os.getenv('PRIMARY_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT').split(',')
GRID_PAIRS = os.getenv('GRID_PAIRS', 'BTCUSDT,ETHUSDT').split(',')
MEAN_REVERSION_PAIRS = os.getenv('MEAN_REVERSION_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,AVAXUSDT,ADAUSDT').split(',')
MOMENTUM_PAIRS = os.getenv('MOMENTUM_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT').split(',')
FUNDING_ARB_PAIRS = os.getenv('FUNDING_ARB_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT').split(',')

# ============================================================
# GRID TRADING PARAMETERS
# ============================================================
GRID_LEVELS = 8                     # Number of grid levels each side
GRID_SPACING_PCT = 0.20             # % spacing between grids
GRID_ORDER_SIZE_PCT = 8.0           # % of grid allocation per order
GRID_REBALANCE_THRESHOLD = 0.5     # % drift before rebalancing grid

# ============================================================
# MEAN REVERSION PARAMETERS
# ============================================================
MR_TIMEFRAME = '5m'                 # Primary timeframe
MR_BB_PERIOD = 20                   # Bollinger Band period
MR_BB_STD = 2.0                     # Bollinger Band std dev
MR_RSI_PERIOD = 14                  # RSI period
MR_RSI_OVERSOLD = 30                # RSI oversold threshold
MR_RSI_OVERBOUGHT = 70             # RSI overbought threshold
MR_ATR_PERIOD = 14                  # ATR for stop loss
MR_ATR_STOP_MULT = 1.5             # ATR multiplier for stop
MR_ATR_TARGET_MULT = 2.0           # ATR multiplier for target
MR_MAX_POSITIONS = 4                # Max concurrent MR positions
MR_LOOKBACK_CANDLES = 100           # Candles to fetch

# ============================================================
# MOMENTUM BREAKOUT PARAMETERS
# ============================================================
MOM_TIMEFRAME = '1h'                # Primary timeframe
MOM_FAST_EMA = 9                    # Fast EMA period
MOM_SLOW_EMA = 21                   # Slow EMA period
MOM_TREND_EMA = 50                  # Trend EMA period
MOM_ADX_PERIOD = 14                 # ADX period
MOM_ADX_THRESHOLD = 25              # Min ADX for trend
MOM_VOLUME_MULT = 1.5               # Volume spike multiplier
MOM_ATR_PERIOD = 14                 # ATR period
MOM_ATR_STOP_MULT = 2.0            # Stop loss ATR mult
MOM_ATR_TARGET_MULT = 3.0          # Take profit ATR mult
MOM_TRAIL_ATR_MULT = 1.5           # Trailing stop ATR mult
MOM_MAX_POSITIONS = 3               # Max concurrent positions
MOM_LOOKBACK_CANDLES = 100          # Candles to fetch

# ============================================================
# FUNDING RATE ARBITRAGE PARAMETERS
# ============================================================
FUND_MIN_RATE = 0.01                # Min funding rate (%) to enter
FUND_EXIT_RATE = 0.005              # Exit when funding drops below
FUND_MAX_POSITIONS = 3              # Max concurrent arb positions
FUND_POSITION_SIZE_PCT = 5.0        # % of arb allocation per position

# ============================================================
# EXECUTION PARAMETERS
# ============================================================
MAX_SLIPPAGE_PCT = 0.1              # Max acceptable slippage
ORDER_TIMEOUT_SEC = 30              # Cancel unfilled orders after
USE_LIMIT_ORDERS = True             # Prefer limit over market
LIMIT_ORDER_OFFSET_PCT = 0.02       # Limit order offset from mid

# ============================================================
# SYSTEM
# ============================================================
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
PAPER_TRADE = os.getenv('PAPER_TRADE', 'true').lower() == 'true'
HEARTBEAT_INTERVAL = 60             # seconds
DATA_DIR = Path(__file__).parent.parent / 'data'
LOG_DIR = Path(__file__).parent.parent / 'logs'

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ============================================================
# COMPOUND TARGET
# ============================================================
DOUBLE_DAYS = 12                    # Target: double in N days
DAILY_TARGET_PCT = (2 ** (1/DOUBLE_DAYS) - 1) * 100  # ~5.95%

print(f"[HYDRA CONFIG] Daily target: {DAILY_TARGET_PCT:.2f}%")
print(f"[HYDRA CONFIG] Paper trading: {PAPER_TRADE}")
