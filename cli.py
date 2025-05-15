import asyncio
import logging
from core.market_utils import MarketUtils
from core.account_utils import AccountUtils
from core.realtime_market_data import RealTimeMarketDataProvider
from strategies.best_order import BestOrderStrategy
from typing import Optional, List

from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.trading_client import PerpetualTradingClient
from config import API_KEY, PUBLIC_KEY, PRIVATE_KEY, VAULT
from x10.utils.log import get_logger
from x10.utils.model import X10BaseModel
from x10.perpetual.orders import OrderSide


logging.basicConfig(
    level=logging.WARNING, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# Set specific log levels. Change to WARNING for less verbose output for the user.
logging.getLogger("TradingCLI").setLevel(logging.WARNING) 
logging.getLogger("strategies.best_order").setLevel(logging.WARNING) 
logging.getLogger("core.realtime_market_data").setLevel(logging.WARNING)

class TradingCLI:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__) 
        """Initialize the CLI with necessary utilities and strategies."""
        self.stark_account = StarkPerpetualAccount(
            vault=VAULT,
            private_key=PRIVATE_KEY,
            public_key=PUBLIC_KEY,
            api_key=API_KEY,
        )
        self.trading_client = PerpetualTradingClient(MAINNET_CONFIG, self.stark_account)
        
        self.market_utils = MarketUtils(self.trading_client)
        self.account_utils = AccountUtils(self.trading_client)
        
        self.realtime_data_provider = RealTimeMarketDataProvider()
        
        self.realtime_provider_management_task: Optional[asyncio.Task] = None 
        self.best_order_strategy = BestOrderStrategy(
            trading_client=self.trading_client, 
            market_utils=self.market_utils,
            realtime_data_provider=self.realtime_data_provider
        )
        self.running = True
        self._running_cli_task = None

    async def execute_order(self, market: str, price_offset: float, side: str, amount_usd: float):
        # This method is now effectively deprecated by user request to remove the general 'order' command
        self.logger.warning("Attempted to use deprecated 'execute_order'. Please use <market> BB/BA <amount> commands.")
        # try:
        #     positions = await self.account_utils.get_positions(market)
        #     if positions:
        #         self.logger.info(f"Current position: {positions[0].to_pretty_json()}")
        #     normalized_side = "buy" if side.lower() == "long" else "sell" if side.lower() == "short" else side
        #     order_id = await self.best_order_strategy.execute(market, normalized_side, amount_usd)
        #     if order_id:
        #         self.logger.info(f"Order placed with ID: {order_id}")
        # except Exception as e:
        #     self.logger.error(f"Error while executing order: {str(e)}")

    async def show_markets(self, top_n=None):
        """Display available markets as an aligned table, sorted by 24h volume descending."""
        try:
            markets = await self.market_utils.get_markets(top_n)
            valid_markets = []
            for m in markets:
                try:
                    if hasattr(m, 'market_stats') and hasattr(m.market_stats, 'daily_volume') and m.market_stats.daily_volume is not None:
                        volume = float(m.market_stats.daily_volume)
                        if volume > 0:
                            valid_markets.append(m)
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"Could not parse volume for market {getattr(m, 'name', 'Unknown')} : {e}")

            valid_markets.sort(key=lambda m: float(m.market_stats.daily_volume), reverse=True)
            
            if top_n is not None:
                markets_to_display = valid_markets[:top_n]
            else:
                markets_to_display = valid_markets
                
            print("\n{:<12} {:>14} {:>18}".format("Market", "Last Price", "24h Volume"))
            print("-"*48)
            for market_obj in markets_to_display:
                name = market_obj.name
                if name.upper().endswith('-USD'):
                    name = name[:-4]
                price = market_obj.market_stats.last_price if hasattr(market_obj.market_stats, 'last_price') else 'N/A'
                volume = market_obj.market_stats.daily_volume
                print(f"{name:<12} {float(price):>14,.4f} {float(volume):>18,.2f}")
            print("-"*48)
        except Exception as e:
            self.logger.error(f"Error while fetching markets: {str(e)}", exc_info=True)

    async def show_position(self, market: Optional[str] = None):
        """Display the current position."""
        try:
            positions = await self.account_utils.get_positions(market)
            
            if not positions:
                print("No open positions")
                return

            print("\nCurrent positions:")
            print("------------------")
            for pos in positions:
                print(f"Market: {pos.market}")
                print(f"Size: {pos.size}")
                print(f"Entry price: {pos.open_price}")
                
                # Attempt to get a more current market price
                current_display_price = "N/A"
                market_name_for_pos = pos.market # Assuming pos.market gives "BTC-USD" etc.
                
                # 1. Try RealTimeMarketDataProvider (WebSocket)
                if self.realtime_data_provider:
                    live_price_data = await self.realtime_data_provider.get_best_bid_ask(market_name_for_pos)
                    if live_price_data:
                        bid = live_price_data.get('bid_price')
                        ask = live_price_data.get('ask_price')
                        if bid and ask:
                            current_display_price = f"Live Bid: {bid}, Ask: {ask}"
                        elif bid:
                            current_display_price = f"Live Bid: {bid}"
                        elif ask:
                            current_display_price = f"Live Ask: {ask}"
                        else:
                            current_display_price = "Live data found, but no bid/ask price."
                
                # 2. If no live price, try MarketUtils (REST API, cached)
                if current_display_price == "N/A" or "Live data found, but no bid/ask price." in current_display_price:
                    if self.market_utils:
                        market_obj = await self.market_utils.get_market_object(market_name_for_pos)
                        if market_obj and hasattr(market_obj, 'market_stats') and hasattr(market_obj.market_stats, 'last_price') and market_obj.market_stats.last_price is not None:
                            current_display_price = f"Last (REST): {market_obj.market_stats.last_price}"
                        elif current_display_price == "N/A": # Only update if it was truly N/A, not if live data was found but empty
                             current_display_price = "Last price (REST) not available."

                print(f"Current Market Price: {current_display_price}")
                print(f"Unrealized P&L: {pos.unrealised_pnl}")
                print("------------------")
        except Exception as e:
            self.logger.error(f"Error while fetching positions: {str(e)}", exc_info=True)

    async def execute_best_order(self, market: str, side: str, amount_usd: float):
        """Place an order at the best bid or ask price using BestOrderStrategy (now with real-time data)."""
        normalized_market_name = market.upper()
        if not normalized_market_name.endswith("-USD") and "-" not in normalized_market_name:
            normalized_market_name += "-USD"

        try:
            self.logger.info(f"[CLI] Delegating to BestOrderStrategy for {normalized_market_name} {side} {amount_usd} USD (using real-time prices).")
            await self.best_order_strategy.execute(
                market=normalized_market_name, 
                side=side, 
                amount_usd=amount_usd
            )
        except Exception as e:
            self.logger.error(f"Error placing order for {normalized_market_name}: {str(e)}", exc_info=True)

    def show_help(self):
        """Display help."""
        print("\nAvailable commands:")
        print("------------------")
        print("help                    - Show this help")
        print("load <m1> [m2...]     - Load real-time data streams for specified markets (e.g., load BTC ETH)")
        print("load?                   - Show currently loaded real-time market streams.")
        print("unload ALL              - Unload ALL currently active real-time data streams.")
        print("markets [N]             - Show all available markets, or top N by 24h volume")
        print("position [market]       - Show current position(s), optionally filtered by market")
        print("<market> BB <amount>    - Place a BUY order at the best bid price (e.g., BTC BB 1000)")
        print("<market> BA <amount>    - Place a SELL order at the best ask price (e.g., ETH BA 500)")
        print("close all               - Cancel all open orders.")
        print("exit                    - Exit the program")
        print("------------------")

    async def handle_load_command(self, markets_to_load: List[str]):
        """Handles the 'load' command to validate and start streams for specified markets."""
        if not markets_to_load:
            self.logger.info("[CLI] No markets specified for 'load'.")
            print("Usage: load <market1> [market2 ...]")
            return
        
        validated_markets_to_attempt_load = []
        self.logger.info(f"[CLI] Validating markets for 'load' command: {markets_to_load}")
        for m_name_raw in markets_to_load:
            m_upper = m_name_raw.upper()
            normalized_m_name = m_upper
            if not m_upper.endswith("-USD") and "-" not in m_upper:
                normalized_m_name += "-USD"
            
            try:
                market_info = await self.market_utils.get_market_object(normalized_m_name)
                if market_info and hasattr(market_info, 'name') and market_info.name == normalized_m_name:
                    validated_markets_to_attempt_load.append(normalized_m_name)
                    self.logger.debug(f"[CLI] Market {normalized_m_name} validated successfully.")
                else:
                    self.logger.warning(f"[CLI] Market {normalized_m_name} (raw: {m_name_raw}) not found or invalid according to MarketUtils.")
                    print(f"Warning: Market \"{normalized_m_name}\" not recognized or is invalid. Stream will not be loaded.")
            except Exception as e:
                self.logger.error(f"[CLI] Error validating market {normalized_m_name}: {e}", exc_info=True)
                print(f"Warning: Error validating market \"{normalized_m_name}\". Stream will not be loaded.")

        if not validated_markets_to_attempt_load:
            self.logger.info("[CLI] No valid markets found after validation to load streams for.")
            print("No valid markets specified or found to load.")
            return

        self.logger.info(f"[CLI] Requesting to load/start streams for validated markets: {validated_markets_to_attempt_load}")
        
        if self.realtime_provider_management_task and not self.realtime_provider_management_task.done():
            self.logger.warning("[CLI] Previous load/unload management task still running. Please wait.")
            print("INFO: Previous load/unload operation is still in progress. Please wait.")
            return 

        self.realtime_provider_management_task = asyncio.create_task(self.realtime_data_provider.start_streams(validated_markets_to_attempt_load))
        try:
            await self.realtime_provider_management_task
            print(f"Loading streams for: {', '.join(validated_markets_to_attempt_load)} initiated.") 
        except Exception as e:
            self.logger.error(f"[CLI] Error during load command management task: {e}", exc_info=True)
            print(f"Error encountered while trying to load streams for {', '.join(validated_markets_to_attempt_load)}.")

    async def handle_unload_command(self, markets_to_unload: List[str]):
        """Handles the 'unload' command to stop all active market data streams."""
        if not markets_to_unload or not (len(markets_to_unload) == 1 and markets_to_unload[0].upper() == "ALL"):
            self.logger.info("[CLI] Invalid 'unload' command. Only 'unload ALL' is supported.")
            print("Usage: unload ALL")
            return

        self.logger.info("[CLI] Requesting to unload ALL real-time streams.")
        if self.realtime_provider_management_task and not self.realtime_provider_management_task.done():
                self.logger.info("[CLI] Cancelling active load/unload management task before unloading ALL streams.")
                self.realtime_provider_management_task.cancel()
                try: await self.realtime_provider_management_task
                except asyncio.CancelledError: self.logger.info("[CLI] Management task cancelled before unload ALL.")
                except Exception as e_task: self.logger.error(f"[CLI] Error cancelling management task before unload ALL: {e_task}")
        
        await self.realtime_data_provider.close_streams() 
        print("All real-time data streams have been requested to stop.")

    async def handle_load_status_command(self):
        """Handles the 'load?' command to show active streams."""
        active_streams = self.realtime_data_provider.get_active_streams()
        if active_streams:
            print(f"Currently loaded real-time market streams: {', '.join(active_streams)}")
        else:
            print("No market streams are currently loaded.")

    async def handle_close_all_orders_command(self):
        """Handles the 'close all' command to cancel all open orders."""
        try:
            self.logger.info("[CLI] Attempting to cancel all open orders...")
            response = await self.trading_client.orders.mass_cancel(cancel_all=True)
            # Assuming response is an EmptyModel or similar upon success,
            # and raises an exception or has an error attribute on failure.
            # The x10 library might have a specific way to check for success.
            # For now, we'll assume no news is good news if no exception is raised.
            print("All open orders have been requested to be cancelled.")
            # If the response object has more details, you might want to log or print them.
            # For example, if response.is_success() or similar exists.
            # Or if it returns a count of cancelled orders.
            # Example: if hasattr(response, 'data') and response.data:
            # print(f"Cancellation response: {response.data}")

        except Exception as e:
            self.logger.error(f"Error cancelling all orders: {str(e)}", exc_info=True)
            print(f"An error occurred while trying to cancel all orders: {str(e)}")

    async def process_command(self, command: str):
        """Process a command."""
        parts = command.strip().split()
        if not parts:
            return

        cmd = parts[0].lower()

        if cmd == "exit":
            self.running = False
        elif cmd == "help":
            self.show_help()
        elif cmd == "load":
            markets_to_load = parts[1:]
            await self.handle_load_command(markets_to_load)
        elif cmd == "unload":
            markets_to_unload = parts[1:] # Should be ["ALL"]
            await self.handle_unload_command(markets_to_unload)
        elif cmd == "load?":
            await self.handle_load_status_command()
        elif cmd == "markets":
            if len(parts) == 2 and parts[1].isdigit(): 
                await self.show_markets(top_n=int(parts[1]))
            else:
                await self.show_markets()
        elif cmd == "position":
            market_arg = parts[1].upper() if len(parts) > 1 else None
            if market_arg and not market_arg.endswith("-USD") and "-" not in market_arg:
                 market_arg += "-USD"
            await self.show_position(market_arg)
        elif command.lower() == "close all":
            await self.handle_close_all_orders_command()
        elif len(parts) == 3 and parts[1].upper() in ("BB", "BA"):
            market = parts[0]
            side = parts[1].upper()
            try:
                amount_usd = float(parts[2])
                await self.execute_best_order(market, side, amount_usd)
            except ValueError as e:
                print(f"Format error for amount: {str(e)}")
        else:
            print("Unknown command. Type 'help' to see available commands.")

    async def run(self):
        """Run the interactive CLI."""
        print("Welcome to the Extended Exchange trading CLI!")
        # No automatic stream loading here anymore
        print("Type 'help' to see available commands or 'load <market(s)>' to start real-time data.")

        while self.running:
            try:
                command = await asyncio.to_thread(input, "\n> ")
                await self.process_command(command)
            except KeyboardInterrupt:
                print("\nProgram stopping by user request...")
                self.running = False
            except EOFError:
                print("\nEOF received, stopping program...")
                self.running = False
            except Exception as e:
                self.logger.error(f"Error in command processing: {str(e)}", exc_info=True)
        
        self.logger.info("[CLI] Exiting program. Starting cleanup...")
        
        if self.realtime_data_provider:
             self.logger.info("[CLI] Ensuring all real-time data streams are stopped on exit...")
             if self.realtime_provider_management_task and not self.realtime_provider_management_task.done():
                self.logger.info("[CLI] Cancelling active load/unload management task due to exit...")
                self.realtime_provider_management_task.cancel()
                try: await self.realtime_provider_management_task
                except asyncio.CancelledError: self.logger.info("[CLI] Management task cancelled on exit.")
                except Exception as e_task: self.logger.error(f"[CLI] Error cancelling management task on exit: {e_task}")
             
             await self.realtime_data_provider.close_streams()

        try:
            if self.trading_client:
                await self.trading_client.close()
                self.logger.info("[CLI] Trading client connection closed properly.")
        except Exception as e:
            self.logger.error(f"[CLI] Error during trading client cleanup: {str(e)}", exc_info=True)
        
        self.logger.info("[CLI] Cleanup finished. Goodbye!")

async def main():
    cli = TradingCLI()
    await cli.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # This is mostly for the case where asyncio.run() itself is interrupted before cli.run() handles it.
        print("\nProgram interrupted externally. Exiting.")
    except Exception as e:
        # Catch-all for unexpected errors during startup/shutdown outside of the main CLI loop
        logging.getLogger("main").error(f"Unhandled exception in main: {e}", exc_info=True) 