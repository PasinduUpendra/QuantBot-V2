"""
HYDRA Trading System - Quick Health Check & Backtester
=======================================================
Run this to verify everything works before going live.
"""
import sys
import time
import json
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}", level="INFO")


def run_health_check():
    """Comprehensive system health check."""
    results = {}
    
    print("\n" + "=" * 60)
    print("  HYDRA TRADING SYSTEM - HEALTH CHECK")
    print("=" * 60 + "\n")
    
    # 1. Import check
    print("[1/7] Checking imports...")
    try:
        from core import BINANCE_API_KEY, BINANCE_SECRET_KEY, PAPER_TRADE, DAILY_TARGET_PCT
        from core.exchange import BinanceConnector
        from core.risk_manager import RiskManager
        from core.regime_detector import MarketRegimeDetector
        from strategies.grid_trading import GridTradingStrategy
        from strategies.mean_reversion import MeanReversionStrategy
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        from strategies.funding_arb import FundingArbStrategy
        print("  ✓ All modules imported successfully")
        results['imports'] = True
    except ImportError as e:
        print(f"  ✗ Import error: {e}")
        results['imports'] = False
        return results
    
    # 2. API Key check
    print("\n[2/7] Checking API keys...")
    if BINANCE_API_KEY and len(BINANCE_API_KEY) > 10:
        print(f"  ✓ Binance API key: {BINANCE_API_KEY[:8]}...{BINANCE_API_KEY[-4:]}")
    else:
        print("  ✗ Binance API key missing or invalid")
    results['api_key'] = bool(BINANCE_API_KEY)
    
    # 3. Binance connectivity
    print("\n[3/7] Testing Binance connectivity...")
    exchange = BinanceConnector()
    
    if exchange.ping():
        print("  ✓ Binance API: Connected")
        results['connectivity'] = True
    else:
        print("  ✗ Binance API: Connection failed")
        results['connectivity'] = False
    
    # 4. Market data
    print("\n[4/7] Fetching market data...")
    try:
        btc_price = exchange.get_price('BTCUSDT')
        eth_price = exchange.get_price('ETHUSDT')
        if btc_price:
            print(f"  ✓ BTC/USDT: ${btc_price:,.2f}")
            print(f"  ✓ ETH/USDT: ${eth_price:,.2f}")
            results['market_data'] = True
        else:
            print("  ✗ Could not fetch prices")
            results['market_data'] = False
    except Exception as e:
        print(f"  ✗ Market data error: {e}")
        results['market_data'] = False
    
    # 5. Account balance
    print("\n[5/7] Checking account balance...")
    try:
        if PAPER_TRADE:
            exchange.init_paper_balance(1000)
            balance = 1000.0
            print(f"  ✓ Paper trading mode: $1,000.00")
        else:
            balance = exchange.get_total_equity_usdt()
            if balance > 0:
                print(f"  ✓ Account equity: ${balance:,.2f}")
            else:
                print("  ⚠ No balance detected (check permissions)")
        results['balance'] = balance
    except Exception as e:
        print(f"  ✗ Balance check error: {e}")
        results['balance'] = 0
    
    # 6. Kline data fetch test
    print("\n[6/7] Testing data pipeline...")
    try:
        klines = exchange.get_klines('BTCUSDT', '5m', limit=100)
        if not klines.empty:
            print(f"  ✓ Fetched {len(klines)} candles (5m BTC)")
            print(f"    Latest: O={klines['open'].iloc[-1]:.2f} H={klines['high'].iloc[-1]:.2f} "
                  f"L={klines['low'].iloc[-1]:.2f} C={klines['close'].iloc[-1]:.2f}")
            results['data_pipeline'] = True
        else:
            print("  ✗ Empty klines returned")
            results['data_pipeline'] = False
    except Exception as e:
        print(f"  ✗ Data pipeline error: {e}")
        results['data_pipeline'] = False
    
    # 7. Strategy signal test
    print("\n[7/7] Testing strategy signals...")
    try:
        risk = RiskManager(1000)
        
        # Test Mean Reversion signals
        mr = MeanReversionStrategy(exchange, risk)
        mr_signals = mr.analyze()
        print(f"  ✓ Mean Reversion: {len(mr_signals)} signals found")
        
        # Test Momentum signals
        mom = MomentumBreakoutStrategy(exchange, risk)
        mom_signals = mom.analyze()
        print(f"  ✓ Momentum: {len(mom_signals)} signals found")
        
        # Test Funding rates
        fund = FundingArbStrategy(exchange, risk)
        fund_signals = fund.analyze()
        print(f"  ✓ Funding Arb: {len(fund_signals)} opportunities found")
        
        results['signals'] = True
    except Exception as e:
        print(f"  ✗ Signal test error: {e}")
        results['signals'] = False
    
    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for v in results.values() if v and v is not False)
    total = len(results)
    print(f"  HEALTH CHECK: {passed}/{total} checks passed")
    
    if passed >= 5:
        print("  STATUS: READY TO TRADE ✓")
    else:
        print("  STATUS: ISSUES DETECTED - Fix before trading")
    
    print(f"  Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")
    print(f"  Target: {DAILY_TARGET_PCT:.2f}% daily")
    print("=" * 60 + "\n")
    
    return results


