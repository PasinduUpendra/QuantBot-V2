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
    MR_LOOKBACK_CANDLES, MR_MIN_STOP_DISTANCE_PCT,
    FUTURES_MODE
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
        self.cooldown_period = 300  # Phase 1: 5 min cooldown (was 10min) — MR undertrades at 68.2% WR
        self.choppy_cooldown_period = 1800  # FIX-10: 30 min cooldown in CHOPPY (was 10min — 248 signals in one day)
    
    def analyze(self) -> List[Dict]:
        """Analyze pairs for mean reversion opportunities."""
        signals = []
        
        for symbol in self.pairs:
            try:
                df = self.exchange.get_klines(symbol, MR_TIMEFRAME, limit=MR_LOOKBACK_CANDLES)
                if df.empty or len(df) < MR_BB_PERIOD + 5:
                    continue
                
                # Skip if in cooldown — FIX-10: longer cooldown in CHOPPY
                effective_cooldown = self.choppy_cooldown_period if self.current_regime == 'CHOPPY' else self.cooldown_period
                if symbol in self.cooldowns:
                    if time.time() - self.cooldowns[symbol] < effective_cooldown:
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
                
                # FIX-10: Adaptive entry thresholds per regime
                choppy_mode = self.current_regime == 'CHOPPY'
                if choppy_mode:
                    # CHOPPY reality: everything mid-range, low volume
                    # Volume filter removed — low vol IS the definition of CHOPPY
                    # Position size already reduced by adaptive risk (0.7x regime mult)
                    entry_pct_b = 0.25    # Bottom quarter of bands
                    entry_rsi = 42        # Mildly oversold (CHOPPY RSI range: 42-58)
                    entry_vol = 0         # No volume gate — chop = low vol by definition
                    short_pct_b = 0.75    # Top quarter for SHORT
                    short_rsi = 58        # Mildly overbought
                    short_vol = 0         # No volume gate
                else:
                    entry_pct_b = 0.10
                    entry_rsi = MR_RSI_OVERSOLD
                    entry_vol = 1.0
                    short_pct_b = 0.85
                    short_rsi = MR_RSI_OVERBOUGHT
                    short_vol = 1.0
                
                # LONG SIGNAL: Price near/below lower band + RSI oversold + volume spike
                sma_slope_limit = -0.15 if choppy_mode else -0.1
                if (current_pct_b < entry_pct_b and 
                    current_rsi < entry_rsi and
                    vol_ratio > entry_vol and
                    sma_slope > sma_slope_limit):
                    
                    # Confluence score (use entry thresholds as reference for strength calc)
                    strength = 0
                    strength += min(1.0, max(0, (entry_rsi - current_rsi) / 20))   # RSI extremity
                    strength += min(1.0, max(0, (entry_pct_b - current_pct_b) / entry_pct_b))  # BB extremity
                    strength += min(0.5, max(0, (vol_ratio - 1) * 0.5))            # Volume (clamped >=0)
                    
                    # Check for bullish candle pattern (hammer, engulfing)
                    if self._is_bullish_reversal(df):
                        strength += 0.5
                    
                    # FIX-12: Wider stops in CHOPPY (more room for noise)
                    atr_stop_mult = MR_ATR_STOP_MULT * 1.25 if choppy_mode else MR_ATR_STOP_MULT
                    stop_loss = current_price - (current_atr * atr_stop_mult)
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
                elif (current_pct_b > short_pct_b and 
                      current_rsi > short_rsi and
                      vol_ratio > short_vol):
                    
                    strength = 0
                    strength += min(1.0, max(0, (current_rsi - short_rsi) / 20))   # RSI extremity
                    strength += min(1.0, max(0, (current_pct_b - 0.9) / 0.1))      # BB extremity
                    strength += min(0.5, max(0, (vol_ratio - 1) * 0.5))             # Volume
                    
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
                
                # FIX-10: Minimum strength gate — entry conditions ARE the filter in CHOPPY
                # In CHOPPY, signals inherently weak — rely on R:R for quality
                min_strength = 0.0 if choppy_mode else 1.5
                
                # FIX-11: Minimum R:R check — lower bar in CHOPPY (smaller moves)
                min_rr = 1.0 if choppy_mode else 1.5
                if signal:
                    if signal['side'] == 'BUY':
                        reward = signal['target'] - signal['entry']
                        risk = signal['entry'] - signal['stop']
                    else:
                        reward = signal['entry'] - signal['target']
                        risk = signal['stop'] - signal['entry']
                    
                    rr = reward / risk if risk > 0 else 0
                    if rr < min_rr:
                        logger.debug(f"[MR] {symbol}: R:R {rr:.2f} < {min_rr} — skipped")
                        signal = None
                
                if signal and signal['strength'] >= min_strength:
                    signals.append(signal)
                    logger.info(f"[MR] Signal: {signal['side']} {symbol} | "
                              f"Strength: {signal['strength']:.2f} | "
                              f"RSI: {signal['rsi']:.1f} | %B: {signal['pct_b']:.2f}")
                else:
                    # Near-miss diagnostic: log closest symbol each cycle
                    if not signal:
                        logger.debug(f"[MR] {symbol}: no signal (RSI={current_rsi:.0f} %B={current_pct_b:.2f} vol={vol_ratio:.1f})")
                    elif signal['strength'] < min_strength:
                        logger.debug(f"[MR] {symbol}: weak signal str={signal['strength']:.2f}<{min_strength}")
                    
            except Exception as e:
                logger.error(f"[MR] Error analyzing {symbol}: {e}")
        
        if not signals and self.allocation > 0:
            logger.info(f"[MR] Scan complete: 0 signals from {len(self.pairs)} pairs (regime={self.current_regime})")
        
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
            
            # Block SHORT signals if Futures mode is not enabled
            if signal['side'] == 'SELL' and not FUTURES_MODE:
                continue
            
            try:
                # Calculate position size
                position_size = self.risk.calculate_position_size(
                    symbol, signal['entry'], signal['stop'],
                    'MEAN_REV', self.allocation
                )
                
                if position_size <= 0:
                    logger.warning(f"[MR] {symbol}: position_size=0 (alloc={self.allocation:.2f})")
                    continue
                
                # Check with risk manager
                approved, reason = self.risk.approve_trade(
                    symbol, signal['side'], position_size, signal['entry'], 'MEAN_REV'
                )
                
                if not approved:
                    logger.warning(f"[MR] REJECTED {symbol}: {reason}")
                    continue
                
                # Execute trade
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
                    entry_price = signal['entry']
                    
                    self.open_positions[symbol] = {
                        'side': signal['side'],
                        'entry_price': entry_price,
                        'quantity': position_size,
                        'stop_loss': signal['stop'],
                        'take_profit': signal['target'],
                        'entry_time': time.time(),
                        'signal_strength': signal['strength'],
                        'atr': signal['atr'],
                    }
                    
                    self.risk.register_position(
                        symbol, signal['side'], position_size, entry_price, 'MEAN_REV',
                        signal['stop'], signal['target']
                    )
                    
                    self.cooldowns[symbol] = time.time()
                    
                    logger.info(f"[MR] ENTERED: {signal['side']} {position_size:.6f} {symbol} @ {entry_price:.2f} | "
                              f"Stop: {signal['stop']:.2f} | Target: {signal['target']:.2f}")
                    
            except Exception as e:
                logger.error(f"[MR] Error executing trade for {symbol}: {e}")
    
    def manage_positions(self):
        """Monitor and manage open mean reversion positions (LONG and SHORT)."""
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
                is_short = pos['side'] == 'SELL'
                
                if is_short:
                    # ===== SHORT POSITION MANAGEMENT =====
                    # Check stop loss (price rising = bad for short)
                    if current_price >= pos['stop_loss']:
                        should_exit = True
                        exit_reason = "Stop loss hit"
                    
                    # Check take profit (price below target = good for short)
                    elif current_price <= pos['take_profit']:
                        should_exit = True
                        exit_reason = "Take profit hit"
                    
                    # Time exit at 45min
                    elif time.time() - pos['entry_time'] > 2700:
                        pnl_pct = (pos['entry_price'] - current_price) / pos['entry_price'] * 100
                        if abs(pnl_pct) < 0.05:
                            should_exit = True
                            exit_reason = "Time exit (stale)"
                    
                    # Profit ratchet for SHORT positions
                    target_distance = pos['entry_price'] - pos['take_profit']  # Positive for shorts
                    current_distance = pos['entry_price'] - current_price       # Positive when in profit
                    
                    if target_distance > 0 and current_distance > 0:
                        new_stop = None
                        if current_distance >= target_distance * 0.8:
                            new_stop = pos['entry_price'] - (target_distance * 0.6)
                        elif current_distance >= target_distance * 0.6:
                            new_stop = pos['entry_price'] - (target_distance * 0.4)
                        elif current_distance >= target_distance * 0.4:
                            new_stop = pos['entry_price'] - (target_distance * 0.1)
                        
                        # For shorts, ratchet moves stop DOWN (closer to current price)
                        if new_stop and new_stop < pos['stop_loss']:
                            pos['stop_loss'] = new_stop
                            if symbol in self.risk.positions:
                                self.risk.positions[symbol]['stop_loss'] = new_stop
                            logger.info(f"[MR] Stop ratcheted to {new_stop:.2f} for SHORT {symbol}")
                    
                    if should_exit:
                        # Close SHORT = BUY to close
                        if FUTURES_MODE:
                            order = self.exchange.futures_market_close(symbol, 'BUY', pos['quantity'])
                        else:
                            order = self.exchange.market_buy(symbol, pos['quantity'] * current_price)
                        
                        if order.get('status') == 'FILLED' or order.get('orderId'):
                            self.risk.close_position(symbol, current_price, exit_reason)
                            del self.open_positions[symbol]
                            logger.info(f"[MR] EXITED SHORT: {symbol} @ {current_price:.2f} | {exit_reason}")
                
                else:
                    # ===== LONG POSITION MANAGEMENT (existing logic) =====
                    # Check stop loss
                    if current_price <= pos['stop_loss']:
                        should_exit = True
                        exit_reason = "Stop loss hit"
                    
                    # Check take profit
                    elif current_price >= pos['take_profit']:
                        should_exit = True
                        exit_reason = "Take profit hit"
                    
                    # Time-based exit at 45min
                    elif time.time() - pos['entry_time'] > 2700:
                        pnl_pct = (current_price - pos['entry_price']) / pos['entry_price'] * 100
                        if abs(pnl_pct) < 0.05:
                            should_exit = True
                            exit_reason = "Time exit (stale)"
                    
                    # Profit ratchet for LONG
                    target_distance = pos['take_profit'] - pos['entry_price']
                    current_distance = current_price - pos['entry_price']
                    
                    if target_distance > 0:
                        new_stop = None
                        if current_distance >= target_distance * 0.8:
                            new_stop = pos['entry_price'] + (target_distance * 0.6)
                        elif current_distance >= target_distance * 0.6:
                            new_stop = pos['entry_price'] + (target_distance * 0.4)
                        elif current_distance >= target_distance * 0.4:
                            new_stop = pos['entry_price'] + (target_distance * 0.1)
                            
                        if new_stop and new_stop > pos['stop_loss']:
                            pos['stop_loss'] = new_stop
                            if symbol in self.risk.positions:
                                self.risk.positions[symbol]['stop_loss'] = new_stop
                            logger.info(f"[MR] Stop loss ratcheted to {new_stop:.2f} for {symbol} to lock profit")
                    
                    if should_exit:
                        if FUTURES_MODE:
                            order = self.exchange.futures_market_close(symbol, 'SELL', pos['quantity'])
                        else:
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
