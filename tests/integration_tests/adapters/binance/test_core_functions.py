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

import pytest

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.common.enums import BinanceFuturesPositionSide
from nautilus_trader.adapters.binance.common.positions import make_venue_position_id
from nautilus_trader.adapters.binance.common.symbol import BinanceSymbol
from nautilus_trader.adapters.binance.common.symbol import BinanceSymbols
from nautilus_trader.adapters.binance.futures.enums import BinanceFuturesEnumParser
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId


class TestBinanceCoreFunctions:
    def test_format_symbol(self):
        # Arrange
        symbol = "ethusdt-perp"

        # Act
        result = BinanceSymbol(symbol)

        # Assert
        assert result == "ETHUSDT"

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            # Linear (USDT-M) perpetuals: strip -PERP suffix
            ("BTCUSDT-PERP", "BTCUSDT"),
            ("ETHUSDT-PERP", "ETHUSDT"),
            ("BNBUSDT-PERP", "BNBUSDT"),
            ("BTCBUSD-PERP", "BTCBUSD"),
            # Short-base linear perpetuals
            ("AIUSDT-PERP", "AIUSDT"),
            # COIN-M (inverse) perpetuals: replace -PERP with _PERP
            ("BTCUSD-PERP", "BTCUSD_PERP"),
            ("ETHUSD-PERP", "ETHUSD_PERP"),
            ("BNBUSD-PERP", "BNBUSD_PERP"),
            ("LINKUSD-PERP", "LINKUSD_PERP"),
            ("DOTUSD-PERP", "DOTUSD_PERP"),
            # Spot / no -PERP suffix: pass through
            ("BTCUSDT", "BTCUSDT"),
            ("ETHUSDT", "ETHUSDT"),
        ],
    )
    def test_format_symbol_perp_variants(self, symbol, expected):
        assert BinanceSymbol(symbol) == expected

    @pytest.mark.parametrize(
        ("symbol", "account_type", "expected"),
        [
            # COIN-M perpetuals round-trip
            ("BTCUSD_PERP", BinanceAccountType.COIN_FUTURES, "BTCUSD-PERP"),
            ("BNBUSD_PERP", BinanceAccountType.COIN_FUTURES, "BNBUSD-PERP"),
            # Linear perpetuals round-trip
            ("BTCUSDT", BinanceAccountType.USDT_FUTURES, "BTCUSDT-PERP"),
            ("ETHUSDT", BinanceAccountType.USDT_FUTURES, "ETHUSDT-PERP"),
            # Spot: no suffix added
            ("BTCUSDT", BinanceAccountType.SPOT, "BTCUSDT"),
        ],
    )
    def test_parse_as_nautilus(self, symbol, account_type, expected):
        result = BinanceSymbol(symbol).parse_as_nautilus(account_type)
        assert result == expected

    def test_convert_symbols_list_to_json_array(self):
        # Arrange
        symbols = ["BTCUSDT", "ETHUSDT-PERP", " XRDUSDT"]

        # Act
        result = BinanceSymbols(symbols)

        # Assert
        assert result == '["BTCUSDT","ETHUSDT","XRDUSDT"]'

    @pytest.mark.parametrize(
        ("account_type", "expected"),
        [
            [BinanceAccountType.SPOT, True],
            [BinanceAccountType.MARGIN, False],
            [BinanceAccountType.ISOLATED_MARGIN, False],
            [BinanceAccountType.USDT_FUTURES, False],
            [BinanceAccountType.COIN_FUTURES, False],
        ],
    )
    def test_binance_account_type_is_spot(self, account_type, expected):
        # Arrange, Act, Assert
        assert account_type.is_spot == expected

    @pytest.mark.parametrize(
        ("account_type", "expected"),
        [
            [BinanceAccountType.SPOT, False],
            [BinanceAccountType.MARGIN, True],
            [BinanceAccountType.ISOLATED_MARGIN, True],
            [BinanceAccountType.USDT_FUTURES, False],
            [BinanceAccountType.COIN_FUTURES, False],
        ],
    )
    def test_binance_account_type_is_margin(self, account_type, expected):
        # Arrange, Act, Assert
        assert account_type.is_margin == expected

    @pytest.mark.parametrize(
        ("account_type", "expected"),
        [
            [BinanceAccountType.SPOT, True],
            [BinanceAccountType.MARGIN, True],
            [BinanceAccountType.ISOLATED_MARGIN, True],
            [BinanceAccountType.USDT_FUTURES, False],
            [BinanceAccountType.COIN_FUTURES, False],
        ],
    )
    def test_binance_account_type_is_spot_or_margin(self, account_type, expected):
        # Arrange, Act, Assert
        assert account_type.is_spot_or_margin == expected

    @pytest.mark.parametrize(
        ("account_type", "expected"),
        [
            [BinanceAccountType.SPOT, False],
            [BinanceAccountType.MARGIN, False],
            [BinanceAccountType.ISOLATED_MARGIN, False],
            [BinanceAccountType.USDT_FUTURES, True],
            [BinanceAccountType.COIN_FUTURES, True],
        ],
    )
    def test_binance_account_type_is_futures(self, account_type, expected):
        # Arrange, Act, Assert
        assert account_type.is_futures == expected

    @pytest.mark.parametrize(
        ("account_id", "is_multi_account", "expected"),
        [
            # A venue with a single account keeps the plain ID, whatever the client
            # (and hence the account issuer) is named
            ("BINANCE-USDT_FUTURES-master", False, "ETHUSDT-PERP.BINANCE-LONG"),
            ("BINANCE1-USDT_FUTURES-master", False, "ETHUSDT-PERP.BINANCE-LONG"),
            # Every account of a venue with multiple accounts is suffixed with its
            # issuer, regardless of which one happens to be named after the venue
            ("BINANCE1-USDT_FUTURES-master", True, "ETHUSDT-PERP.BINANCE-LONG-BINANCE1"),
            ("BINANCE2-USDT_FUTURES-master", True, "ETHUSDT-PERP.BINANCE-LONG-BINANCE2"),
        ],
    )
    def test_make_venue_position_id(self, account_id, is_multi_account, expected):
        # Arrange
        instrument_id = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")

        # Act
        result = make_venue_position_id(
            instrument_id,
            "LONG",
            AccountId(account_id),
            is_multi_account,
        )

        # Assert
        assert result == PositionId(expected)

    @pytest.mark.parametrize(
        ("position_id", "expected"),
        [
            ("001-LONG", BinanceFuturesPositionSide.LONG),
            ("001-SHORT", BinanceFuturesPositionSide.SHORT),
            ("001-BOTH", BinanceFuturesPositionSide.BOTH),
            ("ETHUSDT-PERP.BINANCE-LONG", BinanceFuturesPositionSide.LONG),
            ("ETHUSDT-PERP.BINANCE-LONG-BINANCE1", BinanceFuturesPositionSide.LONG),
            ("ETHUSDT-PERP.BINANCE-SHORT-BINANCE2", BinanceFuturesPositionSide.SHORT),
            ("ETHUSDT-PERP.BINANCE-BOTH-BINANCE10", BinanceFuturesPositionSide.BOTH),
        ],
    )
    def test_parse_position_id_to_position_side(self, position_id, expected):
        # Act
        result = BinanceFuturesEnumParser().parse_position_id_to_binance_futures_position_side(
            PositionId(position_id),
        )

        # Assert
        assert result == expected

    @pytest.mark.parametrize(
        "position_id",
        ["ETHUSDT-PERP.BINANCE-S-001", "ETHUSDT-PERP.BINANCE-S-001-BINANCE2", "P-123"],
    )
    def test_parse_position_id_without_position_side_raises(self, position_id):
        # Act, Assert
        with pytest.raises(RuntimeError, match="unrecognized position id"):
            BinanceFuturesEnumParser().parse_position_id_to_binance_futures_position_side(
                PositionId(position_id),
            )

    @pytest.mark.parametrize("position_side", ["LONG", "SHORT"])
    def test_make_venue_position_id_round_trips_through_parser(self, position_side):
        # Arrange
        position_id = make_venue_position_id(
            InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
            position_side,
            AccountId("BINANCE2-USDT_FUTURES-master"),
            True,
        )

        # Act
        side = BinanceFuturesEnumParser().parse_position_id_to_binance_futures_position_side(
            position_id,
        )

        # Assert
        assert side == BinanceFuturesPositionSide(position_side)
