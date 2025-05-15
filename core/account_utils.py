import logging
from decimal import Decimal
from typing import Optional, List, Dict

from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.trading_client import PerpetualTradingClient

from config import API_KEY, PUBLIC_KEY, PRIVATE_KEY, VAULT

logger = logging.getLogger(__name__)

class AccountUtils:
    """Utility class for account-related operations."""

    def __init__(self, trading_client: PerpetualTradingClient):
        """Initialize with a trading client."""
        self.trading_client = trading_client

    async def get_positions(self, market: Optional[str] = None) -> List[Dict]:
        """Get current positions, optionally filtered by market."""
        try:
            if market:
                positions = await self.trading_client.account.get_positions(market_names=[market])
            else:
                positions = await self.trading_client.account.get_positions()
            return positions.data
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}")
            return []
