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
    MOM_TRAIL_ATR_MULT, MOM_MAX_POSITIONS, MOM_LOOKBACK_CANDLES
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
        self.cooldown_period = 1800  # 30 min cooldown
    
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
                
                # BULLISH MOMENTUM
                bullish_conditions = [
                    current_ema_fast > current_ema_slow,           # Fast above slow
                    prev_ema_fast <= prev_ema_slow or              # Fresh crossover OR
                    current_price > recent_high * 0.995,           # Near breakout
                    current_price > current_ema_trend,             # Above trend EMA
                    current_adx > MOM_ADX_THRESHOLD,               # Strong trend
                    current_plus_di > current_minus_di,            # Bullish DI
                    vol_ratio > MOM_VOLUME_MULT * 0.8,             # Volume confirmation
                    current_macd_hist > 0,                         # MACD bullish
                ]
                
                bullish_score = sum(bullish_conditions) / len(bullish_conditions)
                
                # BEARISH MOMENTUM (for exits / potential shorts)
                bearish_conditions = [
                    current_ema_fast < current_ema_slow,
                    prev_ema_fast >= prev_ema_slow or
                    current_price < recent_low * 1.005,
                    current_price < current_ema_trend,
                    current_adx > MOM_ADX_THRESHOLD,
                    current_minus_di > current_plus_di,
                    vol_ratio > MOM_VOLUME_MULT * 0.8,
                    current_macd_hist < 0,
                ]
                
                bearish_score = sum(bearish_conditions) / len(bearish_conditions)
                
                # Generate signal if strong enough
                if bullish_score >= 0.7:
                    # Fresh crossover gets bonus
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
                
                if signal and signal['strength'] >= 1.3:
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
        
        for signal in signals:
            symbol = signal['symbol']
            
            if symbol in self.open_positions:
                continue
            
            if len(self.open_positions) >= MOM_MAX_POSITIONS:
                break
            
            if signal['side'] != 'BUY':
                continue
            
            try:
                position_size = self.risk.calculate_position_size(
                    symbol, signal['entry'], signal['stop'],
                    'MOMENTUM', self.allocation
                )
                
                if position_size <= 0:
                    continue
                
                approved, reason = self.risk.approve_trade(
                    symbol, signal['side'], position_size, signal['entry'], 'MOMENTUM'
                )
                
                if not approved:
                    logger.debug(f"[MOM] Rejected {symbol}: {reason}")
                    continue
                
                usdt_amount = position_size * signal['entry']
                order = self.exchange.market_buy(symbol, usdt_amount)
                
                if order.get('status') == 'FILLED' or order.get('orderId'):
                    self.open_positions[symbol] = {
                        'side': 'BUY',
                        'entry_price': signal['entry'],
                        'quantity': position_size,
                        'stop_loss': signal['stop'],
                        'take_profit': signal['target'],
                        'trail_stop': signal['stop'],
                        'entry_time': time.time(),
                        'signal_strength': signal['strength'],
                        'atr': signal['atr'],
                        'highest_price': signal['entry'],
                    }
                    
                    self.risk.register_position(
                        symbol, 'BUY', position_size, signal['entry'], 'MOMENTUM',
                        signal['stop'], signal['target']
                    )
                    
                    self.cooldowns[symbol] = time.time()
                    
                    logger.info(f"[MOM] ENTERED: BUY {position_size:.6f} {symbol} @ {signal['entry']:.2f}")
                    
            except Exception as e:
                logger.error(f"[MOM] Error executing {symbol}: {e}")
    
    def manage_positions(self):
        """Manage momentum positions with trailing stops."""
        for symbol in list(self.open_positions.keys()):
            try:
                pos = self.open_positions[symbol]
                current_price = self.exchange.get_price(symbol)
                
                if not current_price:
                    continue
                
                self.risk.update_position_value(symbol, current_price)
                
                should_exit = False
                exit_reason = ""
                
                # Update highest price
                if current_price > pos['highest_price']:
                    pos['highest_price'] = current_price
                
                # Trailing stop logic
                trail_distance = pos['atr'] * MOM_TRAIL_ATR_MULT
                new_trail = pos['highest_price'] - trail_distance
                
                if new_trail > pos['trail_stop']:
                    pos['trail_stop'] = new_trail
                
                # Use the higher of initial stop and trailing stop
                effective_stop = max(pos['stop_loss'], pos['trail_stop'])
                
                # Check stops
                if current_price <= effective_stop:
                    should_exit = True
                    pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
                    exit_reason = f"{'Trailing' if pos['trail_stop'] > pos['stop_loss'] else 'Initial'} stop ({pnl_pct:+.2f}%)"
                
                # Check take profit (but trail beyond if trend is strong)
                elif current_price >= pos['take_profit']:
                    # Instead of taking profit immediately, move stop to lock in gains
                    new_stop = current_price - (pos['atr'] * MOM_TRAIL_ATR_MULT * 0.8)
                    if new_stop > pos['trail_stop']:
                        pos['trail_stop'] = new_stop
                        # Extend target
                        pos['take_profit'] = current_price + (pos['atr'] * MOM_ATR_TARGET_MULT * 0.5)
                        logger.info(f"[MOM] {symbol} target extended, trail moved to {new_stop:.2f}")
                
                # Time-based: force exit after 24h
                elif time.time() - pos['entry_time'] > 86400:
                    should_exit = True
                    exit_reason = "24h time limit"
                
                if should_exit:
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
