"""
HYDRA Trading System - AI Market Regime Detector
==================================================
Uses OpenAI GPT to analyze market conditions and adjust strategy allocation.

Detects:
1. TRENDING (bull/bear) - favor momentum
2. RANGING - favor grid + mean reversion  
3. HIGH_VOLATILITY - reduce size, widen stops
4. LOW_VOLATILITY - increase size, tighten stops
5. RISK_OFF - defensive mode, reduce exposure
"""
import json
import time
from typing import Dict, Optional, Tuple
from loguru import logger

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    logger.warning("OpenAI not available - AI regime detection disabled")

from core import OPENAI_API_KEY


class MarketRegimeDetector:
    """
    AI-powered market regime detection.
    Analyzes price action, volatility, and market structure
    to determine optimal strategy allocation.
    """
    
    REGIMES = ['TRENDING_BULL', 'TRENDING_BEAR', 'RANGING', 'HIGH_VOLATILITY', 'CHOPPY', 'RISK_OFF']
    
    # Strategy weights per regime
    REGIME_WEIGHTS = {
        'TRENDING_BULL': {
            'GRID': 0.15, 'MEAN_REV': 0.15, 'MOMENTUM': 0.50, 'FUND_ARB': 0.20
        },
        'TRENDING_BEAR': {
            'GRID': 0.10, 'MEAN_REV': 0.10, 'MOMENTUM': 0.30, 'FUND_ARB': 0.50
        },
        'RANGING': {
            'GRID': 0.40, 'MEAN_REV': 0.30, 'MOMENTUM': 0.10, 'FUND_ARB': 0.20
        },
        'HIGH_VOLATILITY': {
            'GRID': 0.20, 'MEAN_REV': 0.35, 'MOMENTUM': 0.25, 'FUND_ARB': 0.20
        },
        'CHOPPY': {
            'GRID': 0.30, 'MEAN_REV': 0.25, 'MOMENTUM': 0.15, 'FUND_ARB': 0.30
        },
        'RISK_OFF': {
            'GRID': 0.05, 'MEAN_REV': 0.05, 'MOMENTUM': 0.05, 'FUND_ARB': 0.85
        },
    }
    
    def __init__(self):
        self.client = None
        if HAS_OPENAI and OPENAI_API_KEY:
            try:
                self.client = OpenAI(api_key=OPENAI_API_KEY)
                logger.info("AI Regime Detector initialized with GPT-4o-mini")
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI: {e}")
        
        self.current_regime = 'RANGING'  # Default
        self.confidence = 0.5
        self.last_analysis_time = 0
        self.analysis_interval = 1800  # 30 minutes
        self.history: list = []
    
    def detect_regime(self, market_data: Dict) -> Tuple[str, float, Dict]:
        """
        Analyze market data and detect current regime.
        
        Args:
            market_data: {
                'btc_price': float,
                'btc_change_1h': float,
                'btc_change_24h': float,
                'btc_volatility': float,
                'eth_price': float,
                'total_volume_24h': float,
                'funding_rates': Dict[str, float],
                'fear_greed': Optional[int],
            }
        
        Returns: (regime, confidence, weights)
        """
        # Check if we should re-analyze
        if time.time() - self.last_analysis_time < self.analysis_interval:
            return self.current_regime, self.confidence, self.REGIME_WEIGHTS.get(self.current_regime, {})
        
        self.last_analysis_time = time.time()
        
        # Try AI analysis first
        if self.client:
            try:
                regime, confidence = self._ai_analyze(market_data)
                self.current_regime = regime
                self.confidence = confidence
                self.history.append({
                    'time': time.time(),
                    'regime': regime,
                    'confidence': confidence,
                    'method': 'AI',
                })
                weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS['RANGING'])
                logger.info(f"[AI] Regime: {regime} (confidence: {confidence:.0%})")
                return regime, confidence, weights
            except Exception as e:
                logger.warning(f"AI analysis failed: {e}, falling back to rules")
        
        # Fallback: Rule-based detection
        regime, confidence = self._rules_based_detect(market_data)
        self.current_regime = regime
        self.confidence = confidence
        self.history.append({
            'time': time.time(),
            'regime': regime,
            'confidence': confidence,
            'method': 'RULES',
        })
        weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS['RANGING'])
        logger.info(f"[RULES] Regime: {regime} (confidence: {confidence:.0%})")
        return regime, confidence, weights
    
    def _ai_analyze(self, market_data: Dict) -> Tuple[str, float]:
        """Use GPT to analyze market conditions."""
        prompt = f"""You are a quantitative market regime classifier. Analyze the following crypto market data and classify the current regime.

MARKET DATA:
- BTC Price: ${market_data.get('btc_price', 'N/A')}
- BTC 1h Change: {market_data.get('btc_change_1h', 'N/A')}%
- BTC 24h Change: {market_data.get('btc_change_24h', 'N/A')}%
- BTC Volatility (ATR%): {market_data.get('btc_volatility', 'N/A')}%
- ETH Price: ${market_data.get('eth_price', 'N/A')}
- 24h Total Volume: ${market_data.get('total_volume_24h', 'N/A')}
- BTC Funding Rate: {market_data.get('funding_rates', {}).get('BTCUSDT', 'N/A')}%

CLASSIFY into exactly ONE regime:
1. TRENDING_BULL - Strong uptrend, momentum favored
2. TRENDING_BEAR - Strong downtrend, defensive mode
3. RANGING - Sideways, mean reversion favored
4. HIGH_VOLATILITY - Large swings, reduce exposure
5. CHOPPY - No clear direction, mixed signals
6. RISK_OFF - Extreme fear, flash crash risk, minimize exposure

Respond with ONLY a JSON object:
{{"regime": "REGIME_NAME", "confidence": 0.0_to_1.0, "reasoning": "brief explanation"}}"""

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.1,
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse JSON response
        try:
            # Handle potential markdown wrapping
            if '```' in result_text:
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
            
            result = json.loads(result_text)
            regime = result.get('regime', 'RANGING')
            confidence = float(result.get('confidence', 0.5))
            reasoning = result.get('reasoning', '')
            
            if regime not in self.REGIMES:
                regime = 'RANGING'
                confidence = 0.3
            
            logger.debug(f"[AI] {regime} ({confidence:.0%}): {reasoning}")
            return regime, confidence
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse AI response: {result_text}")
            return 'RANGING', 0.3
    
    def _rules_based_detect(self, market_data: Dict) -> Tuple[str, float]:
        """Rule-based regime detection fallback."""
        btc_1h = market_data.get('btc_change_1h', 0)
        btc_24h = market_data.get('btc_change_24h', 0)
        volatility = market_data.get('btc_volatility', 1)
        
        # High volatility
        if volatility > 3:
            if btc_24h < -8:
                return 'RISK_OFF', 0.8
            return 'HIGH_VOLATILITY', 0.7
        
        # Trending
        if btc_24h > 3 and btc_1h > 0.5:
            return 'TRENDING_BULL', 0.7
        elif btc_24h < -3 and btc_1h < -0.5:
            return 'TRENDING_BEAR', 0.7
        
        # Choppy
        if abs(btc_24h) < 1 and volatility > 1.5:
            return 'CHOPPY', 0.6
        
        # Default: ranging
        return 'RANGING', 0.5
    
    def get_weights(self) -> Dict[str, float]:
        """Get current strategy allocation weights."""
        return self.REGIME_WEIGHTS.get(self.current_regime, self.REGIME_WEIGHTS['RANGING'])
