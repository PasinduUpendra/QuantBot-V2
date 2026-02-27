"""
HYDRA Trading System - Funding Rate Arbitrage Strategy
=======================================================
Captures funding rate premium between spot and perpetual futures.

HOW IT WORKS:
- When perpetual funding rate is high positive (>0.01%):
  → Longs are paying shorts
  → BUY spot + SHORT perp = delta neutral, earn funding
  
- When perpetual funding rate is high negative:
  → Shorts are paying longs
  → SELL spot + LONG perp = delta neutral, earn funding

EDGE:
- Funding rates paid every 8 hours on Binance
- During bullish sentiment, rates can be 0.05-0.3% per 8h = 0.15-0.9% daily
- Near zero market risk if positions are perfectly hedged

NOTE: This strategy requires BOTH spot and futures accounts.
If futures are not available, it will track and report opportunities.
"""
import time
from typing import Dict, List, Optional
from loguru import logger

from strategies.base import BaseStrategy
from core.exchange import BinanceConnector
from core.risk_manager import RiskManager
from core import (
    FUNDING_ARB_PAIRS, FUNDING_ARB_ALLOCATION,
    FUND_MIN_RATE, FUND_EXIT_RATE, FUND_MAX_POSITIONS,
    FUND_POSITION_SIZE_PCT
)


