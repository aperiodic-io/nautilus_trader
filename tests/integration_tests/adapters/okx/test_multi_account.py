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
Tests for running multiple OKX accounts (execution clients) against one cache.
"""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from nautilus_trader.adapters.okx.config import OKXExecClientConfig
from nautilus_trader.adapters.okx.constants import OKX_VENUE
from nautilus_trader.adapters.okx.execution import OKXExecutionClient
from nautilus_trader.common.component import MessageBus
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.execution.engine import ExecutionEngine
from nautilus_trader.execution.messages import CancelAllOrders
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from tests.integration_tests.adapters.okx.conftest import _create_ws_mock


@pytest.fixture
def okx_clients(
    event_loop,
    monkeypatch,
    msgbus,
    cache,
    live_clock,
    mock_instrument_provider,
    instrument,
):
    """
    Build a primary ("OKX") and a secondary ("OKXB") execution client sharing one cache,
    each with its own HTTP client and WebSocket mocks.
    """
    ws_calls: list[dict] = []

    def with_credentials(*args, **kwargs):
        ws_calls.append(kwargs)
        return _create_ws_mock()

    monkeypatch.setattr(
        "nautilus_trader.adapters.okx.execution.nautilus_pyo3.OKXWebSocketClient.with_credentials",
        with_credentials,
    )
    mock_instrument_provider.instrument_types = (nautilus_pyo3.OKXInstrumentType.SPOT,)
    cache.add_instrument(instrument)

    clients = {}
    for name, key in (("OKX", "KEY_A"), ("OKXB", "KEY_B")):
        http_client = MagicMock()
        http_client.api_key = key
        config = OKXExecClientConfig(
            api_key=key,
            api_secret=f"{key}_SECRET",
            api_passphrase=f"{key}_PASSPHRASE",
            instrument_types=(nautilus_pyo3.OKXInstrumentType.SPOT,),
            use_mm_mass_cancel=False,
        )
        client = OKXExecutionClient(
            loop=event_loop,
            client=http_client,
            msgbus=msgbus,
            cache=cache,
            clock=live_clock,
            instrument_provider=mock_instrument_provider,
            config=config,
            name=name,
        )
        clients[name] = client

    return clients, ws_calls


def _open_order(cache, instrument, account_id: AccountId, n: int) -> LimitOrder:
    order = LimitOrder(
        trader_id=TestIdStubs.trader_id(),
        strategy_id=TestIdStubs.strategy_id(),
        instrument_id=instrument.id,
        client_order_id=ClientOrderId(f"O-{account_id.get_issuer()}-{n}"),
        order_side=OrderSide.BUY,
        quantity=Quantity.from_int(100),
        price=Price.from_str("1.0000"),
        init_id=TestIdStubs.uuid(),
        ts_init=0,
    )
    cache.add_order(order, None, None)
    order.apply(TestEventStubs.order_submitted(order=order, account_id=account_id))
    cache.update_order(order)
    order.apply(
        TestEventStubs.order_accepted(
            order=order,
            account_id=account_id,
            venue_order_id=VenueOrderId(f"V-{account_id.get_issuer()}-{n}"),
        ),
    )
    cache.update_order(order)
    return order


def _cancel_all(instrument) -> CancelAllOrders:
    return CancelAllOrders(
        trader_id=TestIdStubs.trader_id(),
        strategy_id=TestIdStubs.strategy_id(),
        instrument_id=instrument.id,
        order_side=OrderSide.NO_ORDER_SIDE,
        command_id=TestIdStubs.uuid(),
        ts_init=0,
    )


def test_client_and_account_ids_derive_from_name(okx_clients):
    # Arrange
    clients, _ = okx_clients

    # Assert
    assert clients["OKX"].id == ClientId("OKX")
    assert clients["OKXB"].id == ClientId("OKXB")
    assert clients["OKX"].account_id == AccountId("OKX-master")
    assert clients["OKXB"].account_id == AccountId("OKXB-master")
    assert clients["OKX"].venue == clients["OKXB"].venue == OKX_VENUE


def test_websockets_created_with_each_accounts_credentials_and_account_id(okx_clients):
    # Arrange
    _, ws_calls = okx_clients

    # Assert - a private and a business WebSocket per account
    assert len(ws_calls) == 4
    by_key = {}
    for call in ws_calls:
        by_key.setdefault(call["api_key"], []).append(call)
    assert set(by_key) == {"KEY_A", "KEY_B"}
    for call in by_key["KEY_A"]:
        assert call["api_secret"] == "KEY_A_SECRET"
        assert call["api_passphrase"] == "KEY_A_PASSPHRASE"
        assert call["account_id"] == nautilus_pyo3.AccountId("OKX-master")
    for call in by_key["KEY_B"]:
        assert call["api_secret"] == "KEY_B_SECRET"
        assert call["api_passphrase"] == "KEY_B_PASSPHRASE"
        assert call["account_id"] == nautilus_pyo3.AccountId("OKXB-master")


def test_both_clients_register_with_engine_for_same_venue(okx_clients, cache, live_clock):
    # Arrange
    clients, _ = okx_clients
    engine_msgbus = MessageBus(trader_id=TestIdStubs.trader_id(), clock=live_clock)
    engine = ExecutionEngine(msgbus=engine_msgbus, cache=cache, clock=live_clock)

    # Act
    engine.register_client(clients["OKX"])
    engine.register_client(clients["OKXB"])

    # Assert
    assert engine._routing_map[OKX_VENUE] == clients["OKX"]
    assert engine._secondary_clients == {ClientId("OKXB"): OKX_VENUE}


@pytest.mark.asyncio
async def test_each_client_cancels_only_its_own_orders(okx_clients, cache, instrument):
    # Arrange
    clients, _ = okx_clients
    primary_orders = [_open_order(cache, instrument, AccountId("OKX-master"), n) for n in range(2)]
    secondary_orders = [
        _open_order(cache, instrument, AccountId("OKXB-master"), n) for n in range(3)
    ]

    # Act
    for client in clients.values():
        await client._cancel_all_orders(_cancel_all(instrument))

    # Assert
    for name, orders in (("OKX", primary_orders), ("OKXB", secondary_orders)):
        ws = clients[name]._ws_client
        ws.batch_cancel_orders.assert_called_once()
        assert len(ws.batch_cancel_orders.call_args[0][0]) == len(orders), name


@pytest.mark.asyncio
async def test_mass_cancel_failure_rejects_only_own_account_orders(
    okx_clients,
    cache,
    instrument,
):
    # Arrange
    clients, _ = okx_clients
    secondary = clients["OKXB"]
    _open_order(cache, instrument, AccountId("OKX-master"), 0)
    secondary_order = _open_order(cache, instrument, AccountId("OKXB-master"), 0)
    secondary._ws_client.mass_cancel_orders = AsyncMock(side_effect=RuntimeError("boom"))
    rejected = []
    secondary.generate_order_cancel_rejected = lambda **kwargs: rejected.append(
        kwargs["client_order_id"],
    )

    # Act
    await secondary._cancel_all_orders_mass_cancel(_cancel_all(instrument))

    # Assert
    assert rejected == [secondary_order.client_order_id]
