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
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_engine import LiveExecutionEngine
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


SIM = Venue("SIM")
AUDUSD_SIM = TestInstrumentProvider.default_fx_ccy("AUD/USD")

# MockLiveExecutionClient sets its account ID to f"{client_id}-001"
PRIMARY_CLIENT_ID = ClientId("SIM")
SECONDARY_CLIENT_ID = ClientId("SIM2")
PRIMARY_ACCOUNT_ID = AccountId("SIM-001")
SECONDARY_ACCOUNT_ID = AccountId("SIM2-001")


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

        self.primary = self._make_client(PRIMARY_CLIENT_ID)
        self.secondary = self._make_client(SECONDARY_CLIENT_ID)
        self.exec_engine.register_client(self.primary)
        self.exec_engine.register_client(self.secondary)

        for account_id in (PRIMARY_ACCOUNT_ID, SECONDARY_ACCOUNT_ID):
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
        self.primary.add_order_status_report(self._order_report(PRIMARY_ACCOUNT_ID, "P-1"))
        self.secondary.add_order_status_report(self._order_report(SECONDARY_ACCOUNT_ID, "S-1"))

        # Act
        result = await self.exec_engine.reconcile_execution_state()

        # Assert
        assert result
        primary_orders = self.cache.orders_open(account_id=PRIMARY_ACCOUNT_ID)
        secondary_orders = self.cache.orders_open(account_id=SECONDARY_ACCOUNT_ID)
        assert [o.venue_order_id for o in primary_orders] == [VenueOrderId("P-1")]
        assert [o.venue_order_id for o in secondary_orders] == [VenueOrderId("S-1")]

    @pytest.mark.asyncio
    async def test_reconcile_filled_orders_creates_separate_positions_per_account(self):
        # Arrange
        self.primary.add_order_status_report(
            self._order_report(PRIMARY_ACCOUNT_ID, "P-1", OrderStatus.FILLED, 10_000),
        )
        self.primary.add_fill_reports(
            VenueOrderId("P-1"),
            [self._fill_report(PRIMARY_ACCOUNT_ID, "P-1", "P-T1")],
        )
        self.secondary.add_order_status_report(
            self._order_report(
                SECONDARY_ACCOUNT_ID,
                "S-1",
                OrderStatus.FILLED,
                10_000,
                side=OrderSide.SELL,
            ),
        )
        self.secondary.add_fill_reports(
            VenueOrderId("S-1"),
            [self._fill_report(SECONDARY_ACCOUNT_ID, "S-1", "S-T1", side=OrderSide.SELL)],
        )

        # Act
        result = await self.exec_engine.reconcile_execution_state()

        # Assert
        assert result
        primary_positions = self.cache.positions_open(account_id=PRIMARY_ACCOUNT_ID)
        secondary_positions = self.cache.positions_open(account_id=SECONDARY_ACCOUNT_ID)
        assert len(primary_positions) == 1
        assert len(secondary_positions) == 1
        assert primary_positions[0].side == PositionSide.LONG
        assert secondary_positions[0].side == PositionSide.SHORT
        assert primary_positions[0].id != secondary_positions[0].id
        assert primary_positions[0].id == PositionId(f"{AUDUSD_SIM.id}-EXTERNAL")
        assert secondary_positions[0].id == PositionId(f"{AUDUSD_SIM.id}-EXTERNAL-SIM2")

    @pytest.mark.asyncio
    async def test_reconcile_same_trade_id_on_both_accounts_applies_both_fills(self):
        # Arrange - trade IDs are only unique per account
        for client, account_id, voi in (
            (self.primary, PRIMARY_ACCOUNT_ID, "P-1"),
            (self.secondary, SECONDARY_ACCOUNT_ID, "S-1"),
        ):
            client.add_order_status_report(
                self._order_report(account_id, voi, OrderStatus.FILLED, 10_000),
            )
            client.add_fill_reports(VenueOrderId(voi), [self._fill_report(account_id, voi, "1")])

        # Act
        result = await self.exec_engine.reconcile_execution_state()

        # Assert
        assert result
        for account_id in (PRIMARY_ACCOUNT_ID, SECONDARY_ACCOUNT_ID):
            positions = self.cache.positions_open(account_id=account_id)
            assert len(positions) == 1, account_id
            assert positions[0].quantity == Quantity.from_int(10_000)

    @pytest.mark.asyncio
    async def test_reconcile_position_reports_per_account(self):
        # Arrange
        self.exec_engine.generate_missing_orders = True
        self.primary.add_position_status_report(
            self._position_report(PRIMARY_ACCOUNT_ID, PositionSide.LONG, 10_000),
        )
        self.secondary.add_position_status_report(
            self._position_report(SECONDARY_ACCOUNT_ID, PositionSide.SHORT, 20_000),
        )

        # Act
        await self.exec_engine.reconcile_execution_state()

        # Assert
        assert self.portfolio.net_position(AUDUSD_SIM.id, PRIMARY_ACCOUNT_ID) == Decimal(10_000)
        assert self.portfolio.net_position(AUDUSD_SIM.id, SECONDARY_ACCOUNT_ID) == Decimal(-20_000)

    @pytest.mark.asyncio
    async def test_order_report_for_cached_order_updates_order_of_that_account(self):
        # Arrange - both accounts have an order with the same venue order ID
        orders = {}
        for account_id in (PRIMARY_ACCOUNT_ID, SECONDARY_ACCOUNT_ID):
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
        report = self._order_report(SECONDARY_ACCOUNT_ID, "1", OrderStatus.CANCELED)

        # Act
        resolved = self.exec_engine._find_order_by_venue_order_id(
            venue_order_id=report.venue_order_id,
            instrument_id=report.instrument_id,
            order_side=report.order_side,
            account_id=report.account_id,
        )

        # Assert
        assert resolved == orders[SECONDARY_ACCOUNT_ID]

    @pytest.mark.asyncio
    async def test_position_query_failure_on_one_account_does_not_skip_the_other(self):
        # Arrange
        async def raise_error(command):
            raise RuntimeError("API error")

        self.secondary.generate_position_status_reports = raise_error
        self.primary.add_position_status_report(
            self._position_report(PRIMARY_ACCOUNT_ID, PositionSide.LONG, 10_000),
        )

        # Act
        venue_positions, failed = await self.exec_engine._query_position_status_reports()

        # Assert
        assert (AUDUSD_SIM.id, PRIMARY_ACCOUNT_ID) in venue_positions
        assert failed == {SECONDARY_ACCOUNT_ID}
        assert not self.exec_engine._did_position_status_query_fail(
            AUDUSD_SIM.id,
            failed,
            PRIMARY_ACCOUNT_ID,
        )
        assert self.exec_engine._did_position_status_query_fail(
            AUDUSD_SIM.id,
            failed,
            SECONDARY_ACCOUNT_ID,
        )

    @pytest.mark.asyncio
    async def test_cached_position_discrepancy_skipped_only_for_failed_account(self):
        # Arrange - both accounts have a cached position, only the secondary query failed
        positions = {}
        for account_id in (PRIMARY_ACCOUNT_ID, SECONDARY_ACCOUNT_ID):
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
            {SECONDARY_ACCOUNT_ID},
        )

        # Assert - only the primary account was reconciled
        assert queried == [AUDUSD_SIM.id]
        assert (AUDUSD_SIM.id, PRIMARY_ACCOUNT_ID) in self.exec_engine._position_recon_retries
        assert (AUDUSD_SIM.id, SECONDARY_ACCOUNT_ID) not in self.exec_engine._position_recon_retries

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
        order.apply(TestEventStubs.order_submitted(order, account_id=SECONDARY_ACCOUNT_ID))
        order.apply(
            TestEventStubs.order_accepted(
                order,
                account_id=SECONDARY_ACCOUNT_ID,
                venue_order_id=VenueOrderId("1"),
            ),
        )
        self.cache.update_order(order)
        report = self._order_report(
            SECONDARY_ACCOUNT_ID,
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

        self.primary.calculate_commission = spy_commission(self.primary)
        self.secondary.calculate_commission = spy_commission(self.secondary)

        # Act
        fill = self.exec_engine._generate_inferred_fill(order, report, AUDUSD_SIM)

        # Assert
        assert fill.account_id == SECONDARY_ACCOUNT_ID
        assert used_clients == [SECONDARY_CLIENT_ID]

    @pytest.mark.asyncio
    async def test_mass_status_includes_reports_from_both_clients(self):
        # Arrange
        self.primary.add_order_status_report(self._order_report(PRIMARY_ACCOUNT_ID, "P-1"))
        self.secondary.add_order_status_report(self._order_report(SECONDARY_ACCOUNT_ID, "S-1"))

        # Act
        await self.exec_engine.reconcile_execution_state()

        # Assert
        orders = self.cache.orders()
        assert {o.account_id for o in orders} == {PRIMARY_ACCOUNT_ID, SECONDARY_ACCOUNT_ID}
        assert all(o.strategy_id == StrategyId("EXTERNAL") for o in orders)

    @pytest.mark.asyncio
    async def test_cancel_of_reconciled_external_order_routes_to_its_account(self):
        # Arrange
        self.primary.add_order_status_report(self._order_report(PRIMARY_ACCOUNT_ID, "P-1"))
        self.secondary.add_order_status_report(self._order_report(SECONDARY_ACCOUNT_ID, "S-1"))
        await self.exec_engine.reconcile_execution_state()
        order = self.cache.orders_open(account_id=SECONDARY_ACCOUNT_ID)[0]
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
        assert order.account_id == SECONDARY_ACCOUNT_ID
        assert client == self.secondary
