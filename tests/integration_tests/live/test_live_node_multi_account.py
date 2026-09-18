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
Tests building a live node with multiple Binance and OKX accounts (no network access).
"""

import msgspec
import pytest

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
from nautilus_trader.adapters.binance.config import BinanceExecClientConfig
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance.factories import BinanceLiveExecClientFactory
from nautilus_trader.adapters.okx.config import OKXDataClientConfig
from nautilus_trader.adapters.okx.config import OKXExecClientConfig
from nautilus_trader.adapters.okx.factories import OKXLiveDataClientFactory
from nautilus_trader.adapters.okx.factories import OKXLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.config import LiveExecClientConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.test_kit.functions import ensure_all_tasks_completed


BINANCE = Venue("BINANCE")
OKX = Venue("OKX")


def _binance_exec_config(n: int) -> BinanceExecClientConfig:
    return BinanceExecClientConfig(
        api_key=f"BINANCE_KEY_{n}",
        api_secret=f"BINANCE_SECRET_{n}",
        account_type=BinanceAccountType.USDT_FUTURES,
        instrument_provider=InstrumentProviderConfig(load_all=False),
    )


def _okx_exec_config(n: int) -> OKXExecClientConfig:
    return OKXExecClientConfig(
        api_key=f"OKX_KEY_{n}",
        api_secret=f"OKX_SECRET_{n}",
        api_passphrase=f"OKX_PASSPHRASE_{n}",
        instrument_types=(nautilus_pyo3.OKXInstrumentType.SWAP,),
        instrument_provider=InstrumentProviderConfig(load_all=False),
    )


def _binance_key(n: int) -> str:
    # A venue with multiple accounts has no default account, so no key is the venue name
    return f"BINANCE{n}"


def _okx_key(n: int) -> str:
    return f"OKX{n}"


class TestTradingNodeMultiAccount:
    def teardown(self):
        ensure_all_tasks_completed()

    def _build_node(
        self,
        event_loop,
        binance_accounts: int,
        okx_accounts: int,
    ) -> TradingNode:
        exec_clients: dict[str, LiveExecClientConfig] = {}
        exec_clients.update(
            {_binance_key(n): _binance_exec_config(n) for n in range(1, binance_accounts + 1)},
        )
        exec_clients.update(
            {_okx_key(n): _okx_exec_config(n) for n in range(1, okx_accounts + 1)},
        )
        data_clients: dict[str, LiveDataClientConfig] = {}
        if binance_accounts:
            data_clients["BINANCE"] = BinanceDataClientConfig(
                api_key="BINANCE_KEY_1",
                api_secret="BINANCE_SECRET_1",
                account_type=BinanceAccountType.USDT_FUTURES,
                instrument_provider=InstrumentProviderConfig(load_all=False),
            )
        if okx_accounts:
            data_clients["OKX"] = OKXDataClientConfig(
                api_key="OKX_KEY_1",
                api_secret="OKX_SECRET_1",
                api_passphrase="OKX_PASSPHRASE_1",
                instrument_types=(nautilus_pyo3.OKXInstrumentType.SWAP,),
                instrument_provider=InstrumentProviderConfig(load_all=False),
            )

        node = TradingNode(
            config=TradingNodeConfig(
                logging=LoggingConfig(bypass_logging=True),
                data_clients=data_clients,
                exec_clients=exec_clients,
            ),
            loop=event_loop,
        )
        if binance_accounts:
            node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
        if okx_accounts:
            node.add_data_client_factory("OKX", OKXLiveDataClientFactory)
        for n in range(1, binance_accounts + 1):
            node.add_exec_client_factory(_binance_key(n), BinanceLiveExecClientFactory)
        for n in range(1, okx_accounts + 1):
            node.add_exec_client_factory(_okx_key(n), OKXLiveExecClientFactory)

        node.build()
        return node

    def test_build_with_two_binance_and_two_okx_accounts(self, event_loop_for_setup):
        # Act
        node = self._build_node(event_loop_for_setup, binance_accounts=2, okx_accounts=2)

        # Assert
        exec_engine = node.kernel.exec_engine
        assert sorted(c.value for c in exec_engine.registered_clients) == [
            "BINANCE1",
            "BINANCE2",
            "OKX1",
            "OKX2",
        ]
        # No default account: multi-account venues have no venue routing
        assert exec_engine._routing_map == {}
        assert [c.id.value for c in exec_engine._venue_clients[BINANCE]] == ["BINANCE1", "BINANCE2"]
        assert [c.id.value for c in exec_engine._venue_clients[OKX]] == ["OKX1", "OKX2"]

        clients = exec_engine._clients
        assert clients[ClientId("BINANCE1")].account_id == AccountId(
            "BINANCE1-USDT_FUTURES-master",
        )
        assert clients[ClientId("BINANCE2")].account_id == AccountId(
            "BINANCE2-USDT_FUTURES-master",
        )
        assert clients[ClientId("OKX1")].account_id == AccountId("OKX1-master")
        assert clients[ClientId("OKX2")].account_id == AccountId("OKX2-master")

    def test_each_account_uses_its_own_credentials(self, event_loop_for_setup):
        # Act
        node = self._build_node(event_loop_for_setup, binance_accounts=2, okx_accounts=2)

        # Assert
        clients = node.kernel.exec_engine._clients
        assert clients[ClientId("BINANCE1")]._http_client.api_key == "BINANCE_KEY_1"
        assert clients[ClientId("BINANCE2")]._http_client.api_key == "BINANCE_KEY_2"
        assert clients[ClientId("OKX1")]._http_client.api_key == "OKX_KEY_1"
        assert clients[ClientId("OKX2")]._http_client.api_key == "OKX_KEY_2"
        assert (
            clients[ClientId("BINANCE1")]._http_client
            is not clients[ClientId("BINANCE2")]._http_client
        )
        assert clients[ClientId("OKX1")]._http_client is not clients[ClientId("OKX2")]._http_client

    def test_build_with_ten_accounts_on_single_venue(self, event_loop_for_setup):
        # Act
        node = self._build_node(event_loop_for_setup, binance_accounts=10, okx_accounts=0)

        # Assert
        exec_engine = node.kernel.exec_engine
        clients = exec_engine._clients
        assert len(clients) == 10
        assert BINANCE not in exec_engine._routing_map
        assert {c.id for c in exec_engine._venue_clients[BINANCE]} == {
            ClientId(f"BINANCE{n}") for n in range(1, 11)
        }
        account_ids = {client.account_id for client in clients.values()}
        assert len(account_ids) == 10
        api_keys = {client._http_client.api_key for client in clients.values()}
        assert api_keys == {f"BINANCE_KEY_{n}" for n in range(1, 11)}

    def test_build_with_twenty_accounts_across_two_venues(self, event_loop_for_setup):
        # Act
        node = self._build_node(event_loop_for_setup, binance_accounts=10, okx_accounts=10)

        # Assert
        exec_engine = node.kernel.exec_engine
        clients = exec_engine._clients
        assert len(clients) == 20
        assert exec_engine._routing_map == {}
        for venue, venue_clients in exec_engine._venue_clients.items():
            assert len(venue_clients) == 10
            for client in venue_clients:
                assert client.venue == venue
                assert client.id.value.startswith(venue.value)
        for client in clients.values():
            assert client.account_id.get_issuer() == client.id.value
        assert len({client.account_id for client in clients.values()}) == 20

    def test_build_from_json_config(self, event_loop_for_setup):
        # Arrange - config file style with one key per account
        exec_client = {
            "path": "nautilus_trader.adapters.binance.config:BinanceExecClientConfig",
        }
        raw = msgspec.json.encode(
            {
                "environment": "live",
                "trader_id": "TESTER-001",
                "logging": {"bypass_logging": True},
                "exec_clients": {
                    key: {
                        **exec_client,
                        "config": {
                            "api_key": f"{key}_KEY",
                            "api_secret": f"{key}_SECRET",
                            "account_type": "USDT_FUTURES",
                            "instrument_provider": {"load_all": False},
                        },
                    }
                    for key in ("BINANCE1", "BINANCE2", "BINANCE3")
                },
            },
        )

        node = TradingNode(config=TradingNodeConfig.parse(raw), loop=event_loop_for_setup)
        for key in ("BINANCE1", "BINANCE2", "BINANCE3"):
            node.add_exec_client_factory(key, BinanceLiveExecClientFactory)

        # Act
        node.build()

        # Assert
        clients = node.kernel.exec_engine._clients
        assert sorted(c.value for c in clients) == ["BINANCE1", "BINANCE2", "BINANCE3"]
        assert clients[ClientId("BINANCE3")]._http_client.api_key == "BINANCE3_KEY"

    def test_hyphenated_keys_collapse_to_same_client_id_and_fail(self, event_loop_for_setup):
        # Arrange - the node builder only uses the part of a key before the first hyphen
        node = TradingNode(
            config=TradingNodeConfig(
                logging=LoggingConfig(bypass_logging=True),
                exec_clients={
                    "BINANCE-A": _binance_exec_config(1),
                    "BINANCE-B": _binance_exec_config(2),
                },
            ),
            loop=event_loop_for_setup,
        )
        node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)

        # Act, Assert
        with pytest.raises(KeyError, match="BINANCE"):
            node.build()

    def test_client_named_after_venue_with_other_accounts_fails_to_build(
        self,
        event_loop_for_setup,
    ):
        # Arrange - "BINANCE" would be a default account among multiple accounts
        node = TradingNode(
            config=TradingNodeConfig(
                logging=LoggingConfig(bypass_logging=True),
                exec_clients={
                    "BINANCE": _binance_exec_config(1),
                    "BINANCE2": _binance_exec_config(2),
                },
            ),
            loop=event_loop_for_setup,
        )
        node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
        node.add_exec_client_factory("BINANCE2", BinanceLiveExecClientFactory)

        # Act, Assert
        with pytest.raises(ValueError, match="no default account"):
            node.build()

    def test_single_account_named_after_venue_builds_with_venue_routing(
        self,
        event_loop_for_setup,
    ):
        # Arrange
        node = TradingNode(
            config=TradingNodeConfig(
                logging=LoggingConfig(bypass_logging=True),
                exec_clients={"BINANCE": _binance_exec_config(1)},
            ),
            loop=event_loop_for_setup,
        )
        node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)

        # Act
        node.build()

        # Assert
        exec_engine = node.kernel.exec_engine
        assert exec_engine._routing_map[BINANCE].id == ClientId("BINANCE")
        assert exec_engine._clients[ClientId("BINANCE")].account_id == AccountId(
            "BINANCE-USDT_FUTURES-master",
        )
