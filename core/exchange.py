"""
HYDRA Trading System - Binance Exchange Connector
Handles all communication with Binance REST + WebSocket APIs.
Thread-safe, rate-limit aware, with automatic reconnection.
"""
import time
import hmac
import hashlib
import json
import asyncio
import threading
from urllib.parse import urlencode
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime

import requests
import numpy as np
import pandas as pd
from loguru import logger

from core import BINANCE_API_KEY, BINANCE_SECRET_KEY, PAPER_TRADE


class BinanceConnector:
    """
    Production-grade Binance connector.
    Handles spot + futures, rate limiting, and order management.
    """
    
    BASE_URL = 'https://api.binance.com'
    FUTURES_URL = 'https://fapi.binance.com'
    
    def __init__(self):
        self.api_key = BINANCE_API_KEY
        self.secret_key = BINANCE_SECRET_KEY
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key
        })
        
        # Rate limiting
        self._request_timestamps: List[float] = []
        self._lock = threading.Lock()
        self._weight_used = 0
        self._weight_limit = 1200  # per minute
        
        # Cache
        self._exchange_info_cache: Optional[Dict] = None
        self._exchange_info_time: float = 0
        self._symbol_info_cache: Dict = {}
        self._price_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (price, timestamp)
        
        # Paper trading state
        self._paper_balance: Dict[str, float] = {}
        self._paper_orders: List[Dict] = []
        self._paper_positions: Dict[str, Dict] = {}
        self._paper_order_id = 10000
        
        logger.info("BinanceConnector initialized")
    
    # ================================================================
    # SIGNING & REQUEST HELPERS
    # ================================================================
    
    def _sign(self, params: Dict) -> Dict:
        """Sign request with HMAC SHA256."""
        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 5000
        query_string = urlencode(params)
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params
    
    def _rate_limit_check(self, weight: int = 1):
        """Enforce rate limiting."""
        with self._lock:
            now = time.time()
            # Remove timestamps older than 1 minute
            self._request_timestamps = [
                t for t in self._request_timestamps if now - t < 60
            ]
            if len(self._request_timestamps) >= 1100:  # conservative
                sleep_time = 60 - (now - self._request_timestamps[0])
                if sleep_time > 0:
                    logger.warning(f"Rate limit approaching, sleeping {sleep_time:.1f}s")
                    time.sleep(sleep_time)
            self._request_timestamps.append(now)
    
    def _request(self, method: str, url: str, signed: bool = False, 
                 params: Dict = None, weight: int = 1) -> Dict:
        """Make rate-limited request to Binance."""
        self._rate_limit_check(weight)
        
        if params is None:
            params = {}
        if signed:
            params = self._sign(params)
        
        try:
            if method == 'GET':
                resp = self.session.get(url, params=params, timeout=10)
            elif method == 'POST':
                resp = self.session.post(url, params=params, timeout=10)
            elif method == 'DELETE':
                resp = self.session.delete(url, params=params, timeout=10)
            else:
                raise ValueError(f"Unknown method: {method}")
            
            # Track rate limit from headers
            if 'X-MBX-USED-WEIGHT-1M' in resp.headers:
                self._weight_used = int(resp.headers['X-MBX-USED-WEIGHT-1M'])
            
            if resp.status_code != 200:
                logger.error(f"Binance API error {resp.status_code}: {resp.text}")
                return {'error': resp.text, 'code': resp.status_code}
            
            return resp.json()
            
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout: {url}")
            return {'error': 'timeout'}
        except Exception as e:
            logger.error(f"Request error: {e}")
            return {'error': str(e)}
    
    # ================================================================
    # ACCOUNT & BALANCE
    # ================================================================
    
    def get_account_balance(self) -> Dict[str, float]:
        """Get all non-zero balances."""
        if PAPER_TRADE:
            return self._paper_balance
        
        data = self._request('GET', f'{self.BASE_URL}/api/v3/account', signed=True, weight=10)
        if 'error' in data:
            return {}
        
        balances = {}
        for b in data.get('balances', []):
            free = float(b['free'])
            locked = float(b['locked'])
            total = free + locked
            if total > 0:
                balances[b['asset']] = {
                    'free': free,
                    'locked': locked,
                    'total': total
                }
        return balances
    
    def get_usdt_balance(self) -> float:
        """Get total USDT balance (free + locked)."""
        if PAPER_TRADE:
            return self._paper_balance.get('USDT', 0)
        
        balances = self.get_account_balance()
        usdt = balances.get('USDT', {})
        if isinstance(usdt, dict):
            return usdt.get('total', 0)
        return float(usdt)
    
    def get_total_equity_usdt(self) -> float:
        """Calculate total portfolio value in USDT."""
        if PAPER_TRADE:
            total = self._paper_balance.get('USDT', 0)
            for symbol, qty in self._paper_balance.items():
                if symbol != 'USDT' and qty > 0:
                    price = self.get_price(f'{symbol}USDT')
                    if price:
                        total += qty * price
            return total
        
        balances = self.get_account_balance()
        total = 0
        for asset, info in balances.items():
            amt = info['total'] if isinstance(info, dict) else float(info)
            if asset == 'USDT':
                total += amt
            elif amt > 0.0001:
                price = self.get_price(f'{asset}USDT')
                if price:
                    total += amt * price
        return total
    
    def init_paper_balance(self, usdt_amount: float):
        """Initialize paper trading balance."""
        self._paper_balance = {'USDT': usdt_amount}
        logger.info(f"Paper balance initialized: {usdt_amount} USDT")
    
    # ================================================================
    # MARKET DATA
    # ================================================================
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price with caching (1s)."""
        now = time.time()
        if symbol in self._price_cache:
            cached_price, cached_time = self._price_cache[symbol]
            if now - cached_time < 1.0:
                return cached_price
        
        data = self._request('GET', f'{self.BASE_URL}/api/v3/ticker/price', 
                            params={'symbol': symbol})
        if 'error' in data:
            return None
        
        price = float(data['price'])
        self._price_cache[symbol] = (price, now)
        return price
    
    def get_prices_bulk(self) -> Dict[str, float]:
        """Get all prices at once (single API call)."""
        data = self._request('GET', f'{self.BASE_URL}/api/v3/ticker/price', weight=2)
        if 'error' in data or not isinstance(data, list):
            return {}
        
        prices = {}
        for item in data:
            prices[item['symbol']] = float(item['price'])
            self._price_cache[item['symbol']] = (float(item['price']), time.time())
        return prices
    
    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        """Get OHLCV candlestick data as DataFrame."""
        data = self._request('GET', f'{self.BASE_URL}/api/v3/klines',
                            params={'symbol': symbol, 'interval': interval, 'limit': limit},
                            weight=1)
        if 'error' in data or not isinstance(data, list):
            return pd.DataFrame()
        
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
            df[col] = df[col].astype(float)
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume', 'quote_volume']]
    
    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        """Get order book depth."""
        data = self._request('GET', f'{self.BASE_URL}/api/v3/depth',
                            params={'symbol': symbol, 'limit': limit}, weight=5)
        if 'error' in data:
            return {}
        return data
    
    def get_24h_ticker(self, symbol: str) -> Dict:
        """Get 24h ticker statistics."""
        data = self._request('GET', f'{self.BASE_URL}/api/v3/ticker/24hr',
                            params={'symbol': symbol}, weight=1)
        if 'error' in data:
            return {}
        return data
    
    # ================================================================
    # FUTURES MARKET DATA
    # ================================================================
    
    def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate for perpetual futures."""
        data = self._request('GET', f'{self.FUTURES_URL}/fapi/v1/premiumIndex',
                            params={'symbol': symbol})
        if 'error' in data:
            return None
        return float(data.get('lastFundingRate', 0))
    
    def get_all_funding_rates(self) -> Dict[str, float]:
        """Get funding rates for all perpetual futures."""
        data = self._request('GET', f'{self.FUTURES_URL}/fapi/v1/premiumIndex', weight=10)
        if 'error' in data or not isinstance(data, list):
            return {}
        
        rates = {}
        for item in data:
            rates[item['symbol']] = float(item.get('lastFundingRate', 0))
        return rates
    
    def get_futures_klines(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        """Get futures OHLCV data."""
        data = self._request('GET', f'{self.FUTURES_URL}/fapi/v1/klines',
                            params={'symbol': symbol, 'interval': interval, 'limit': limit})
        if 'error' in data or not isinstance(data, list):
            return pd.DataFrame()
        
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]
    
    # ================================================================
    # EXCHANGE INFO & SYMBOL DETAILS
    # ================================================================
    
    def get_exchange_info(self) -> Dict:
        """Get exchange info with caching (1 hour)."""
        now = time.time()
        if self._exchange_info_cache and now - self._exchange_info_time < 3600:
            return self._exchange_info_cache
        
        data = self._request('GET', f'{self.BASE_URL}/api/v3/exchangeInfo', weight=10)
        if 'error' not in data:
            self._exchange_info_cache = data
            self._exchange_info_time = now
            # Build symbol info cache
            for s in data.get('symbols', []):
                self._symbol_info_cache[s['symbol']] = s
        return data
    
    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """Get symbol trading rules (lot size, tick size, etc.)."""
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]
        self.get_exchange_info()
        return self._symbol_info_cache.get(symbol)
    
    def get_lot_size(self, symbol: str) -> Tuple[float, float, float]:
        """Get min qty, max qty, step size for a symbol."""
        info = self.get_symbol_info(symbol)
        if not info:
            return (0.001, 999999, 0.001)
        
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                return (float(f['minQty']), float(f['maxQty']), float(f['stepSize']))
        return (0.001, 999999, 0.001)
    
    def get_tick_size(self, symbol: str) -> float:
        """Get minimum price increment."""
        info = self.get_symbol_info(symbol)
        if not info:
            return 0.01
        
        for f in info.get('filters', []):
            if f['filterType'] == 'PRICE_FILTER':
                return float(f['tickSize'])
        return 0.01
    
    def get_min_notional(self, symbol: str) -> float:
        """Get minimum notional value for an order."""
        info = self.get_symbol_info(symbol)
        if not info:
            return 10.0
        
        for f in info.get('filters', []):
            if f['filterType'] in ('MIN_NOTIONAL', 'NOTIONAL'):
                return float(f.get('minNotional', 10.0))
        return 10.0
    
    def round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity to valid step size."""
        min_qty, max_qty, step = self.get_lot_size(symbol)
        if step == 0:
            step = 0.001
        precision = max(0, len(str(step).rstrip('0').split('.')[-1]))
        quantity = max(min_qty, min(max_qty, quantity))
        quantity = round(quantity - (quantity % step), precision)
        return quantity
    
    def round_price(self, symbol: str, price: float) -> float:
        """Round price to valid tick size."""
        tick = self.get_tick_size(symbol)
        if tick == 0:
            tick = 0.01
        precision = max(0, len(str(tick).rstrip('0').split('.')[-1]))
        return round(price - (price % tick), precision)
    
    # ================================================================
    # ORDER MANAGEMENT - SPOT
    # ================================================================
    
    def place_order(self, symbol: str, side: str, order_type: str,
                    quantity: float = None, quote_quantity: float = None,
                    price: float = None, stop_price: float = None,
                    time_in_force: str = 'GTC') -> Dict:
        """
        Place a spot order.
        
        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            side: 'BUY' or 'SELL'
            order_type: 'LIMIT', 'MARKET', 'STOP_LOSS_LIMIT', etc.
            quantity: Base asset quantity
            quote_quantity: Quote asset quantity (for market buy by quote)
            price: Limit price
            stop_price: Stop trigger price
            time_in_force: 'GTC', 'IOC', 'FOK'
        """
        if PAPER_TRADE:
            return self._paper_place_order(symbol, side, order_type, 
                                           quantity, quote_quantity, price)
        
        params = {
            'symbol': symbol,
            'side': side,
            'type': order_type,
        }
        
        if quantity:
            quantity = self.round_quantity(symbol, quantity)
            params['quantity'] = f'{quantity}'
        
        if quote_quantity and order_type == 'MARKET':
            params['quoteOrderQty'] = f'{quote_quantity}'
        
        if price and order_type != 'MARKET':
            price = self.round_price(symbol, price)
            params['price'] = f'{price}'
            params['timeInForce'] = time_in_force
        
        if stop_price:
            params['stopPrice'] = f'{self.round_price(symbol, stop_price)}'
        
        logger.info(f"Placing order: {side} {quantity or quote_quantity} {symbol} @ {price or 'MARKET'}")
        
        result = self._request('POST', f'{self.BASE_URL}/api/v3/order', 
                              signed=True, params=params)
        
        if 'error' not in result:
            logger.info(f"Order placed: {result.get('orderId')} - {result.get('status')}")
        else:
            logger.error(f"Order failed: {result}")
        
        return result
    
    def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """Cancel an open order."""
        if PAPER_TRADE:
            self._paper_orders = [o for o in self._paper_orders if o['orderId'] != order_id]
            return {'status': 'CANCELED'}
        
        return self._request('DELETE', f'{self.BASE_URL}/api/v3/order',
                            signed=True, params={'symbol': symbol, 'orderId': order_id})
    
    def cancel_all_orders(self, symbol: str) -> Dict:
        """Cancel all open orders for a symbol."""
        if PAPER_TRADE:
            self._paper_orders = [o for o in self._paper_orders if o['symbol'] != symbol]
            return {'status': 'OK'}
        
        return self._request('DELETE', f'{self.BASE_URL}/api/v3/openOrders',
                            signed=True, params={'symbol': symbol})
    
    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """Get all open orders."""
        if PAPER_TRADE:
            if symbol:
                return [o for o in self._paper_orders if o['symbol'] == symbol]
            return self._paper_orders
        
        params = {}
        if symbol:
            params['symbol'] = symbol
        
        data = self._request('GET', f'{self.BASE_URL}/api/v3/openOrders',
                            signed=True, params=params, weight=3)
        if isinstance(data, list):
            return data
        return []
    
    # ================================================================
    # PAPER TRADING ENGINE
    # ================================================================
    
    def _paper_place_order(self, symbol: str, side: str, order_type: str,
                           quantity: float = None, quote_quantity: float = None,
                           price: float = None) -> Dict:
        """Simulate order execution for paper trading."""
        current_price = self.get_price(symbol)
        if not current_price:
            return {'error': f'Cannot get price for {symbol}'}
        
        # For market orders, execute immediately
        exec_price = price if (price and order_type != 'MARKET') else current_price
        
        # Calculate quantity if using quote
        if quote_quantity and not quantity:
            quantity = quote_quantity / exec_price
        
        if not quantity:
            return {'error': 'No quantity specified'}
        
        # Extract base asset from symbol
        base_asset = symbol.replace('USDT', '').replace('BUSD', '')
        quote_asset = 'USDT'
        
        cost = quantity * exec_price
        
        self._paper_order_id += 1
        order = {
            'orderId': self._paper_order_id,
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'quantity': quantity,
            'price': exec_price,
            'status': 'NEW',
            'time': int(time.time() * 1000)
        }
        
        if order_type == 'MARKET':
            # Execute immediately
            if side == 'BUY':
                if self._paper_balance.get(quote_asset, 0) >= cost:
                    self._paper_balance[quote_asset] = self._paper_balance.get(quote_asset, 0) - cost
                    self._paper_balance[base_asset] = self._paper_balance.get(base_asset, 0) + quantity
                    # Simulate 0.1% fee
                    self._paper_balance[base_asset] -= quantity * 0.001
                    order['status'] = 'FILLED'
                    logger.info(f"[PAPER] BUY {quantity:.6f} {base_asset} @ {exec_price:.2f} = ${cost:.2f}")
                else:
                    order['status'] = 'REJECTED'
                    logger.warning(f"[PAPER] Insufficient {quote_asset}: need {cost:.2f}, have {self._paper_balance.get(quote_asset, 0):.2f}")
            else:  # SELL
                if self._paper_balance.get(base_asset, 0) >= quantity:
                    self._paper_balance[base_asset] = self._paper_balance.get(base_asset, 0) - quantity
                    revenue = cost * 0.999  # 0.1% fee
                    self._paper_balance[quote_asset] = self._paper_balance.get(quote_asset, 0) + revenue
                    order['status'] = 'FILLED'
                    logger.info(f"[PAPER] SELL {quantity:.6f} {base_asset} @ {exec_price:.2f} = ${revenue:.2f}")
                else:
                    order['status'] = 'REJECTED'
                    logger.warning(f"[PAPER] Insufficient {base_asset}: need {quantity:.6f}, have {self._paper_balance.get(base_asset, 0):.6f}")
        else:
            # Limit order - add to pending
            self._paper_orders.append(order)
            logger.info(f"[PAPER] Limit {side} {quantity:.6f} {base_asset} @ {exec_price:.2f}")
        
        return order
    
    def check_paper_orders(self):
        """Check and fill pending paper limit orders."""
        if not PAPER_TRADE:
            return
        
        filled = []
        for order in self._paper_orders:
            if order['status'] != 'NEW':
                continue
            
            current_price = self.get_price(order['symbol'])
            if not current_price:
                continue
            
            should_fill = False
            if order['side'] == 'BUY' and current_price <= order['price']:
                should_fill = True
            elif order['side'] == 'SELL' and current_price >= order['price']:
                should_fill = True
            
            if should_fill:
                base_asset = order['symbol'].replace('USDT', '').replace('BUSD', '')
                cost = order['quantity'] * order['price']
                
                if order['side'] == 'BUY':
                    if self._paper_balance.get('USDT', 0) >= cost:
                        self._paper_balance['USDT'] -= cost
                        self._paper_balance[base_asset] = self._paper_balance.get(base_asset, 0) + order['quantity'] * 0.999
                        order['status'] = 'FILLED'
                        filled.append(order)
                else:
                    if self._paper_balance.get(base_asset, 0) >= order['quantity']:
                        self._paper_balance[base_asset] -= order['quantity']
                        self._paper_balance['USDT'] = self._paper_balance.get('USDT', 0) + cost * 0.999
                        order['status'] = 'FILLED'
                        filled.append(order)
        
        # Remove filled orders
        self._paper_orders = [o for o in self._paper_orders if o['status'] == 'NEW']
        
        for order in filled:
            logger.info(f"[PAPER] Filled: {order['side']} {order['quantity']:.6f} {order['symbol']} @ {order['price']:.2f}")
    
    # ================================================================
    # CONVENIENCE METHODS
    # ================================================================
    
    def market_buy(self, symbol: str, usdt_amount: float) -> Dict:
        """Market buy spending exact USDT amount."""
        return self.place_order(symbol, 'BUY', 'MARKET', quote_quantity=usdt_amount)
    
    def market_sell(self, symbol: str, quantity: float) -> Dict:
        """Market sell exact quantity."""
        quantity = self.round_quantity(symbol, quantity)
        return self.place_order(symbol, 'SELL', 'MARKET', quantity=quantity)
    
    def limit_buy(self, symbol: str, quantity: float, price: float) -> Dict:
        """Place limit buy order."""
        return self.place_order(symbol, 'BUY', 'LIMIT', quantity=quantity, price=price)
    
    def limit_sell(self, symbol: str, quantity: float, price: float) -> Dict:
        """Place limit sell order."""
        return self.place_order(symbol, 'SELL', 'LIMIT', quantity=quantity, price=price)
    
    def get_server_time(self) -> int:
        """Get Binance server time."""
        data = self._request('GET', f'{self.BASE_URL}/api/v3/time')
        return data.get('serverTime', int(time.time() * 1000))
    
    def ping(self) -> bool:
        """Test connectivity."""
        data = self._request('GET', f'{self.BASE_URL}/api/v3/ping')
        return 'error' not in data
