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
End-to-end tests for trading multiple accounts on the same venue from one node.

The fixture mirrors a live node with two venues (BINANCE and OKX), each with two
execution clients (one per account), all driven through a strategy, the risk engine,
the order emulator, the execution engine, and the portfolio. A venue with multiple
accounts has no default account: every command must resolve to an account.

"""

from decimal import Decimal

import pytest

from nautilus_trader.backtest.data_client import BacktestMarketDataClient
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.config import DataEngineConfig
from nautilus_trader.config import ExecEngineConfig
from nautilus_trader.config import RiskEngineConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.examples.algorithms.twap import TWAPExecAlgorithm
from nautilus_trader.execution.emulator import OrderEmulator
from nautilus_trader.execution.engine import ExecutionEngine
from nautilus_trader.execution.messages import BatchCancelOrders
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.messages import ModifyOrder
from nautilus_trader.execution.messages import QueryAccount
from nautilus_trader.execution.messages import QueryOrder
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.execution.messages import SubmitOrderList
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import TradingState
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ExecAlgorithmId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import AccountBalance
from nautilus_trader.model.objects import MarginBalance
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.risk.engine import RiskEngine
from nautilus_trader.test_kit.mocks.cache_database import MockCacheDatabase
from nautilus_trader.test_kit.mocks.exec_clients import MockExecutionClient
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.trading.strategy import Strategy


BINANCE = Venue("BINANCE")
OKX = Venue("OKX")

ETHUSDT_PERP_BINANCE = TestInstrumentProvider.ethusdt_perp_binance()
ETHUSDT_SWAP_OKX = CryptoPerpetual.from_dict(
    {
        **CryptoPerpetual.to_dict(ETHUSDT_PERP_BINANCE),
        "id": "ETH-USDT-SWAP.OKX",
        "raw_symbol": "ETH-USDT-SWAP",
    },
)
BTCUSDT_BINANCE = TestInstrumentProvider.btcusdt_binance()

# Client IDs follow the node config keys; account ID issuers must equal the client IDs
BINANCE1_CLIENT_ID = ClientId("BINANCE1")
BINANCE2_CLIENT_ID = ClientId("BINANCE2")
OKX1_CLIENT_ID = ClientId("OKX1")
OKX2_CLIENT_ID = ClientId("OKX2")

BINANCE1_ACCOUNT_ID = AccountId("BINANCE1-USDT_FUTURES-master")
BINANCE2_ACCOUNT_ID = AccountId("BINANCE2-USDT_FUTURES-master")
OKX1_ACCOUNT_ID = AccountId("OKX1-master")
OKX2_ACCOUNT_ID = AccountId("OKX2-master")

ACCOUNT_FOR_CLIENT = {
    BINANCE1_CLIENT_ID: BINANCE1_ACCOUNT_ID,
    BINANCE2_CLIENT_ID: BINANCE2_ACCOUNT_ID,
    OKX1_CLIENT_ID: OKX1_ACCOUNT_ID,
    OKX2_CLIENT_ID: OKX2_ACCOUNT_ID,
}


class _RecordingExecutionClient(MockExecutionClient):
    # The mock client does not record every command type, so record the remainder here

    def batch_cancel_orders(self, command) -> None:
        self.calls.append("batch_cancel_orders")
        self.commands.append(command)

    def query_account(self, command) -> None:
        self.calls.append("query_account")
        self.commands.append(command)

    def query_order(self, command) -> None:
        self.calls.append("query_order")
        self.commands.append(command)


def _margin_account_state(account_id: AccountId) -> AccountState:
    return AccountState(
        account_id=account_id,
        account_type=AccountType.MARGIN,
        base_currency=None,
        reported=True,
        balances=[
            AccountBalance(
                Money(1_000_000, USDT),
                Money(0, USDT),
                Money(1_000_000, USDT),
            ),
        ],
        margins=[
            MarginBalance(
                Money(0, USDT),
                Money(0, USDT),
            ),
        ],
        info={},
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )


def _cash_account_state(account_id: AccountId, free_usdt: int) -> AccountState:
    return AccountState(
        account_id=account_id,
        account_type=AccountType.CASH,
        base_currency=None,
        reported=True,
        balances=[
            AccountBalance(
                Money(free_usdt, USDT),
                Money(0, USDT),
                Money(free_usdt, USDT),
            ),
        ],
        margins=[],
        info={},
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )


class _MultiAccountFixture:
    oms_type = "NETTING"

    def setup(self) -> None:
        # Fixture Setup
        self.clock = TestClock()
        self.trader_id = TestIdStubs.trader_id()

        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = Cache(database=MockCacheDatabase())
        self.cache.add_instrument(ETHUSDT_PERP_BINANCE)
        self.cache.add_instrument(ETHUSDT_SWAP_OKX)

        self.portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.data_engine = DataEngine(
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            config=DataEngineConfig(debug=True),
        )
        self.exec_engine = ExecutionEngine(
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            config=ExecEngineConfig(debug=True),
        )
        self.risk_engine = RiskEngine(
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            config=RiskEngineConfig(debug=True),
        )
        self.emulator = OrderEmulator(
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )

        for venue in (BINANCE, OKX):
            self.data_engine.register_client(
                BacktestMarketDataClient(
                    client_id=ClientId(venue.value),
                    msgbus=self.msgbus,
                    cache=self.cache,
                    clock=self.clock,
                ),
            )

        self.clients: dict[ClientId, _RecordingExecutionClient] = {}
        for client_id, venue in (
            (BINANCE1_CLIENT_ID, BINANCE),
            (OKX1_CLIENT_ID, OKX),
            (BINANCE2_CLIENT_ID, BINANCE),
            (OKX2_CLIENT_ID, OKX),
        ):
            client = self._make_client(client_id, venue)
            self.clients[client_id] = client
            self.exec_engine.register_client(client)

        self._update_accounts()

        self.strategy = Strategy(StrategyConfig(oms_type=self.oms_type))
        self.strategy.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        self.exec_engine.register_oms_type(self.strategy)

        self.data_engine.start()
        self.risk_engine.start()
        self.exec_engine.start()
        self.emulator.start()
        self.strategy.start()

        self._venue_order_id_count = 0

    def _make_client(self, client_id: ClientId, venue: Venue) -> _RecordingExecutionClient:
        return _RecordingExecutionClient(
            client_id=client_id,
            venue=venue,
            account_type=AccountType.MARGIN,
            base_currency=None,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            oms_type=OmsType.NETTING,
        )

    def _update_accounts(self) -> None:
        for account_id in ACCOUNT_FOR_CLIENT.values():
            self.portfolio.update_account(_margin_account_state(account_id))

    # -- Helpers ------------------------------------------------------------------------------

    def _calls(self, client_id: ClientId) -> list[str]:
        return [c for c in self.clients[client_id].calls if c != "_start"]

    def _market(
        self,
        instrument_id: InstrumentId = ETHUSDT_PERP_BINANCE.id,
        side: OrderSide = OrderSide.BUY,
        quantity: str = "1.000",
    ) -> Order:
        return self.strategy.order_factory.market(
            instrument_id,
            side,
            Quantity.from_str(quantity),
        )

    def _limit(
        self,
        instrument_id: InstrumentId = ETHUSDT_PERP_BINANCE.id,
        side: OrderSide = OrderSide.BUY,
        quantity: str = "1.000",
        price: str = "1000.00",
        emulation_trigger: TriggerType = TriggerType.NO_TRIGGER,
    ) -> Order:
        return self.strategy.order_factory.limit(
            instrument_id,
            side,
            Quantity.from_str(quantity),
            Price.from_str(price),
            emulation_trigger=emulation_trigger,
        )

    def _next_venue_order_id(self) -> VenueOrderId:
        self._venue_order_id_count += 1
        return VenueOrderId(f"V-{self._venue_order_id_count}")

    def _accept(self, order: Order, account_id: AccountId) -> None:
        # Simulate the venue acknowledging the order for the account
        self.exec_engine.process(TestEventStubs.order_submitted(order, account_id=account_id))
        self.exec_engine.process(
            TestEventStubs.order_accepted(
                order,
                account_id=account_id,
                venue_order_id=self._next_venue_order_id(),
            ),
        )

    def _fill(
        self,
        order: Order,
        account_id: AccountId,
        last_px: str = "1000.00",
        last_qty: str | None = None,
    ) -> None:
        if order.status == OrderStatus.INITIALIZED:
            self._accept(order, account_id)

        instrument = self.cache.instrument(order.instrument_id)
        self._venue_order_id_count += 1
        self.exec_engine.process(
            TestEventStubs.order_filled(
                order,
                instrument,
                account_id=account_id,
                venue_order_id=order.venue_order_id,
                trade_id=TradeId(f"T-{self._venue_order_id_count}"),
                last_px=Price.from_str(last_px),
                last_qty=Quantity.from_str(last_qty) if last_qty else None,
            ),
        )

    def _open(
        self,
        client_id: ClientId,
        instrument_id: InstrumentId = ETHUSDT_PERP_BINANCE.id,
        side: OrderSide = OrderSide.BUY,
        quantity: str = "1.000",
    ) -> Order:
        order = self._market(instrument_id, side, quantity)
        self.strategy.submit_order(order, client_id=client_id)
        self._fill(order, ACCOUNT_FOR_CLIENT[client_id])
        return order

    def _netting_position_id(
        self,
        client_id: ClientId,
        instrument_id: InstrumentId = ETHUSDT_PERP_BINANCE.id,
    ) -> PositionId:
        # On a venue with multiple accounts, every NETTING position ID carries the account
        return PositionId(f"{instrument_id}-{self.strategy.id}-{client_id}")

    def _cancel_order_command(self, order: Order, client_id: ClientId | None = None) -> CancelOrder:
        return CancelOrder(
            trader_id=self.trader_id,
            strategy_id=self.strategy.id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            command_id=UUID4(),
            ts_init=0,
            client_id=client_id,
        )

    def _open_limit_orders(self, *client_ids: ClientId) -> dict[ClientId, Order]:
        orders = {}
        for client_id in client_ids:
            instrument_id = (
                ETHUSDT_SWAP_OKX.id
                if client_id in (OKX1_CLIENT_ID, OKX2_CLIENT_ID)
                else ETHUSDT_PERP_BINANCE.id
            )
            order = self._limit(instrument_id)
            self.strategy.submit_order(order, client_id=client_id)
            self._accept(order, ACCOUNT_FOR_CLIENT[client_id])
            self.clients[client_id].calls.clear()
            orders[client_id] = order
        return orders

    def _clear_calls(self) -> None:
        for client in self.clients.values():
            client.calls.clear()


class TestMultiAccountRegistration(_MultiAccountFixture):
    def test_multi_account_venues_have_no_venue_routing(self) -> None:
        # Assert - no default account for a venue with multiple accounts
        assert BINANCE not in self.exec_engine._routing_map
        assert OKX not in self.exec_engine._routing_map
        assert self.exec_engine._is_multi_account_venue(BINANCE)
        assert self.exec_engine._is_multi_account_venue(OKX)
        assert self.exec_engine._venue_client(BINANCE) is None
        assert self.exec_engine._venue_client(OKX) is None

    def test_all_clients_registered_for_their_venue(self) -> None:
        # Assert
        assert [c.id for c in self.exec_engine._venue_clients[BINANCE]] == [
            BINANCE1_CLIENT_ID,
            BINANCE2_CLIENT_ID,
        ]
        assert [c.id for c in self.exec_engine._venue_clients[OKX]] == [
            OKX1_CLIENT_ID,
            OKX2_CLIENT_ID,
        ]
        assert sorted(c.value for c in self.exec_engine.registered_clients) == [
            "BINANCE1",
            "BINANCE2",
            "OKX1",
            "OKX2",
        ]

    def test_duplicate_client_id_still_rejected(self) -> None:
        # Arrange
        duplicate = self._make_client(BINANCE2_CLIENT_ID, BINANCE)

        # Act, Assert
        with pytest.raises(KeyError):
            self.exec_engine.register_client(duplicate)

    def test_client_named_after_venue_rejected_on_multi_account_venue(self) -> None:
        # Arrange
        client = self._make_client(ClientId("BINANCE"), BINANCE)

        # Act, Assert
        with pytest.raises(ValueError, match="no default account"):
            self.exec_engine.register_client(client)
        assert ClientId("BINANCE") not in self.exec_engine._clients
        assert len(self.exec_engine._venue_clients[BINANCE]) == 2

    def test_second_client_rejected_when_first_is_named_after_venue(self) -> None:
        # Arrange
        engine = ExecutionEngine(
            msgbus=MessageBus(trader_id=self.trader_id, clock=self.clock),
            cache=self.cache,
            clock=self.clock,
        )
        engine.register_client(self._make_client(ClientId("BINANCE"), BINANCE))

        # Act, Assert
        with pytest.raises(ValueError, match="no default account"):
            engine.register_client(self._make_client(ClientId("BINANCE2"), BINANCE))
        assert engine._routing_map[BINANCE].id == ClientId("BINANCE")

    def test_single_client_named_after_venue_is_allowed(self) -> None:
        # Arrange
        engine = ExecutionEngine(
            msgbus=MessageBus(trader_id=self.trader_id, clock=self.clock),
            cache=self.cache,
            clock=self.clock,
        )

        # Act
        engine.register_client(self._make_client(ClientId("BINANCE"), BINANCE))

        # Assert
        assert engine._routing_map[BINANCE].id == ClientId("BINANCE")
        assert not engine._is_multi_account_venue(BINANCE)

    @pytest.mark.parametrize("name", ["BINANCE-2", "BINANCE-A-B"])
    def test_hyphenated_client_id_rejected_for_venue_client(self, name: str) -> None:
        # Arrange
        client = self._make_client(ClientId(name), BINANCE)

        # Act, Assert
        with pytest.raises(ValueError, match="must not contain '-'"):
            self.exec_engine.register_client(client)

    def test_hyphenated_client_id_allowed_for_venueless_client(self) -> None:
        # Arrange - multi-venue brokers (e.g. IB) route by account and have no venue
        client = _RecordingExecutionClient(
            client_id=ClientId("IB-PAPER"),
            venue=None,
            account_type=AccountType.MARGIN,
            base_currency=None,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )

        # Act
        self.exec_engine.register_client(client)

        # Assert
        assert self.exec_engine.default_client == ClientId("IB-PAPER")

    def test_third_account_for_venue(self) -> None:
        # Arrange
        client = self._make_client(ClientId("BINANCE3"), BINANCE)

        # Act
        self.exec_engine.register_client(client)

        # Assert
        assert len(self.exec_engine._venue_clients[BINANCE]) == 3
        assert BINANCE not in self.exec_engine._routing_map

    def test_register_venue_routing_rejected_for_multi_account_venue(self) -> None:
        # Act, Assert
        with pytest.raises(ValueError, match="no default account"):
            self.exec_engine.register_venue_routing(self.clients[BINANCE1_CLIENT_ID], BINANCE)
        assert BINANCE not in self.exec_engine._routing_map

    def test_default_client_never_used_for_multi_account_venue(self) -> None:
        # Arrange
        default = _RecordingExecutionClient(
            client_id=ClientId("DEFAULT"),
            venue=None,
            account_type=AccountType.MARGIN,
            base_currency=None,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        self.exec_engine.register_client(default)
        order = self._market()

        # Act
        self.strategy.submit_order(order)

        # Assert
        assert order.status == OrderStatus.DENIED
        assert default.calls == []

    def test_deregister_down_to_single_account_restores_venue_routing(self) -> None:
        # Act
        self.exec_engine.deregister_client(self.clients[BINANCE1_CLIENT_ID])

        # Assert
        assert self.exec_engine._routing_map[BINANCE] == self.clients[BINANCE2_CLIENT_ID]
        assert not self.exec_engine._is_multi_account_venue(BINANCE)

    def test_deregister_all_clients_of_venue(self) -> None:
        # Act
        self.exec_engine.deregister_client(self.clients[BINANCE1_CLIENT_ID])
        self.exec_engine.deregister_client(self.clients[BINANCE2_CLIENT_ID])

        # Assert
        assert BINANCE not in self.exec_engine._routing_map
        assert BINANCE not in self.exec_engine._venue_clients

    def test_get_clients_for_orders_resolves_client_of_each_order(self) -> None:
        # Arrange
        pinned = self._limit()
        self.strategy.submit_order(pinned, client_id=OKX2_CLIENT_ID)
        by_account = self._limit()
        self.cache.add_order(by_account)
        self._accept(by_account, BINANCE2_ACCOUNT_ID)
        unresolved = self._limit(ETHUSDT_SWAP_OKX.id)
        self.cache.add_order(unresolved)

        # Act
        clients = self.exec_engine.get_clients_for_orders([pinned, by_account, unresolved])

        # Assert - an order that resolves to no account adds no client
        assert clients == {
            self.clients[OKX2_CLIENT_ID],
            self.clients[BINANCE2_CLIENT_ID],
        }


class TestMultiAccountOrderRouting(_MultiAccountFixture):
    @pytest.mark.parametrize(
        ("client_id", "instrument_id"),
        [
            (BINANCE1_CLIENT_ID, ETHUSDT_PERP_BINANCE.id),
            (BINANCE2_CLIENT_ID, ETHUSDT_PERP_BINANCE.id),
            (OKX1_CLIENT_ID, ETHUSDT_SWAP_OKX.id),
            (OKX2_CLIENT_ID, ETHUSDT_SWAP_OKX.id),
        ],
    )
    def test_submit_order_with_client_id_routes_to_that_client_only(
        self,
        client_id: ClientId,
        instrument_id: InstrumentId,
    ) -> None:
        # Arrange
        order = self._market(instrument_id)

        # Act
        self.strategy.submit_order(order, client_id=client_id)

        # Assert
        for other_id in self.clients:
            expected = ["submit_order"] if other_id == client_id else []
            assert self._calls(other_id) == expected, other_id
        assert self.cache.client_id(order.client_order_id) == client_id

    @pytest.mark.parametrize("instrument_id", [ETHUSDT_PERP_BINANCE.id, ETHUSDT_SWAP_OKX.id])
    def test_submit_order_without_client_id_is_denied(self, instrument_id: InstrumentId) -> None:
        # Arrange
        order = self._market(instrument_id)

        # Act
        self.strategy.submit_order(order)

        # Assert - no default account
        assert order.status == OrderStatus.DENIED
        assert "AMBIGUOUS_ACCOUNT" in order.last_event.reason
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_submit_order_with_client_named_after_venue_is_denied(self) -> None:
        # Arrange - the venue-named client does not exist on a multi-account venue
        order = self._market()

        # Act
        self.strategy.submit_order(order, client_id=ClientId("BINANCE"))

        # Assert
        assert order.status == OrderStatus.DENIED
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_order_list_without_client_id_is_denied(self) -> None:
        # Arrange
        bracket = self.strategy.order_factory.bracket(
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str("1.000"),
            sl_trigger_price=Price.from_str("900.00"),
            tp_price=Price.from_str("1100.00"),
        )

        # Act
        self.strategy.submit_order_list(bracket)

        # Assert
        assert all(o.status == OrderStatus.DENIED for o in bracket.orders)
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_orders_are_indexed_by_account_after_submission(self) -> None:
        # Arrange
        orders = self._open_limit_orders(BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID)

        # Assert
        assert self.cache.orders_open(account_id=BINANCE1_ACCOUNT_ID) == [
            orders[BINANCE1_CLIENT_ID],
        ]
        assert self.cache.orders_open(account_id=BINANCE2_ACCOUNT_ID) == [
            orders[BINANCE2_CLIENT_ID],
        ]
        assert len(self.cache.orders_open(venue=BINANCE)) == 2

    @pytest.mark.parametrize("client_id", [BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID])
    def test_cancel_order_without_client_id_routes_to_account_of_order(
        self,
        client_id: ClientId,
    ) -> None:
        # Arrange
        order = self._open_limit_orders(client_id)[client_id]

        # Act
        self.strategy.cancel_order(order)

        # Assert
        for other_id in self.clients:
            expected = ["cancel_order"] if other_id == client_id else []
            assert self._calls(other_id) == expected, other_id

    def test_cancel_order_before_acknowledgement_uses_cached_client(self) -> None:
        # Arrange - order submitted to BINANCE2, but no venue acknowledgement yet
        order = self._limit()
        self.strategy.submit_order(order, client_id=BINANCE2_CLIENT_ID)
        cancel = self._cancel_order_command(order)

        # Act
        self.exec_engine.execute(cancel)

        # Assert
        assert cancel.client_id == ClientId("BINANCE")  # Default filled in from the venue
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order", "cancel_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_cancel_order_for_unknown_order_reaches_no_client(self) -> None:
        # Arrange
        order = self._limit()  # Never submitted, so not in the cache

        # Act
        self.exec_engine.execute(self._cancel_order_command(order))

        # Assert - no default account to fall back to
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_cancel_order_with_explicit_client_id_is_honored(self) -> None:
        # Arrange
        order = self._open_limit_orders(BINANCE1_CLIENT_ID)[BINANCE1_CLIENT_ID]

        # Act
        self.strategy.cancel_order(order, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["cancel_order"]

    @pytest.mark.parametrize("client_id", [BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID])
    def test_modify_order_without_client_id_routes_to_account_of_order(
        self,
        client_id: ClientId,
    ) -> None:
        # Arrange
        order = self._open_limit_orders(client_id)[client_id]

        # Act
        self.strategy.modify_order(order, price=Price.from_str("999.00"))

        # Assert
        for other_id in self.clients:
            expected = ["modify_order"] if other_id == client_id else []
            assert self._calls(other_id) == expected, other_id

    def test_modify_order_known_only_by_account_routes_to_account(self) -> None:
        # Arrange - order known only by its account (e.g. an external order after restart)
        order = self._limit()
        self.cache.add_order(order)
        self._accept(order, BINANCE2_ACCOUNT_ID)
        modify = ModifyOrder(
            trader_id=self.trader_id,
            strategy_id=self.strategy.id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            quantity=Quantity.from_str("0.500"),
            price=None,
            trigger_price=None,
            command_id=UUID4(),
            ts_init=0,
        )

        # Act
        self.exec_engine.execute(modify)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["modify_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_query_order_routes_to_account_of_order(self) -> None:
        # Arrange
        order = self._open_limit_orders(OKX2_CLIENT_ID)[OKX2_CLIENT_ID]
        query = QueryOrder(
            trader_id=self.trader_id,
            strategy_id=self.strategy.id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            command_id=UUID4(),
            ts_init=0,
        )

        # Act
        self.exec_engine.execute(query)

        # Assert
        assert self._calls(OKX2_CLIENT_ID) == ["query_order"]
        assert self._calls(OKX1_CLIENT_ID) == []

    @pytest.mark.parametrize("client_id", list(ACCOUNT_FOR_CLIENT))
    def test_query_account_routes_by_account_issuer(self, client_id: ClientId) -> None:
        # Arrange
        query = QueryAccount(
            trader_id=self.trader_id,
            account_id=ACCOUNT_FOR_CLIENT[client_id],
            command_id=UUID4(),
            ts_init=0,
        )

        # Act
        self.exec_engine.execute(query)

        # Assert
        for other_id in self.clients:
            expected = ["query_account"] if other_id == client_id else []
            assert self._calls(other_id) == expected, other_id

    def test_cancel_all_orders_without_client_id_goes_to_accounts_with_orders(self) -> None:
        # Arrange
        self._open_limit_orders(*self.clients)

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_PERP_BINANCE.id)

        # Assert
        assert self._calls(BINANCE1_CLIENT_ID) == ["cancel_all_orders"]
        assert self._calls(BINANCE2_CLIENT_ID) == ["cancel_all_orders"]
        assert self._calls(OKX1_CLIENT_ID) == []
        assert self._calls(OKX2_CLIENT_ID) == []

    def test_cancel_all_orders_skips_accounts_without_strategy_orders(self) -> None:
        # Arrange - only BINANCE2 has working orders for the strategy
        self._open_limit_orders(BINANCE2_CLIENT_ID)

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_PERP_BINANCE.id)

        # Assert - BINANCE1 is not sent a cancel all (it may cancel every order on the venue)
        assert self._calls(BINANCE2_CLIENT_ID) == ["cancel_all_orders"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_cancel_all_orders_ignores_other_strategies_orders_when_targeting(self) -> None:
        # Arrange - another strategy has an order on BINANCE1
        other = Strategy(StrategyConfig(strategy_id="S-OTHER", oms_type="NETTING"))
        other.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        other_order = other.order_factory.limit(
            ETHUSDT_PERP_BINANCE.id,
            OrderSide.BUY,
            Quantity.from_str("1.000"),
            Price.from_str("1000.00"),
        )
        other.submit_order(other_order, client_id=BINANCE1_CLIENT_ID)
        self._accept(other_order, BINANCE1_ACCOUNT_ID)
        self._open_limit_orders(BINANCE2_CLIENT_ID)
        self._clear_calls()

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_PERP_BINANCE.id)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["cancel_all_orders"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_cancel_all_orders_with_no_orders_reaches_no_client(self) -> None:
        # Arrange
        command = CancelAllOrders(
            trader_id=self.trader_id,
            strategy_id=self.strategy.id,
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            order_side=OrderSide.NO_ORDER_SIDE,
            command_id=UUID4(),
            ts_init=0,
        )

        # Act
        self.exec_engine.execute(command)

        # Assert
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_cancel_all_orders_with_client_id_goes_to_that_client_only(self) -> None:
        # Arrange
        self._open_limit_orders(*self.clients)

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_SWAP_OKX.id, client_id=OKX2_CLIENT_ID)

        # Assert
        assert self._calls(OKX2_CLIENT_ID) == ["cancel_all_orders"]
        assert self._calls(OKX1_CLIENT_ID) == []
        assert self._calls(BINANCE1_CLIENT_ID) == []
        assert self._calls(BINANCE2_CLIENT_ID) == []

    def test_cancel_all_orders_with_client_id_leaves_other_accounts_orders_untouched(self) -> None:
        # Arrange
        orders = self._open_limit_orders(BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID)

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_PERP_BINANCE.id, client_id=BINANCE2_CLIENT_ID)

        # Assert - only BINANCE2's order is marked pending cancel
        assert orders[BINANCE2_CLIENT_ID].status == OrderStatus.PENDING_CANCEL
        assert orders[BINANCE1_CLIENT_ID].status == OrderStatus.ACCEPTED
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_cancel_all_orders_for_venue_with_single_client_is_unchanged(self) -> None:
        # Arrange
        self.exec_engine.deregister_client(self.clients[OKX2_CLIENT_ID])

        # Act - no working orders, but the venue's client still receives the command
        self.exec_engine.execute(
            CancelAllOrders(
                trader_id=self.trader_id,
                strategy_id=self.strategy.id,
                instrument_id=ETHUSDT_SWAP_OKX.id,
                order_side=OrderSide.NO_ORDER_SIDE,
                command_id=UUID4(),
                ts_init=0,
            ),
        )

        # Assert
        assert self._calls(OKX1_CLIENT_ID) == ["cancel_all_orders"]

    def test_batch_cancel_across_accounts_is_split_per_client(self) -> None:
        # Arrange
        orders = {}
        for client_id in (BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID):
            orders[client_id] = [self._limit(), self._limit()]
            for order in orders[client_id]:
                self.strategy.submit_order(order, client_id=client_id)
                self._accept(order, ACCOUNT_FOR_CLIENT[client_id])

        # Act
        self.strategy.cancel_orders(orders[BINANCE1_CLIENT_ID] + orders[BINANCE2_CLIENT_ID])

        # Assert
        for client_id in (BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID):
            batch = self.clients[client_id].commands[-1]
            assert isinstance(batch, BatchCancelOrders)
            assert batch.client_id == client_id
            assert [c.client_order_id for c in batch.cancels] == [
                o.client_order_id for o in orders[client_id]
            ]

    def test_batch_cancel_skips_orders_that_resolve_to_no_account(self) -> None:
        # Arrange
        resolved = self._open_limit_orders(BINANCE2_CLIENT_ID)[BINANCE2_CLIENT_ID]
        unresolved = self._limit()
        self.cache.add_order(unresolved)
        command = BatchCancelOrders(
            trader_id=self.trader_id,
            strategy_id=self.strategy.id,
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            cancels=[self._cancel_order_command(o) for o in (resolved, unresolved)],
            command_id=UUID4(),
            ts_init=0,
        )

        # Act
        self.exec_engine.execute(command)

        # Assert
        batch = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert [c.client_order_id for c in batch.cancels] == [resolved.client_order_id]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_batch_cancel_for_single_account_is_sent_unchanged(self) -> None:
        # Arrange
        orders = [self._limit(), self._limit()]
        for order in orders:
            self.strategy.submit_order(order, client_id=BINANCE2_CLIENT_ID)
            self._accept(order, BINANCE2_ACCOUNT_ID)

        # Act
        self.strategy.cancel_orders(orders, client_id=BINANCE2_CLIENT_ID)

        # Assert
        batch = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert isinstance(batch, BatchCancelOrders)
        assert len(batch.cancels) == 2
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_order_list_with_client_id_routes_to_that_client(self) -> None:
        # Arrange
        bracket = self.strategy.order_factory.bracket(
            instrument_id=ETHUSDT_PERP_BINANCE.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str("1.000"),
            sl_trigger_price=Price.from_str("900.00"),
            tp_price=Price.from_str("1100.00"),
        )

        # Act
        self.strategy.submit_order_list(bracket, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order_list"]
        assert self._calls(BINANCE1_CLIENT_ID) == []
        assert isinstance(self.clients[BINANCE2_CLIENT_ID].commands[-1], SubmitOrderList)
        for order in bracket.orders:
            assert self.cache.client_id(order.client_order_id) == BINANCE2_CLIENT_ID

    def test_emulated_order_is_released_to_its_client(self) -> None:
        # Arrange
        order = self._limit(emulation_trigger=TriggerType.LAST_PRICE)
        self.strategy.submit_order(order, client_id=BINANCE2_CLIENT_ID)
        assert self._calls(BINANCE2_CLIENT_ID) == []  # Held by the emulator

        # Act - trade through the limit price triggers the release
        self.data_engine.process(
            TestDataStubs.trade_tick(instrument=ETHUSDT_PERP_BINANCE, price=999.0),
        )

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_emulated_order_without_client_id_is_denied_on_release(self) -> None:
        # Arrange
        order = self._limit(emulation_trigger=TriggerType.LAST_PRICE)
        self.strategy.submit_order(order)

        # Act
        self.data_engine.process(
            TestDataStubs.trade_tick(instrument=ETHUSDT_PERP_BINANCE, price=999.0),
        )

        # Assert
        released = self.cache.order(order.client_order_id)
        assert released.status == OrderStatus.DENIED
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_cancel_of_emulated_order_does_not_reach_any_client(self) -> None:
        # Arrange
        order = self._limit(emulation_trigger=TriggerType.LAST_PRICE)
        self.strategy.submit_order(order, client_id=BINANCE2_CLIENT_ID)

        # Act
        self.strategy.cancel_order(order)

        # Assert
        assert order.status == OrderStatus.CANCELED
        for client_id in self.clients:
            assert self._calls(client_id) == [], client_id

    def test_cancel_all_with_client_id_cancels_only_that_clients_emulated_orders(self) -> None:
        # Arrange
        order1 = self._limit(emulation_trigger=TriggerType.LAST_PRICE)
        order2 = self._limit(emulation_trigger=TriggerType.LAST_PRICE)
        self.strategy.submit_order(order1, client_id=BINANCE1_CLIENT_ID)
        self.strategy.submit_order(order2, client_id=BINANCE2_CLIENT_ID)

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_PERP_BINANCE.id, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert order2.status == OrderStatus.CANCELED
        assert order1.status == OrderStatus.EMULATED


class TestMultiAccountNettingPositions(_MultiAccountFixture):
    def test_same_strategy_and_instrument_has_one_position_per_account(self) -> None:
        # Act
        for client_id in self.clients:
            instrument_id = (
                ETHUSDT_PERP_BINANCE.id
                if client_id in (BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID)
                else ETHUSDT_SWAP_OKX.id
            )
            self._open(client_id, instrument_id)

        # Assert
        assert len(self.cache.positions_open()) == 4
        for client_id, account_id in ACCOUNT_FOR_CLIENT.items():
            instrument_id = (
                ETHUSDT_PERP_BINANCE.id
                if client_id in (BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID)
                else ETHUSDT_SWAP_OKX.id
            )
            position = self.cache.position(self._netting_position_id(client_id, instrument_id))
            assert position is not None, client_id
            assert position.account_id == account_id
            assert position.quantity == Quantity.from_str("1.000")

    @pytest.mark.parametrize("client_id", [BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID])
    def test_every_account_position_id_is_suffixed_with_client_id(
        self,
        client_id: ClientId,
    ) -> None:
        # Act
        self._open(client_id)

        # Assert - no account has the plain (default) position ID
        position = self.cache.positions_open()[0]
        assert position.id == PositionId(
            f"{ETHUSDT_PERP_BINANCE.id}-{self.strategy.id}-{client_id}",
        )

    def test_position_ids_do_not_depend_on_registration_order(self) -> None:
        # Arrange - rebuild with the clients registered in reverse order
        engine_ids = []
        for order in (
            (BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID),
            (BINANCE2_CLIENT_ID, BINANCE1_CLIENT_ID),
        ):
            engine = ExecutionEngine(
                msgbus=MessageBus(trader_id=self.trader_id, clock=self.clock),
                cache=Cache(database=MockCacheDatabase()),
                clock=self.clock,
            )
            for client_id in order:
                engine.register_client(self._make_client(client_id, BINANCE))
            fill = TestEventStubs.order_filled(
                self._market(),
                ETHUSDT_PERP_BINANCE,
                strategy_id=self.strategy.id,
                account_id=BINANCE2_ACCOUNT_ID,
            )
            engine_ids.append(engine._determine_netting_position_id(fill))

        # Assert
        assert engine_ids[0] == engine_ids[1]
        assert engine_ids[0] == PositionId(
            f"{ETHUSDT_PERP_BINANCE.id}-{self.strategy.id}-BINANCE2",
        )

    def test_opposite_positions_on_two_accounts_do_not_net(self) -> None:
        # Act
        self._open(BINANCE1_CLIENT_ID, side=OrderSide.BUY, quantity="2.000")
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL, quantity="0.500")

        # Assert
        first = self.cache.position(self._netting_position_id(BINANCE1_CLIENT_ID))
        second = self.cache.position(self._netting_position_id(BINANCE2_CLIENT_ID))
        assert first.side == PositionSide.LONG
        assert first.quantity == Quantity.from_str("2.000")
        assert second.side == PositionSide.SHORT
        assert second.quantity == Quantity.from_str("0.500")

    def test_portfolio_net_position_per_account_and_in_aggregate(self) -> None:
        # Act
        self._open(BINANCE1_CLIENT_ID, side=OrderSide.BUY, quantity="2.000")
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL, quantity="0.500")

        # Assert
        instrument_id = ETHUSDT_PERP_BINANCE.id
        assert self.portfolio.net_position(instrument_id, BINANCE1_ACCOUNT_ID) == Decimal("2.000")
        assert self.portfolio.net_position(instrument_id, BINANCE2_ACCOUNT_ID) == Decimal("-0.500")
        assert self.portfolio.net_position(instrument_id) == Decimal("1.500")
        assert self.portfolio.is_net_long(instrument_id, BINANCE1_ACCOUNT_ID)
        assert self.portfolio.is_net_short(instrument_id, BINANCE2_ACCOUNT_ID)

    def test_adding_to_one_account_position_does_not_touch_other(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, quantity="1.000")

        # Act
        self._open(BINANCE2_CLIENT_ID, quantity="0.250")

        # Assert
        first = self.cache.position(self._netting_position_id(BINANCE1_CLIENT_ID))
        second = self.cache.position(self._netting_position_id(BINANCE2_CLIENT_ID))
        assert first.quantity == Quantity.from_str("1.000")
        assert second.quantity == Quantity.from_str("1.250")

    @pytest.mark.parametrize("client_id", [BINANCE1_CLIENT_ID, BINANCE2_CLIENT_ID])
    def test_close_position_routes_to_account_holding_position(
        self,
        client_id: ClientId,
    ) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        self._open(BINANCE2_CLIENT_ID)
        position = self.cache.position(self._netting_position_id(client_id))
        self._clear_calls()

        # Act
        self.strategy.close_position(position)

        # Assert
        for other_id in self.clients:
            expected = ["submit_order"] if other_id == client_id else []
            assert self._calls(other_id) == expected, other_id
        command = self.clients[client_id].commands[-1]
        assert isinstance(command, SubmitOrder)
        assert command.position_id == position.id
        assert command.order.side == OrderSide.SELL
        assert command.order.status != OrderStatus.DENIED

    def test_cancel_of_close_order_before_acknowledgement_routes_to_position_account(
        self,
    ) -> None:
        # Arrange - close submitted without client_id (routed by the position)
        self._open(BINANCE2_CLIENT_ID)
        position = self.cache.position(self._netting_position_id(BINANCE2_CLIENT_ID))
        self.strategy.close_position(position)
        close_order = self.clients[BINANCE2_CLIENT_ID].commands[-1].order
        assert close_order.account_id is None
        self._clear_calls()

        # Act
        self.exec_engine.execute(self._cancel_order_command(close_order))

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["cancel_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_close_fill_closes_only_that_accounts_position(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        self._open(BINANCE2_CLIENT_ID)
        position = self.cache.position(self._netting_position_id(BINANCE2_CLIENT_ID))
        self.strategy.close_position(position)
        close_order = self.clients[BINANCE2_CLIENT_ID].commands[-1].order

        # Act
        self._fill(close_order, BINANCE2_ACCOUNT_ID)

        # Assert
        assert self.cache.position(self._netting_position_id(BINANCE2_CLIENT_ID)).is_closed
        first = self.cache.position(self._netting_position_id(BINANCE1_CLIENT_ID))
        assert first.is_open
        assert first.quantity == Quantity.from_str("1.000")
        assert self.portfolio.is_flat(ETHUSDT_PERP_BINANCE.id, BINANCE2_ACCOUNT_ID)
        assert not self.portfolio.is_flat(ETHUSDT_PERP_BINANCE.id, BINANCE1_ACCOUNT_ID)

    def test_close_all_positions_closes_each_account_through_its_client(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, quantity="2.000")
        self._clear_calls()

        # Act
        self.strategy.close_all_positions(ETHUSDT_PERP_BINANCE.id)

        # Assert
        assert self._calls(BINANCE1_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order"]
        first_close = self.clients[BINANCE1_CLIENT_ID].commands[-1]
        second_close = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert first_close.order.quantity == Quantity.from_str("1.000")
        assert second_close.order.quantity == Quantity.from_str("2.000")

        # Act - fills flatten both accounts
        self._fill(first_close.order, BINANCE1_ACCOUNT_ID)
        self._fill(second_close.order, BINANCE2_ACCOUNT_ID)

        # Assert
        assert self.cache.positions_open(instrument_id=ETHUSDT_PERP_BINANCE.id) == []
        assert self.portfolio.is_flat(ETHUSDT_PERP_BINANCE.id)

    def test_close_all_positions_with_client_id_closes_only_that_account(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, quantity="2.000")
        self._clear_calls()

        # Act
        self.strategy.close_all_positions(ETHUSDT_PERP_BINANCE.id, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []
        close = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert close.position_id == self._netting_position_id(BINANCE2_CLIENT_ID)
        assert close.order.quantity == Quantity.from_str("2.000")
        assert close.order.status != OrderStatus.DENIED

    def test_flip_on_one_account_keeps_positions_separate(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID, side=OrderSide.BUY, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.BUY, quantity="1.000")

        # Act - sell more than held on BINANCE2 flips it short
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL, quantity="3.000")

        # Assert
        first = self.cache.position(self._netting_position_id(BINANCE1_CLIENT_ID))
        assert first.side == PositionSide.LONG
        assert first.quantity == Quantity.from_str("1.000")
        second_open = self.cache.positions_open(account_id=BINANCE2_ACCOUNT_ID)
        assert len(second_open) == 1
        assert second_open[0].side == PositionSide.SHORT
        assert second_open[0].quantity == Quantity.from_str("2.000")
        assert second_open[0].account_id == BINANCE2_ACCOUNT_ID

    def test_reopen_after_close_reuses_suffixed_position_id(self) -> None:
        # Arrange
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.BUY)
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL)
        position_id = self._netting_position_id(BINANCE2_CLIENT_ID)
        assert self.cache.position(position_id).is_closed

        # Act
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.BUY, quantity="0.300")

        # Assert
        position = self.cache.position(position_id)
        assert position.is_open
        assert position.account_id == BINANCE2_ACCOUNT_ID
        assert position.quantity == Quantity.from_str("0.300")

    def test_fill_from_another_account_is_not_applied_to_position(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        position_id = self._netting_position_id(BINANCE1_CLIENT_ID)
        order = self._market()
        self.strategy.submit_order(order, position_id=position_id)

        # Act - the venue reports the fill on the other account
        self._fill(order, BINANCE2_ACCOUNT_ID)

        # Assert
        position = self.cache.position(position_id)
        assert position.account_id == BINANCE1_ACCOUNT_ID
        assert position.quantity == Quantity.from_str("1.000")
        assert self.cache.positions_open(account_id=BINANCE2_ACCOUNT_ID) == []

    def test_submit_with_other_accounts_netting_position_id_is_denied(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        order = self._market(side=OrderSide.SELL)

        # Act
        self.strategy.submit_order(
            order,
            position_id=self._netting_position_id(BINANCE1_CLIENT_ID),
            client_id=BINANCE2_CLIENT_ID,
        )

        # Assert
        assert order.status == OrderStatus.DENIED
        assert self._calls(BINANCE2_CLIENT_ID) == []

    def test_partial_fills_on_both_accounts_accumulate_separately(self) -> None:
        # Arrange
        order1 = self._market(quantity="2.000")
        order2 = self._market(quantity="2.000")
        self.strategy.submit_order(order1, client_id=BINANCE1_CLIENT_ID)
        self.strategy.submit_order(order2, client_id=BINANCE2_CLIENT_ID)

        # Act
        self._fill(order1, BINANCE1_ACCOUNT_ID, last_qty="0.500")
        self._fill(order2, BINANCE2_ACCOUNT_ID, last_qty="1.500")
        self._fill(order1, BINANCE1_ACCOUNT_ID, last_qty="1.500")

        # Assert
        first = self.cache.position(self._netting_position_id(BINANCE1_CLIENT_ID))
        second = self.cache.position(self._netting_position_id(BINANCE2_CLIENT_ID))
        assert first.quantity == Quantity.from_str("2.000")
        assert second.quantity == Quantity.from_str("1.500")
        assert order1.status == OrderStatus.FILLED
        assert order2.status == OrderStatus.PARTIALLY_FILLED


class TestSingleAccountVenueUnchanged:
    def setup(self) -> None:
        self.clock = TestClock()
        self.trader_id = TestIdStubs.trader_id()
        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = Cache(database=MockCacheDatabase())
        self.cache.add_instrument(ETHUSDT_PERP_BINANCE)
        self.portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.exec_engine = ExecutionEngine(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.risk_engine = RiskEngine(
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        self.client = _RecordingExecutionClient(
            client_id=ClientId("BINANCE"),
            venue=BINANCE,
            account_type=AccountType.MARGIN,
            base_currency=None,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            oms_type=OmsType.NETTING,
        )
        self.exec_engine.register_client(self.client)
        self.account_id = AccountId("BINANCE-USDT_FUTURES-master")
        self.portfolio.update_account(_margin_account_state(self.account_id))
        self.strategy = Strategy(StrategyConfig(oms_type="NETTING"))
        self.strategy.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        self.exec_engine.start()

    def test_submit_without_client_id_routes_to_venue_client(self) -> None:
        # Arrange
        order = self.strategy.order_factory.market(
            ETHUSDT_PERP_BINANCE.id,
            OrderSide.BUY,
            Quantity.from_str("1.000"),
        )

        # Act
        self.strategy.submit_order(order)

        # Assert
        assert self.client.calls == ["_start", "submit_order"]

    def test_position_id_is_plain(self) -> None:
        # Arrange
        order = self.strategy.order_factory.market(
            ETHUSDT_PERP_BINANCE.id,
            OrderSide.BUY,
            Quantity.from_str("1.000"),
        )
        self.strategy.submit_order(order)
        self.exec_engine.process(TestEventStubs.order_submitted(order, account_id=self.account_id))
        self.exec_engine.process(TestEventStubs.order_accepted(order, account_id=self.account_id))

        # Act
        self.exec_engine.process(
            TestEventStubs.order_filled(order, ETHUSDT_PERP_BINANCE, account_id=self.account_id),
        )

        # Assert
        position = self.cache.positions_open()[0]
        assert position.id == PositionId(f"{ETHUSDT_PERP_BINANCE.id}-{self.strategy.id}")

    def test_venue_client_and_account_resolve_by_venue(self) -> None:
        # Assert
        assert self.exec_engine._venue_client(BINANCE) == self.client
        assert self.portfolio.account(BINANCE).id == self.account_id


class TestMultiAccountHedgingPositions(_MultiAccountFixture):
    oms_type = "HEDGING"

    def test_positions_per_account_are_separate(self) -> None:
        # Act
        self._open(BINANCE1_CLIENT_ID)
        self._open(BINANCE2_CLIENT_ID)

        # Assert
        assert len(self.cache.positions_open(account_id=BINANCE1_ACCOUNT_ID)) == 1
        assert len(self.cache.positions_open(account_id=BINANCE2_ACCOUNT_ID)) == 1

    def test_close_position_routes_to_account_holding_position(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        self._open(BINANCE2_CLIENT_ID)
        position = self.cache.positions_open(account_id=BINANCE2_ACCOUNT_ID)[0]
        self._clear_calls()

        # Act
        self.strategy.close_position(position)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []

    def test_close_all_positions_with_client_id_does_not_touch_other_accounts(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        self._open(BINANCE2_CLIENT_ID)
        self._clear_calls()

        # Act
        self.strategy.close_all_positions(ETHUSDT_PERP_BINANCE.id, client_id=BINANCE1_CLIENT_ID)

        # Assert - only BINANCE1's position is closed, through BINANCE1
        assert self._calls(BINANCE1_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE2_CLIENT_ID) == []
        close = self.clients[BINANCE1_CLIENT_ID].commands[-1]
        assert close.position_id == self.cache.positions_open(account_id=BINANCE1_ACCOUNT_ID)[0].id

    def test_custom_position_id_on_account_is_allowed(self) -> None:
        # Arrange
        order = self._market()

        # Act
        self.strategy.submit_order(
            order,
            position_id=PositionId("ACC2-LONG"),
            client_id=BINANCE2_CLIENT_ID,
        )
        self._fill(order, BINANCE2_ACCOUNT_ID)

        # Assert
        position = self.cache.position(PositionId("ACC2-LONG"))
        assert position.account_id == BINANCE2_ACCOUNT_ID

    def test_oms_type_is_resolved_per_client(self) -> None:
        # Arrange - a third account configured for HEDGING while the others are NETTING
        hedging_client = _RecordingExecutionClient(
            client_id=ClientId("BINANCE3"),
            venue=BINANCE,
            account_type=AccountType.MARGIN,
            base_currency=None,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            oms_type=OmsType.HEDGING,
        )
        self.exec_engine.register_client(hedging_client)
        order = self._market()
        fill = TestEventStubs.order_filled(
            order,
            ETHUSDT_PERP_BINANCE,
            strategy_id=StrategyId("S-OTHER"),
            account_id=AccountId("BINANCE3-001"),
        )
        other_fill = TestEventStubs.order_filled(
            order,
            ETHUSDT_PERP_BINANCE,
            strategy_id=StrategyId("S-OTHER"),
            account_id=BINANCE1_ACCOUNT_ID,
        )

        # Act, Assert
        assert self.exec_engine._determine_oms_type(fill) == OmsType.HEDGING
        assert self.exec_engine._determine_oms_type(other_fill) == OmsType.NETTING


class TestMultiAccountExecAlgorithm(_MultiAccountFixture):
    def test_spawned_order_routes_to_account_of_target_position(self) -> None:
        # Arrange
        exec_algorithm = TWAPExecAlgorithm()
        exec_algorithm.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        exec_algorithm.start()
        self._open(BINANCE2_CLIENT_ID, quantity="2.000")
        position_id = self._netting_position_id(BINANCE2_CLIENT_ID)
        self._clear_calls()

        primary = self.strategy.order_factory.market(
            ETHUSDT_PERP_BINANCE.id,
            OrderSide.SELL,
            Quantity.from_str("2.000"),
            exec_algorithm_id=ExecAlgorithmId("TWAP"),
            exec_algorithm_params={"horizon_secs": 2, "interval_secs": 1},
        )

        # Act - no client_id: the target position determines the account. The TWAP
        # algorithm spawns and submits its first child order on receipt.
        self.strategy.submit_order(primary, position_id=position_id)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []
        command = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert command.order.exec_spawn_id == primary.client_order_id
        assert command.order.client_order_id != primary.client_order_id
        assert command.position_id == position_id

    def test_spawned_order_routes_to_client_of_primary_order(self) -> None:
        # Arrange
        exec_algorithm = TWAPExecAlgorithm()
        exec_algorithm.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        exec_algorithm.start()
        primary = self.strategy.order_factory.market(
            ETHUSDT_PERP_BINANCE.id,
            OrderSide.BUY,
            Quantity.from_str("2.000"),
            exec_algorithm_id=ExecAlgorithmId("TWAP"),
            exec_algorithm_params={"horizon_secs": 2, "interval_secs": 1},
        )

        # Act - the TWAP algorithm spawns and submits its first child order on receipt
        self.strategy.submit_order(primary, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order"]
        assert self._calls(BINANCE1_CLIENT_ID) == []
        command = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert command.order.exec_spawn_id == primary.client_order_id


class TestMultiAccountRisk(_MultiAccountFixture):
    def setup(self) -> None:
        super().setup()
        self.cache.add_instrument(BTCUSDT_BINANCE)
        self.cache.add_quote_tick(
            TestDataStubs.quote_tick(
                instrument=BTCUSDT_BINANCE,
                bid_price=10_000.0,
                ask_price=10_000.0,
            ),
        )

    def _make_client(self, client_id: ClientId, venue: Venue) -> _RecordingExecutionClient:
        return _RecordingExecutionClient(
            client_id=client_id,
            venue=venue,
            account_type=AccountType.CASH,
            base_currency=None,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            oms_type=OmsType.NETTING,
        )

    def _update_accounts(self) -> None:
        # The first account is well funded, the second nearly empty
        self.portfolio.update_account(_cash_account_state(BINANCE1_ACCOUNT_ID, 10_000_000))
        self.portfolio.update_account(_cash_account_state(BINANCE2_ACCOUNT_ID, 10))

    def _btc_market(self, side: OrderSide = OrderSide.BUY) -> Order:
        return self.strategy.order_factory.market(
            BTCUSDT_BINANCE.id,
            side,
            Quantity.from_str("1.000000"),
        )

    def _bracket(self):
        return self.strategy.order_factory.bracket(
            instrument_id=BTCUSDT_BINANCE.id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str("1.000000"),
            entry_order_type=OrderType.LIMIT,
            entry_price=Price.from_str("10000.00"),
            sl_trigger_price=Price.from_str("9000.00"),
            tp_price=Price.from_str("11000.00"),
        )

    def test_order_to_funded_account_is_accepted(self) -> None:
        # Arrange
        order = self._btc_market()

        # Act
        self.strategy.submit_order(order, client_id=BINANCE1_CLIENT_ID)

        # Assert
        assert order.status == OrderStatus.INITIALIZED
        assert self._calls(BINANCE1_CLIENT_ID) == ["submit_order"]

    def test_order_to_underfunded_account_is_denied(self) -> None:
        # Arrange
        order = self._btc_market()

        # Act
        self.strategy.submit_order(order, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert order.status == OrderStatus.DENIED
        assert self._calls(BINANCE2_CLIENT_ID) == []

    def test_order_without_client_id_is_not_checked_against_any_account(self) -> None:
        # Arrange
        order = self._btc_market()

        # Act
        self.strategy.submit_order(order)

        # Assert - denied for the ambiguous account, not for a balance
        assert order.status == OrderStatus.DENIED
        assert "AMBIGUOUS_ACCOUNT" in order.last_event.reason

    def test_order_list_to_underfunded_account_is_denied(self) -> None:
        # Arrange
        bracket = self._bracket()

        # Act
        self.strategy.submit_order_list(bracket, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert all(o.status == OrderStatus.DENIED for o in bracket.orders)
        assert self._calls(BINANCE2_CLIENT_ID) == []

    def test_order_list_to_funded_account_is_accepted(self) -> None:
        # Act
        self.strategy.submit_order_list(self._bracket(), client_id=BINANCE1_CLIENT_ID)

        # Assert
        assert self._calls(BINANCE1_CLIENT_ID) == ["submit_order_list"]

    def test_sell_on_account_without_long_position_is_balance_checked(self) -> None:
        # Arrange - a LONG position exists on the first account only
        entry = self._btc_market(OrderSide.BUY)
        self.strategy.submit_order(entry, client_id=BINANCE1_CLIENT_ID)
        self._fill(entry, BINANCE1_ACCOUNT_ID, last_px="10000.00")

        # The second account holds only 0.1 BTC, so selling 1 BTC there is not covered
        self.portfolio.update_account(
            AccountState(
                account_id=BINANCE2_ACCOUNT_ID,
                account_type=AccountType.CASH,
                base_currency=None,
                reported=True,
                balances=[
                    AccountBalance(Money(10, USDT), Money(0, USDT), Money(10, USDT)),
                    AccountBalance(Money(0.1, BTC), Money(0, BTC), Money(0.1, BTC)),
                ],
                margins=[],
                info={},
                event_id=UUID4(),
                ts_event=0,
                ts_init=0,
            ),
        )
        sell = self._btc_market(OrderSide.SELL)

        # Act
        self.strategy.submit_order(sell, client_id=BINANCE2_CLIENT_ID)

        # Assert - the first account's LONG position does not make this a reducing sell
        assert sell.status == OrderStatus.DENIED

    def test_sell_reducing_long_on_same_account_skips_balance_check(self) -> None:
        # Arrange
        self.portfolio.update_account(_cash_account_state(BINANCE2_ACCOUNT_ID, 20_000))
        entry = self._btc_market(OrderSide.BUY)
        self.strategy.submit_order(entry, client_id=BINANCE2_CLIENT_ID)
        self._fill(entry, BINANCE2_ACCOUNT_ID, last_px="10000.00")

        # Simulate the account's cash having been spent on the purchase
        self.portfolio.update_account(_cash_account_state(BINANCE2_ACCOUNT_ID, 10))
        sell = self._btc_market(OrderSide.SELL)

        # Act
        self.strategy.submit_order(sell, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert sell.status == OrderStatus.INITIALIZED
        assert self._calls(BINANCE2_CLIENT_ID) == ["submit_order", "submit_order"]

    def test_close_position_resolves_account_from_position(self) -> None:
        # Arrange
        self.portfolio.update_account(_cash_account_state(BINANCE2_ACCOUNT_ID, 20_000))
        entry = self._btc_market(OrderSide.BUY)
        self.strategy.submit_order(entry, client_id=BINANCE2_CLIENT_ID)
        self._fill(entry, BINANCE2_ACCOUNT_ID, last_px="10000.00")
        self.portfolio.update_account(_cash_account_state(BINANCE2_ACCOUNT_ID, 10))
        position = self.cache.positions_open(account_id=BINANCE2_ACCOUNT_ID)[0]

        # Act
        self.strategy.close_position(position)

        # Assert
        close = self.clients[BINANCE2_CLIENT_ID].commands[-1]
        assert close.order.status == OrderStatus.INITIALIZED
        assert close.position_id == position.id


class TestMultiAccountReducingState(_MultiAccountFixture):
    def test_reducing_checks_each_accounts_own_net_position(self) -> None:
        # Arrange - LONG 1 on BINANCE1, SHORT 2 on BINANCE2 (aggregate is net SHORT)
        self._open(BINANCE1_CLIENT_ID, side=OrderSide.BUY, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL, quantity="2.000")
        self.risk_engine.set_trading_state(TradingState.REDUCING)
        reduce_long = self._market(side=OrderSide.SELL)
        increase_long = self._market(side=OrderSide.BUY)
        reduce_short = self._market(side=OrderSide.BUY)
        increase_short = self._market(side=OrderSide.SELL)

        # Act
        self.strategy.submit_order(reduce_long, client_id=BINANCE1_CLIENT_ID)
        self.strategy.submit_order(increase_long, client_id=BINANCE1_CLIENT_ID)
        self.strategy.submit_order(reduce_short, client_id=BINANCE2_CLIENT_ID)
        self.strategy.submit_order(increase_short, client_id=BINANCE2_CLIENT_ID)

        # Assert
        assert reduce_long.status == OrderStatus.INITIALIZED
        assert increase_long.status == OrderStatus.DENIED
        assert reduce_short.status == OrderStatus.INITIALIZED
        assert increase_short.status == OrderStatus.DENIED

    def test_reducing_close_position_resolves_account_from_position(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID, side=OrderSide.BUY, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL, quantity="2.000")
        self.risk_engine.set_trading_state(TradingState.REDUCING)
        self._clear_calls()

        # Act
        self.strategy.close_position(
            self.cache.position(self._netting_position_id(BINANCE1_CLIENT_ID)),
        )

        # Assert - a reducing SELL for BINANCE1 even though the aggregate is net SHORT
        assert self._calls(BINANCE1_CLIENT_ID) == ["submit_order"]

    def test_reducing_modify_uses_account_of_order(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID, side=OrderSide.BUY, quantity="1.000")
        self._open(BINANCE2_CLIENT_ID, side=OrderSide.SELL, quantity="2.000")
        order = self._limit(side=OrderSide.BUY, quantity="0.100")
        self.strategy.submit_order(order, client_id=BINANCE2_CLIENT_ID)
        self._accept(order, BINANCE2_ACCOUNT_ID)
        self.risk_engine.set_trading_state(TradingState.REDUCING)
        self._clear_calls()

        # Act - increasing a BUY on the SHORT account still reduces that account
        self.strategy.modify_order(order, quantity=Quantity.from_str("0.200"))

        # Assert
        assert self._calls(BINANCE2_CLIENT_ID) == ["modify_order"]


class TestMultiAccountPortfolioQueries(_MultiAccountFixture):
    def test_account_by_venue_has_no_default_account(self) -> None:
        # Assert
        assert self.portfolio.account(BINANCE) is None
        assert self.portfolio.account(OKX) is None

    @pytest.mark.parametrize("account_id", list(ACCOUNT_FOR_CLIENT.values()))
    def test_account_by_account_id_returns_each_account(self, account_id: AccountId) -> None:
        # Assert
        assert self.portfolio.account(account_id=account_id).id == account_id

    def test_accounts_indexed_under_their_issuer(self) -> None:
        # Assert
        assert self.cache.account_id(Venue("BINANCE1")) == BINANCE1_ACCOUNT_ID
        assert self.cache.account_id(Venue("OKX2")) == OKX2_ACCOUNT_ID
        assert self.cache.account_id(BINANCE) is None

    def test_positions_queried_by_account(self) -> None:
        # Arrange
        self._open(BINANCE1_CLIENT_ID)
        self._open(BINANCE2_CLIENT_ID)
        self._open(OKX2_CLIENT_ID, ETHUSDT_SWAP_OKX.id)

        # Assert
        assert len(self.cache.positions_open(venue=BINANCE)) == 2
        assert len(self.cache.positions_open(venue=OKX)) == 1
        assert len(self.cache.positions_open(account_id=OKX2_ACCOUNT_ID)) == 1
        assert self.cache.positions_open(account_id=OKX1_ACCOUNT_ID) == []


class TestMultiAccountScale:
    """
    Many accounts per venue: 10 accounts on a single venue, and 20 accounts across two
    venues (10 each), all traded by one strategy on the same instrument per venue.
    """

    def _setup(self, accounts_per_venue: dict[Venue, int]) -> None:
        self.clock = TestClock()
        self.trader_id = TestIdStubs.trader_id()
        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = Cache(database=MockCacheDatabase())
        self.portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.exec_engine = ExecutionEngine(
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            config=ExecEngineConfig(debug=True),
        )
        self.risk_engine = RiskEngine(
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )

        self.instruments = {BINANCE: ETHUSDT_PERP_BINANCE, OKX: ETHUSDT_SWAP_OKX}
        self.clients: dict[ClientId, _RecordingExecutionClient] = {}
        self.accounts: dict[ClientId, AccountId] = {}
        self.venue_clients: dict[Venue, list[ClientId]] = {}

        for venue, count in accounts_per_venue.items():
            self.cache.add_instrument(self.instruments[venue])
            self.venue_clients[venue] = []
            for n in range(1, count + 1):
                client_id = ClientId(f"{venue.value}{n}")
                client = _RecordingExecutionClient(
                    client_id=client_id,
                    venue=venue,
                    account_type=AccountType.MARGIN,
                    base_currency=None,
                    msgbus=self.msgbus,
                    cache=self.cache,
                    clock=self.clock,
                    oms_type=OmsType.NETTING,
                )
                self.exec_engine.register_client(client)
                account_id = AccountId(f"{client_id}-master")
                self.portfolio.update_account(_margin_account_state(account_id))
                self.clients[client_id] = client
                self.accounts[client_id] = account_id
                self.venue_clients[venue].append(client_id)

        self.strategy = Strategy(StrategyConfig(oms_type="NETTING"))
        self.strategy.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        self.exec_engine.register_oms_type(self.strategy)
        self.risk_engine.start()
        self.exec_engine.start()
        self.strategy.start()
        self._trade_count = 0

    def _calls(self, client_id: ClientId) -> list[str]:
        return [c for c in self.clients[client_id].calls if c != "_start"]

    def _clear_calls(self) -> None:
        for client in self.clients.values():
            client.calls.clear()

    def _fill(self, order: Order, account_id: AccountId) -> None:
        self._trade_count += 1
        if order.status == OrderStatus.INITIALIZED:
            self.exec_engine.process(TestEventStubs.order_submitted(order, account_id=account_id))
            self.exec_engine.process(
                TestEventStubs.order_accepted(
                    order,
                    account_id=account_id,
                    venue_order_id=VenueOrderId(f"V-{self._trade_count}"),
                ),
            )
        self.exec_engine.process(
            TestEventStubs.order_filled(
                order,
                self.cache.instrument(order.instrument_id),
                account_id=account_id,
                venue_order_id=order.venue_order_id,
                trade_id=TradeId(f"T-{self._trade_count}"),
                last_px=Price.from_str("1000.00"),
            ),
        )

    def _quantity(self, n: int) -> Quantity:
        # A distinct quantity per account makes cross-account mixing detectable
        return Quantity.from_str(f"{n}.000")

    def _open_all(self) -> None:
        for venue, client_ids in self.venue_clients.items():
            for n, client_id in enumerate(client_ids, start=1):
                order = self.strategy.order_factory.market(
                    self.instruments[venue].id,
                    OrderSide.BUY,
                    self._quantity(n),
                )
                self.strategy.submit_order(order, client_id=client_id)
                self._fill(order, self.accounts[client_id])

    def _assert_routing_and_positions(self) -> None:
        # Each client received exactly its own order
        for client_id in self.clients:
            assert self._calls(client_id) == ["submit_order"], client_id

        # One position per account, carrying that account's quantity
        for venue, client_ids in self.venue_clients.items():
            instrument_id = self.instruments[venue].id
            positions = self.cache.positions_open(instrument_id=instrument_id)
            assert len(positions) == len(client_ids)
            assert len({p.id for p in positions}) == len(client_ids)
            for n, client_id in enumerate(client_ids, start=1):
                account_id = self.accounts[client_id]
                account_positions = self.cache.positions_open(account_id=account_id)
                assert len(account_positions) == 1, client_id
                assert account_positions[0].quantity == self._quantity(n)
                assert self.portfolio.net_position(instrument_id, account_id) == Decimal(n)

            total = sum(range(1, len(client_ids) + 1))
            assert self.portfolio.net_position(instrument_id) == Decimal(total)

    def _assert_close_all_positions(self) -> None:
        self._clear_calls()
        for venue in self.venue_clients:
            self.strategy.close_all_positions(self.instruments[venue].id)

        # Each account's close goes to its own client with its own quantity
        for client_ids in self.venue_clients.values():
            for n, client_id in enumerate(client_ids, start=1):
                assert self._calls(client_id) == ["submit_order"], client_id
                command = self.clients[client_id].commands[-1]
                assert command.order.side == OrderSide.SELL
                assert command.order.quantity == self._quantity(n)
                self._fill(command.order, self.accounts[client_id])

        assert self.cache.positions_open() == []
        for venue in self.venue_clients:
            assert self.portfolio.is_flat(self.instruments[venue].id)

    def test_ten_accounts_on_single_venue(self) -> None:
        # Arrange
        self._setup({BINANCE: 10})

        # Assert registration
        assert len(self.exec_engine.registered_clients) == 10
        assert len(self.exec_engine._venue_clients[BINANCE]) == 10
        assert BINANCE not in self.exec_engine._routing_map

        # Act, Assert - open a position on every account
        self._open_all()
        self._assert_routing_and_positions()

        # Act, Assert - close every account's position through its own client
        self._assert_close_all_positions()

    def test_twenty_accounts_across_two_venues(self) -> None:
        # Arrange
        self._setup({BINANCE: 10, OKX: 10})

        # Assert registration
        assert len(self.exec_engine.registered_clients) == 20
        assert len(self.exec_engine._venue_clients[BINANCE]) == 10
        assert len(self.exec_engine._venue_clients[OKX]) == 10
        assert self.exec_engine._routing_map == {}

        # Act, Assert
        self._open_all()
        self._assert_routing_and_positions()
        self._assert_close_all_positions()

    @pytest.mark.parametrize(
        "accounts_per_venue",
        [{BINANCE: 10}, {BINANCE: 10, OKX: 10}],
        ids=["10-accounts-1-venue", "20-accounts-2-venues"],
    )
    def test_cancel_all_orders_reaches_every_account_of_venue_only(
        self,
        accounts_per_venue: dict[Venue, int],
    ) -> None:
        # Arrange - an open order on every account
        self._setup(accounts_per_venue)
        for venue, client_ids in self.venue_clients.items():
            for client_id in client_ids:
                order = self.strategy.order_factory.limit(
                    self.instruments[venue].id,
                    OrderSide.BUY,
                    Quantity.from_str("1.000"),
                    Price.from_str("900.00"),
                )
                self.strategy.submit_order(order, client_id=client_id)
                account_id = self.accounts[client_id]
                self.exec_engine.process(
                    TestEventStubs.order_submitted(order, account_id=account_id),
                )
        self._clear_calls()

        # Act
        self.strategy.cancel_all_orders(ETHUSDT_PERP_BINANCE.id)

        # Assert
        for client_id in self.venue_clients[BINANCE]:
            assert self._calls(client_id) == ["cancel_all_orders"], client_id
        for client_id in self.venue_clients.get(OKX, []):
            assert self._calls(client_id) == [], client_id

    @pytest.mark.parametrize(
        "accounts_per_venue",
        [{BINANCE: 10}, {BINANCE: 10, OKX: 10}],
        ids=["10-accounts-1-venue", "20-accounts-2-venues"],
    )
    def test_cancel_orders_across_all_accounts_is_split_per_client(
        self,
        accounts_per_venue: dict[Venue, int],
    ) -> None:
        # Arrange
        self._setup(accounts_per_venue)
        orders_by_client: dict[ClientId, list[Order]] = {}
        for client_id in self.venue_clients[BINANCE]:
            orders = []
            for _ in range(2):
                order = self.strategy.order_factory.limit(
                    ETHUSDT_PERP_BINANCE.id,
                    OrderSide.BUY,
                    Quantity.from_str("1.000"),
                    Price.from_str("900.00"),
                )
                self.strategy.submit_order(order, client_id=client_id)
                self.exec_engine.process(
                    TestEventStubs.order_submitted(order, account_id=self.accounts[client_id]),
                )
                orders.append(order)
            orders_by_client[client_id] = orders
        self._clear_calls()
        all_orders = [o for orders in orders_by_client.values() for o in orders]

        # Act
        self.strategy.cancel_orders(all_orders)

        # Assert
        for client_id, orders in orders_by_client.items():
            assert self._calls(client_id) == ["batch_cancel_orders"], client_id
            batch = self.clients[client_id].commands[-1]
            assert [c.client_order_id for c in batch.cancels] == [o.client_order_id for o in orders]

    @pytest.mark.parametrize(
        "accounts_per_venue",
        [{BINANCE: 10}, {BINANCE: 10, OKX: 10}],
        ids=["10-accounts-1-venue", "20-accounts-2-venues"],
    )
    def test_cancel_and_query_route_to_account_of_each_order(
        self,
        accounts_per_venue: dict[Venue, int],
    ) -> None:
        # Arrange
        self._setup(accounts_per_venue)
        orders: dict[ClientId, Order] = {}
        for venue, client_ids in self.venue_clients.items():
            for client_id in client_ids:
                order = self.strategy.order_factory.limit(
                    self.instruments[venue].id,
                    OrderSide.BUY,
                    Quantity.from_str("1.000"),
                    Price.from_str("900.00"),
                )
                self.strategy.submit_order(order, client_id=client_id)
                self.exec_engine.process(
                    TestEventStubs.order_submitted(order, account_id=self.accounts[client_id]),
                )
                orders[client_id] = order
        self._clear_calls()

        # Act
        for order in orders.values():
            self.strategy.cancel_order(order)
        for account_id in self.accounts.values():
            self.exec_engine.execute(
                QueryAccount(
                    trader_id=self.trader_id,
                    account_id=account_id,
                    command_id=UUID4(),
                    ts_init=0,
                ),
            )

        # Assert
        for client_id in self.clients:
            assert self._calls(client_id) == ["cancel_order", "query_account"], client_id
            cancel, query = self.clients[client_id].commands[-2:]
            assert cancel.client_order_id == orders[client_id].client_order_id
            assert query.account_id == self.accounts[client_id]

    @pytest.mark.parametrize(
        "accounts_per_venue",
        [{BINANCE: 10}, {BINANCE: 10, OKX: 10}],
        ids=["10-accounts-1-venue", "20-accounts-2-venues"],
    )
    def test_closing_one_account_leaves_all_others_open(
        self,
        accounts_per_venue: dict[Venue, int],
    ) -> None:
        # Arrange
        self._setup(accounts_per_venue)
        self._open_all()
        target = self.venue_clients[BINANCE][4]  # BINANCE5
        position = self.cache.positions_open(account_id=self.accounts[target])[0]
        self._clear_calls()

        # Act
        self.strategy.close_position(position)
        close = self.clients[target].commands[-1]
        self._fill(close.order, self.accounts[target])

        # Assert
        assert self._calls(target) == ["submit_order"]
        for client_id in self.clients:
            if client_id != target:
                assert self._calls(client_id) == [], client_id
        assert self.cache.positions_open(account_id=self.accounts[target]) == []
        assert len(self.cache.positions_open()) == len(self.clients) - 1
