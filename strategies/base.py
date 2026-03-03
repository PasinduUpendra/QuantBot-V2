"""
HYDRA Trading System - Base Strategy Class
All strategies inherit from this.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from loguru import logger

from core.exchange import BinanceConnector
from core.risk_manager import RiskManager


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    """
    
    def __init__(self, name: str, exchange: BinanceConnector, 
                 risk_manager: RiskManager, allocation: float):
        self.name = name
        self.exchange = exchange
        self.risk = risk_manager
        self.allocation = allocation
        self.active = True
        self.positions: Dict[str, Dict] = {}
        self._signals: List[Dict] = []
        self.current_regime = 'RANGING'  # Updated by engine each regime cycle
        
        logger.info(f"Strategy [{name}] initialized | Allocation: {allocation*100:.0f}%")
    
    @abstractmethod
    def analyze(self) -> List[Dict]:
        """
        Analyze market and generate signals.
        Returns list of signal dicts: {symbol, side, strength, entry, stop, target}
        """
        pass
    
    @abstractmethod
    def execute(self):
        """Execute strategy logic - analyze, manage positions, place orders."""
        pass
    
    @abstractmethod
    def manage_positions(self):
        """Check and manage open positions (stops, targets, trailing)."""
        pass
    
    def pause(self):
        """Pause strategy."""
        self.active = False
        logger.info(f"Strategy [{self.name}] paused")
    
    def resume(self):
        """Resume strategy."""
        self.active = True
        logger.info(f"Strategy [{self.name}] resumed")
    
    def get_status(self) -> Dict:
        """Get strategy status."""
        return {
            'name': self.name,
            'active': self.active,
            'positions': len(self.positions),
            'allocation': self.allocation,
        }
