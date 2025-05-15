import asyncio
import json
import logging
import time
import websockets
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)

class RealTimeMarketDataProvider:
    """
    Manages WebSocket connections to receive real-time top-of-book market data (best bid/ask).
    Stores only the latest snapshot for each market.
    """
    WEBSOCKET_URL_TEMPLATE = "wss://api.extended.exchange/stream.extended.exchange/v1/orderbooks/{market}?depth=1"
    # If you want to subscribe to all markets with depth=1, the URL might be:
    # WEBSOCKET_URL_ALL_MARKETS = "wss://api.extended.exchange/stream.extended.exchange/v1/orderbooks?depth=1"
    # However, processing messages for "all" markets requires parsing the market from each message.
    # For simplicity, we'll initially connect to specified markets individually.

    def __init__(self):
        self._latest_market_data: Dict[str, Dict[str, Any]] = {}
        self._market_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._connection_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._active_market_listeners: Dict[str, bool] = {}
        self._lock = asyncio.Lock()

    async def _listen_to_market_stream(self, market_name: str):
        """Continuously listens to the WebSocket stream for a single market if active."""
        url = self.WEBSOCKET_URL_TEMPLATE.format(market=market_name)
        while self._running and self._active_market_listeners.get(market_name, False):
            logger.info(f"[RealTimeDataProvider] Attempting to connect or re-connect to WebSocket for {market_name} at {url}")
            try:
                async with websockets.connect(url) as websocket:
                    self._market_connections[market_name] = websocket
                    logger.info(f"[RealTimeDataProvider] Successfully connected to {market_name} stream.")
                    
                    while self._running and self._active_market_listeners.get(market_name, False):
                        try:
                            message_str = await websocket.recv()
                            snapshot = json.loads(message_str)
                            
                            if snapshot.get("type") == "SNAPSHOT" and snapshot.get("data", {}).get("m") == market_name:
                                data = snapshot["data"]
                                best_bid = data.get("b", [{}])[0]
                                best_ask = data.get("a", [{}])[0]

                                if best_bid.get("p") and best_ask.get("p"):
                                    async with self._lock:
                                        if self._active_market_listeners.get(market_name):
                                            self._latest_market_data[market_name] = {
                                                "bid_price": best_bid["p"],
                                                "bid_qty": best_bid.get("q", "0"),
                                                "ask_price": best_ask["p"],
                                                "ask_qty": best_ask.get("q", "0"),
                                                "timestamp": snapshot.get("ts", time.time() * 1000)
                                            }
                                else:
                                    logger.warning(f"[RealTimeDataProvider] Snapshot for {market_name} lacked bid/ask price: {snapshot}")

                        except websockets.exceptions.ConnectionClosed as e:
                            logger.warning(f"[RealTimeDataProvider] Connection for {market_name} closed: {e}. Will attempt to reconnect if still active.")
                            break
                        except json.JSONDecodeError as e:
                            logger.error(f"[RealTimeDataProvider] Error decoding JSON for {market_name}: {message_str} - Error: {e}")
                        except Exception as e:
                            logger.error(f"[RealTimeDataProvider] Error in {market_name} stream listener: {e}", exc_info=True)
                            await asyncio.sleep(5)
                            break
            
            except (websockets.exceptions.InvalidURI, ConnectionRefusedError, websockets.exceptions.WebSocketException) as e:
                logger.error(f"[RealTimeDataProvider] WebSocket connection error for {market_name} ({url}): {e}. Retrying in 10s if still active.")
            except Exception as e:
                 logger.error(f"[RealTimeDataProvider] Unexpected error connecting to {market_name} ({url}): {e}. Retrying in 10s if still active.", exc_info=True)

            if self._running and self._active_market_listeners.get(market_name, False):
                await asyncio.sleep(10)
            else:
                logger.info(f"[RealTimeDataProvider] Listener for {market_name} stopping as it's no longer marked active or provider is shutting down.")
                break

        logger.info(f"[RealTimeDataProvider] Listener task for {market_name} fully stopped.")
        async with self._lock:
            if market_name in self._market_connections:
                del self._market_connections[market_name]
            if market_name in self._latest_market_data:
                del self._latest_market_data[market_name]

    async def start_streams(self, markets: List[str]):
        """Starts or ensures listening to WebSocket streams for the given list of markets."""
        self._running = True
        markets_started_or_restarted = []
        for market_name_raw in markets:
            market_name = market_name_raw.upper()
            self._active_market_listeners[market_name] = True
            
            if market_name not in self._connection_tasks or self._connection_tasks[market_name].done():
                logger.info(f"[RealTimeDataProvider] Creating new listener task for {market_name}.")
                task = asyncio.create_task(self._listen_to_market_stream(market_name))
                self._connection_tasks[market_name] = task
                markets_started_or_restarted.append(market_name)
            else:
                logger.info(f"[RealTimeDataProvider] Listener task for {market_name} already exists and is active. Ensuring it continues.")

        if markets_started_or_restarted:
            logger.info(f"[RealTimeDataProvider] Started/Restarted listener tasks for: {markets_started_or_restarted}")
        else:
            logger.info(f"[RealTimeDataProvider] All requested markets {markets} listeners were already active or being managed.")

    async def stop_specific_streams(self, markets_to_stop: List[str]):
        """Stops listening to WebSocket streams for the specified markets."""
        logger.info(f"[RealTimeDataProvider] Attempting to stop streams for: {markets_to_stop}")
        stopped_market_tasks = []

        for market_name_raw in markets_to_stop:
            market_name = market_name_raw.upper()
            self._active_market_listeners[market_name] = False
            
            task = self._connection_tasks.get(market_name)
            if task and not task.done():
                logger.info(f"[RealTimeDataProvider] Cancelling listener task for {market_name}.")
                task.cancel()
                stopped_market_tasks.append(task)
            else:
                logger.info(f"[RealTimeDataProvider] No active listener task found for {market_name} to stop, or already done.")
            
            conn = self._market_connections.get(market_name)
            if conn and not conn.closed:
                try:
                    logger.info(f"[RealTimeDataProvider] Explicitly closing WebSocket for {market_name}.")
                    await conn.close()
                except Exception as e:
                    logger.error(f"[RealTimeDataProvider] Error explicitly closing WebSocket for {market_name}: {e}")

            async with self._lock:
                if market_name in self._latest_market_data:
                    logger.debug(f"[RealTimeDataProvider] Clearing latest data for stopped market {market_name}.")
                    del self._latest_market_data[market_name]
                if market_name in self._market_connections:
                    del self._market_connections[market_name]

        if stopped_market_tasks:
            results = await asyncio.gather(*stopped_market_tasks, return_exceptions=True)
            for market_raw, result in zip(markets_to_stop, results):
                market = market_raw.upper()
                if isinstance(result, asyncio.CancelledError):
                    logger.info(f"[RealTimeDataProvider] Listener task for {market} was cancelled successfully.")
                elif isinstance(result, Exception):
                    logger.error(f"[RealTimeDataProvider] Exception during task cancellation for {market}: {result}")
                if market in self._connection_tasks:
                    del self._connection_tasks[market]
        
        if not any(self._active_market_listeners.values()):
            logger.info("[RealTimeDataProvider] All specific streams stopped, and no other streams are active. Setting provider to inactive.")

        logger.info(f"[RealTimeDataProvider] Finished stopping streams for: {markets_to_stop}")

    async def get_best_bid_ask(self, market_name: str) -> Optional[Dict[str, Any]]:
        """Returns the latest best bid and ask for the given market."""
        market_name = market_name.upper()
        async with self._lock:
            return self._latest_market_data.get(market_name)

    async def close_streams(self):
        """Stops all listening tasks and closes WebSocket connections. Full shutdown of provider."""
        logger.info("[RealTimeDataProvider] Attempting to fully close all streams and shut down provider...")
        self._running = False
        self._active_market_listeners.clear()

        tasks_to_wait_for = []
        for market_name, task in list(self._connection_tasks.items()):
            if task and not task.done():
                logger.info(f"[RealTimeDataProvider] Cancelling task for market {market_name} during full shutdown.")
                task.cancel()
                tasks_to_wait_for.append(task)
        
        if tasks_to_wait_for:
            results = await asyncio.gather(*tasks_to_wait_for, return_exceptions=True)

        for market_name, ws in list(self._market_connections.items()):
            try:
                if ws and not ws.closed:
                    logger.info(f"[RealTimeDataProvider] Closing WebSocket connection for {market_name} during full shutdown.")
                    await ws.close()
            except Exception as e:
                logger.error(f"[RealTimeDataProvider] Error closing WebSocket for {market_name} during full shutdown: {e}")
        
        self._market_connections.clear()
        self._connection_tasks.clear()
        async with self._lock:
            self._latest_market_data.clear()
        logger.info("[RealTimeDataProvider] All streams, connections, and data cleared for full shutdown.")

    def get_active_streams(self) -> List[str]:
        """Returns a list of market names for which streams are currently marked as active."""
        # Ensure this is thread-safe if accessed concurrently, though current CLI usage is serial for commands.
        # If _active_market_listeners could be modified by another thread while iterating,
        # a lock might be needed, but typically it's modified by async tasks managed by the event loop.
        return [market for market, is_active in self._active_market_listeners.items() if is_active]

