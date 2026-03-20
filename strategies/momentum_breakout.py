"""
HYDRA Trading System - Momentum Breakout Strategy
===================================================
Captures large directional moves with trend following.

SIGNALS:
- EMA crossover (9/21) with trend confirmation (50 EMA)
- ADX > 25 confirms trend strength
- Volume spike confirmation (>1.5x average)
- Breakout of recent high/low with momentum

EDGE:
- Catches 2-5% moves that happen daily in crypto
- Trailing stop captures maximum profit from strong trends
- ADX filter avoids whipsaws in ranging markets

TIMEFRAME: 1h (primary), confirmed on 4h
"""
import time
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from loguru import logger

from strategies.base import BaseStrategy
from core.exchange import BinanceConnector
from core.risk_manager import RiskManager
from core import (
    MOMENTUM_PAIRS, MOMENTUM_ALLOCATION,
    MOM_TIMEFRAME, MOM_FAST_EMA, MOM_SLOW_EMA, MOM_TREND_EMA,
    MOM_ADX_PERIOD, MOM_ADX_THRESHOLD, MOM_VOLUME_MULT,
    MOM_ATR_PERIOD, MOM_ATR_STOP_MULT, MOM_ATR_TARGET_MULT,
    MOM_TRAIL_ATR_MULT, MOM_MAX_POSITIONS, MOM_LOOKBACK_CANDLES,
    FUTURES_MODE
)


