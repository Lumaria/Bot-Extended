import logging
from decimal import Decimal
from typing import Optional
from x10.perpetual.orders import OrderSide
from x10.perpetual.trading_client import PerpetualTradingClient

from .base_strategy import BaseStrategy
from core.market_utils import MarketUtils
from core.realtime_market_data import RealTimeMarketDataProvider

class APIMessageFilter(logging.Filter):
    def filter(self, record):
        return not ("Error response from POST" in record.getMessage() and "api.extended.exchange" in record.getMessage())


logger = logging.getLogger(__name__)
logger.addFilter(APIMessageFilter())

class BestOrderStrategy(BaseStrategy):
    """Strategy for placing orders at the best bid or ask price using real-time WebSocket data for price."""

    def __init__(self, trading_client: PerpetualTradingClient, market_utils: MarketUtils, realtime_data_provider: RealTimeMarketDataProvider):
        super().__init__(trading_client=trading_client, market_utils=market_utils)
        self.realtime_data_provider = realtime_data_provider

    async def execute(self, market: str, side: str, amount_usd: float):
        """Execute the best order strategy using real-time WebSocket for price and REST API for market config."""
        try:
            market_config = await self.get_market_info(market)
            if not market_config or not hasattr(market_config, 'trading_config'):
                logger.error(f"[BestOrderStrategy] Market trading configuration for {market} not found via MarketUtils.")
                return None

            # Get real-time best bid/ask from RealTimeMarketDataProvider
            realtime_prices = await self.realtime_data_provider.get_best_bid_ask(market)
            if not realtime_prices:
                logger.error(f"[BestOrderStrategy] Real-time price data not available for {market} from RealTimeMarketDataProvider.")
                return None

            price: Optional[Decimal] = None
            if side.lower() in ("bb", "buy"):
                if realtime_prices.get('bid_price'):
                    price = Decimal(str(realtime_prices['bid_price']))
                    logger.debug(f"[BestOrderStrategy] Using WebSocket real-time bid price: {price} for {market} {side}")
                else:
                    logger.error(f"[BestOrderStrategy] Real-time bid price not available for {market}.")
                    return None
            elif side.lower() in ("ba", "sell"):
                if realtime_prices.get('ask_price'):
                    price = Decimal(str(realtime_prices['ask_price']))
                    logger.debug(f"[BestOrderStrategy] Using WebSocket real-time ask price: {price} for {market} {side}")
                else:
                    logger.error(f"[BestOrderStrategy] Real-time ask price not available for {market}.")
                    return None
            else:
                logger.error(f"[BestOrderStrategy] Invalid side: {side} for market {market}")
                return None

            if price is None: 
                logger.error(f"[BestOrderStrategy] Could not determine real-time price for {market} {side}. Order aborted.")
                return None
            
            order_side_api = OrderSide.BUY if side.lower() in ("bb", "buy") else OrderSide.SELL

            min_order_size = Decimal(str(market_config.trading_config.min_order_size))
            min_order_size_change = Decimal(str(market_config.trading_config.min_order_size_change))
            
            quantity = (Decimal(str(amount_usd)) / price).quantize(min_order_size_change)

            if quantity < min_order_size:
                logger.error(f"[BestOrderStrategy] Calculated quantity ({quantity}) is below minimum order size ({min_order_size}) for {market}")
                return None

            logger.debug(f"[BestOrderStrategy] Placing {order_side_api.value} order for {quantity} {market} at {price} (real-time)")
            order_response = await self.trading_client.place_order(
                market_name=market,
                amount_of_synthetic=quantity,
                price=price,
                side=order_side_api,
                post_only=True,
            )
            
            order = order_response.data
            logger.info(f"[BestOrderStrategy] Order placed successfully! ID: {order.id} using real-time price.")
            return order.id
        
        except Exception as e:
            error_message = str(e)
            if "New order cost exceeds available balance" in error_message:
                logger.error("[BestOrderStrategy] Insufficient balance to place this order")
            elif "Invalid quantity precision" in error_message:
                 logger.error(f"[BestOrderStrategy] Invalid quantity precision for order: {quantity if 'quantity' in locals() else 'unknown'} market {market}")
            else:
                logger.error(f"[BestOrderStrategy] An unexpected error occurred: {error_message}", exc_info=True)
            return None 