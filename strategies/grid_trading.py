"""
HYDRA Trading System - Grid Trading Strategy
===============================================
Market-making strategy that places buy/sell orders in a grid pattern.
Profits from price oscillation within a range.

HOW IT WORKS:
1. Detect current trading range using ATR and recent highs/lows
2. Place graduated buy orders below price and sell orders above
3. When a buy fills, place a sell at next grid level (and vice versa)
4. Rebalance grid when price drifts too far from center

EDGE: Captures mean-reversion in ranging markets (crypto ranges 70%+ of time)
RISK: Trend breakouts - managed by tight stops and auto-rebalancing
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
    GRID_PAIRS, GRID_LEVELS, GRID_SPACING_PCT, GRID_ORDER_SIZE_PCT,
    GRID_REBALANCE_THRESHOLD, GRID_ALLOCATION
)


class GridTradingStrategy(BaseStrategy):
    """
    Adaptive grid trading / market making strategy.
    Places a ladder of buy and sell orders around current price.
    """
    
    def __init__(self, exchange: BinanceConnector, risk_manager: RiskManager):
        super().__init__("GRID", exchange, risk_manager, GRID_ALLOCATION)
        self.pairs = GRID_PAIRS
        self.grids: Dict[str, Dict] = {}  # symbol -> grid_state
        self.grid_orders: Dict[str, List[Dict]] = {}  # symbol -> [orders]
        self.last_rebalance: Dict[str, float] = {}
        self._rejection_cooldowns: Dict[str, float] = {}  # symbol -> last rejection log time
        self._REJECTION_COOLDOWN = 300  # Only log rejections once per 5 min per symbol
        
    def analyze(self) -> List[Dict]:
        """Analyze each pair to determine optimal grid parameters."""
        signals = []
        
        for symbol in self.pairs:
            try:
                # Get recent price data for range detection
                df = self.exchange.get_klines(symbol, '15m', limit=96)  # 24h of 15m candles
                if df.empty:
                    continue
                
                current_price = float(df['close'].iloc[-1])
                
                # Calculate volatility metrics
                atr = self._calculate_atr(df)
                volatility_pct = (atr / current_price) * 100
                
                # Detect range: use 24h high/low with ATR buffer
                range_high = float(df['high'].max())
                range_low = float(df['low'].min())
                range_pct = ((range_high - range_low) / current_price) * 100
                
                # Check if market is ranging (good for grid) vs trending (bad)
                # Use price vs 50-period SMA slope
                sma50 = df['close'].rolling(50).mean()
                if len(sma50.dropna()) > 5:
                    slope = (float(sma50.iloc[-1]) - float(sma50.iloc[-6])) / float(sma50.iloc[-6]) * 100
                else:
                    slope = 0
                
                is_ranging = abs(slope) < 0.5  # Less than 0.5% SMA slope = ranging
                
                # Adaptive grid spacing based on volatility
                adaptive_spacing = max(GRID_SPACING_PCT, volatility_pct * 0.3)
                
                signal = {
                    'symbol': symbol,
                    'price': current_price,
                    'atr': atr,
                    'volatility_pct': volatility_pct,
                    'range_high': range_high,
                    'range_low': range_low,
                    'range_pct': range_pct,
                    'is_ranging': is_ranging,
                    'sma_slope': slope,
                    'grid_spacing': adaptive_spacing,
                    'strength': 1.0 if is_ranging else 0.5,
                }
                signals.append(signal)
                
            except Exception as e:
                logger.error(f"[GRID] Error analyzing {symbol}: {e}")
        
        self._signals = signals
        return signals
    
    def execute(self):
        """Set up and manage grids for each pair."""
        if not self.active:
            return
        
        signals = self.analyze()
        
        for signal in signals:
            symbol = signal['symbol']
            
            try:
                # Skip if market is strongly trending
                if not signal['is_ranging'] and abs(signal['sma_slope']) > 1.0:
                    if symbol in self.grids:
                        logger.info(f"[GRID] {symbol} trending (slope: {signal['sma_slope']:.2f}%), pausing grid")
                        self._remove_grid(symbol)
                    continue
                
                # Check if grid needs rebalancing
                if symbol in self.grids:
                    grid = self.grids[symbol]
                    center_drift = abs(signal['price'] - grid['center_price']) / grid['center_price'] * 100
                    
                    if center_drift > GRID_REBALANCE_THRESHOLD:
                        logger.info(f"[GRID] {symbol} rebalancing (drift: {center_drift:.2f}%)")
                        self._remove_grid(symbol)
                
                # Set up new grid if needed
                if symbol not in self.grids:
                    self._setup_grid(symbol, signal)
                
                # Check for filled orders and place counter-orders
                self._manage_grid_fills(symbol)
                
            except Exception as e:
                logger.error(f"[GRID] Error executing {symbol}: {e}")
    
    def manage_positions(self):
        """Check grid positions and manage P&L."""
        for symbol in list(self.grids.keys()):
            try:
                current_price = self.exchange.get_price(symbol)
                if not current_price:
                    continue
                
                grid = self.grids[symbol]
                
                # Emergency exit: price moved too far from grid range
                if current_price > grid['upper_bound'] * 1.02 or current_price < grid['lower_bound'] * 0.98:
                    logger.warning(f"[GRID] {symbol} broke out of range, removing grid")
                    self._remove_grid(symbol)
                    
            except Exception as e:
                logger.error(f"[GRID] Error managing {symbol}: {e}")
    
    def _setup_grid(self, symbol: str, signal: Dict):
        """Set up a new grid of orders around current price."""
        price = signal['price']
        spacing = signal['grid_spacing'] / 100  # Convert to decimal
        
        # Calculate grid boundaries
        half_range = GRID_LEVELS * spacing
        upper_bound = price * (1 + half_range)
        lower_bound = price * (1 - half_range)
        
        # Calculate position size per grid level
        equity = self.risk.current_equity
        grid_budget = equity * self.allocation
        size_per_level = (grid_budget * GRID_ORDER_SIZE_PCT / 100)
        
        # Check with risk manager
        approved, reason = self.risk.approve_trade(
            symbol, 'BUY', size_per_level / price, price, 'GRID'
        )
        if not approved:
            now = time.time()
            last_log = self._rejection_cooldowns.get(symbol, 0)
            if now - last_log > self._REJECTION_COOLDOWN:
                logger.warning(f"[GRID] Setup rejected for {symbol}: {reason} (suppressing for 5min)")
                self._rejection_cooldowns[symbol] = now
            return
        
        # Place buy orders below current price
        buy_orders = []
        for i in range(1, GRID_LEVELS + 1):
            buy_price = price * (1 - i * spacing)
            buy_price = self.exchange.round_price(symbol, buy_price)
            quantity = size_per_level / buy_price
            quantity = self.exchange.round_quantity(symbol, quantity)
            
            if quantity > 0:
                order = self.exchange.limit_buy(symbol, quantity, buy_price)
                if order.get('status') != 'REJECTED' and 'error' not in order:
                    buy_orders.append({
                        'orderId': order.get('orderId'),
                        'side': 'BUY',
                        'price': buy_price,
                        'quantity': quantity,
                        'level': i,
                    })
        
        # Place sell orders above current price
        sell_orders = []
        for i in range(1, GRID_LEVELS + 1):
            sell_price = price * (1 + i * spacing)
            sell_price = self.exchange.round_price(symbol, sell_price)
            quantity = size_per_level / sell_price
            quantity = self.exchange.round_quantity(symbol, quantity)
            
            # For sells, we need the base asset - in paper mode we might not have it
            # Grid strategy: buy low, sell high - sells are placed as take profits
            # We only place sells when we have inventory from filled buys
        
        # Store grid state
        self.grids[symbol] = {
            'center_price': price,
            'upper_bound': upper_bound,
            'lower_bound': lower_bound,
            'spacing': spacing,
            'size_per_level': size_per_level,
            'buy_orders': buy_orders,
            'sell_orders': sell_orders,
            'fills': [],
            'total_profit': 0,
            'setup_time': time.time(),
        }
        
        logger.info(f"[GRID] {symbol} grid setup | Center: {price:.2f} | "
                    f"Range: [{lower_bound:.2f}, {upper_bound:.2f}] | "
                    f"Levels: {GRID_LEVELS} | Spacing: {spacing*100:.2f}%")
    
    def _manage_grid_fills(self, symbol: str):
        """Check for filled grid orders and place counter-orders."""
        if symbol not in self.grids:
            return
        
        grid = self.grids[symbol]
        
        # In paper trading, check if any limit orders filled
        self.exchange.check_paper_orders()
        
        # Check open orders
        open_orders = self.exchange.get_open_orders(symbol)
        open_ids = {o.get('orderId') for o in open_orders}
        
        # Find filled buy orders
        for order in grid['buy_orders'][:]:
            if order['orderId'] not in open_ids:
                # Buy order filled - place sell at next level up
                sell_price = order['price'] * (1 + grid['spacing'])
                sell_price = self.exchange.round_price(symbol, sell_price)
                
                sell_order = self.exchange.limit_sell(
                    symbol, order['quantity'], sell_price
                )
                
                if sell_order.get('status') != 'REJECTED':
                    grid['sell_orders'].append({
                        'orderId': sell_order.get('orderId'),
                        'side': 'SELL',
                        'price': sell_price,
                        'quantity': order['quantity'],
                        'level': order['level'],
                        'buy_price': order['price'],
                    })
                    grid['fills'].append({
                        'side': 'BUY',
                        'price': order['price'],
                        'quantity': order['quantity'],
                        'time': time.time(),
                    })
                
                grid['buy_orders'].remove(order)
        
        # Find filled sell orders (profit taken!)
        for order in grid['sell_orders'][:]:
            if order.get('orderId') and order['orderId'] not in open_ids:
                # Sell filled - record profit
                if 'buy_price' in order:
                    profit = (order['price'] - order['buy_price']) * order['quantity']
                    grid['total_profit'] += profit
                    
                    self.risk.close_position(
                        f"{symbol}_grid_{order.get('level', 0)}", 
                        order['price'],
                        reason="Grid level hit"
                    )
                    
                    logger.info(f"[GRID] {symbol} profit: ${profit:.4f} | Total: ${grid['total_profit']:.4f}")
                
                # Re-place buy order at lower level
                buy_price = order['price'] * (1 - grid['spacing'])
                buy_price = self.exchange.round_price(symbol, buy_price)
                
                new_buy = self.exchange.limit_buy(
                    symbol, order['quantity'], buy_price
                )
                
                if new_buy.get('status') != 'REJECTED':
                    grid['buy_orders'].append({
                        'orderId': new_buy.get('orderId'),
                        'side': 'BUY',
                        'price': buy_price,
                        'quantity': order['quantity'],
                        'level': order.get('level', 0),
                    })
                
                grid['sell_orders'].remove(order)
    
    def _remove_grid(self, symbol: str):
        """Remove all grid orders for a symbol."""
        if symbol in self.grids:
            # Cancel all open orders
            self.exchange.cancel_all_orders(symbol)
            
            grid = self.grids[symbol]
            logger.info(f"[GRID] {symbol} grid removed | Total profit: ${grid['total_profit']:.4f}")
            del self.grids[symbol]
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        
        return float(atr)
    
    def get_status(self) -> Dict:
        """Get grid strategy status."""
        base = super().get_status()
        base['grids'] = {}
        for symbol, grid in self.grids.items():
            base['grids'][symbol] = {
                'center': grid['center_price'],
                'range': f"[{grid['lower_bound']:.2f}, {grid['upper_bound']:.2f}]",
                'active_buys': len(grid['buy_orders']),
                'active_sells': len(grid['sell_orders']),
                'profit': grid['total_profit'],
            }
        return base