class MomentumBreakoutStrategy(BaseStrategy):
    """
    Trend-following strategy using EMA crossovers + ADX + volume.
    Uses trailing stops to ride trends.
    """
    
    def __init__(self, exchange: BinanceConnector, risk_manager: RiskManager):
        super().__init__("MOMENTUM", exchange, risk_manager, MOMENTUM_ALLOCATION)
        self.pairs = MOMENTUM_PAIRS
        self.open_positions: Dict[str, Dict] = {}
        self.cooldowns: Dict[str, float] = {}
        self.cooldown_period = 600  # 10 min cooldown (was 30 min)
        # FIX-4: Global entry spacing — prevent correlated burst entries on restart
        self.last_global_entry_time = 0
        self.global_entry_spacing = 600  # 10 min between ANY MOM entry
    
    def analyze(self) -> List[Dict]:
        """Analyze pairs for momentum breakout opportunities."""
        signals = []
        
        for symbol in self.pairs:
            try:
                # Get primary timeframe data
                df = self.exchange.get_klines(symbol, MOM_TIMEFRAME, limit=MOM_LOOKBACK_CANDLES)
                if df.empty or len(df) < MOM_TREND_EMA + 5:
                    continue
                
                # Cooldown check
                if symbol in self.cooldowns:
                    if time.time() - self.cooldowns[symbol] < self.cooldown_period:
                        continue
                
                close = df['close']
                high = df['high']
                low = df['low']
                volume = df['volume']
                
                # =====================================================
                # INDICATORS
                # =====================================================
                
                # EMAs
                ema_fast = close.ewm(span=MOM_FAST_EMA, adjust=False).mean()
                ema_slow = close.ewm(span=MOM_SLOW_EMA, adjust=False).mean()
                ema_trend = close.ewm(span=MOM_TREND_EMA, adjust=False).mean()
                
                # ADX
                adx, plus_di, minus_di = self._calculate_adx(df, MOM_ADX_PERIOD)
                
                # ATR
                atr = self._calculate_atr(df, MOM_ATR_PERIOD)
                
                # Volume
                vol_sma = volume.rolling(20).mean()
                vol_ratio = float(volume.iloc[-1] / vol_sma.iloc[-1]) if float(vol_sma.iloc[-1]) > 0 else 1.0
                
                # MACD
                macd_line = ema_fast - ema_slow
                macd_signal = macd_line.ewm(span=9, adjust=False).mean()
                macd_hist = macd_line - macd_signal
                
                # Price action
                recent_high = float(high.iloc[-20:].max())
                recent_low = float(low.iloc[-20:].min())
                
                # Current values
                current_price = float(close.iloc[-1])
                current_ema_fast = float(ema_fast.iloc[-1])
                current_ema_slow = float(ema_slow.iloc[-1])
                current_ema_trend = float(ema_trend.iloc[-1])
                prev_ema_fast = float(ema_fast.iloc[-2])
                prev_ema_slow = float(ema_slow.iloc[-2])
                current_adx = float(adx) if not pd.isna(adx) else 0
                current_plus_di = float(plus_di) if not pd.isna(plus_di) else 0
                current_minus_di = float(minus_di) if not pd.isna(minus_di) else 0
                current_macd_hist = float(macd_hist.iloc[-1])
                prev_macd_hist = float(macd_hist.iloc[-2])
                current_atr = float(atr) if not pd.isna(atr) else current_price * 0.01
                
                # =====================================================
                # SIGNAL LOGIC
                # =====================================================
                
                signal = None
                
                # FIX-2: HARD VOLUME GATE — no volume = no trade, period
                # 48% of MOM signals had vol < 1.0x and most lost money
                if vol_ratio < 1.0:
                    continue  # Below-average volume = no conviction

                # DIRECTIONAL FILTER: Separate paths for bull vs bear
                is_bearish_di = current_minus_di > current_plus_di
                
                # REGIME FILTER: In bear markets, require very strong momentum for LONGS
                if self.current_regime in ('TRENDING_BEAR', 'RISK_OFF'):
                    if not is_bearish_di:
                        # Trying to go long in bear — require very strong signal
                        if current_adx < 30 or current_plus_di < current_minus_di * 1.5:
                            continue
                
                # BULLISH MOMENTUM (only when +DI > -DI)
                if not is_bearish_di:
                    bullish_conditions = [
                        current_ema_fast > current_ema_slow,           # Fast above slow
                        prev_ema_fast <= prev_ema_slow or              # Fresh crossover OR
                        current_price > recent_high * 0.995,           # Near breakout
                        current_price > current_ema_trend,             # Above trend EMA
                        current_adx > MOM_ADX_THRESHOLD,               # Strong trend
                        current_plus_di > current_minus_di * 1.2,      # Clear bullish DI gap
                        vol_ratio > MOM_VOLUME_MULT,                   # Full volume confirmation
                        current_macd_hist > 0,                         # MACD bullish
                    ]
                    
                    bullish_score = sum(bullish_conditions) / len(bullish_conditions)
                    
                    if bullish_score >= 0.70:
                        is_fresh = prev_ema_fast <= prev_ema_slow and current_ema_fast > current_ema_slow
                        strength = bullish_score * 2 + (0.5 if is_fresh else 0)
                        
                        stop_loss = current_price - (current_atr * MOM_ATR_STOP_MULT)
                        take_profit = current_price + (current_atr * MOM_ATR_TARGET_MULT)
                        
                        signal = {
                            'symbol': symbol,
                            'side': 'BUY',
                            'strength': strength,
                            'entry': current_price,
                            'stop': stop_loss,
                            'target': take_profit,
                            'adx': current_adx,
                            'ema_fast': current_ema_fast,
                            'ema_slow': current_ema_slow,
                            'vol_ratio': vol_ratio,
                            'atr': current_atr,
                            'is_fresh_crossover': is_fresh,
                            'macd_hist': current_macd_hist,
                        }
                
                # BEARISH MOMENTUM — SHORT signals (Futures only)
                if FUTURES_MODE and is_bearish_di:
                    bearish_conditions = [
                        current_ema_fast < current_ema_slow,           # Fast below slow
                        prev_ema_fast >= prev_ema_slow or              # Fresh crossover OR
                        current_price < recent_low * 1.005,            # Near breakdown
                        current_price < current_ema_trend,             # Below trend EMA
                        current_adx > MOM_ADX_THRESHOLD,               # Strong trend
                        current_minus_di > current_plus_di * 1.2,      # Clear bearish DI gap
                        vol_ratio > MOM_VOLUME_MULT * 0.8,             # Volume confirmation (slightly relaxed for shorts)
                        current_macd_hist < 0,                         # MACD bearish
                    ]
                    
                    bearish_score = sum(bearish_conditions) / len(bearish_conditions)
                    
                    # Only SHORT in regimes where bears have control
                    if bearish_score >= 0.70 and self.current_regime in ('TRENDING_BEAR', 'HIGH_VOLATILITY', 'RISK_OFF', 'CHOPPY'):
                        is_fresh = prev_ema_fast >= prev_ema_slow and current_ema_fast < current_ema_slow
                        strength = bearish_score * 2 + (0.5 if is_fresh else 0)
                        
                        stop_loss = current_price + (current_atr * MOM_ATR_STOP_MULT)
                        take_profit = current_price - (current_atr * MOM_ATR_TARGET_MULT)
                        
                        signal = {
                            'symbol': symbol,
                            'side': 'SELL',
                            'strength': strength,
                            'entry': current_price,
                            'stop': stop_loss,
                            'target': take_profit,
                            'adx': current_adx,
                            'ema_fast': current_ema_fast,
                            'ema_slow': current_ema_slow,
                            'vol_ratio': vol_ratio,
                            'atr': current_atr,
                            'is_fresh_crossover': is_fresh,
                            'macd_hist': current_macd_hist,
                        }
                
                if signal and signal['strength'] >= 1.3:  # Raised back — high confidence only
                    signals.append(signal)
                    logger.info(f"[MOM] Signal: {signal['side']} {symbol} | "
                              f"Strength: {signal['strength']:.2f} | "
                              f"ADX: {signal['adx']:.1f} | Vol: {signal['vol_ratio']:.1f}x")
                    
            except Exception as e:
                logger.error(f"[MOM] Error analyzing {symbol}: {e}")
        
        self._signals = signals
        return signals
    
    def execute(self):
        """Execute momentum trades."""
        if not self.active:
            return
        
        # Manage existing positions first
        self.manage_positions()
        
        if len(self.open_positions) >= MOM_MAX_POSITIONS:
            return
        
        signals = self.analyze()
        signals.sort(key=lambda s: s['strength'], reverse=True)

        # FIX-4: Global entry spacing — max 1 new MOM entry per cycle, 10min apart
        entries_this_cycle = 0

        for signal in signals:
            symbol = signal['symbol']
            
            if symbol in self.open_positions:
                continue
            
            if len(self.open_positions) >= MOM_MAX_POSITIONS:
                break

            # FIX-4: Only 1 entry per execute cycle, and min 10min since last
            if entries_this_cycle >= 1:
                break
            if time.time() - self.last_global_entry_time < self.global_entry_spacing:
                logger.debug(f"[MOM] Skipping {symbol}: global entry cooldown ({self.global_entry_spacing}s)")
                break
            
            # Block SHORT signals if Futures mode is not enabled
            if signal['side'] == 'SELL' and not FUTURES_MODE:
                continue
            
            try:
                position_size = self.risk.calculate_position_size(
                    symbol, signal['entry'], signal['stop'],
                    'MOMENTUM', self.allocation
                )
                
                if position_size <= 0:
                    logger.warning(f"[MOM] {symbol}: position_size=0 (alloc={self.allocation:.2f})")
                    continue
                
                approved, reason = self.risk.approve_trade(
                    symbol, signal['side'], position_size, signal['entry'], 'MOMENTUM'
                )
                
                if not approved:
                    logger.warning(f"[MOM] REJECTED {symbol}: {reason}")
                    continue
                
                # Execute based on side
                if signal['side'] == 'BUY':
                    usdt_amount = position_size * signal['entry']
                    if FUTURES_MODE:
                        order = self.exchange.futures_market_open(symbol, 'BUY', position_size)
                    else:
                        order = self.exchange.market_buy(symbol, usdt_amount)
                else:
                    # SELL = open SHORT via Futures
                    order = self.exchange.futures_market_open(symbol, 'SELL', position_size)
                
                if order.get('status') == 'FILLED' or order.get('orderId'):
                    self.open_positions[symbol] = {
                        'side': signal['side'],
                        'entry_price': signal['entry'],
                        'quantity': position_size,
                        'stop_loss': signal['stop'],
                        'take_profit': signal['target'],
                        'trail_stop': signal['stop'],
                        'entry_time': time.time(),
                        'signal_strength': signal['strength'],
                        'atr': signal['atr'],
                        'highest_price': signal['entry'],
                        'lowest_price': signal['entry'],  # Track for SHORT trailing
                    }
                    
                    self.risk.register_position(
                        symbol, signal['side'], position_size, signal['entry'], 'MOMENTUM',
                        signal['stop'], signal['target']
                    )
                    
                    self.cooldowns[symbol] = time.time()
                    self.last_global_entry_time = time.time()
                    entries_this_cycle += 1
                    
                    logger.info(f"[MOM] ENTERED: {signal['side']} {position_size:.6f} {symbol} @ {signal['entry']:.2f}")
                    
            except Exception as e:
                logger.error(f"[MOM] Error executing {symbol}: {e}")
    
    def manage_positions(self):
        """Manage momentum positions with trailing stops (LONG and SHORT)."""
        for symbol in list(self.open_positions.keys()):
            try:
                pos = self.open_positions[symbol]
                current_price = self.exchange.get_price(symbol)
                
                if not current_price:
                    continue
                
                self.risk.update_position_value(symbol, current_price)
                
                should_exit = False
                exit_reason = ""
                
                is_short = pos['side'] == 'SELL'
                
                if is_short:
                    # ===== SHORT POSITION MANAGEMENT =====
                    # Track lowest price (best price for shorts)
                    if current_price < pos.get('lowest_price', pos['entry_price']):
                        pos['lowest_price'] = current_price
                    
                    curr_pnl_pct = (pos['entry_price'] - current_price) / pos['entry_price'] * 100
                    max_pnl_pct = (pos['entry_price'] - pos['lowest_price']) / pos['entry_price'] * 100
                    
                    # Dynamic ATR multiplier based on profit (mirror of LONG ratchet)
                    if max_pnl_pct >= 4.0:
                        current_trail_mult = 0.4
                    elif max_pnl_pct >= 2.5:
                        current_trail_mult = 0.6
                    elif max_pnl_pct >= 1.5:
                        current_trail_mult = 0.8
                    elif max_pnl_pct >= 0.8:
                        current_trail_mult = 1.0
                    else:
                        if time.time() - pos['entry_time'] < 900:
                            current_trail_mult = MOM_TRAIL_ATR_MULT * 0.8
                        else:
                            current_trail_mult = MOM_TRAIL_ATR_MULT
                    
                    trail_distance = pos['atr'] * current_trail_mult
                    new_trail = pos['lowest_price'] + trail_distance  # Trail ABOVE for shorts
                    
                    # HARD LOCK: If max profit > 1.5%, never let it go red
                    if max_pnl_pct >= 1.5:
                        breakeven_minus = pos['entry_price'] * 0.998  # -0.2%
                        new_trail = min(new_trail, breakeven_minus)
                    
                    if new_trail < pos['trail_stop']:
                        pos['trail_stop'] = new_trail
                    
                    # Effective stop is the LOWER of initial stop and trail (for shorts, stop is above)
                    effective_stop = min(pos['stop_loss'], pos['trail_stop'])
                    
                    # Check stops (SHORT: price going UP = bad)
                    if current_price >= effective_stop:
                        should_exit = True
                        exit_reason = f"{'Trailing' if pos['trail_stop'] < pos['stop_loss'] else 'Initial'} stop ({curr_pnl_pct:+.2f}%)"
                    
                    # Check take profit (price below target)
                    elif current_price <= pos['take_profit']:
                        new_stop = current_price + (pos['atr'] * MOM_TRAIL_ATR_MULT * 0.8)
                        if new_stop < pos['trail_stop']:
                            pos['trail_stop'] = new_stop
                            pos['take_profit'] = current_price - (pos['atr'] * MOM_ATR_TARGET_MULT * 0.5)
                            logger.info(f"[MOM] {symbol} SHORT target extended, trail moved to {new_stop:.2f}")
                    
                    # 30-MIN DEAD ZONE ESCAPE (symmetric for shorts)
                    elif time.time() - pos['entry_time'] > 1800:
                        if -0.3 <= curr_pnl_pct <= 0.15:
                            should_exit = True
                            exit_reason = f"Stale exit (dead zone, {curr_pnl_pct:+.2f}%)"
                        elif time.time() - pos['entry_time'] > 86400:
                            should_exit = True
                            exit_reason = "24h time limit"
                    
                    if should_exit:
                        # Close SHORT = BUY to close
                        if FUTURES_MODE:
                            order = self.exchange.futures_market_close(symbol, 'BUY', pos['quantity'])
                        else:
                            order = self.exchange.market_buy(symbol, pos['quantity'] * current_price)
                        
                        if order.get('status') == 'FILLED' or order.get('orderId'):
                            self.risk.close_position(symbol, current_price, exit_reason)
                            del self.open_positions[symbol]
                            logger.info(f"[MOM] EXITED SHORT: {symbol} @ {current_price:.2f} | {exit_reason}")
                
                else:
                    # ===== LONG POSITION MANAGEMENT (existing logic) =====
                    # Update highest price
                    if current_price > pos['highest_price']:
                        pos['highest_price'] = current_price
                    
                    curr_pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
                    max_pnl_pct = (pos['highest_price'] - pos['entry_price']) / pos['entry_price'] * 100
                    
                    # Dynamic ATR multiplier based on profit
                    if max_pnl_pct >= 4.0:
                        current_trail_mult = 0.4
                    elif max_pnl_pct >= 2.5:
                        current_trail_mult = 0.6
                    elif max_pnl_pct >= 1.5:
                        current_trail_mult = 0.8
                    elif max_pnl_pct >= 0.8:
                        current_trail_mult = 1.0
                    else:
                        if time.time() - pos['entry_time'] < 900:
                            current_trail_mult = MOM_TRAIL_ATR_MULT * 0.8
                        else:
                            current_trail_mult = MOM_TRAIL_ATR_MULT
                        
                    trail_distance = pos['atr'] * current_trail_mult
                    new_trail = pos['highest_price'] - trail_distance
                    
                    if max_pnl_pct >= 1.5:
                        breakeven_plus = pos['entry_price'] * 1.002
                        new_trail = max(new_trail, breakeven_plus)
                    
                    if new_trail > pos['trail_stop']:
                        pos['trail_stop'] = new_trail
                    
                    effective_stop = max(pos['stop_loss'], pos['trail_stop'])
                    
                    if current_price <= effective_stop:
                        should_exit = True
                        exit_reason = f"{'Trailing' if pos['trail_stop'] > pos['stop_loss'] else 'Initial'} stop ({curr_pnl_pct:+.2f}%)"
                    
                    elif current_price >= pos['take_profit']:
                        new_stop = current_price - (pos['atr'] * MOM_TRAIL_ATR_MULT * 0.8)
                        if new_stop > pos['trail_stop']:
                            pos['trail_stop'] = new_stop
                            pos['take_profit'] = current_price + (pos['atr'] * MOM_ATR_TARGET_MULT * 0.5)
                            logger.info(f"[MOM] {symbol} target extended, trail moved to {new_stop:.2f}")
                    
                    elif time.time() - pos['entry_time'] > 1800:
                        if -0.3 <= curr_pnl_pct <= 0.15:
                            should_exit = True
                            exit_reason = f"Stale exit (dead zone, {curr_pnl_pct:+.2f}%)"
                        elif time.time() - pos['entry_time'] > 86400:
                            should_exit = True
                            exit_reason = "24h time limit"
                    
                    if should_exit:
                        if FUTURES_MODE:
                            order = self.exchange.futures_market_close(symbol, 'SELL', pos['quantity'])
                        else:
                            order = self.exchange.market_sell(symbol, pos['quantity'])
                        
                        if order.get('status') == 'FILLED' or order.get('orderId'):
                            self.risk.close_position(symbol, current_price, exit_reason)
                            del self.open_positions[symbol]
                            logger.info(f"[MOM] EXITED: {symbol} @ {current_price:.2f} | {exit_reason}")
                        
            except Exception as e:
                logger.error(f"[MOM] Error managing {symbol}: {e}")
    
    def _calculate_adx(self, df: pd.DataFrame, period: int = 14):
        """Calculate ADX, +DI, -DI."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Directional Movement
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        
        # Smoothed
        atr = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
        
        # DX and ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1)
        adx = dx.rolling(period).mean()
        
        return float(adx.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift(1))
        tr3 = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    
    def get_status(self) -> Dict:
        """Get strategy status."""
        base = super().get_status()
        base['open_positions'] = {}
        for sym, p in self.open_positions.items():
            pnl_pct = 0
            cp = self.exchange.get_price(sym)
            if cp:
                pnl_pct = (cp - p['entry_price']) / p['entry_price'] * 100
            base['open_positions'][sym] = {
                'entry': p['entry_price'],
                'stop': max(p['stop_loss'], p['trail_stop']),
                'target': p['take_profit'],
                'highest': p['highest_price'],
                'pnl_pct': pnl_pct,
            }
        return base
