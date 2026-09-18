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
Tests for parsing `external_order_claims` config entries, including the account-scoped
`INSTRUMENT@CLIENT_ID` and `INSTRUMENT@*` (wildcard) syntax used on venues with multiple
accounts.
"""

import pytest

from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import ExternalOrderClaim
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.config import parse_external_order_claims
from nautilus_trader.trading.strategy import Strategy


DYDX = InstrumentId.from_str("ETHUSDT-PERP.DYDX")
BINANCE = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")


class TestParseExternalOrderClaims:
    def test_none_returns_empty_list(self) -> None:
        assert parse_external_order_claims(None) == []

    def test_empty_list_returns_empty_list(self) -> None:
        assert parse_external_order_claims([]) == []

    def test_bare_string_is_unscoped(self) -> None:
        # Act
        result = parse_external_order_claims(["ETHUSDT-PERP.DYDX"])

        # Assert
        assert result == [ExternalOrderClaim(DYDX, None, False)]

    def test_bare_instrument_id_is_unscoped(self) -> None:
        # Act
        result = parse_external_order_claims([DYDX])

        # Assert
        assert result == [ExternalOrderClaim(DYDX, None, False)]

    def test_scoped_string_parses_client_id(self) -> None:
        # Act
        result = parse_external_order_claims(["ETHUSDT-PERP.BINANCE@BINANCE2"])

        # Assert
        assert result == [ExternalOrderClaim(BINANCE, ClientId("BINANCE2"), False)]

    def test_wildcard_string_parses_as_explicit_wildcard(self) -> None:
        # Act
        result = parse_external_order_claims(["ETHUSDT-PERP.BINANCE@*"])

        # Assert
        assert result == [ExternalOrderClaim(BINANCE, None, True)]

    def test_multiple_entries_mixed_forms(self) -> None:
        # Act
        result = parse_external_order_claims(
            [
                "ETHUSDT-PERP.DYDX",
                "ETHUSDT-PERP.BINANCE@BINANCE1",
                "ETHUSDT-PERP.BINANCE@BINANCE2",
            ],
        )

        # Assert
        assert result == [
            ExternalOrderClaim(DYDX, None, False),
            ExternalOrderClaim(BINANCE, ClientId("BINANCE1"), False),
            ExternalOrderClaim(BINANCE, ClientId("BINANCE2"), False),
        ]

    def test_multiple_at_symbols_raises(self) -> None:
        # Act, Assert
        with pytest.raises(ValueError, match="expected at most one"):
            parse_external_order_claims(["ETHUSDT-PERP.BINANCE@BINANCE2@extra"])

    def test_empty_account_part_raises(self) -> None:
        # Act, Assert
        with pytest.raises(ValueError, match="empty account"):
            parse_external_order_claims(["ETHUSDT-PERP.BINANCE@"])


class TestStrategyExternalOrderClaimsParsing:
    def test_strategy_default_has_no_claims(self) -> None:
        # Arrange, Act
        strategy = Strategy()

        # Assert
        assert strategy.external_order_claims == []

    def test_strategy_parses_scoped_and_wildcard_claims(self) -> None:
        # Arrange
        config = StrategyConfig(
            external_order_claims=[
                "ETHUSDT-PERP.BINANCE@BINANCE1",
                "ETHUSDT-PERP.OKX@*",
            ],
        )

        # Act
        strategy = Strategy(config=config)

        # Assert
        assert strategy.external_order_claims == [
            ExternalOrderClaim(BINANCE, ClientId("BINANCE1"), False),
            ExternalOrderClaim(InstrumentId.from_str("ETHUSDT-PERP.OKX"), None, True),
        ]
