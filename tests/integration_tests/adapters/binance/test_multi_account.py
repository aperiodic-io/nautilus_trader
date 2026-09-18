# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Tests for running multiple Binance accounts (execution clients) against one cache.
"""

import pkgutil
from unittest.mock import AsyncMock

import msgspec
import pytest

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.adapters.binance.common.enums import BinanceFuturesPositionSide
from nautilus_trader.adapters.binance.config import BinanceExecClientConfig
from nautilus_trader.adapters.binance.futures.enums import BinanceFuturesEnumParser
from nautilus_trader.adapters.binance.futures.execution import BinanceFuturesExecutionClient
from nautilus_trader.adapters.binance.futures.providers import BinanceFuturesInstrumentProvider
from nautilus_trader.adapters.binance.futures.schemas.account import BinanceFuturesPositionRisk
from nautilus_trader.adapters.binance.futures.schemas.user import BinanceFuturesOrderUpdateWrapper
from nautilus_trader.adapters.binance.http.client import BinanceHttpClient
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.engine import ExecutionEngine
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.trading.strategy import Strategy


ETHUSDT_PERP_BINANCE = TestInstrumentProvider.ethusdt_perp_binance()
BTCUSDT_BINANCE = TestInstrumentProvider.btcusdt_binance()

PRIMARY_ACCOUNT_ID = AccountId("BINANCE-USDT_FUTURES-master")
SECONDARY_ACCOUNT_ID = AccountId("BINANCE2-USDT_FUTURES-master")

# Base64-encoded 32 zero bytes for an Ed25519 private key (test only)
DUMMY_API_SECRET = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class TestBinanceMultiAccountClients:
    @pytest.fixture(autouse=True)
    def setup(self, request):
        # Fixture Setup
        self.loop = request.getfixturevalue("event_loop")
        self.clock = LiveClock()
        self.trader_id = TestIdStubs.trader_id()
        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = TestComponentStubs.cache()
        self.cache.add_instrument(ETHUSDT_PERP_BINANCE)
        self.portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.exec_engine = ExecutionEngine(msgbus=self.msgbus, cache=self.cache, clock=self.clock)

        self.primary = self._make_client("BINANCE", "PRIMARY_KEY")
        self.secondary = self._make_client("BINANCE2", "SECONDARY_KEY")
        self.exec_engine.register_client(self.primary)
        self.exec_engine.register_client(self.secondary)

        self.strategy = Strategy()
        self.strategy.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )

    def _make_client(self, name: str, api_key: str) -> BinanceFuturesExecutionClient:
        http_client = BinanceHttpClient(
            clock=self.clock,
            api_key=api_key,
            api_secret=DUMMY_API_SECRET,
            base_url="https://fapi.binance.com/",
        )
        provider = BinanceFuturesInstrumentProvider(
            client=http_client,
            clock=self.clock,
            config=InstrumentProviderConfig(load_all=False),
        )
        return BinanceFuturesExecutionClient(
            loop=self.loop,
            client=http_client,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            instrument_provider=provider,
            base_url_ws="",
            config=BinanceExecClientConfig(),
            account_type=BinanceAccountType.USDT_FUTURES,
            environment=BinanceEnvironment.LIVE,
            api_key=api_key,
            api_secret=DUMMY_API_SECRET,
            name=name,
        )

    def _open_order(self, account_id: AccountId, price: str = "3000.00"):
        order = self.strategy.order_factory.limit(
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(1),
            price=Price.from_str(price),
        )
        self.cache.add_order(order, None)
        order.apply(TestEventStubs.order_submitted(order, account_id=account_id))
        self.cache.update_order(order)
        order.apply(TestEventStubs.order_accepted(order, account_id=account_id))
        self.cache.update_order(order)
        return order

    def _cancel_all_command(self) -> CancelAllOrders:
        return CancelAllOrders(
            trader_id=self.trader_id,
            strategy_id=self.strategy.id,
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            order_side=OrderSide.NO_ORDER_SIDE,
            command_id=UUID4(),
            ts_init=0,
        )

    def test_client_and_account_ids_derive_from_name(self):
        # Assert
        assert self.primary.id == ClientId("BINANCE")
        assert self.secondary.id == ClientId("BINANCE2")
        assert self.primary.account_id == PRIMARY_ACCOUNT_ID
        assert self.secondary.account_id == SECONDARY_ACCOUNT_ID
        assert self.primary.venue == self.secondary.venue

    def test_clients_use_their_own_http_credentials(self):
        # Assert
        assert self.primary._http_client.api_key == "PRIMARY_KEY"
        assert self.secondary._http_client.api_key == "SECONDARY_KEY"

    def test_both_clients_register_with_engine_for_same_venue(self):
        # Assert
        assert self.exec_engine._routing_map[self.primary.venue] == self.primary
        assert self.exec_engine._secondary_clients == {ClientId("BINANCE2"): self.primary.venue}

    def test_active_symbols_scoped_to_client_account(self):
        # Arrange
        self.cache.add_instrument(BTCUSDT_BINANCE)
        self._open_order(PRIMARY_ACCOUNT_ID)
        btc_order = self.strategy.order_factory.limit(
            instrument_id=BTCUSDT_BINANCE.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str("0.001000"),
            price=Price.from_str("10000.00"),
        )
        self.cache.add_order(btc_order, None)
        btc_order.apply(TestEventStubs.order_submitted(btc_order, account_id=SECONDARY_ACCOUNT_ID))
        self.cache.update_order(btc_order)
        btc_order.apply(TestEventStubs.order_accepted(btc_order, account_id=SECONDARY_ACCOUNT_ID))
        self.cache.update_order(btc_order)

        # Act, Assert
        assert self.primary._get_cache_active_symbols() == {"ETHUSDT-PERP"}
        assert self.secondary._get_cache_active_symbols() == {"BTCUSDT"}

    @pytest.mark.asyncio
    async def test_each_client_cancels_only_its_own_orders(self, mocker):
        # Arrange
        primary_orders = [self._open_order(PRIMARY_ACCOUNT_ID) for _ in range(2)]
        secondary_orders = [self._open_order(SECONDARY_ACCOUNT_ID) for _ in range(3)]
        primary_batch = mocker.patch.object(self.primary, "_cancel_orders_batch", AsyncMock())
        secondary_batch = mocker.patch.object(self.secondary, "_cancel_orders_batch", AsyncMock())
        mocker.patch.object(self.primary, "_cancel_orders_for_strategy", AsyncMock())
        mocker.patch.object(self.secondary, "_cancel_orders_for_strategy", AsyncMock())

        # Act - the engine sends a cancel all without client ID to both clients
        await self.primary._cancel_all_orders(self._cancel_all_command())
        await self.secondary._cancel_all_orders(self._cancel_all_command())

        # Assert
        assert sorted(o.client_order_id.value for o in primary_batch.call_args[0][1]) == sorted(
            o.client_order_id.value for o in primary_orders
        )
        assert sorted(o.client_order_id.value for o in secondary_batch.call_args[0][1]) == sorted(
            o.client_order_id.value for o in secondary_orders
        )

    @pytest.mark.asyncio
    async def test_cancel_all_with_no_orders_on_account_sends_nothing(self, mocker):
        # Arrange - only the secondary has orders
        self._open_order(SECONDARY_ACCOUNT_ID)
        primary_batch = mocker.patch.object(self.primary, "_cancel_orders_batch", AsyncMock())
        primary_individual = mocker.patch.object(
            self.primary,
            "_cancel_orders_for_strategy",
            AsyncMock(),
        )

        # Act
        await self.primary._cancel_all_orders(self._cancel_all_command())

        # Assert - nothing to cancel on the primary's own account
        primary_batch.assert_not_called()
        primary_individual.assert_not_called()


class TestBinanceMultiAccountPositionIds:
    @pytest.mark.parametrize(
        ("account_id", "expected"),
        [
            (PRIMARY_ACCOUNT_ID, "BTCUSDT-PERP.BINANCE-LONG"),
            (SECONDARY_ACCOUNT_ID, "BTCUSDT-PERP.BINANCE-LONG-BINANCE2"),
        ],
    )
    def test_liquidation_fill_report_position_id_per_account(self, mocker, account_id, expected):
        # Arrange
        raw = pkgutil.get_data(
            package="tests.integration_tests.adapters.binance.resources.ws_messages",
            resource="ws_futures_order_update_liquidation.json",
        )
        wrapper = msgspec.json.Decoder(BinanceFuturesOrderUpdateWrapper).decode(raw)
        instrument = TestInstrumentProvider.btcusdt_perp_binance()

        exec_client = mocker.MagicMock()
        exec_client.account_id = account_id
        exec_client.use_position_ids = True
        exec_client._cache.strategy_id_for_order.return_value = None
        exec_client._get_cached_instrument_id.return_value = instrument.id
        exec_client._instrument_provider.find.return_value = instrument
        exec_client._enum_parser.parse_binance_order_side.return_value = OrderSide.SELL
        exec_client._clock.timestamp_ns.return_value = 1759347763167000000

        # Act
        wrapper.data.o.handle_order_trade_update(exec_client)

        # Assert
        order_report = exec_client._send_order_status_report.call_args[0][0]
        fill_report = exec_client._send_fill_report.call_args[0][0]
        assert order_report.account_id == account_id
        assert fill_report.account_id == account_id
        assert fill_report.venue_position_id == PositionId(expected)

    @pytest.mark.parametrize(
        ("account_id", "expected"),
        [
            (PRIMARY_ACCOUNT_ID, "ETHUSDT-PERP.BINANCE-SHORT"),
            (SECONDARY_ACCOUNT_ID, "ETHUSDT-PERP.BINANCE-SHORT-BINANCE2"),
        ],
    )
    def test_position_status_report_position_id_per_account(self, account_id, expected):
        # Arrange
        position = BinanceFuturesPositionRisk(
            symbol="ETHUSDT",
            positionSide=BinanceFuturesPositionSide.SHORT,
            positionAmt="-1.000",
            entryPrice="3000.00",
            markPrice="3000.00",
            unRealizedProfit="0",
            liquidationPrice="0",
            isolatedMargin="0",
            updateTime=0,
        )

        # Act
        report = position.parse_to_position_status_report(
            account_id=account_id,
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            enum_parser=BinanceFuturesEnumParser(),
            report_id=UUID4(),
            ts_init=0,
        )

        # Assert
        assert report.account_id == account_id
        assert report.position_side == PositionSide.SHORT
        assert report.venue_position_id == PositionId(expected)

    def test_hedge_mode_positions_for_two_accounts_are_distinct(self):
        # Arrange
        reports = []
        for account_id in (PRIMARY_ACCOUNT_ID, SECONDARY_ACCOUNT_ID):
            position = BinanceFuturesPositionRisk(
                symbol="ETHUSDT",
                positionSide=BinanceFuturesPositionSide.LONG,
                positionAmt="1.000",
                entryPrice="3000.00",
                markPrice="3000.00",
                unRealizedProfit="0",
                liquidationPrice="0",
                isolatedMargin="0",
                updateTime=0,
            )
            reports.append(
                position.parse_to_position_status_report(
                    account_id=account_id,
                    instrument_id=ETHUSDT_PERP_BINANCE.id,
                    enum_parser=BinanceFuturesEnumParser(),
                    report_id=UUID4(),
                    ts_init=0,
                ),
            )

        # Assert
        assert reports[0].venue_position_id != reports[1].venue_position_id
