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
Tests for `Portfolio` behaviour when a venue has multiple accounts.

A venue with multiple accounts has no default account. Aggregates that would silently
merge two accounts' *exposure* (long vs short cancelling out, hiding real risk) now
refuse and log an error instead of guessing. PnL aggregation across accounts is left
unchanged: summing realized/unrealized PnL across accounts is mathematically valid (it
is not subject to the long/short cancellation problem that exposure has), so it is only
guarded against currency mismatches, as before.

`Portfolio.account_ids(venue)` and `Portfolio.accounts(venue)` give an explicit,
discoverable way to enumerate every account of a venue, so a caller can always compute
its own per-account breakdown instead of relying on a default that does not exist.

"""

from decimal import Decimal

from nautilus_trader.accounting.factory import AccountFactory
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import AccountBalance
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.config import PortfolioConfig
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs


BINANCE = Venue("BINANCE")
SIM = Venue("SIM")

ETHUSDT_PERP_BINANCE = TestInstrumentProvider.ethusdt_perp_binance()
AUDUSD_SIM = TestInstrumentProvider.default_fx_ccy("AUD/USD")

BINANCE1_ACCOUNT_ID = AccountId("BINANCE1-001")
BINANCE2_ACCOUNT_ID = AccountId("BINANCE2-001")


def _margin_account_state(account_id: AccountId, currency=USDT, balance: float = 1_000_000):
    return AccountState(
        account_id=account_id,
        account_type=AccountType.MARGIN,
        base_currency=currency,
        reported=True,
        balances=[
            AccountBalance(
                Money(balance, currency),
                Money(0, currency),
                Money(balance, currency),
            ),
        ],
        margins=[],
        info={},
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )


class TestPortfolioMultiAccount:
    def setup(self) -> None:
        # Fixture Setup
        self.clock = TestClock()
        self.trader_id = TestIdStubs.trader_id()
        self.order_factory = OrderFactory(
            trader_id=self.trader_id,
            strategy_id=StrategyId("S-001"),
            clock=TestClock(),
        )
        self.msgbus = MessageBus(trader_id=self.trader_id, clock=self.clock)
        self.cache = TestComponentStubs.cache()
        self.portfolio = Portfolio(
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            config=PortfolioConfig(debug=True),
        )
        self.cache.add_instrument(ETHUSDT_PERP_BINANCE)
        self.cache.add_instrument(AUDUSD_SIM)

    def _open_position(
        self,
        account_id: AccountId,
        side: OrderSide,
        quantity: str,
        price: str,
        position_id: str,
    ) -> Position:
        order = self.order_factory.market(
            ETHUSDT_PERP_BINANCE.id,
            side,
            Quantity.from_str(quantity),
        )
        fill = TestEventStubs.order_filled(
            order,
            instrument=ETHUSDT_PERP_BINANCE,
            account_id=account_id,
            position_id=PositionId(position_id),
            last_px=Price.from_str(price),
        )
        position = Position(instrument=ETHUSDT_PERP_BINANCE, fill=fill)
        self.cache.add_position(position, OmsType.HEDGING)
        self.portfolio.update_position(TestEventStubs.position_opened(position))
        return position

    def _two_account_long_short_setup(self) -> None:
        self.portfolio.update_account(_margin_account_state(BINANCE1_ACCOUNT_ID))
        self.portfolio.update_account(_margin_account_state(BINANCE2_ACCOUNT_ID))
        self._open_position(BINANCE1_ACCOUNT_ID, OrderSide.BUY, "2.000", "1100.00", "P-1")
        self._open_position(BINANCE2_ACCOUNT_ID, OrderSide.SELL, "0.500", "1100.00", "P-2")
        self.cache.add_quote_tick(
            TestDataStubs.quote_tick(
                instrument=ETHUSDT_PERP_BINANCE,
                bid_price=1100.0,
                ask_price=1100.0,
            ),
        )

    # -- account_ids / accounts -----------------------------------------------------------

    def test_account_ids_when_no_accounts_returns_empty_set(self) -> None:
        # Arrange, Act, Assert
        assert self.portfolio.account_ids(BINANCE) == set()

    def test_account_ids_single_account_via_pseudo_index(self) -> None:
        # Arrange - an account named after the venue, no positions/orders yet
        self.portfolio.update_account(_margin_account_state(AccountId("SIM-001")))

        # Act, Assert
        assert self.portfolio.account_ids(SIM) == {AccountId("SIM-001")}

    def test_account_ids_discovers_accounts_from_open_positions(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert
        assert self.portfolio.account_ids(BINANCE) == {BINANCE1_ACCOUNT_ID, BINANCE2_ACCOUNT_ID}

    def test_account_ids_does_not_include_other_venues(self) -> None:
        # Arrange
        self._two_account_long_short_setup()
        self.portfolio.update_account(_margin_account_state(AccountId("SIM-001"), USD))

        # Act, Assert
        assert self.portfolio.account_ids(SIM) == {AccountId("SIM-001")}
        assert AccountId("SIM-001") not in self.portfolio.account_ids(BINANCE)

    def test_accounts_returns_account_objects_for_every_account(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        accounts = self.portfolio.accounts(BINANCE)

        # Assert
        assert {a.id for a in accounts} == {BINANCE1_ACCOUNT_ID, BINANCE2_ACCOUNT_ID}

    def test_accounts_when_no_accounts_returns_empty_list(self) -> None:
        # Arrange, Act, Assert
        assert self.portfolio.accounts(BINANCE) == []

    # -- net_exposure (instrument-level) refuses to net across accounts -------------------

    def test_net_exposure_single_account_is_unchanged(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert - explicit account_id is unaffected by the multi-account guard
        assert self.portfolio.net_exposure(
            ETHUSDT_PERP_BINANCE.id,
            account_id=BINANCE1_ACCOUNT_ID,
        ) == Money(2200.00000000, USDT)
        assert self.portfolio.net_exposure(
            ETHUSDT_PERP_BINANCE.id,
            account_id=BINANCE2_ACCOUNT_ID,
        ) == Money(550.00000000, USDT)

    def test_net_exposure_two_accounts_same_currency_refuses_to_net(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.net_exposure(ETHUSDT_PERP_BINANCE.id)

        # Assert - previously this silently netted to 2200 - 550 = 1650, hiding real exposure
        assert result is None

    # -- net_exposures (venue-level) refuses to net across accounts -----------------------

    def test_net_exposures_two_accounts_same_currency_refuses_to_net(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.net_exposures(BINANCE)

        # Assert
        assert result is None

    def test_net_exposures_single_account_is_unchanged(self) -> None:
        # Arrange
        self.portfolio.update_account(_margin_account_state(BINANCE1_ACCOUNT_ID))
        self._open_position(BINANCE1_ACCOUNT_ID, OrderSide.BUY, "2.000", "1100.00", "P-1")
        self.cache.add_quote_tick(
            TestDataStubs.quote_tick(
                instrument=ETHUSDT_PERP_BINANCE,
                bid_price=1100.0,
                ask_price=1100.0,
            ),
        )

        # Act, Assert
        assert self.portfolio.net_exposures(BINANCE) == {USDT: Money(2200.00000000, USDT)}

    def test_net_exposures_explicit_account_id_is_unaffected(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert
        assert self.portfolio.net_exposures(BINANCE, account_id=BINANCE1_ACCOUNT_ID) == {
            USDT: Money(2200.00000000, USDT),
        }

    # -- mark_values refuses to net across accounts ----------------------------------------

    def test_mark_values_two_accounts_refuses_to_net(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.mark_values(BINANCE)

        # Assert
        assert result is None

    def test_mark_values_explicit_account_id_is_unaffected(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.mark_values(BINANCE, account_id=BINANCE1_ACCOUNT_ID)

        # Assert
        assert result == {USDT: Money(2200.00000000, USDT)}

    # -- equity: unchanged contract ({} for "no resolvable account"), clearer diagnostics --

    def test_equity_two_accounts_still_returns_empty_dict(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert - no default account to compute a balance from; contract unchanged
        assert self.portfolio.equity(BINANCE) == {}
        # But the ambiguity is now discoverable
        assert len(self.portfolio.account_ids(BINANCE)) == 2

    def test_equity_explicit_account_id_is_unaffected(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.equity(account_id=BINANCE1_ACCOUNT_ID)

        # Assert
        assert result[USDT].as_double() > 0

    # -- account / balances_locked / margins_*: unchanged contract (None), no crash -------

    def test_account_two_accounts_still_returns_none(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert
        assert self.portfolio.account(BINANCE) is None

    def test_balances_locked_two_accounts_still_returns_none(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert
        assert self.portfolio.balances_locked(BINANCE) is None

    def test_margins_init_two_accounts_still_returns_none(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert
        assert self.portfolio.margins_init(BINANCE) is None

    def test_margins_maint_two_accounts_still_returns_none(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert
        assert self.portfolio.margins_maint(BINANCE) is None

    # -- PnL aggregation across accounts is intentionally preserved -----------------------
    # Unlike exposure, summing realized/unrealized PnL across accounts is mathematically
    # valid (no long/short cancellation problem), so it is not guarded by the new
    # multi-account check -- only by the pre-existing currency-mismatch guard.

    def test_unrealized_pnls_two_accounts_same_currency_still_sums(self) -> None:
        # Arrange
        AccountFactory.register_calculated_account("BINANCE1")
        AccountFactory.register_calculated_account("BINANCE2")
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.unrealized_pnls(BINANCE)

        # Assert - PnL summing across accounts is intentional and unaffected
        assert result is not None
        assert USDT in result

    def test_net_position_by_account(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act
        result = self.portfolio.net_position_by_account(ETHUSDT_PERP_BINANCE.id)

        # Assert
        assert result == {
            BINANCE1_ACCOUNT_ID: Decimal("2.000"),
            BINANCE2_ACCOUNT_ID: Decimal("-0.500"),
        }

    def test_net_position_by_account_when_no_positions_returns_empty_dict(self) -> None:
        # Arrange, Act, Assert
        assert self.portfolio.net_position_by_account(ETHUSDT_PERP_BINANCE.id) == {}

    def test_net_position_aggregate_is_unchanged(self) -> None:
        # Arrange
        self._two_account_long_short_setup()

        # Act, Assert - instrument-level net_position() still aggregates by design (documented)
        assert self.portfolio.net_position(ETHUSDT_PERP_BINANCE.id) == Decimal("1.500")
