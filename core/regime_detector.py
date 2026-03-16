"""
HYDRA Trading System - Rule-Based Market Regime Detector
==========================================================
Pure mathematical regime detection. GPT-4o-mini was removed because it
called TRENDING_BULL 73% of the time and NEVER detected TRENDING_BEAR,
even while BTC dropped -2.8% intraday. This caused the bot to keep
entering long trades in a declining market.

Now uses BTC 1h/24h change, volatility (ATR%), and SMA slope to
classify regimes deterministically.

Detects:
1. TRENDING_BULL  - clear uptrend, momentum favored
2. TRENDING_BEAR  - clear downtrend, defensive mode
3. RANGING        - sideways, mean reversion favored
4. HIGH_VOLATILITY - large swings, reduce exposure
5. CHOPPY         - no clear direction, mixed signals
6. RISK_OFF       - extreme fear / flash crash, minimize exposure
"""
import time
from typing import Dict, Tuple
from loguru import logger


class MarketRegimeDetector:
    """
    Rule-based market regime detection.
    Uses BTC price action + volatility to classify the market.
    No OpenAI dependency — deterministic and instant.
    """

    REGIMES = ['TRENDING_BULL', 'TRENDING_BEAR', 'RANGING', 'HIGH_VOLATILITY', 'CHOPPY', 'RISK_OFF']

    # Strategy weights per regime
    # Phase 1: Grid permanently disabled (0% in .env) — all weight redistributed to MR+MOM
    # CHOPPY: MOM stays 0 (proven to lose in chop). MR gets 100% since it's the only active strategy.
    # RISK_OFF: MOM allocation reserved for future short capability.
    REGIME_WEIGHTS = {
        'TRENDING_BULL': {
            'GRID': 0.00, 'MEAN_REV': 0.25, 'MOMENTUM': 0.75, 'FUND_ARB': 0.00
        },
        'TRENDING_BEAR': {
            'GRID': 0.00, 'MEAN_REV': 0.60, 'MOMENTUM': 0.40, 'FUND_ARB': 0.00
        },
        'RANGING': {
            'GRID': 0.00, 'MEAN_REV': 0.55, 'MOMENTUM': 0.45, 'FUND_ARB': 0.00
        },
        'HIGH_VOLATILITY': {
            'GRID': 0.00, 'MEAN_REV': 0.40, 'MOMENTUM': 0.60, 'FUND_ARB': 0.00
        },
        'CHOPPY': {
            'GRID': 0.00, 'MEAN_REV': 1.00, 'MOMENTUM': 0.00, 'FUND_ARB': 0.00
        },
        'RISK_OFF': {
            'GRID': 0.00, 'MEAN_REV': 0.30, 'MOMENTUM': 0.70, 'FUND_ARB': 0.00
        },
    }

    def __init__(self):
        self.current_regime = 'RANGING'  # Safe default
        self.confidence = 0.5
        self.last_analysis_time = 0
        self.analysis_interval = 1200  # 20 minutes
        self.history: list = []
        # FIX-8: Regime hysteresis — prevent rapid flipping
        # Mar 3: regime flipped 111 times (CHOPPY↔BULL every minute), causing whipsaw entries
        self.last_regime_change_time = 0
        self.min_regime_hold = 1800  # Hold regime for at least 30 min
        logger.info("Rule-Based Regime Detector initialized (GPT removed — biased bull 73%)")

    def detect_regime(self, market_data: Dict) -> Tuple[str, float, Dict]:
        """
        Detect regime from market data using pure math.

        Returns: (regime, confidence, weights)
        """
        # Throttle — re-analyze every 20 min
        if time.time() - self.last_analysis_time < self.analysis_interval:
            return self.current_regime, self.confidence, self.REGIME_WEIGHTS.get(self.current_regime, {})

        self.last_analysis_time = time.time()

        regime, confidence = self._rules_based_detect(market_data)

        # FIX-8: Regime hysteresis — don't flip unless held for 30 min
        # Exception: RISK_OFF and TRENDING_BEAR always override (safety)
        if regime != self.current_regime:
            time_in_current = time.time() - self.last_regime_change_time
            emergency_override = regime in ('RISK_OFF', 'TRENDING_BEAR')
            if time_in_current < self.min_regime_hold and not emergency_override:
                logger.debug(f"[RULES] Regime would change to {regime} but holding {self.current_regime} "
                           f"({time_in_current:.0f}s < {self.min_regime_hold}s hysteresis)")
                regime = self.current_regime  # Keep current regime
            else:
                logger.info(f"[RULES] Regime changed: {self.current_regime} → {regime}")
                self.last_regime_change_time = time.time()

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

    def _rules_based_detect(self, market_data: Dict) -> Tuple[str, float]:
        """
        Enhanced rule-based regime detection.
        
        Uses multiple thresholds so a slow grind-down (-1% to -2% 24h)
        is correctly classified as TRENDING_BEAR or CHOPPY instead of
        being lumped into RANGING (which the old rules did).
        """
        btc_1h = market_data.get('btc_change_1h', 0)
        btc_24h = market_data.get('btc_change_24h', 0)
        volatility = market_data.get('btc_volatility', 1)

        # ---------- RISK OFF: extreme sell-off ----------
        if volatility > 3 and btc_24h < -5:
            return 'RISK_OFF', 0.85
        if btc_24h < -8:
            return 'RISK_OFF', 0.80

        # ---------- HIGH VOLATILITY ----------
        if volatility > 3:
            return 'HIGH_VOLATILITY', 0.75

        # ---------- STRONG TRENDING (both 1h AND 24h agree) ----------
        if btc_24h > 2.5 and btc_1h > 0.3:
            return 'TRENDING_BULL', 0.75
        if btc_24h < -2.5 and btc_1h < -0.3:
            return 'TRENDING_BEAR', 0.75

        # ---------- MODERATE TRENDING (24h clear, 1h not opposing) ----------
        if btc_24h > 1.5 and btc_1h >= -0.1:
            return 'TRENDING_BULL', 0.65
        if btc_24h < -1.5 and btc_1h <= 0.1:
            return 'TRENDING_BEAR', 0.65

        # ---------- MILD BEARISH DRIFT (the scenario GPT always missed) ----------
        if btc_24h < -0.5 and btc_1h < 0:
            return 'CHOPPY', 0.60

        # ---------- MILD BULLISH DRIFT ----------
        if btc_24h > 0.5 and btc_1h > 0:
            return 'CHOPPY', 0.55

        # ---------- CHOPPY: 1h and 24h disagree on direction ----------
        if (btc_1h > 0.3 and btc_24h < -0.3) or (btc_1h < -0.3 and btc_24h > 0.3):
            return 'CHOPPY', 0.60

        # ---------- RANGING: very flat ----------
        if abs(btc_24h) < 0.5 and volatility < 1.5:
            return 'RANGING', 0.60

        # ---------- Default: CHOPPY ----------
        return 'CHOPPY', 0.50

    def get_weights(self) -> Dict[str, float]:
        """Get current strategy allocation weights."""
        return self.REGIME_WEIGHTS.get(self.current_regime, self.REGIME_WEIGHTS['RANGING'])
