"""
HYDRA Trading System - Mean Reversion Strategy
================================================
Captures price snaps back to mean after extreme moves.

SIGNALS:
- Bollinger Band touch/break + RSI extreme + volume confirmation
- Entry at band touch, exit at middle band or opposite extreme
- Dynamic stop using ATR

EDGE: 
- Crypto reverts to mean ~65% of time on 5m/15m timeframes
- Combining BB + RSI + Volume gives high-probability entries
- Tight risk/reward with ATR-based stops

TIMEFRAME: 5m (primary), confirmed on 15m
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
    MEAN_REVERSION_PAIRS, MEAN_REVERSION_ALLOCATION,
    MR_TIMEFRAME, MR_BB_PERIOD, MR_BB_STD, MR_RSI_PERIOD,
    MR_RSI_OVERSOLD, MR_RSI_OVERBOUGHT, MR_ATR_PERIOD,
    MR_ATR_STOP_MULT, MR_ATR_TARGET_MULT, MR_MAX_POSITIONS,
    MR_LOOKBACK_CANDLES, MR_MIN_STOP_DISTANCE_PCT
)


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion using Bollinger Bands + RSI + Volume.
    Enters on extreme readings, exits at mean.
    """
    
    def __init__(self, exchange: BinanceConnector, risk_manager: RiskManager):
        super().__init__("MEAN_REV", exchange, risk_manager, MEAN_REVERSION_ALLOCATION)
        self.pairs = MEAN_REVERSION_PAIRS
        self.open_positions: Dict[str, Dict] = {}
        self.cooldowns: Dict[str, float] = {}  # symbol -> last_trade_time
        self.cooldown_period = 600  # 10 min cooldown — stop overtrading
    
    def analyze(self) -> List[Dict]:
        """Analyze pairs for mean reversion opportunities."""
        signals = []
        
        for symbol in self.pairs:
            try:
                df = self.exchange.get_klines(symbol, MR_TIMEFRAME, limit=MR_LOOKBACK_CANDLES)
                if df.empty or len(df) < MR_BB_PERIOD + 5:
                    continue
                
                # Skip if in cooldown
                if symbol in self.cooldowns:
                    if time.time() - self.cooldowns[symbol] < self.cooldown_period:
                        continue
                
                # Calculate indicators
                close = df['close']
                volume = df['volume']
                
                # Bollinger Bands
                sma = close.rolling(MR_BB_PERIOD).mean()
                std = close.rolling(MR_BB_PERIOD).std()
                upper_band = sma + MR_BB_STD * std
                lower_band = sma - MR_BB_STD * std
                bb_width = ((upper_band - lower_band) / sma * 100)
                
                # %B (position within bands)
                pct_b = (close - lower_band) / (upper_band - lower_band)
                
                # RSI
                rsi = self._calculate_rsi(close, MR_RSI_PERIOD)
                
                # ATR for stop/target
                atr = self._calculate_atr(df, MR_ATR_PERIOD)
                
                # Volume analysis
                vol_sma = volume.rolling(20).mean()
                vol_ratio = float(volume.iloc[-1] / vol_sma.iloc[-1]) if vol_sma.iloc[-1] > 0 else 1.0
                
                # Current values
                current_price = float(close.iloc[-1])
                current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50
                current_pct_b = float(pct_b.iloc[-1]) if not pd.isna(pct_b.iloc[-1]) else 0.5
                current_bb_width = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 0
                current_sma = float(sma.iloc[-1])
                current_upper = float(upper_band.iloc[-1])
                current_lower = float(lower_band.iloc[-1])
                current_atr = float(atr) if not pd.isna(atr) else current_price * 0.01
                
                # =====================================================
                # SIGNAL GENERATION
                # =====================================================
                
                signal = None
                
                # TREND FILTER: Check SMA slope — don't buy dips in downtrends!
                sma50 = close.rolling(50).mean()
                if len(sma50.dropna()) >= 5:
                    sma_slope = (float(sma50.iloc[-1]) - float(sma50.iloc[-5])) / float(sma50.iloc[-5]) * 100
                else:
                    sma_slope = 0
                
                # REGIME GATE: Skip LONG entries in strong downtrends
                if self.current_regime in ('TRENDING_BEAR', 'RISK_OFF'):
                    # In bear markets, only allow MR if the pair itself is ranging
                    if sma_slope < -0.1:  # FIX-5: Tightened from -0.3 — any SMA decline = no entry
                        continue
                
                # LONG SIGNAL: Price near/below lower band + RSI oversold + volume spike
                if (current_pct_b < 0.10 and 
                    current_rsi < MR_RSI_OVERSOLD and
                    vol_ratio > 1.0 and
                    sma_slope > -0.1):  # FIX-5: Tightened from -0.5 — SMA must be flat or rising
                    
                    # Confluence score
                    strength = 0
                    strength += min(1.0, (MR_RSI_OVERSOLD - current_rsi) / 20)  # RSI extremity
                    strength += min(1.0, max(0, (0.1 - current_pct_b) / 0.1))   # BB extremity
                    strength += min(0.5, (vol_ratio - 1) * 0.5)                   # Volume
                    
                    # Check for bullish candle pattern (hammer, engulfing)
                    if self._is_bullish_reversal(df):
                        strength += 0.5
                    
                    stop_loss = current_price - (current_atr * MR_ATR_STOP_MULT)
                    # Enforce minimum stop distance to avoid precision bugs
                    min_stop_dist = current_price * MR_MIN_STOP_DISTANCE_PCT / 100
                    if abs(current_price - stop_loss) < min_stop_dist:
                        stop_loss = current_price - min_stop_dist
                    take_profit = current_sma  # Target middle band
                    # Ensure target is at least min_stop_dist away
                    if abs(take_profit - current_price) < min_stop_dist:
                        take_profit = current_price + (min_stop_dist * 2)
                    
                    signal = {
                        'symbol': symbol,
                        'side': 'BUY',
                        'strength': min(3.0, strength),
                        'entry': current_price,
                        'stop': stop_loss,
                        'target': take_profit,
                        'rsi': current_rsi,
                        'pct_b': current_pct_b,
                        'vol_ratio': vol_ratio,
                        'atr': current_atr,
                        'bb_width': current_bb_width,
                    }
                
                # SHORT SIGNAL: Price near/above upper band + RSI overbought + volume spike
                elif (current_pct_b > 0.85 and 
                      current_rsi > MR_RSI_OVERBOUGHT and
                      vol_ratio > 1.0):
                    
                    strength = 0
                    strength += min(1.0, (current_rsi - MR_RSI_OVERBOUGHT) / 20)
                    strength += min(1.0, max(0, (current_pct_b - 0.9) / 0.1))
                    strength += min(0.5, (vol_ratio - 1) * 0.5)
                    
                    if self._is_bearish_reversal(df):
                        strength += 0.5
                    
                    stop_loss = current_price + (current_atr * MR_ATR_STOP_MULT)
                    # Enforce minimum stop distance
                    min_stop_dist = current_price * MR_MIN_STOP_DISTANCE_PCT / 100
                    if abs(stop_loss - current_price) < min_stop_dist:
                        stop_loss = current_price + min_stop_dist
                    take_profit = current_sma
                    if abs(current_price - take_profit) < min_stop_dist:
                        take_profit = current_price - (min_stop_dist * 2)
                    
                    signal = {
                        'symbol': symbol,
                        'side': 'SELL',
                        'strength': min(3.0, strength),
                        'entry': current_price,
                        'stop': stop_loss,
                        'target': take_profit,
                        'rsi': current_rsi,
                        'pct_b': current_pct_b,
                        'vol_ratio': vol_ratio,
                        'atr': current_atr,
                        'bb_width': current_bb_width,
                    }
                
                if signal and signal['strength'] >= 1.5:  # Raised back — only high-confidence signals
                    signals.append(signal)
                    logger.info(f"[MR] Signal: {signal['side']} {symbol} | "
                              f"Strength: {signal['strength']:.2f} | "
                              f"RSI: {signal['rsi']:.1f} | %B: {signal['pct_b']:.2f}")
                    
            except Exception as e:
                logger.error(f"[MR] Error analyzing {symbol}: {e}")
        
        self._signals = signals
        return signals
    
    def execute(self):
        """Execute mean reversion trades."""
        if not self.active:
            return
        
        # First manage existing positions
        self.manage_positions()
        
        # Check position limit
        if len(self.open_positions) >= MR_MAX_POSITIONS:
            return
        
        signals = self.analyze()
        
        # Sort by strength (best opportunities first)
        signals.sort(key=lambda s: s['strength'], reverse=True)
        
        for signal in signals:
            symbol = signal['symbol']
            
            # Skip if already in position
            if symbol in self.open_positions:
                continue
            
            # Skip if at position limit
            if len(self.open_positions) >= MR_MAX_POSITIONS:
                break
            
            # Only take BUY signals on spot (can't easily short on spot)
            if signal['side'] != 'BUY':
                continue
            
            try:
                # Calculate position size
                position_size = self.risk.calculate_position_size(
                    symbol, signal['entry'], signal['stop'],
                    'MEAN_REV', self.allocation
                )
                
                if position_size <= 0:
                    continue
                
                # Check with risk manager
                approved, reason = self.risk.approve_trade(
                    symbol, signal['side'], position_size, signal['entry'], 'MEAN_REV'
                )
                
                if not approved:
                    logger.debug(f"[MR] Trade rejected for {symbol}: {reason}")
                    continue
                
                # Execute trade
                usdt_amount = position_size * signal['entry']
                order = self.exchange.market_buy(symbol, usdt_amount)
                
                if order.get('status') == 'FILLED' or order.get('orderId'):
                    entry_price = signal['entry']
                    
                    self.open_positions[symbol] = {
                        'side': 'BUY',
                        'entry_price': entry_price,
                        'quantity': position_size,
                        'stop_loss': signal['stop'],
                        'take_profit': signal['target'],
                        'entry_time': time.time(),
                        'signal_strength': signal['strength'],
                        'atr': signal['atr'],
                    }
                    
                    self.risk.register_position(
                        symbol, 'BUY', position_size, entry_price, 'MEAN_REV',
                        signal['stop'], signal['target']
                    )
                    
                    self.cooldowns[symbol] = time.time()
                    
                    logger.info(f"[MR] ENTERED: BUY {position_size:.6f} {symbol} @ {entry_price:.2f} | "
                              f"Stop: {signal['stop']:.2f} | Target: {signal['target']:.2f}")
                    
            except Exception as e:
                logger.error(f"[MR] Error executing trade for {symbol}: {e}")
    
    def manage_positions(self):
        """Monitor and manage open mean reversion positions."""
        for symbol in list(self.open_positions.keys()):
            try:
                pos = self.open_positions[symbol]
                current_price = self.exchange.get_price(symbol)
                
                if not current_price:
                    continue
                
                # Update risk manager
                self.risk.update_position_value(symbol, current_price)
                
                should_exit = False
                exit_reason = ""
                
                # Check stop loss
                if current_price <= pos['stop_loss']:
                    should_exit = True
                    exit_reason = "Stop loss hit"
                
                # Check take profit
                elif current_price >= pos['take_profit']:
                    should_exit = True
                    exit_reason = "Take profit hit"
                
                # Time-based exit: close after 1 hour if minimal movement
                elif time.time() - pos['entry_time'] > 3600:  # 1 hour (was 2h)
                    pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
                    if abs(pnl_pct) < 0.05:
                        should_exit = True
                        exit_reason = "Time exit (stale)"
                
                # Adaptive trailing stop after 50% of target reached
                target_distance = pos['take_profit'] - pos['entry_price']
                current_distance = current_price - pos['entry_price']
                if target_distance > 0 and current_distance > target_distance * 0.5:
                    # Move stop to breakeven + small buffer
                    new_stop = pos['entry_price'] + (target_distance * 0.25)
                    if new_stop > pos['stop_loss']:
                        pos['stop_loss'] = new_stop
                        self.risk.positions.get(symbol, {})['stop_loss'] = new_stop
                
                if should_exit:
                    # Execute exit
                    order = self.exchange.market_sell(symbol, pos['quantity'])
                    
                    if order.get('status') == 'FILLED' or order.get('orderId'):
                        self.risk.close_position(symbol, current_price, exit_reason)
                        del self.open_positions[symbol]
                        logger.info(f"[MR] EXITED: {symbol} @ {current_price:.2f} | {exit_reason}")
                        
            except Exception as e:
                logger.error(f"[MR] Error managing {symbol}: {e}")
    
    # ========================================
    # TECHNICAL INDICATORS
    # ========================================
    
    def _calculate_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.inf)
        return 100 - (100 / (1 + rs))
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift(1))
        tr3 = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    
    def _is_bullish_reversal(self, df: pd.DataFrame) -> bool:
        """Detect bullish reversal candle patterns."""
        if len(df) < 3:
            return False
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        body = abs(last['close'] - last['open'])
        lower_wick = min(last['open'], last['close']) - last['low']
        upper_wick = last['high'] - max(last['open'], last['close'])
        candle_range = last['high'] - last['low']
        
        # Hammer pattern
        if candle_range > 0:
            is_hammer = (lower_wick > body * 2 and 
                        upper_wick < body * 0.5 and
                        last['close'] > last['open'])
            if is_hammer:
                return True
        
        # Bullish engulfing
        if (prev['close'] < prev['open'] and  # Previous candle is red
            last['close'] > last['open'] and    # Current is green
            last['close'] > prev['open'] and    # Current close > prev open
            last['open'] < prev['close']):      # Current open < prev close
            return True
        
        return False
    
    def _is_bearish_reversal(self, df: pd.DataFrame) -> bool:
        """Detect bearish reversal candle patterns."""
        if len(df) < 3:
            return False
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        body = abs(last['close'] - last['open'])
        lower_wick = min(last['open'], last['close']) - last['low']
        upper_wick = last['high'] - max(last['open'], last['close'])
        candle_range = last['high'] - last['low']
        
        # Shooting star
        if candle_range > 0:
            is_star = (upper_wick > body * 2 and 
                      lower_wick < body * 0.5 and
                      last['close'] < last['open'])
            if is_star:
                return True
        
        # Bearish engulfing
        if (prev['close'] > prev['open'] and
            last['close'] < last['open'] and
            last['open'] > prev['close'] and
            last['close'] < prev['open']):
            return True
        
        return False
    
    def get_status(self) -> Dict:
        """Get strategy status."""
        base = super().get_status()
        base['open_positions'] = {
            sym: {
                'entry': p['entry_price'],
                'stop': p['stop_loss'],
                'target': p['take_profit'],
                'strength': p['signal_strength'],
            } for sym, p in self.open_positions.items()
        }
        return base