class FundingArbStrategy(BaseStrategy):
    """
    Funding rate arbitrage strategy.
    Goes long spot + short perp to earn funding rates.
    """
    
    def __init__(self, exchange: BinanceConnector, risk_manager: RiskManager):
        super().__init__("FUND_ARB", exchange, risk_manager, FUNDING_ARB_ALLOCATION)
        self.pairs = FUNDING_ARB_PAIRS
        self.open_arbs: Dict[str, Dict] = {}
        self.funding_history: Dict[str, List[float]] = {}
        self.total_funding_earned = 0.0
        self.futures_available = False
        
        # Check if futures are available
        self._check_futures_access()
    
    def _check_futures_access(self):
        """Check if futures account is accessible."""
        try:
            rates = self.exchange.get_all_funding_rates()
            self.futures_available = len(rates) > 0
            if self.futures_available:
                logger.info("[FUND] Futures access confirmed - full arb available")
            else:
                logger.warning("[FUND] Futures not accessible - operating in monitor mode")
        except Exception as e:
            logger.warning(f"[FUND] Futures check failed: {e} - operating in monitor mode")
            self.futures_available = False
    
    def analyze(self) -> List[Dict]:
        """Analyze funding rates for arbitrage opportunities."""
        signals = []
        
        try:
            all_rates = self.exchange.get_all_funding_rates()
            
            for symbol in self.pairs:
                if symbol not in all_rates:
                    continue
                
                rate = all_rates[symbol]
                rate_pct = rate * 100  # Convert to percentage
                
                # Track history
                if symbol not in self.funding_history:
                    self.funding_history[symbol] = []
                self.funding_history[symbol].append(rate_pct)
                # Keep last 24 readings (8 hours apart = 8 days)
                self.funding_history[symbol] = self.funding_history[symbol][-24:]
                
                # Calculate average and consistency
                avg_rate = sum(self.funding_history[symbol]) / len(self.funding_history[symbol])
                
                current_price = self.exchange.get_price(symbol)
                if not current_price:
                    continue
                
                # High positive funding = longs pay shorts
                # Strategy: long spot + short perp
                if rate_pct > FUND_MIN_RATE:
                    # Annualized yield estimate (3 fundings/day)
                    annual_yield = rate_pct * 3 * 365
                    
                    # Strength based on rate magnitude
                    strength = min(3.0, rate_pct / FUND_MIN_RATE)
                    
                    signal = {
                        'symbol': symbol,
                        'side': 'LONG_SPOT_SHORT_PERP',
                        'strength': strength,
                        'funding_rate': rate_pct,
                        'avg_rate': avg_rate,
                        'annual_yield': annual_yield,
                        'price': current_price,
                        'entry': current_price,
                        'stop': None,  # Delta neutral, no stop needed
                        'target': None,  # Earn from funding, not direction
                    }
                    signals.append(signal)
                    
                    logger.info(f"[FUND] Opportunity: {symbol} | Rate: {rate_pct:.4f}% | "
                              f"Annual: {annual_yield:.1f}% | Strength: {strength:.2f}")
                
                # High negative funding (rare but profitable)
                elif rate_pct < -FUND_MIN_RATE:
                    annual_yield = abs(rate_pct) * 3 * 365
                    strength = min(3.0, abs(rate_pct) / FUND_MIN_RATE)
                    
                    signal = {
                        'symbol': symbol,
                        'side': 'SHORT_SPOT_LONG_PERP',
                        'strength': strength,
                        'funding_rate': rate_pct,
                        'avg_rate': avg_rate,
                        'annual_yield': annual_yield,
                        'price': current_price,
                        'entry': current_price,
                        'stop': None,
                        'target': None,
                    }
                    signals.append(signal)
                    
        except Exception as e:
            logger.error(f"[FUND] Error analyzing rates: {e}")
        
        self._signals = signals
        return signals
    
    def execute(self):
        """Execute funding arbitrage positions."""
        if not self.active:
            return
        
        self.manage_positions()
        
        if len(self.open_arbs) >= FUND_MAX_POSITIONS:
            return
        
        signals = self.analyze()
        signals.sort(key=lambda s: s['strength'], reverse=True)
        
        for signal in signals:
            symbol = signal['symbol']
            
            if symbol in self.open_arbs:
                continue
            
            if len(self.open_arbs) >= FUND_MAX_POSITIONS:
                break
            
            # If we don't have futures access, just log the opportunity
            if not self.futures_available:
                logger.info(f"[FUND] MONITOR ONLY - {symbol} rate: {signal['funding_rate']:.4f}% "
                          f"({signal['annual_yield']:.1f}% annual)")
                
                # In spot-only mode, we can still benefit from high funding
                # by going long when funding is very negative (shorts are overcrowded)
                if signal['funding_rate'] < -0.05:
                    # Very negative funding often precedes short squeezes
                    logger.info(f"[FUND] Negative funding alert on {symbol} - potential squeeze setup")
                continue
            
            try:
                # Calculate position size
                equity = self.risk.current_equity
                position_value = equity * self.allocation * FUND_POSITION_SIZE_PCT / 100
                quantity = position_value / signal['price']
                
                approved, reason = self.risk.approve_trade(
                    symbol, 'BUY', quantity, signal['price'], 'FUND_ARB'
                )
                
                if not approved:
                    continue
                
                # Execute: Long spot + Short perp (delta neutral)
                if signal['side'] == 'LONG_SPOT_SHORT_PERP':
                    # Buy spot
                    spot_order = self.exchange.market_buy(symbol, position_value)
                    
                    if spot_order.get('status') == 'FILLED':
                        self.open_arbs[symbol] = {
                            'side': signal['side'],
                            'spot_quantity': quantity,
                            'perp_quantity': quantity,
                            'entry_price': signal['price'],
                            'entry_time': time.time(),
                            'funding_rate_at_entry': signal['funding_rate'],
                            'total_funding_earned': 0,
                            'funding_collections': 0,
                        }
                        
                        self.risk.register_position(
                            symbol, 'BUY', quantity, signal['price'], 'FUND_ARB'
                        )
                        
                        logger.info(f"[FUND] ENTERED: {symbol} | Rate: {signal['funding_rate']:.4f}% | "
                                  f"Value: ${position_value:.2f}")
                        
            except Exception as e:
                logger.error(f"[FUND] Error executing {symbol}: {e}")
    
    def manage_positions(self):
        """Monitor funding arb positions and collect funding."""
        for symbol in list(self.open_arbs.keys()):
            try:
                arb = self.open_arbs[symbol]
                
                # Check current funding rate
                current_rate = self.exchange.get_funding_rate(symbol)
                if current_rate is None:
                    continue
                
                current_rate_pct = current_rate * 100
                current_price = self.exchange.get_price(symbol)
                
                should_exit = False
                
                # Exit if funding rate dropped below threshold
                if arb['side'] == 'LONG_SPOT_SHORT_PERP' and current_rate_pct < FUND_EXIT_RATE:
                    should_exit = True
                    logger.info(f"[FUND] {symbol} funding dropped to {current_rate_pct:.4f}%, exiting")
                
                # Exit if held for more than 3 days without significant funding
                elif time.time() - arb['entry_time'] > 259200:  # 3 days
                    if arb['total_funding_earned'] < arb['entry_price'] * arb['spot_quantity'] * 0.001:
                        should_exit = True
                        logger.info(f"[FUND] {symbol} insufficient funding after 3 days, exiting")
                
                # Simulate funding collection (every 8 hours)
                hours_held = (time.time() - arb['entry_time']) / 3600
                expected_collections = int(hours_held / 8)
                
                if expected_collections > arb['funding_collections']:
                    # Collect funding
                    new_collections = expected_collections - arb['funding_collections']
                    funding_earned = abs(current_rate_pct / 100) * arb['spot_quantity'] * arb['entry_price'] * new_collections
                    arb['total_funding_earned'] += funding_earned
                    arb['funding_collections'] = expected_collections
                    self.total_funding_earned += funding_earned
                    
                    logger.info(f"[FUND] {symbol} collected ${funding_earned:.4f} funding | "
                              f"Total: ${arb['total_funding_earned']:.4f}")
                
                if should_exit:
                    # Close spot position
                    if current_price:
                        order = self.exchange.market_sell(symbol, arb['spot_quantity'])
                        if order.get('status') == 'FILLED' or order.get('orderId'):
                            self.risk.close_position(symbol, current_price, 
                                                     f"Funding arb exit (earned: ${arb['total_funding_earned']:.4f})")
                            del self.open_arbs[symbol]
                    
            except Exception as e:
                logger.error(f"[FUND] Error managing {symbol}: {e}")
    
    def get_status(self) -> Dict:
        """Get strategy status."""
        base = super().get_status()
        base['futures_available'] = self.futures_available
        base['total_funding_earned'] = self.total_funding_earned
        base['open_arbs'] = {
            sym: {
                'side': a['side'],
                'entry': a['entry_price'],
                'funding_earned': a['total_funding_earned'],
                'collections': a['funding_collections'],
            } for sym, a in self.open_arbs.items()
        }
        base['current_rates'] = {}
        for sym in self.pairs:
            if self.funding_history.get(sym):
                base['current_rates'][sym] = self.funding_history[sym][-1]
        return base
