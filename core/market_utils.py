import logging
from decimal import Decimal
from typing import Optional, List, Dict, Any
import time

from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.trading_client import PerpetualTradingClient

from config import API_KEY, PUBLIC_KEY, PRIVATE_KEY, VAULT

logger = logging.getLogger(__name__)

CACHE_DURATION_SECONDS = 60  

class MarketUtils:
    """Utility class for market-related operations."""

    def __init__(self, trading_client: PerpetualTradingClient):
        """Initialize with a trading client."""
        self.trading_client = trading_client
        self._market_cache: Dict[str, Any] = {} 
        self._cache_timestamps: Dict[str, float] = {} 

    async def _fetch_and_cache_all_markets(self) -> None:
        """Fetches all markets from the API and updates the cache."""
        try:
            logger.debug("[MarketUtils] Fetching all markets from API to update cache...")
            markets_response = await self.trading_client.markets_info.get_markets()
            current_time = time.time()
            for m in markets_response.data:
                self._market_cache[m.name] = m
                self._cache_timestamps[m.name] = current_time
            logger.debug(f"[MarketUtils] Market cache updated with {len(markets_response.data)} markets.")
        except Exception as e:
            logger.error(f"[MarketUtils] Error fetching and caching all markets: {str(e)}")

    async def get_market_object(self, market_name: str) -> Optional[Any]:
        """Get the full market object for a specific market name, using a cache."""
        cached_market = self._market_cache.get(market_name)
        cache_timestamp = self._cache_timestamps.get(market_name)

        if cached_market and cache_timestamp and (time.time() - cache_timestamp < CACHE_DURATION_SECONDS):
            logger.debug(f"[MarketUtils] Returning cached market object for {market_name}.")
            return cached_market
        
        logger.debug(f"[MarketUtils] No valid cache for {market_name} or cache expired. Fetching from API.")
        await self._fetch_and_cache_all_markets() 
        

        final_market = self._market_cache.get(market_name)
        if not final_market:
             logger.warning(f"[MarketUtils] Market object for {market_name} not found even after cache refresh.")
        return final_market

    async def get_markets(self, top_n: Optional[int] = None) -> List[Any]:
        """Get all available markets, using cache and optionally sorting by 24h volume."""
        needs_refresh = True
        if self._market_cache and self._cache_timestamps:
            if not any(time.time() - ts < CACHE_DURATION_SECONDS for ts in self._cache_timestamps.values()):
                logger.debug("[MarketUtils] get_markets: Cache seems entirely stale or empty, attempting refresh.")
                await self._fetch_and_cache_all_markets()
            elif not self._market_cache: # If cache is empty
                 logger.debug("[MarketUtils] get_markets: Cache empty, attempting refresh.")
                 await self._fetch_and_cache_all_markets()

        markets_list = list(self._market_cache.values())
        
        if not markets_list and not self._market_cache: 
            logger.warning("[MarketUtils] get_markets: No markets available even after attempting cache refresh.")
            return []
        
        if top_n is not None:
            markets_list_to_sort = []
            for m in markets_list:
                try:
                    volume = float(m.market_stats.daily_volume)
                    markets_list_to_sort.append((m, volume))
                except (AttributeError, ValueError, TypeError) as e:
                    logger.warning(f"Could not parse volume for market {getattr(m, 'name', 'Unknown')} while sorting: {e}")
            
            markets_list_to_sort.sort(key=lambda item: item[1], reverse=True)
            return [item[0] for item in markets_list_to_sort[:top_n]]
        
        return markets_list

