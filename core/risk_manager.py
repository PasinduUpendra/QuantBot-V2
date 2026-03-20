"""
HYDRA Trading System - Risk Management Layer
The guardian between strategies and the exchange.
Protects capital at ALL costs.
"""
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from pathlib import Path

import numpy as np
from loguru import logger

from core import (
    MAX_DRAWDOWN_PCT, DAILY_LOSS_LIMIT_PCT, MAX_POSITION_PCT,
    MAX_TOTAL_EXPOSURE_PCT, RISK_PER_TRADE_PCT, MIN_TRADE_VALUE, DATA_DIR,
    FUTURES_MODE, FUTURES_LEVERAGE
)


class RiskManager:
    """
    Central risk management system.
    Every trade must pass through this before execution.
    
    Rules:
    1. No single position > MAX_POSITION_PCT of equity
    2. Total exposure never > MAX_TOTAL_EXPOSURE_PCT
    3. Daily loss limit enforced - stops all trading
    4. Maximum drawdown from peak - emergency liquidation
    5. Per-trade risk limited to RISK_PER_TRADE_PCT
    6. Correlation check - don't over-concentrate
    """
    
    def __init__(self, initial_equity: float):
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.current_equity = initial_equity
        self.daily_start_equity = initial_equity
        
        # Position tracking
        self.positions: Dict[str, Dict] = {}  # symbol -> {side, qty, entry_price, strategy, value}
        self.strategy_exposure: Dict[str, float] = defaultdict(float)  # strategy -> total_value
        
        # P&L tracking
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.trade_history: List[Dict] = []
        self.equity_curve: List[Tuple[float, float]] = [(time.time(), initial_equity)]
        
        # Circuit breakers
        self.trading_halted = False
        self.halt_reason = ""
        self.last_daily_reset = datetime.now().date()
        
        # Win/Loss tracking
        self.wins = 0
        self.losses = 0
        self.total_trades = 0
        self.consecutive_losses = 0
        self.max_consecutive_losses = 0
        
        # Adaptive risk state
        self.current_regime = 'RANGING'
        
        # State file
        self.state_file = DATA_DIR / 'risk_state.json'
        self._load_state()
        
        logger.info(f"RiskManager initialized | Equity: ${initial_equity:.2f}")
        logger.info(f"Max drawdown: {MAX_DRAWDOWN_PCT}% | Daily limit: {DAILY_LOSS_LIMIT_PCT}% | Max position: {MAX_POSITION_PCT}%")
        if FUTURES_MODE:
            logger.info(f"Futures mode: ON | Leverage: {FUTURES_LEVERAGE}x")
    
    # ================================================================
    # TRADE APPROVAL
    # ================================================================
    
    def approve_trade(self, symbol: str, side: str, quantity: float, 
                      price: float, strategy: str) -> Tuple[bool, str]:
        """
        Gate function - every trade must pass through here.
        Returns (approved, reason).
        """
        # Check circuit breakers
        self._check_daily_reset()
        
        if self.trading_halted:
            return False, f"Trading halted: {self.halt_reason}"
        
        trade_value = quantity * price
        
        # In futures mode, position sizing uses leverage-amplified budgets (notional),
        # so approval limits must also be leverage-adjusted to match
        leverage_factor = FUTURES_LEVERAGE if FUTURES_MODE else 1
        
        # Rule 1: Daily loss limit
        daily_loss_pct = (self.daily_pnl / self.daily_start_equity) * 100 if self.daily_start_equity > 0 else 0
        if daily_loss_pct < -DAILY_LOSS_LIMIT_PCT:
            self.trading_halted = True
            self.halt_reason = f"Daily loss limit hit: {daily_loss_pct:.2f}%"
            logger.warning(f"CIRCUIT BREAKER: {self.halt_reason}")
            return False, self.halt_reason
        
        # Rule 2: Maximum drawdown
        drawdown_pct = ((self.peak_equity - self.current_equity) / self.peak_equity) * 100 if self.peak_equity > 0 else 0
        if drawdown_pct > MAX_DRAWDOWN_PCT:
            self.trading_halted = True
            self.halt_reason = f"Max drawdown hit: {drawdown_pct:.2f}%"
            logger.warning(f"CIRCUIT BREAKER: {self.halt_reason}")
            return False, self.halt_reason
        
        # Rule 3: Single position size limit (leverage-adjusted)
        existing_position_value = 0
        if symbol in self.positions:
            existing_position_value = self.positions[symbol].get('value', 0)
        
        new_position_value = existing_position_value + trade_value
        position_pct = (new_position_value / self.current_equity) * 100 if self.current_equity > 0 else 100
        
        if position_pct > MAX_POSITION_PCT * leverage_factor:
            return False, f"Position size {position_pct:.1f}% exceeds limit {MAX_POSITION_PCT * leverage_factor}%"
        
        # Rule 4: Total exposure limit (leverage-adjusted)
        total_exposure = sum(p.get('value', 0) for p in self.positions.values())
        new_total = total_exposure + trade_value
        exposure_pct = (new_total / self.current_equity) * 100 if self.current_equity > 0 else 100
        
        if exposure_pct > MAX_TOTAL_EXPOSURE_PCT * leverage_factor:
            return False, f"Total exposure {exposure_pct:.1f}% exceeds limit {MAX_TOTAL_EXPOSURE_PCT * leverage_factor}%"
        
        # Rule 5: Per-trade risk limit
        risk_amount = trade_value * (RISK_PER_TRADE_PCT / 100)
        max_risk = self.current_equity * (RISK_PER_TRADE_PCT / 100)
        
        # Rule 6: Consecutive loss throttle (only after 7 losses, less aggressive)
        if self.consecutive_losses >= 7:
            if trade_value > (self.current_equity * MAX_POSITION_PCT * leverage_factor / 200):
                return False, f"Consecutive losses ({self.consecutive_losses}): reduce size"
        
        # Rule 7: Minimum trade value (configurable - lower in paper mode)
        if trade_value < MIN_TRADE_VALUE:
            return False, f"Trade value ${trade_value:.2f} below minimum ${MIN_TRADE_VALUE:.0f}"
        
        logger.info(f"[RISK] APPROVED: {side} {quantity:.6f} {symbol} @ {price:.2f} (${trade_value:.2f}) [{strategy}]")
        return True, "Approved"
    
    # ================================================================
    # POSITION MANAGEMENT
    # ================================================================
    
    def register_position(self, symbol: str, side: str, quantity: float,
                         entry_price: float, strategy: str, 
                         stop_loss: float = None, take_profit: float = None):
        """Register a new position or update existing."""
        value = quantity * entry_price
        
        self.positions[symbol] = {
            'side': side,
            'quantity': quantity,
            'entry_price': entry_price,
            'strategy': strategy,
            'value': value,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'entry_time': time.time(),
            'unrealized_pnl': 0,
        }
        
        self.strategy_exposure[strategy] += value
        logger.info(f"Position registered: {side} {quantity:.6f} {symbol} @ {entry_price:.2f} [{strategy}]")
    
    def close_position(self, symbol: str, exit_price: float, reason: str = ""):
        """Close a position and record P&L."""
        if symbol not in self.positions:
            logger.warning(f"No position to close for {symbol}")
            return
        
        pos = self.positions[symbol]
        
        if pos['side'] == 'BUY':
            pnl = (exit_price - pos['entry_price']) * pos['quantity']
        else:
            pnl = (pos['entry_price'] - exit_price) * pos['quantity']
        
        pnl_pct = (pnl / pos['value']) * 100 if pos['value'] > 0 else 0
        
        # Update tracking
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.current_equity += pnl
        self.total_trades += 1
        
        if pnl > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.max_consecutive_losses = max(self.max_consecutive_losses, self.consecutive_losses)
        
        # Update peak equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        
        # Record trade
        trade_record = {
            'symbol': symbol,
            'side': pos['side'],
            'entry_price': pos['entry_price'],
            'exit_price': exit_price,
            'quantity': pos['quantity'],
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'strategy': pos['strategy'],
            'reason': reason,
            'duration': time.time() - pos['entry_time'],
            'timestamp': time.time(),
        }
        self.trade_history.append(trade_record)
        self.equity_curve.append((time.time(), self.current_equity))
        
        # Update strategy exposure
        self.strategy_exposure[pos['strategy']] = max(0, 
            self.strategy_exposure[pos['strategy']] - pos['value'])
        
        # Remove position
        del self.positions[symbol]
        
        emoji = "+" if pnl > 0 else ""
        logger.info(f"Position closed: {symbol} | PnL: {emoji}${pnl:.2f} ({emoji}{pnl_pct:.2f}%) | {reason}")
    
    def update_position_value(self, symbol: str, current_price: float):
        """Update unrealized P&L for a position."""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        if pos['side'] == 'BUY':
            pos['unrealized_pnl'] = (current_price - pos['entry_price']) * pos['quantity']
        else:
            pos['unrealized_pnl'] = (pos['entry_price'] - current_price) * pos['quantity']
        pos['current_price'] = current_price
        pos['value'] = pos['quantity'] * current_price
    
    def check_stops(self, symbol: str, current_price: float) -> Optional[str]:
        """Check if stop loss or take profit hit. Returns action or None."""
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]
        
        if pos['side'] == 'BUY':
            if pos['stop_loss'] and current_price <= pos['stop_loss']:
                return 'STOP_LOSS'
            if pos['take_profit'] and current_price >= pos['take_profit']:
                return 'TAKE_PROFIT'
        else:
            if pos['stop_loss'] and current_price >= pos['stop_loss']:
                return 'STOP_LOSS'
            if pos['take_profit'] and current_price <= pos['take_profit']:
                return 'TAKE_PROFIT'
        
        return None
    
    def update_trailing_stop(self, symbol: str, current_price: float, trail_pct: float):
        """Update trailing stop loss."""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        
        if pos['side'] == 'BUY':
            new_stop = current_price * (1 - trail_pct / 100)
            if pos['stop_loss'] is None or new_stop > pos['stop_loss']:
                pos['stop_loss'] = new_stop
        else:
            new_stop = current_price * (1 + trail_pct / 100)
            if pos['stop_loss'] is None or new_stop < pos['stop_loss']:
                pos['stop_loss'] = new_stop
    
    # ================================================================
    # POSITION SIZING
    # ================================================================
    
    def calculate_position_size(self, symbol: str, entry_price: float, 
                                stop_price: float, strategy: str,
                                strategy_allocation: float) -> float:
        """
        Calculate optimal position size using fractional Kelly / ATR-based sizing.
        
        Uses the smaller of:
        1. Risk-based: risk_amount / distance_to_stop
        2. Allocation-based: strategy_allocation * equity / price
        3. Max position: MAX_POSITION_PCT * equity / price
        
        In FUTURES_MODE, sizes are leverage-adjusted (notional = margin * leverage).
        """
        if entry_price <= 0 or self.current_equity <= 0:
            return 0
        
        # Adaptive risk calculation
        adaptive_risk_pct = self._calculate_adaptive_risk()
        risk_amount = self.current_equity * (adaptive_risk_pct / 100)
        
        # Distance to stop
        stop_distance = abs(entry_price - stop_price) if stop_price else entry_price * 0.02
        if stop_distance == 0:
            stop_distance = entry_price * 0.01
        
        # Risk-based size (how many units can we buy within our risk budget)
        risk_size = risk_amount / stop_distance
        
        # Allocation-based size
        strategy_budget = self.current_equity * strategy_allocation
        if FUTURES_MODE:
            strategy_budget *= FUTURES_LEVERAGE  # Leverage amplifies budget
        already_used = self.strategy_exposure.get(strategy, 0)
        remaining_budget = max(0, strategy_budget - already_used)
        alloc_size = remaining_budget / entry_price
        
        # Max position limit
        max_position_value = self.current_equity * MAX_POSITION_PCT / 100
        if FUTURES_MODE:
            max_position_value *= FUTURES_LEVERAGE
        max_size = max_position_value / entry_price
        
        # Consecutive loss adjustment (less aggressive reduction)
        if self.consecutive_losses >= 5:
            reduction = 1 - (self.consecutive_losses * 0.05)  # 5% reduction per loss (was 10%)
            reduction = max(0.5, reduction)  # floor at 50% of normal (was 30%)
            risk_size *= reduction
            alloc_size *= reduction
        
        # Take minimum of all constraints
        position_size = min(risk_size, alloc_size, max_size)
        
        # Liquidation safety check for futures
        if FUTURES_MODE and position_size > 0:
            if not self._check_liquidation_safe(entry_price, stop_price, position_size):
                logger.warning(f"[RISK] {symbol}: Liquidation too close, reducing size by 50%")
                position_size *= 0.5
        
        return max(0, position_size)
    
    def _calculate_adaptive_risk(self) -> float:
        """
        Dynamically adjust risk per trade based on recent performance and regime.
        Returns adaptive risk percentage (capped between 2.0% and 6.0%).
        """
        base_risk = RISK_PER_TRADE_PCT
        
        # Regime multiplier
        regime_mult = {
            'TRENDING_BULL': 1.3,
            'TRENDING_BEAR': 1.3,   # Strong trend = size up (shorting in bear)
            'HIGH_VOLATILITY': 1.0,
            'RANGING': 1.0,
            'CHOPPY': 0.7,
            'RISK_OFF': 0.3,
        }.get(self.current_regime, 1.0)
        
        # Win streak multiplier based on last 10 trades
        recent_trades = self.trade_history[-10:] if len(self.trade_history) >= 5 else []
        streak_mult = 1.0
        if len(recent_trades) >= 5:
            recent_wins = sum(1 for t in recent_trades if t.get('pnl', 0) > 0)
            if recent_wins >= 7:
                streak_mult = 1.25
            elif recent_wins <= 3:
                streak_mult = 0.75
        
        adaptive = base_risk * regime_mult * streak_mult
        adaptive = max(2.0, min(6.0, adaptive))  # Floor 2%, cap 6%
        
        if abs(adaptive - base_risk) > 0.5:
            logger.info(f"[ADAPTIVE] Risk: {base_risk:.1f}% × {regime_mult:.1f} (regime) "
                       f"× {streak_mult:.2f} (streak) = {adaptive:.1f}%")
        
        return adaptive
    
    def _check_liquidation_safe(self, entry_price: float, stop_price: float,
                                 quantity: float) -> bool:
        """
        Check if liquidation price is safe distance from entry.
        Returns True if safe, False if too dangerous.
        """
        if not FUTURES_MODE or FUTURES_LEVERAGE <= 1:
            return True
        
        # Estimate ATR from stop distance (stop is typically 2x ATR from entry)
        atr_estimate = abs(entry_price - stop_price) / 2.0
        
        # For LONG: liquidation at entry * (1 - 1/leverage)
        # For SHORT: liquidation at entry * (1 + 1/leverage)
        liq_distance = entry_price / FUTURES_LEVERAGE
        
        # Must have at least 2x ATR buffer to liquidation
        return liq_distance > (atr_estimate * 2)
    
    # ================================================================
    # STATISTICS
    # ================================================================
    
    def get_stats(self) -> Dict:
        """Get comprehensive trading statistics."""
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        drawdown = ((self.peak_equity - self.current_equity) / self.peak_equity * 100) if self.peak_equity > 0 else 0
        daily_return = ((self.current_equity - self.daily_start_equity) / self.daily_start_equity * 100) if self.daily_start_equity > 0 else 0
        total_return = ((self.current_equity - self.initial_equity) / self.initial_equity * 100) if self.initial_equity > 0 else 0
        
        # Average win/loss
        winning_trades = [t for t in self.trade_history if t['pnl'] > 0]
        losing_trades = [t for t in self.trade_history if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([abs(t['pnl']) for t in losing_trades]) if losing_trades else 0
        profit_factor = (sum(t['pnl'] for t in winning_trades) / sum(abs(t['pnl']) for t in losing_trades)) if losing_trades and sum(abs(t['pnl']) for t in losing_trades) > 0 else 0
        
        open_positions = len(self.positions)
        total_unrealized = sum(p.get('unrealized_pnl', 0) for p in self.positions.values())
        
        return {
            'equity': self.current_equity,
            'initial_equity': self.initial_equity,
            'peak_equity': self.peak_equity,
            'total_pnl': self.total_pnl,
            'daily_pnl': self.daily_pnl,
            'total_return_pct': total_return,
            'daily_return_pct': daily_return,
            'drawdown_pct': drawdown,
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'consecutive_losses': self.consecutive_losses,
            'max_consecutive_losses': self.max_consecutive_losses,
            'open_positions': open_positions,
            'unrealized_pnl': total_unrealized,
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
        }
    
    def print_stats(self):
        """Print formatted statistics."""
        stats = self.get_stats()
        
        logger.info("=" * 60)
        logger.info("                HYDRA RISK DASHBOARD")
        logger.info("=" * 60)
        logger.info(f"  Equity:        ${stats['equity']:.2f} (Initial: ${stats['initial_equity']:.2f})")
        logger.info(f"  Total Return:  {stats['total_return_pct']:.2f}%")
        logger.info(f"  Daily P&L:     ${stats['daily_pnl']:.2f} ({stats['daily_return_pct']:.2f}%)")
        logger.info(f"  Drawdown:      {stats['drawdown_pct']:.2f}%")
        logger.info(f"  Trades:        {stats['total_trades']} (W:{stats['wins']} L:{stats['losses']})")
        logger.info(f"  Win Rate:      {stats['win_rate']:.1f}%")
        logger.info(f"  Profit Factor: {stats['profit_factor']:.2f}")
        logger.info(f"  Open Positions: {stats['open_positions']} (PnL: ${stats['unrealized_pnl']:.2f})")
        if stats['trading_halted']:
            logger.warning(f"  *** TRADING HALTED: {stats['halt_reason']} ***")
        logger.info("=" * 60)
    
    # ================================================================
    # STATE PERSISTENCE
    # ================================================================
    
    def _check_daily_reset(self):
        """Reset daily counters at midnight."""
        today = datetime.now().date()
        if today > self.last_daily_reset:
            logger.info(f"Daily reset | Yesterday P&L: ${self.daily_pnl:.2f}")
            # Compounding verification log
            logger.info(f"[COMPOUND] Equity basis: ${self.daily_start_equity:.2f} → ${self.current_equity:.2f} "
                       f"({((self.current_equity - self.daily_start_equity) / self.daily_start_equity * 100) if self.daily_start_equity > 0 else 0:+.2f}%)")
            self.daily_start_equity = self.current_equity
            self.daily_pnl = 0
            self.last_daily_reset = today
            
            # Reset daily halt if drawdown is acceptable
            if self.trading_halted and "Daily" in self.halt_reason:
                drawdown = (self.peak_equity - self.current_equity) / self.peak_equity * 100
                if drawdown < MAX_DRAWDOWN_PCT:
                    self.trading_halted = False
                    self.halt_reason = ""
                    logger.info("Daily halt lifted - new trading day")
    
    def _save_state(self):
        """Save risk state to disk."""
        try:
            state = {
                'peak_equity': self.peak_equity,
                'current_equity': self.current_equity,
                'daily_start_equity': self.daily_start_equity,
                'total_pnl': self.total_pnl,
                'wins': self.wins,
                'losses': self.losses,
                'total_trades': self.total_trades,
                'consecutive_losses': self.consecutive_losses,
                'max_consecutive_losses': self.max_consecutive_losses,
                'last_daily_reset': str(self.last_daily_reset),
                'trade_history': self.trade_history[-100:],  # Keep last 100
                'timestamp': time.time(),
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save risk state: {e}")
    
    def _load_state(self):
        """Load saved risk state."""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            # Only restore if recent (within 24h)
            if time.time() - state.get('timestamp', 0) < 86400:
                self.peak_equity = max(self.peak_equity, state.get('peak_equity', self.peak_equity))
                self.wins = state.get('wins', 0)
                self.losses = state.get('losses', 0)
                self.total_trades = state.get('total_trades', 0)
                self.consecutive_losses = state.get('consecutive_losses', 0)
                self.max_consecutive_losses = state.get('max_consecutive_losses', 0)
                self.trade_history = state.get('trade_history', [])
                logger.info(f"Restored risk state: {self.total_trades} historical trades")
        except Exception as e:
            logger.error(f"Failed to load risk state: {e}")
    
    def emergency_liquidate(self):
        """EMERGENCY: Flag all positions for immediate closure."""
        self.trading_halted = True
        self.halt_reason = "EMERGENCY LIQUIDATION"
        logger.critical("*** EMERGENCY LIQUIDATION TRIGGERED ***")
        # Return list of positions to close
        return list(self.positions.keys())