# Example Usage (for testing this module directly)
async def main_test():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    provider = RealTimeMarketDataProvider()
    
    try:
        markets1 = ["BTC-USD"]
        print(f"--- Test: Loading {markets1} ---")
        await provider.start_streams(markets1)
        await asyncio.sleep(5)
        data_btc = provider.get_best_bid_ask("BTC-USD")
        print(f"BTC Data after load: {data_btc}")

        markets2 = ["ETH-USD"]
        print(f"--- Test: Loading {markets2} (BTC should continue) ---")
        await provider.start_streams(markets2)
        await asyncio.sleep(5)
        data_eth = provider.get_best_bid_ask("ETH-USD")
        data_btc_after_eth = provider.get_best_bid_ask("BTC-USD")
        print(f"ETH Data: {data_eth}")
        print(f"BTC Data after ETH load: {data_btc_after_eth}")

        print(f"--- Test: Unloading BTC-USD ---")
        await provider.stop_specific_streams(["BTC-USD"])
        await asyncio.sleep(2)
        data_btc_after_unload = provider.get_best_bid_ask("BTC-USD")
        data_eth_after_btc_unload = provider.get_best_bid_ask("ETH-USD")
        print(f"BTC Data after unload: {data_btc_after_unload}")
        print(f"ETH Data after BTC unload: {data_eth_after_btc_unload}")

        print(f"--- Test: Unloading ETH-USD (all streams should now be inactive) ---")
        await provider.stop_specific_streams(["ETH-USD"])
        await asyncio.sleep(2)
        data_eth_after_unload = provider.get_best_bid_ask("ETH-USD")
        print(f"ETH Data after unload: {data_eth_after_unload}")

        print(f"--- Test: Reloading BTC-USD ---")
        await provider.start_streams(["BTC-USD"])
        await asyncio.sleep(5)
        data_btc_reloaded = provider.get_best_bid_ask("BTC-USD")
        print(f"BTC Data after reload: {data_btc_reloaded}")

    except KeyboardInterrupt:
        logger.info("Test interrupted by user.")
    finally:
        print("--- Test: Shutting down all streams via close_streams() ---")
        await provider.close_streams()
        print("--- Test: Provider shutdown complete ---")

if __name__ == "__main__":
    # To run this test: python -m CLI.core.realtime_market_data 
    # (assuming you are in the directory above CLI)
    # Or adjust python path if running directly.
    asyncio.run(main_test()) 