def run_mini_backtest():
    """Run a quick backtest on recent data to validate strategies."""
    print("\n" + "=" * 60)
    print("  HYDRA MINI-BACKTEST (Last 24h)")
    print("=" * 60 + "\n")
    
    from core.exchange import BinanceConnector
    import numpy as np
    
    exchange = BinanceConnector()
    
    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    
    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        
        try:
            # Fetch 24h of 5m data
            df = exchange.get_klines(symbol, '5m', limit=288)
            if df.empty:
                print(f"  No data for {symbol}")
                continue
            
            close = df['close']
            
            # Simulate mean reversion signals
            sma = close.rolling(20).mean()
            std = close.rolling(20).std()
            upper = sma + 2 * std
            lower = sma - 2 * std
            
            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.inf)
            rsi = 100 - (100 / (1 + rs))
            
            # Count signals
            buy_signals = ((close < lower) & (rsi < 30)).sum()
            sell_signals = ((close > upper) & (rsi > 70)).sum()
            
            # Price stats
            price_change = (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100
            volatility = float(close.pct_change().std() * 100)
            
            # Simple backtest: buy at lower BB + RSI < 30, sell at SMA
            positions = []
            in_position = False
            entry_price = 0
            
            for i in range(30, len(df)):
                if not in_position:
                    if float(close.iloc[i]) < float(lower.iloc[i]) and float(rsi.iloc[i]) < 35:
                        in_position = True
                        entry_price = float(close.iloc[i])
                else:
                    # Exit at SMA or after 20 bars
                    if float(close.iloc[i]) > float(sma.iloc[i]) or True:
                        exit_price = float(close.iloc[i])
                        pnl_pct = (exit_price - entry_price) / entry_price * 100
                        positions.append(pnl_pct)
                        in_position = False
            
            print(f"  24h Change: {price_change:+.2f}%")
            print(f"  Volatility: {volatility:.3f}%")
            print(f"  Buy signals: {buy_signals} | Sell signals: {sell_signals}")
            
            if positions:
                wins = sum(1 for p in positions if p > 0)
                avg_pnl = np.mean(positions)
                print(f"  MR Trades: {len(positions)} | Wins: {wins} | Avg PnL: {avg_pnl:+.3f}%")
            
        except Exception as e:
            print(f"  Error: {e}")
    
    print("\n" + "=" * 60)


if __name__ == '__main__':
    import sys
    
    if '--backtest' in sys.argv:
        run_mini_backtest()
    else:
        run_health_check()
        
    if '--backtest' not in sys.argv and '--no-backtest' not in sys.argv:
        run_mini_backtest()
