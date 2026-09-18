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
Live reconciliation tests with two execution clients (accounts) for the same venue.
"""

from decimal import Decimal

import pytest

from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.live.reconciliation import create_inferred_order_filled_event
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import LiquiditySide
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.mocks.exec_clients import MockLiveExecutionClient
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.trading.strategy import Strategy


SIM = Venue("SIM")
AUDUSD_SIM = TestInstrumentProvider.default_fx_ccy("AUD/USD")

# A venue with multiple accounts (no client is named after the venue); the mock client
# sets its account ID to f"{client_id}-001"
FIRST_CLIENT_ID = ClientId("SIM1")
SECOND_CLIENT_ID = ClientId("SIM2")
FIRST_ACCOUNT_ID = AccountId("SIM1-001")
SECOND_ACCOUNT_ID = AccountId("SIM2-001")


class TestLiveReconciliationMultiAccount:
    @pytest.fixture(autouse=True)
    def setup(self, request):
        # Fixture Setup
        self.loop = request.getfixturevalue("event_loop")
        self.clock = LiveClock()
        self.trader_id = TestIdStubs.trader_id()
        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = TestComponentStubs.cache()
        self.cache.add_instrument(AUDUSD_SIM)

        self.portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.exec_engine = LiveExecutionEngine(
            loop=self.loop,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )

        self.first = self._make_client(FIRST_CLIENT_ID)
        self.second = self._make_client(SECOND_CLIENT_ID)
        self.exec_engine.register_client(self.first)
        self.exec_engine.register_client(self.second)

        for account_id in (FIRST_ACCOUNT_ID, SECOND_ACCOUNT_ID):
            self.portfolio.update_account(TestEventStubs.margin_account_state(account_id))

    def _make_client(self, client_id: ClientId) -> MockLiveExecutionClient:
        return MockLiveExecutionClient(
            loop=self.loop,
            client_id=client_id,
            venue=SIM,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            instrument_provider=InstrumentProvider(),
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            oms_type=OmsType.NETTING,
        )

    def _order_report(
        self,
        account_id: AccountId,
        venue_order_id: str,
        status: OrderStatus = OrderStatus.ACCEPTED,
        filled_qty: int = 0,
        side: OrderSide = OrderSide.BUY,
        client_order_id: ClientOrderId | None = None,
    ) -> OrderStatusReport:
        return OrderStatusReport(
            account_id=account_id,
            instrument_id=AUDUSD_SIM.id,
            client_order_id=client_order_id,
            venue_order_id=VenueOrderId(venue_order_id),
            order_side=side,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            order_status=status,
            price=Price.from_str("1.00000"),
            quantity=Quantity.from_int(10_000),
            filled_qty=Quantity.from_int(filled_qty),
            avg_px=Decimal("1.00000") if filled_qty else None,
            report_id=UUID4(),
            ts_accepted=0,
            ts_last=0,
            ts_init=0,
        )

    def _fill_report(
        self,
        account_id: AccountId,
        venue_order_id: str,
        trade_id: str,
        side: OrderSide = OrderSide.BUY,
        client_order_id: ClientOrderId | None = None,
    ) -> FillReport:
        return FillReport(
            account_id=account_id,
            instrument_id=AUDUSD_SIM.id,
            client_order_id=client_order_id,
            venue_order_id=VenueOrderId(venue_order_id),
            trade_id=TradeId(trade_id),
            order_side=side,
            last_qty=Quantity.from_int(10_000),
            last_px=Price.from_str("1.00000"),
            commission=Money(0, USD),
            liquidity_side=LiquiditySide.MAKER,
            report_id=UUID4(),
            ts_event=0,
            ts_init=0,
        )

    def _position_report(
        self,
        account_id: AccountId,
        side: PositionSide,
        quantity: int,
    ) -> PositionStatusReport:
        return PositionStatusReport(
            account_id=account_id,
            instrument_id=AUDUSD_SIM.id,
            position_side=side,
            quantity=Quantity.from_int(quantity),
            report_id=UUID4(),
            ts_last=0,
            ts_init=0,
        )

    @pytest.mark.asyncio
    async def test_reconcile_open_orders_from_both_accounts(self):
        # Arrange
        self.first.add_order_status_report(self._order_report(FIRST_ACCOUNT_ID, "P-1"))
        self.second.add_order_status_report(self._order_report(SECOND_ACCOUNT_ID, "S-1"))

        # Act
        result = await self.exec_engine.reconcile_execution_state()

        # Assert
        assert result
        primary_orders = self.cache.orders_open(account_id=FIRST_ACCOUNT_ID)
        secondary_orders = self.cache.orders_open(account_id=SECOND_ACCOUNT_ID)
        assert [o.venue_order_id for o in primary_orders] == [VenueOrderId("P-1")]
        assert [o.venue_order_id for o in secondary_orders] == [VenueOrderId("S-1")]

    @pytest.mark.asyncio
    async def test_reconcile_filled_orders_creates_separate_positions_per_account(self):
        # Arrange
        self.first.add_order_status_report(
            self._order_report(FIRST_ACCOUNT_ID, "P-1", OrderStatus.FILLED, 10_000),
        )
        self.first.add_fill_reports(
            VenueOrderId("P-1"),
            [self._fill_report(FIRST_ACCOUNT_ID, "P-1", "P-T1")],
        )
        self.second.add_order_status_report(
            self._order_report(
                SECOND_ACCOUNT_ID,
                "S-1",
                OrderStatus.FILLED,
                10_000,
                side=OrderSide.SELL,
            ),
        )
        self.second.add_fill_reports(
            VenueOrderId("S-1"),
            [self._fill_report(SECOND_ACCOUNT_ID, "S-1", "S-T1", side=OrderSide.SELL)],
        )

        # Act
        result = await self.exec_engine.reconcile_execution_state()

        # Assert
        assert result
        first_positions = self.cache.positions_open(account_id=FIRST_ACCOUNT_ID)
        second_positions = self.cache.positions_open(account_id=SECOND_ACCOUNT_ID)
        assert len(first_positions) == 1
        assert len(second_positions) == 1
        assert first_positions[0].side == PositionSide.LONG
        assert second_positions[0].side == PositionSide.SHORT
        assert first_positions[0].id != second_positions[0].id
        assert first_positions[0].id == PositionId(f"{AUDUSD_SIM.id}-EXTERNAL-SIM1")
        assert second_positions[0].id == PositionId(f"{AUDUSD_SIM.id}-EXTERNAL-SIM2")

    @pytest.mark.asyncio
    async def test_reconcile_same_trade_id_on_both_accounts_applies_both_fills(self):
        # Arrange - trade IDs are only unique per account
        for client, account_id, voi in (
            (self.first, FIRST_ACCOUNT_ID, "P-1"),
            (self.second, SECOND_ACCOUNT_ID, "S-1"),
        ):
            client.add_order_status_report(
                self._order_report(account_id, voi, OrderStatus.FILLED, 10_000),
            )
            client.add_fill_reports(VenueOrderId(voi), [self._fill_report(account_id, voi, "1")])

        # Act
        result = await self.exec_engine.reconcile_execution_state()

        # Assert
        assert result
        for account_id in (FIRST_ACCOUNT_ID, SECOND_ACCOUNT_ID):
            positions = self.cache.positions_open(account_id=account_id)
            assert len(positions) == 1, account_id
            assert positions[0].quantity == Quantity.from_int(10_000)

    @pytest.mark.asyncio
    async def test_reconcile_position_reports_per_account(self):
        # Arrange
        self.exec_engine.generate_missing_orders = True
        self.first.add_position_status_report(
            self._position_report(FIRST_ACCOUNT_ID, PositionSide.LONG, 10_000),
        )
        self.second.add_position_status_report(
            self._position_report(SECOND_ACCOUNT_ID, PositionSide.SHORT, 20_000),
        )

        # Act
        await self.exec_engine.reconcile_execution_state()

        # Assert
        assert self.portfolio.net_position(AUDUSD_SIM.id, FIRST_ACCOUNT_ID) == Decimal(10_000)
        assert self.portfolio.net_position(AUDUSD_SIM.id, SECOND_ACCOUNT_ID) == Decimal(-20_000)

    @pytest.mark.asyncio
    async def test_order_report_for_cached_order_updates_order_of_that_account(self):
        # Arrange - both accounts have an order with the same venue order ID
        orders = {}
        for account_id in (FIRST_ACCOUNT_ID, SECOND_ACCOUNT_ID):
            order = TestExecStubs.limit_order(
                instrument=AUDUSD_SIM,
                order_side=OrderSide.BUY,
                price=Price.from_str("1.00000"),
                quantity=Quantity.from_int(10_000),
                client_order_id=ClientOrderId(f"O-{account_id}"),
            )
            self.cache.add_order(order)
            order.apply(TestEventStubs.order_submitted(order, account_id=account_id))
            self.cache.update_order(order)
            order.apply(
                TestEventStubs.order_accepted(
                    order,
                    account_id=account_id,
                    venue_order_id=VenueOrderId("1"),
                ),
            )
            self.cache.update_order(order)
            orders[account_id] = order

        # A report without client order ID must resolve by account, not venue alone
        report = self._order_report(SECOND_ACCOUNT_ID, "1", OrderStatus.CANCELED)

        # Act
        resolved = self.exec_engine._find_order_by_venue_order_id(
            venue_order_id=report.venue_order_id,
            instrument_id=report.instrument_id,
            order_side=report.order_side,
            account_id=report.account_id,
        )

        # Assert
        assert resolved == orders[SECOND_ACCOUNT_ID]

    @pytest.mark.asyncio
    async def test_position_query_failure_on_one_account_does_not_skip_the_other(self):
        # Arrange
        async def raise_error(command):
            raise RuntimeError("API error")

        self.second.generate_position_status_reports = raise_error
        self.first.add_position_status_report(
            self._position_report(FIRST_ACCOUNT_ID, PositionSide.LONG, 10_000),
        )

        # Act
        venue_positions, failed = await self.exec_engine._query_position_status_reports()

        # Assert
        assert (AUDUSD_SIM.id, FIRST_ACCOUNT_ID) in venue_positions
        assert failed == {SECOND_ACCOUNT_ID}
        assert not self.exec_engine._did_position_status_query_fail(
            AUDUSD_SIM.id,
            failed,
            FIRST_ACCOUNT_ID,
        )
        assert self.exec_engine._did_position_status_query_fail(
            AUDUSD_SIM.id,
            failed,
            SECOND_ACCOUNT_ID,
        )

    @pytest.mark.asyncio
    async def test_cached_position_discrepancy_skipped_only_for_failed_account(self):
        # Arrange - both accounts have a cached position, only the secondary query failed
        positions = {}
        for account_id in (FIRST_ACCOUNT_ID, SECOND_ACCOUNT_ID):
            order = TestExecStubs.limit_order(
                instrument=AUDUSD_SIM,
                order_side=OrderSide.BUY,
                client_order_id=ClientOrderId(f"O-{account_id}"),
            )
            fill = TestEventStubs.order_filled(
                order,
                instrument=AUDUSD_SIM,
                account_id=account_id,
                position_id=PositionId(f"P-{account_id}"),
                last_px=Price.from_str("1.00000"),
            )
            position = Position(instrument=AUDUSD_SIM, fill=fill)
            self.cache.add_position(position, OmsType.NETTING)
            positions[account_id] = position

        queried = []

        async def capture_query(instrument_id, clients):
            queried.append(instrument_id)
            return [], False

        self.exec_engine._query_and_find_missing_fills = capture_query
        self.exec_engine._reconcile_position_report = lambda report: False

        # Act - the venue reports no position for the primary (a discrepancy)
        await self.exec_engine._process_cached_position_discrepancies(
            {(AUDUSD_SIM.id, a): [p] for a, p in positions.items()},
            {},
            {SECOND_ACCOUNT_ID},
        )

        # Assert - only the primary account was reconciled
        assert queried == [AUDUSD_SIM.id]
        assert (AUDUSD_SIM.id, FIRST_ACCOUNT_ID) in self.exec_engine._position_recon_retries
        assert (AUDUSD_SIM.id, SECOND_ACCOUNT_ID) not in self.exec_engine._position_recon_retries

    @pytest.mark.asyncio
    async def test_inferred_fill_uses_client_of_report_account(self):
        # Arrange
        order = TestExecStubs.limit_order(
            instrument=AUDUSD_SIM,
            order_side=OrderSide.BUY,
            price=Price.from_str("1.00000"),
            quantity=Quantity.from_int(10_000),
        )
        self.cache.add_order(order)
        order.apply(TestEventStubs.order_submitted(order, account_id=SECOND_ACCOUNT_ID))
        order.apply(
            TestEventStubs.order_accepted(
                order,
                account_id=SECOND_ACCOUNT_ID,
                venue_order_id=VenueOrderId("1"),
            ),
        )
        self.cache.update_order(order)
        report = self._order_report(
            SECOND_ACCOUNT_ID,
            "1",
            OrderStatus.FILLED,
            10_000,
            client_order_id=order.client_order_id,
        )
        used_clients = []

        def spy_commission(client):
            def calculate_commission(*args, **kwargs):
                used_clients.append(client.id)
                return Money(0, USD)

            return calculate_commission

        self.first.calculate_commission = spy_commission(self.first)
        self.second.calculate_commission = spy_commission(self.second)

        # Act
        fill = self.exec_engine._generate_inferred_fill(order, report, AUDUSD_SIM)

        # Assert
        assert fill.account_id == SECOND_ACCOUNT_ID
        assert used_clients == [SECOND_CLIENT_ID]

    @pytest.mark.asyncio
    async def test_mass_status_includes_reports_from_both_clients(self):
        # Arrange
        self.first.add_order_status_report(self._order_report(FIRST_ACCOUNT_ID, "P-1"))
        self.second.add_order_status_report(self._order_report(SECOND_ACCOUNT_ID, "S-1"))

        # Act
        await self.exec_engine.reconcile_execution_state()

        # Assert
        orders = self.cache.orders()
        assert {o.account_id for o in orders} == {FIRST_ACCOUNT_ID, SECOND_ACCOUNT_ID}
        assert all(o.strategy_id == StrategyId("EXTERNAL") for o in orders)

    @pytest.mark.asyncio
    async def test_cancel_of_reconciled_external_order_routes_to_its_account(self):
        # Arrange
        self.first.add_order_status_report(self._order_report(FIRST_ACCOUNT_ID, "P-1"))
        self.second.add_order_status_report(self._order_report(SECOND_ACCOUNT_ID, "S-1"))
        await self.exec_engine.reconcile_execution_state()
        order = self.cache.orders_open(account_id=SECOND_ACCOUNT_ID)[0]
        cancel = CancelOrder(
            trader_id=self.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            command_id=UUID4(),
            ts_init=0,
        )

        # Act
        client = self.exec_engine._find_client_for_command(cancel)

        # Assert
        assert order.account_id == SECOND_ACCOUNT_ID
        assert client == self.second

    def _accepted_order(self, account_id: AccountId, venue_order_id: str, cache_client=None):
        order = TestExecStubs.limit_order(
            instrument=AUDUSD_SIM,
            order_side=OrderSide.BUY,
            price=Price.from_str("1.00000"),
            quantity=Quantity.from_int(10_000),
            client_order_id=ClientOrderId(f"O-{account_id}-{venue_order_id}"),
        )
        self.cache.add_order(order, None, cache_client)
        order.apply(TestEventStubs.order_submitted(order, account_id=account_id))
        self.cache.update_order(order)
        order.apply(
            TestEventStubs.order_accepted(
                order,
                account_id=account_id,
                venue_order_id=VenueOrderId(venue_order_id),
            ),
        )
        self.cache.update_order(order)
        return order

    @pytest.mark.asyncio
    async def test_open_order_check_does_not_treat_failed_accounts_orders_as_missing(self):
        # Arrange - both accounts have an open order the venue does not report,
        # but only the second account's query failed
        self.exec_engine.open_check_open_only = False
        first_order = self._accepted_order(FIRST_ACCOUNT_ID, "1")
        second_order = self._accepted_order(SECOND_ACCOUNT_ID, "2")

        async def raise_error(command):
            raise RuntimeError("timeout")

        self.second.generate_order_status_reports = raise_error

        # Act
        await self.exec_engine._check_orders_consistency()

        # Assert - the first account's order counts as missing, the second's is unknown
        assert self.exec_engine._failed_order_query_clients == {SECOND_CLIENT_ID}
        assert self.exec_engine._recon_check_retries.get(first_order.client_order_id) == 1
        assert second_order.client_order_id not in self.exec_engine._recon_check_retries

    @pytest.mark.asyncio
    async def test_open_order_check_failure_tracking_resets_each_cycle(self):
        # Arrange
        self.exec_engine.open_check_open_only = False

        async def raise_error(command):
            raise RuntimeError("timeout")

        original = self.second.generate_order_status_reports
        self.second.generate_order_status_reports = raise_error
        await self.exec_engine._check_orders_consistency()

        # Act
        self.second.generate_order_status_reports = original
        await self.exec_engine._check_orders_consistency()

        # Assert
        assert self.exec_engine._failed_order_query_clients == set()

    @pytest.mark.asyncio
    async def test_targeted_query_uses_client_of_order_account(self):
        # Arrange - a reconciled order has an account but no cached client
        order = self._accepted_order(SECOND_ACCOUNT_ID, "S-1")
        assert self.cache.client_id(order.client_order_id) is None
        self.second.add_order_status_report(
            self._order_report(
                SECOND_ACCOUNT_ID,
                "S-1",
                OrderStatus.ACCEPTED,
                client_order_id=order.client_order_id,
            ),
        )

        # Act
        await self.exec_engine._resolve_order_not_found_at_venue(order)

        # Assert - found through the account's client, so not marked rejected
        assert "generate_order_status_report" in self.second.calls
        assert "generate_order_status_report" not in self.first.calls
        assert order.status == OrderStatus.ACCEPTED

    def test_clients_for_account_query_selects_account_client(self):
        # Act
        clients = self.exec_engine._clients_for_account_query(
            SECOND_ACCOUNT_ID,
            [self.first, self.second],
        )

        # Assert
        assert clients == [self.second]

    def test_clients_for_account_query_without_registered_client_uses_all(self):
        # Act
        clients = self.exec_engine._clients_for_account_query(
            AccountId("OTHER-001"),
            [self.first, self.second],
        )

        # Assert
        assert clients == [self.first, self.second]

    @pytest.mark.asyncio
    async def test_missing_fill_with_trade_id_shared_across_accounts_is_found(self):
        # Arrange - both sides of a trade between the two accounts share the trade ID;
        # the first account's side is already cached
        order = self._accepted_order(FIRST_ACCOUNT_ID, "P-1")
        self.exec_engine._handle_event_with_tracking(
            TestEventStubs.order_filled(
                order,
                instrument=AUDUSD_SIM,
                account_id=FIRST_ACCOUNT_ID,
                trade_id=TradeId("T-SHARED"),
                last_px=Price.from_str("1.00000"),
            ),
        )
        now = self.clock.timestamp_ns()
        second_fill = FillReport(
            account_id=SECOND_ACCOUNT_ID,
            instrument_id=AUDUSD_SIM.id,
            client_order_id=None,
            venue_order_id=VenueOrderId("S-1"),
            trade_id=TradeId("T-SHARED"),
            order_side=OrderSide.SELL,
            last_qty=Quantity.from_int(10_000),
            last_px=Price.from_str("1.00000"),
            commission=Money(0, USD),
            liquidity_side=LiquiditySide.MAKER,
            report_id=UUID4(),
            ts_event=now,
            ts_init=now,
        )
        self.second.add_fill_reports(VenueOrderId("S-1"), [second_fill])

        # Act
        missing, had_errors = await self.exec_engine._query_and_find_missing_fills(
            AUDUSD_SIM.id,
            self.exec_engine._clients_for_account_query(
                SECOND_ACCOUNT_ID,
                self.exec_engine._clients.values(),
            ),
        )

        # Assert - the first account's fill is cached, the second account's is missing
        assert order.status == OrderStatus.FILLED
        assert not had_errors
        assert missing == [second_fill]
        assert (FIRST_ACCOUNT_ID, TradeId("T-SHARED")) in self.exec_engine._recent_fills_cache
        assert (SECOND_ACCOUNT_ID, TradeId("T-SHARED")) not in (
            self.exec_engine._recent_fills_cache
        )

    @pytest.mark.asyncio
    async def test_other_accounts_fill_query_error_does_not_block_account(self):
        # Arrange
        async def raise_error(command):
            raise RuntimeError("API error")

        self.second.generate_fill_reports = raise_error

        # Act
        _, errors_all_clients = await self.exec_engine._query_and_find_missing_fills(
            AUDUSD_SIM.id,
            self.exec_engine._clients.values(),
        )
        _, errors_first_account = await self.exec_engine._query_and_find_missing_fills(
            AUDUSD_SIM.id,
            self.exec_engine._clients_for_account_query(
                FIRST_ACCOUNT_ID,
                self.exec_engine._clients.values(),
            ),
        )

        # Assert
        assert errors_all_clients
        assert not errors_first_account

    @pytest.mark.parametrize(
        ("client_name", "expected"),
        [
            ("SIM1", "AUD/USD.SIM-EXTERNAL-SIM1"),
            ("SIM2", "AUD/USD.SIM-EXTERNAL-SIM2"),
        ],
    )
    def test_inferred_fill_fallback_position_id_is_per_account(self, client_name, expected):
        # Arrange - no venue position ID in the report (e.g. HEDGING without venue IDs)
        client = self.first if client_name == "SIM1" else self.second
        account_id = client.account_id
        order = self._accepted_order(account_id, f"V-{client_name}")
        report = self._order_report(
            account_id,
            f"V-{client_name}",
            OrderStatus.FILLED,
            10_000,
            client_order_id=order.client_order_id,
        )

        # Act
        fill = create_inferred_order_filled_event(
            order=order,
            ts_now=0,
            report=report,
            instrument=AUDUSD_SIM,
            client=client,
        )

        # Assert
        assert fill.position_id == PositionId(expected)
        assert fill.account_id == account_id

    def test_inferred_fill_fallback_position_id_plain_for_venue_named_client(self):
        # Arrange - a single account venue client named after the venue
        client = MockLiveExecutionClient(
            loop=self.loop,
            client_id=ClientId("OTHER"),
            venue=Venue("OTHER"),
            account_type=AccountType.MARGIN,
            base_currency=USD,
            instrument_provider=InstrumentProvider(),
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        order = self._accepted_order(FIRST_ACCOUNT_ID, "V-1")
        report = self._order_report(
            FIRST_ACCOUNT_ID,
            "V-1",
            OrderStatus.FILLED,
            10_000,
            client_order_id=order.client_order_id,
        )

        # Act
        fill = create_inferred_order_filled_event(
            order=order,
            ts_now=0,
            report=report,
            instrument=AUDUSD_SIM,
            client=client,
        )

        # Assert
        assert fill.position_id == PositionId(f"{AUDUSD_SIM.id}-EXTERNAL")

    def test_inferred_fill_for_unknown_account_uses_no_default_client(self):
        # Arrange - the report's account has no registered client
        order = self._accepted_order(AccountId("SIM3-001"), "V-3")
        report = self._order_report(
            AccountId("SIM3-001"),
            "V-3",
            OrderStatus.FILLED,
            10_000,
            client_order_id=order.client_order_id,
        )
        used_clients = []
        for client in (self.first, self.second):
            client.calculate_commission = lambda *args, c=client, **kwargs: used_clients.append(
                c.id,
            )

        # Act
        fill = self.exec_engine._generate_inferred_fill(order, report, AUDUSD_SIM)

        # Assert
        assert used_clients == []
        assert fill.account_id == AccountId("SIM3-001")
        assert fill.commission == Money(0, AUDUSD_SIM.quote_currency)

    @pytest.mark.asyncio
    async def test_hedging_reconciliation_keeps_accounts_positions_separate(self):
        # Arrange - HEDGING clients whose reports carry no venue position IDs
        engine = LiveExecutionEngine(
            loop=self.loop,
            msgbus=MessageBus(trader_id=self.trader_id, clock=self.clock),
            cache=self.cache,
            clock=self.clock,
        )
        msgbus = engine._msgbus
        clients = []
        for name in ("SIM1", "SIM2"):
            client = MockLiveExecutionClient(
                loop=self.loop,
                client_id=ClientId(name),
                venue=SIM,
                account_type=AccountType.MARGIN,
                base_currency=USD,
                instrument_provider=InstrumentProvider(),
                msgbus=msgbus,
                cache=self.cache,
                clock=self.clock,
                oms_type=OmsType.HEDGING,
            )
            engine.register_client(client)
            clients.append(client)
            client.add_order_status_report(
                self._order_report(client.account_id, f"{name}-1", OrderStatus.FILLED, 10_000),
            )

        # Act - FILLED reports without fill reports produce inferred fills
        result = await engine.reconcile_execution_state()

        # Assert
        assert result
        for client in clients:
            positions = self.cache.positions_open(account_id=client.account_id)
            assert len(positions) == 1, client.id
            assert positions[0].quantity == Quantity.from_int(10_000)
            assert positions[0].id == PositionId(f"{AUDUSD_SIM.id}-EXTERNAL-{client.id}")


class TestLiveReconciliationExternalOrderClaims:
    """
    A reconciled order's strategy claim is resolved from the report's account, so a
    strategy scoped to one account does not claim another account's external orders.
    """

    @pytest.fixture(autouse=True)
    def setup(self, request):
        self.loop = request.getfixturevalue("event_loop")
        self.clock = LiveClock()
        self.trader_id = TestIdStubs.trader_id()
        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = TestComponentStubs.cache()
        self.cache.add_instrument(AUDUSD_SIM)
        self.portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.exec_engine = LiveExecutionEngine(
            loop=self.loop,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        for client_id in (FIRST_CLIENT_ID, SECOND_CLIENT_ID):
            self.exec_engine.register_client(
                MockLiveExecutionClient(
                    loop=self.loop,
                    client_id=client_id,
                    venue=SIM,
                    account_type=AccountType.MARGIN,
                    base_currency=USD,
                    instrument_provider=InstrumentProvider(),
                    msgbus=self.msgbus,
                    cache=self.cache,
                    clock=self.clock,
                ),
            )

    def _register_claim_strategy(self, *claims: str) -> Strategy:
        strategy = Strategy(StrategyConfig(external_order_claims=list(claims)))
        strategy.register(
            trader_id=self.trader_id,
            portfolio=self.portfolio,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )
        self.exec_engine.register_external_order_claims(strategy)
        return strategy

    def _order_status_report(self, account_id: AccountId) -> OrderStatusReport:
        return OrderStatusReport(
            account_id=account_id,
            instrument_id=AUDUSD_SIM.id,
            client_order_id=ClientOrderId(f"O-{account_id}-1"),
            venue_order_id=VenueOrderId("V-1"),
            order_side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            order_status=OrderStatus.ACCEPTED,
            price=Price.from_str("1.00000"),
            quantity=Quantity.from_int(10_000),
            filled_qty=Quantity.from_int(0),
            report_id=UUID4(),
            ts_accepted=0,
            ts_last=0,
            ts_init=0,
        )

    def test_reconciled_order_on_scoped_account_is_claimed(self) -> None:
        # Arrange
        strategy = self._register_claim_strategy(f"{AUDUSD_SIM.id}@{SECOND_CLIENT_ID}")
        report = self._order_status_report(SECOND_ACCOUNT_ID)

        # Act
        order = self.exec_engine._generate_order(report)

        # Assert
        assert order.strategy_id == strategy.id

    def test_reconciled_order_on_unclaimed_account_is_external(self) -> None:
        # Arrange
        self._register_claim_strategy(f"{AUDUSD_SIM.id}@{SECOND_CLIENT_ID}")
        report = self._order_status_report(FIRST_ACCOUNT_ID)

        # Act
        order = self.exec_engine._generate_order(report)

        # Assert
        assert order.strategy_id == StrategyId("EXTERNAL")

    def test_reconciled_order_with_wildcard_claim_applies_to_both_accounts(self) -> None:
        # Arrange
        strategy = self._register_claim_strategy(f"{AUDUSD_SIM.id}@*")

        # Act
        first_order = self.exec_engine._generate_order(
            self._order_status_report(FIRST_ACCOUNT_ID),
        )
        second_order = self.exec_engine._generate_order(
            self._order_status_report(SECOND_ACCOUNT_ID),
        )

        # Assert
        assert first_order.strategy_id == strategy.id
        assert second_order.strategy_id == strategy.id

    def test_reconciled_order_with_bare_claim_on_multi_account_venue_is_external(self) -> None:
        # Arrange - SIM has two accounts, so the bare claim does not apply
        self._register_claim_strategy(str(AUDUSD_SIM.id))
        report = self._order_status_report(FIRST_ACCOUNT_ID)

        # Act
        order = self.exec_engine._generate_order(report)

        # Assert
        assert order.strategy_id == StrategyId("EXTERNAL")
