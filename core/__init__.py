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
# RISK MANAGEMENT PARAMETERS (AGGRESSIVE SURVIVAL MODE)
# ============================================================
MAX_DRAWDOWN_PCT = float(os.getenv('MAX_DRAWDOWN_PCT', 20))
DAILY_LOSS_LIMIT_PCT = float(os.getenv('DAILY_LOSS_LIMIT_PCT', 8))
MAX_POSITION_PCT = float(os.getenv('MAX_POSITION_PCT', 35))
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv('MAX_TOTAL_EXPOSURE_PCT', 95))
RISK_PER_TRADE_PCT = float(os.getenv('RISK_PER_TRADE_PCT', 1.5))  # Reduced from 3% — protect capital
MIN_TRADE_VALUE = 1.0 if os.getenv('PAPER_TRADE', 'true').lower() == 'true' else 10.0

# ============================================================
# STRATEGY CAPITAL ALLOCATION (% of total) - FundArb disabled, redistributed
# ============================================================
GRID_ALLOCATION = float(os.getenv('GRID_ALLOCATION', 40)) / 100
MEAN_REVERSION_ALLOCATION = float(os.getenv('MEAN_REVERSION_ALLOCATION', 35)) / 100
MOMENTUM_ALLOCATION = float(os.getenv('MOMENTUM_ALLOCATION', 25)) / 100
FUNDING_ARB_ALLOCATION = float(os.getenv('FUNDING_ARB_ALLOCATION', 0)) / 100

# ============================================================
# TRADING PAIRS (expanded for more opportunities)
# ============================================================
PRIMARY_PAIRS = os.getenv('PRIMARY_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,AVAXUSDT,ADAUSDT').split(',')
GRID_PAIRS = os.getenv('GRID_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT').split(',')
MEAN_REVERSION_PAIRS = os.getenv('MEAN_REVERSION_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,AVAXUSDT,ADAUSDT,DOTUSDT,LINKUSDT').split(',')
MOMENTUM_PAIRS = os.getenv('MOMENTUM_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,AVAXUSDT,ADAUSDT').split(',')
FUNDING_ARB_PAIRS = os.getenv('FUNDING_ARB_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT').split(',')

# ============================================================
# GRID TRADING PARAMETERS (AGGRESSIVE)
# ============================================================
GRID_LEVELS = 5                     # Fewer levels = bigger per order
GRID_SPACING_PCT = 0.15             # % spacing between grids (tight = more fills)
GRID_ORDER_SIZE_PCT = 40.0          # % of grid allocation per order (aggressive)
GRID_REBALANCE_THRESHOLD = 0.3     # % drift before rebalancing grid (faster rebalance)

# ============================================================
# MEAN REVERSION PARAMETERS (AGGRESSIVE)
# ============================================================
MR_TIMEFRAME = '5m'                 # Primary timeframe
MR_BB_PERIOD = 20                   # Bollinger Band period
MR_BB_STD = 2.0                     # Bollinger Band std dev (standard = fewer but better signals)
MR_RSI_PERIOD = 14                  # RSI period
MR_RSI_OVERSOLD = 30                # RSI oversold threshold (tighter = only true oversold)
MR_RSI_OVERBOUGHT = 70             # RSI overbought threshold (standard)
MR_ATR_PERIOD = 14                  # ATR for stop loss
MR_ATR_STOP_MULT = 2.0             # ATR multiplier for stop (wider = survive noise)
MR_ATR_TARGET_MULT = 2.5           # ATR multiplier for target (bigger R:R)
MR_MAX_POSITIONS = 2                # FIX-7: Reduced from 3 — less correlation risk on $1k account
MR_LOOKBACK_CANDLES = 100           # Candles to fetch
MR_MIN_STOP_DISTANCE_PCT = 0.5     # Min 0.5% stop distance (was 0.3% — too tight)

# ============================================================
# MOMENTUM BREAKOUT PARAMETERS (AGGRESSIVE)
# ============================================================
MOM_TIMEFRAME = '15m'               # Faster timeframe = more signals
MOM_FAST_EMA = 8                    # Fast EMA period
MOM_SLOW_EMA = 18                   # Slow EMA period (tighter)
MOM_TREND_EMA = 40                  # Trend EMA period (faster)
MOM_ADX_PERIOD = 14                 # ADX period
MOM_ADX_THRESHOLD = 20              # Min ADX for trend (requires real trend)
MOM_VOLUME_MULT = 1.3               # Volume spike multiplier (require decent volume)
MOM_ATR_PERIOD = 14                 # ATR period
MOM_ATR_STOP_MULT = 2.0            # Stop loss ATR mult (wider = survive noise)
MOM_ATR_TARGET_MULT = 3.5          # Take profit ATR mult (bigger R:R)
MOM_TRAIL_ATR_MULT = 1.5           # Trailing stop ATR mult (wider trail = ride trends)
MOM_MAX_POSITIONS = 2               # FIX-7: Reduced from 3 — max 4 directional positions total (2 MR + 2 MOM)
MOM_LOOKBACK_CANDLES = 100          # Candles to fetch

# ============================================================
# FUTURES CONFIGURATION
# ============================================================
FUTURES_MODE = os.getenv('FUTURES_MODE', 'false').lower() == 'true'
FUTURES_LEVERAGE = int(os.getenv('FUTURES_LEVERAGE', 3))

# ============================================================
# FUNDING RATE ARBITRAGE PARAMETERS (DISABLED - no futures perms)
# ============================================================
FUND_MIN_RATE = 0.01                # Min funding rate (%) to enter
FUND_EXIT_RATE = 0.005              # Exit when funding drops below
FUND_MAX_POSITIONS = 0              # DISABLED - no futures permissions
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
