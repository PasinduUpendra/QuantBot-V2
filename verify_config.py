"""Quick config validation"""
from core import *

print(f"MIN_TRADE_VALUE: {MIN_TRADE_VALUE}")
print(f"GRID_ALLOCATION: {GRID_ALLOCATION*100}%")
print(f"MR_ALLOCATION: {MEAN_REVERSION_ALLOCATION*100}%")
print(f"MOM_ALLOCATION: {MOMENTUM_ALLOCATION*100}%")
print(f"FUND_ALLOCATION: {FUNDING_ARB_ALLOCATION*100}%")
print(f"MR_MIN_STOP_DISTANCE_PCT: {MR_MIN_STOP_DISTANCE_PCT}")
print(f"GRID_LEVELS: {GRID_LEVELS}, GRID_ORDER_SIZE_PCT: {GRID_ORDER_SIZE_PCT}")
print(f"MOM_ADX_THRESHOLD: {MOM_ADX_THRESHOLD}, MOM_TIMEFRAME: {MOM_TIMEFRAME}")
print(f"RISK_PER_TRADE: {RISK_PER_TRADE_PCT}%, MAX_POS: {MAX_POSITION_PCT}%, MAX_EXPOSURE: {MAX_TOTAL_EXPOSURE_PCT}%")

# Grid math check
grid_budget = 1000 * GRID_ALLOCATION
per_level = grid_budget * GRID_ORDER_SIZE_PCT / 100
print(f"\nGrid budget: ${grid_budget:.0f}, per level: ${per_level:.0f} (min: ${MIN_TRADE_VALUE})")
print("ALL CHECKS PASSED" if per_level >= MIN_TRADE_VALUE else "GRID STILL TOO SMALL!")

# Import validation
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum_breakout import MomentumBreakoutStrategy
from strategies.grid_trading import GridTradingStrategy
from strategies.funding_arb import FundingArbStrategy
from core.risk_manager import RiskManager
from core.regime_detector import MarketRegimeDetector
print("\nAll imports successful!")
