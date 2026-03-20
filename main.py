"""
HYDRA Trading System - Main Engine
====================================
Orchestrates all strategies, risk management, and monitoring.
This is the brain that keeps everything running.

"When you have no choice, you find a way."
"""
import time
import json
import signal
import sys
import traceback
from datetime import datetime, timedelta
from typing import Dict, List
from pathlib import Path

import numpy as np
from loguru import logger

from core import (
    PAPER_TRADE, DAILY_TARGET_PCT, HEARTBEAT_INTERVAL,
    LOG_DIR, DATA_DIR, LOG_LEVEL,
    GRID_ALLOCATION, MEAN_REVERSION_ALLOCATION,
    MOMENTUM_ALLOCATION, FUNDING_ARB_ALLOCATION,
    FUTURES_MODE, FUTURES_LEVERAGE,
    PRIMARY_PAIRS, MOMENTUM_PAIRS, MEAN_REVERSION_PAIRS
)
from core.exchange import BinanceConnector
from core.risk_manager import RiskManager
from core.regime_detector import MarketRegimeDetector
from strategies.grid_trading import GridTradingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum_breakout import MomentumBreakoutStrategy
from strategies.funding_arb import FundingArbStrategy


# ================================================================
# LOGGING SETUP
# ================================================================
logger.remove()  # Remove default handler
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level=LOG_LEVEL,
    colorize=True,
)
logger.add(
    str(LOG_DIR / "hydra_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
)


class HydraEngine:
    """
    Main trading engine that orchestrates everything.
    
    Loop:
    1. Check connectivity
    2. Detect market regime (AI + rules)
    3. Adjust strategy allocations
    4. Run each strategy (analyze → execute → manage)
    5. Update risk monitoring
    6. Log performance
    7. Sleep and repeat
    """
    
    # Strategy execution intervals (seconds) — AGGRESSIVE mode
    INTERVALS = {
        'GRID': 20,        # Grid needs frequent checking (was 30)
        'MEAN_REV': 30,    # Mean reversion on 5m candles (was 60)
        'MOMENTUM': 120,   # Momentum on 15m candles (was 300)
        'FUND_ARB': 3600,  # Funding rate — disabled, just health-check hourly
        'REGIME': 1200,    # Regime check every 20 min (was 30)
        'STATS': 120,      # Stats every 2 min
        'SAVE': 300,       # Save state every 5 min
        'HEALTH': 1800,    # Self-diagnostic watchdog every 30 min
    }
    
    def __init__(self):
        logger.info("=" * 70)
        logger.info("    HYDRA QUANTITATIVE TRADING SYSTEM v3.0")
        logger.info("    'Survive. Compound. Dominate.'")
        logger.info("=" * 70)
        logger.info(f"    Mode: {'PAPER TRADING' if PAPER_TRADE else 'LIVE TRADING'}")
        logger.info(f"    Futures: {'ON (' + str(FUTURES_LEVERAGE) + 'x leverage)' if FUTURES_MODE else 'OFF (spot only)'}")
        logger.info(f"    Target: {DAILY_TARGET_PCT:.2f}% daily (2x every 12 days)")
        logger.info(f"    Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 70)
        
        # Initialize exchange connector
        self.exchange = BinanceConnector()
        
        # Get initial capital
        self._init_capital()
        
        # Initialize risk manager
        self.risk = RiskManager(self.initial_equity)
        
        # Initialize AI regime detector
        self.regime_detector = MarketRegimeDetector()
        
        # Initialize strategies (FUND_ARB disabled — no futures perms)
        self.strategies = {
            'GRID': GridTradingStrategy(self.exchange, self.risk),
            'MEAN_REV': MeanReversionStrategy(self.exchange, self.risk),
            'MOMENTUM': MomentumBreakoutStrategy(self.exchange, self.risk),
            'FUND_ARB': FundingArbStrategy(self.exchange, self.risk),
        }
        # Deactivate FundArb since we have no futures trading permissions
        self.strategies['FUND_ARB'].active = False
        logger.warning("[ENGINE] FUND_ARB DISABLED — no futures trading permissions")
        
        # Initialize Futures symbols if in Futures mode
        if FUTURES_MODE:
            self._init_futures_symbols()
        
        # Timing
        self.last_run: Dict[str, float] = {k: 0 for k in self.INTERVALS}
        self.start_time = time.time()
        self.cycle_count = 0
        
        # FIX-3: Startup cooldown — block new entries for 5 min after boot
        # Prevents burst of 6 correlated MOM entries on restart (16:07 incident)
        self.startup_cooldown_sec = 300  # 5 minutes
        self.startup_ready = False
        
        # Graceful shutdown
        self.running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Performance log
        self.perf_log_file = DATA_DIR / 'performance.jsonl'
        
        # Watchdog: signal & execution tracking
        self._watchdog = {
            'signals_generated': 0,
            'trades_executed': 0,
            'rejections': 0,
            'zero_size': 0,
            'last_trade_time': 0,
            'last_signal_time': 0,
            'signals_by_strategy': {'MOMENTUM': 0, 'MEAN_REV': 0},
            'audit_count': 0,
        }
        
        logger.info("HYDRA Engine initialized successfully")
    
    def _init_futures_symbols(self):
        """Initialize all trading pairs for Futures (set leverage + margin type)."""
        all_pairs = set(MOMENTUM_PAIRS + MEAN_REVERSION_PAIRS)
        logger.info(f"[FUTURES] Initializing {len(all_pairs)} symbols with {FUTURES_LEVERAGE}x leverage...")
        for symbol in all_pairs:
            self.exchange.init_futures_symbol(symbol, FUTURES_LEVERAGE)
        logger.info("[FUTURES] All symbols initialized")
    
    def _init_capital(self):
        """Initialize capital - detect from Binance or set paper amount."""
        if PAPER_TRADE:
            # For paper trading, start with a realistic amount
            # Try to detect real balance first for accurate simulation
            try:
                real_balance = self.exchange.get_total_equity_usdt()
                if real_balance > 0:
                    self.initial_equity = real_balance
                    self.exchange.init_paper_balance(real_balance)
                    logger.info(f"Paper trading using real balance: ${real_balance:.2f}")
                else:
                    # Default paper balance
                    self.initial_equity = 1000.0
                    self.exchange.init_paper_balance(1000.0)
                    logger.info("Paper trading with $1000 default balance")
            except:
                self.initial_equity = 1000.0
                self.exchange.init_paper_balance(1000.0)
                logger.info("Paper trading with $1000 default balance")
        else:
            self.initial_equity = self.exchange.get_total_equity_usdt()
            if self.initial_equity <= 0:
                logger.critical("NO CAPITAL DETECTED! Check Binance balance.")
                sys.exit(1)
            logger.info(f"Live trading capital: ${self.initial_equity:.2f}")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.warning(f"Shutdown signal received ({signum})")
        self.running = False
    
    # ================================================================
    # MAIN LOOP
    # ================================================================
    
    def run(self):
        """Main trading loop - runs forever until stopped."""
        logger.info("Starting main trading loop...")
        
        # Initial connectivity check
        if not self.exchange.ping():
            logger.error("Cannot connect to Binance! Check network/API keys.")
            if not PAPER_TRADE:
                return
            logger.warning("Continuing in paper mode with cached data...")
        else:
            logger.info("Binance connectivity: OK")
        
        while self.running:
            try:
                self.cycle_count += 1
                cycle_start = time.time()
                now = time.time()
                
                # ===== 1. MARKET REGIME DETECTION =====
                if now - self.last_run.get('REGIME', 0) > self.INTERVALS['REGIME']:
                    self._run_regime_detection()
                    self.last_run['REGIME'] = now
                
                # ===== FIX-3: STARTUP COOLDOWN =====
                # Block new entries for 5 min after boot to let indicators stabilize
                if not self.startup_ready:
                    elapsed = now - self.start_time
                    if elapsed < self.startup_cooldown_sec:
                        remaining = self.startup_cooldown_sec - elapsed
                        if self.cycle_count % 30 == 1:  # Log every ~90s
                            logger.info(f"[ENGINE] Startup cooldown: {remaining:.0f}s remaining — no new entries")
                        # Still manage existing positions (Grid rebalance, trailing stops)
                        if PAPER_TRADE:
                            self.exchange.check_paper_orders()
                        if now - self.last_run.get('STATS', 0) > self.INTERVALS['STATS']:
                            self._log_performance()
                            self.last_run['STATS'] = now
                        time.sleep(3)
                        continue
                    else:
                        self.startup_ready = True
                        logger.info("[ENGINE] Startup cooldown complete — strategies armed")
                
                # ===== 2. EXECUTE STRATEGIES =====
                
                # Grid Trading (most frequent)
                if now - self.last_run.get('GRID', 0) > self.INTERVALS['GRID']:
                    self._run_strategy('GRID')
                    self.last_run['GRID'] = now
                
                # Mean Reversion
                if now - self.last_run.get('MEAN_REV', 0) > self.INTERVALS['MEAN_REV']:
                    self._run_strategy('MEAN_REV')
                    self.last_run['MEAN_REV'] = now
                
                # Momentum Breakout
                if now - self.last_run.get('MOMENTUM', 0) > self.INTERVALS['MOMENTUM']:
                    self._run_strategy('MOMENTUM')
                    self.last_run['MOMENTUM'] = now
                
                # Funding Arbitrage
                if now - self.last_run.get('FUND_ARB', 0) > self.INTERVALS['FUND_ARB']:
                    self._run_strategy('FUND_ARB')
                    self.last_run['FUND_ARB'] = now
                
                # ===== 3. CHECK PAPER ORDERS =====
                if PAPER_TRADE:
                    self.exchange.check_paper_orders()
                
                # ===== 4. STATS & MONITORING =====
                if now - self.last_run.get('STATS', 0) > self.INTERVALS['STATS']:
                    self._log_performance()
                    self.last_run['STATS'] = now
                
                # ===== 5. SAVE STATE =====
                if now - self.last_run.get('SAVE', 0) > self.INTERVALS['SAVE']:
                    self._save_state()
                    self.last_run['SAVE'] = now
                
                # ===== 6. SELF-DIAGNOSTIC WATCHDOG =====
                if now - self.last_run.get('HEALTH', 0) > self.INTERVALS['HEALTH']:
                    self._run_health_audit()
                    self.last_run['HEALTH'] = now
                
                # Sleep between cycles (faster for aggressive mode)
                cycle_time = time.time() - cycle_start
                sleep_time = max(0.5, 3 - cycle_time)  # Target 3-second cycles (was 5)
                time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                self.running = False
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                logger.error(traceback.format_exc())
                time.sleep(10)  # Wait before retrying
        
        # Cleanup
        self._shutdown()
    
    # ================================================================
    # STRATEGY EXECUTION
    # ================================================================
    
    def _run_strategy(self, name: str):
        """Execute a strategy with error handling."""
        strategy = self.strategies.get(name)
        if not strategy or not strategy.active:
            return
        
        if self.risk.trading_halted:
            return
        
        try:
            strategy.execute()
        except Exception as e:
            logger.error(f"Strategy [{name}] error: {e}")
            logger.debug(traceback.format_exc())
    
    def _run_health_audit(self):
        """Self-diagnostic watchdog — detects silent failures that prevent trading."""
        self._watchdog['audit_count'] += 1
        uptime_h = (time.time() - self.start_time) / 3600
        total_trades = self.risk.total_trades
        
        # Count current signals & rejections from strategy internals
        mom_signals = len(getattr(self.strategies.get('MOMENTUM'), '_signals', []) or [])
        mr_signals = len(getattr(self.strategies.get('MEAN_REV'), '_signals', []) or [])
        self._watchdog['signals_generated'] += mom_signals + mr_signals
        
        problems = []
        
        # CHECK 1: Zero trades after significant uptime
        if uptime_h >= 1.0 and total_trades == 0:
            problems.append(f"ZERO TRADES in {uptime_h:.1f}h — bot is not trading!")
            
            # Diagnose WHY
            for name in ['MOMENTUM', 'MEAN_REV']:
                strat = self.strategies.get(name)
                if not strat:
                    continue
                alloc = strat.allocation
                regime = strat.current_regime
                
                if alloc <= 0:
                    problems.append(f"  {name}: allocation=0% (regime={regime}) → will never trade")
                elif alloc < 0.2:
                    problems.append(f"  {name}: allocation={alloc*100:.0f}% (regime={regime}) → very low budget")
                else:
                    # Has allocation but no trades — check signal pipeline
                    signals = getattr(strat, '_signals', []) or []
                    if not signals:
                        problems.append(f"  {name}: alloc={alloc*100:.0f}% but 0 signals → thresholds too tight?")
                    else:
                        problems.append(f"  {name}: alloc={alloc*100:.0f}%, {len(signals)} signals → execution blocked")
        
        # CHECK 2: Signals generated but conversion rate is 0%
        if self._watchdog['signals_generated'] > 10 and total_trades == 0:
            problems.append(f"SIGNAL LEAK: {self._watchdog['signals_generated']} signals generated, 0 executed")
        
        # CHECK 3: Risk manager halted
        if self.risk.trading_halted:
            problems.append(f"TRADING HALTED: {self.risk.halt_reason}")
        
        # CHECK 4: All strategies disabled or zero allocation
        active_alloc = sum(s.allocation for name, s in self.strategies.items() 
                          if s.active and name not in ('GRID', 'FUND_ARB'))
        if active_alloc <= 0:
            problems.append(f"ALL ACTIVE STRATEGIES HAVE 0% ALLOCATION (regime={self.regime_detector.current_regime})")
        
        # CHECK 5: No trade in extended period (after first trade)
        if total_trades > 0:
            last_trade_age = time.time() - max((t.get('timestamp', 0) for t in self.risk.trade_history), default=0)
            if last_trade_age > 7200:  # 2 hours since last trade
                problems.append(f"STALE: Last trade was {last_trade_age/3600:.1f}h ago")
        
        # REPORT
        if problems:
            logger.warning("="*60)
            logger.warning("[WATCHDOG] HEALTH AUDIT — PROBLEMS DETECTED")
            logger.warning("="*60)
            for p in problems:
                logger.warning(f"[WATCHDOG] {p}")
            logger.warning(f"[WATCHDOG] Uptime: {uptime_h:.1f}h | Trades: {total_trades} | "
                          f"Regime: {self.regime_detector.current_regime} | "
                          f"Signals seen: {self._watchdog['signals_generated']}")
            logger.warning("="*60)
        else:
            logger.info(f"[WATCHDOG] Health OK | {uptime_h:.1f}h | {total_trades} trades | "
                       f"Regime: {self.regime_detector.current_regime}")
    
    def _run_regime_detection(self):
        """Run market regime detection and adjust allocations."""
        try:
            # Gather market data for regime analysis
            market_data = self._gather_market_data()
            
            # Detect regime
            regime, confidence, weights = self.regime_detector.detect_regime(market_data)
            
            # Adjust strategy allocations based on regime
            # Fixed: was > 0.5, but default CHOPPY returns exactly 0.50, causing weights to never apply
            if confidence >= 0.5:
                for strat_name, weight in weights.items():
                    if strat_name in self.strategies:
                        old_alloc = self.strategies[strat_name].allocation
                        self.strategies[strat_name].allocation = weight
                        self.strategies[strat_name].current_regime = regime
                        if abs(old_alloc - weight) > 0.05:
                            logger.info(f"[REGIME] {strat_name} allocation: "
                                      f"{old_alloc*100:.0f}% → {weight*100:.0f}%")
                
                # Update risk manager regime for adaptive sizing
                self.risk.current_regime = regime
                
                logger.info(f"[REGIME] Current: {regime} (confidence: {confidence:.0%})")
            
        except Exception as e:
            logger.error(f"Regime detection error: {e}")
    
    def _gather_market_data(self) -> Dict:
        """Gather market data for regime analysis."""
        data = {}
        
        try:
            # BTC data
            btc_price = self.exchange.get_price('BTCUSDT')
            data['btc_price'] = btc_price
            
            btc_ticker = self.exchange.get_24h_ticker('BTCUSDT')
            if btc_ticker:
                data['btc_change_24h'] = float(btc_ticker.get('priceChangePercent', 0))
            
            # Calculate 1h change from klines
            btc_klines = self.exchange.get_klines('BTCUSDT', '1h', limit=2)
            if not btc_klines.empty:
                data['btc_change_1h'] = ((float(btc_klines['close'].iloc[-1]) - float(btc_klines['open'].iloc[-2])) / 
                                         float(btc_klines['open'].iloc[-2]) * 100)
            
            # Volatility (ATR as % of price)
            btc_klines_long = self.exchange.get_klines('BTCUSDT', '1h', limit=24)
            if not btc_klines_long.empty:
                tr = (btc_klines_long['high'] - btc_klines_long['low']).mean()
                data['btc_volatility'] = (tr / btc_price * 100) if btc_price else 0
            
            # ETH
            data['eth_price'] = self.exchange.get_price('ETHUSDT')
            
            # Funding rates
            data['funding_rates'] = {}
            for pair in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']:
                rate = self.exchange.get_funding_rate(pair)
                if rate is not None:
                    data['funding_rates'][pair] = rate * 100
            
        except Exception as e:
            logger.error(f"Error gathering market data: {e}")
        
        return data
    
    # ================================================================
    # MONITORING & LOGGING
    # ================================================================
    
    def _log_performance(self):
        """Log comprehensive performance metrics."""
        # Update equity
        equity = self.exchange.get_total_equity_usdt()
        if equity > 0:
            self.risk.current_equity = equity
            if equity > self.risk.peak_equity:
                self.risk.peak_equity = equity
        
        stats = self.risk.get_stats()
        uptime = time.time() - self.start_time
        
        # Print dashboard
        self._print_dashboard(stats, uptime)
        
        # Log to file
        perf_record = {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'equity': stats['equity'],
            'daily_pnl': stats['daily_pnl'],
            'daily_return_pct': stats['daily_return_pct'],
            'total_return_pct': stats['total_return_pct'],
            'drawdown_pct': stats['drawdown_pct'],
            'open_positions': stats['open_positions'],
            'total_trades': stats['total_trades'],
            'win_rate': stats['win_rate'],
            'regime': self.regime_detector.current_regime,
            'cycle': self.cycle_count,
        }
        
        try:
            with open(self.perf_log_file, 'a') as f:
                f.write(json.dumps(perf_record) + '\n')
        except:
            pass
    
    def _print_dashboard(self, stats: Dict, uptime: float):
        """Print formatted dashboard."""
        hours = uptime / 3600
        
        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════════╗")
        logger.info("║              HYDRA TRADING DASHBOARD                     ║")
        logger.info("╠══════════════════════════════════════════════════════════╣")
        logger.info(f"║  Equity:       ${stats['equity']:>12.2f}  (Peak: ${stats['peak_equity']:>10.2f})  ║")
        logger.info(f"║  Daily P&L:    ${stats['daily_pnl']:>+12.2f}  ({stats['daily_return_pct']:>+8.2f}%)       ║")
        logger.info(f"║  Total P&L:    ${stats['total_pnl']:>+12.2f}  ({stats['total_return_pct']:>+8.2f}%)       ║")
        logger.info(f"║  Drawdown:      {stats['drawdown_pct']:>10.2f}%                          ║")
        logger.info(f"║  Target:        {DAILY_TARGET_PCT:>10.2f}%  daily                    ║")
        logger.info("╠══════════════════════════════════════════════════════════╣")
        logger.info(f"║  Trades: {stats['total_trades']:>4} | Win: {stats['win_rate']:>5.1f}% | PF: {stats['profit_factor']:>5.2f}           ║")
        logger.info(f"║  Open: {stats['open_positions']:>3} pos | Unrealized: ${stats['unrealized_pnl']:>+10.2f}     ║")
        logger.info(f"║  Regime: {self.regime_detector.current_regime:<20}                  ║")
        logger.info(f"║  Uptime: {hours:>6.1f}h | Cycles: {self.cycle_count:>8}              ║")
        
        if stats['trading_halted']:
            logger.info(f"║  *** HALTED: {stats['halt_reason']:<40} ║")
        
        logger.info("╠══════════════════════════════════════════════════════════╣")
        
        # Strategy status
        for name, strategy in self.strategies.items():
            status = strategy.get_status()
            alloc = status['allocation'] * 100
            pos = status['positions']
            active = "ON" if status['active'] else "OFF"
            logger.info(f"║  [{active:>3}] {name:<12} | Alloc: {alloc:>4.0f}% | Positions: {pos:>2}   ║")
        
        logger.info("╚══════════════════════════════════════════════════════════╝")
        logger.info("")
    
    # ================================================================
    # STATE & SHUTDOWN
    # ================================================================
    
    def _save_state(self):
        """Save complete engine state."""
        self.risk._save_state()
        
        state = {
            'timestamp': time.time(),
            'equity': self.risk.current_equity,
            'regime': self.regime_detector.current_regime,
            'strategies': {},
        }
        
        for name, strat in self.strategies.items():
            state['strategies'][name] = strat.get_status()
        
        try:
            with open(DATA_DIR / 'engine_state.json', 'w') as f:
                json.dump(state, f, indent=2, default=str)
        except:
            pass
    
    def _shutdown(self):
        """Graceful shutdown."""
        logger.info("=" * 60)
        logger.info("HYDRA ENGINE SHUTTING DOWN")
        logger.info("=" * 60)
        
        # Save final state
        self._save_state()
        
        # Print final stats
        stats = self.risk.get_stats()
        logger.info(f"Final Equity: ${stats['equity']:.2f}")
        logger.info(f"Total P&L: ${stats['total_pnl']:+.2f} ({stats['total_return_pct']:+.2f}%)")
        logger.info(f"Trades: {stats['total_trades']} | Win Rate: {stats['win_rate']:.1f}%")
        logger.info(f"Sessions: {self.cycle_count} cycles in {(time.time()-self.start_time)/3600:.1f}h")
        logger.info("=" * 60)
        logger.info("Until next time. Survive.")
        logger.info("=" * 60)


# ================================================================
# ENTRY POINT
# ================================================================

def main():
    """Start the HYDRA trading engine."""
    engine = HydraEngine()
    engine.run()


if __name__ == '__main__':
    main()
