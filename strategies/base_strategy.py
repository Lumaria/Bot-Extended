import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.trading_client import PerpetualTradingClient

from config import API_KEY, PUBLIC_KEY, PRIVATE_KEY, VAULT

from core.market_utils import MarketUtils 

logger = logging.getLogger(__name__)

class BaseStrategy(ABC):
    """Base class for all trading strategies."""
    
    def __init__(self, trading_client: PerpetualTradingClient, market_utils: MarketUtils):
        """Initialize with a trading client and market utils."""
        self.trading_client = trading_client
        self.market_utils = market_utils

    async def get_market_info(self, market: str):
        """Get market information including current prices and trading config."""
        return await self.market_utils.get_market_object(market)

    @abstractmethod
    async def execute(self, *args, **kwargs):
        """Execute the strategy. Must be implemented by subclasses."""
        pass 