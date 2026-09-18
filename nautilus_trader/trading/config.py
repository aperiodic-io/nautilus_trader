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

from __future__ import annotations

from typing import Any
from typing import NamedTuple

import msgspec

from nautilus_trader.common.config import NautilusConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.config import msgspec_encoding_hook
from nautilus_trader.common.config import resolve_config_path
from nautilus_trader.common.config import resolve_path
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import StrategyId


class ExternalOrderClaim(NamedTuple):
    """
    A parsed `external_order_claims` entry.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument ID being claimed.
    client_id : ClientId, optional
        The specific account (execution client) the claim is scoped to. ``None`` means
        the claim was written without an `@` suffix (see `wildcard`).
    wildcard : bool, default False
        If the claim was written as an explicit `INSTRUMENT@*`, meaning it applies to
        every account of the instrument's venue, including a venue with multiple
        accounts. When `client_id` is ``None`` and `wildcard` is ``False`` (a bare
        `INSTRUMENT` claim), the claim only applies on a venue with a single account;
        a venue with multiple accounts has no default account, so a bare claim there is
        not applied.

    """

    instrument_id: InstrumentId
    client_id: ClientId | None = None
    wildcard: bool = False


def parse_external_order_claims(
    config_claims: list[InstrumentId | str] | None,
) -> list[ExternalOrderClaim]:
    """
    Parse `external_order_claims` config entries into `ExternalOrderClaim` objects.

    Each entry is either an `InstrumentId`, a bare instrument string (for example
    ``"ETHUSDT-PERP.BINANCE"``), or an account-scoped string with a single ``@``
    suffix: ``"ETHUSDT-PERP.BINANCE@BINANCE2"`` claims that instrument for the
    `BINANCE2` account only, and ``"ETHUSDT-PERP.BINANCE@*"`` claims it for every
    account of the venue (an explicit wildcard).

    Parameters
    ----------
    config_claims : list[InstrumentId | str], optional
        The raw `external_order_claims` config value.

    Returns
    -------
    list[ExternalOrderClaim]

    Raises
    ------
    ValueError
        If a string entry contains more than one `@`, or an empty account part.

    """
    if config_claims is None:
        return []

    claims: list[ExternalOrderClaim] = []

    for entry in config_claims:
        if isinstance(entry, InstrumentId):
            claims.append(ExternalOrderClaim(entry, None, False))
            continue

        parts = entry.split("@")

        if len(parts) == 1:
            claims.append(ExternalOrderClaim(InstrumentId.from_str(parts[0]), None, False))
        elif len(parts) == 2:
            instrument_id = InstrumentId.from_str(parts[0])
            account_part = parts[1]

            if not account_part:
                raise ValueError(
                    f"Invalid external order claim {entry!r}: empty account part after '@' "
                    "(use 'INSTRUMENT@CLIENT_ID' or 'INSTRUMENT@*')",
                )

            if account_part == "*":
                claims.append(ExternalOrderClaim(instrument_id, None, True))
            else:
                claims.append(ExternalOrderClaim(instrument_id, ClientId(account_part), False))
        else:
            raise ValueError(
                f"Invalid external order claim {entry!r}: expected at most one '@' "
                "(use 'INSTRUMENT', 'INSTRUMENT@CLIENT_ID', or 'INSTRUMENT@*')",
            )

    return claims


class StrategyConfig(NautilusConfig, kw_only=True, frozen=True):
    """
    The base model for all trading strategy configurations.

    Parameters
    ----------
    strategy_id : StrategyId, optional
        The unique ID for the strategy. Will become the strategy ID if not None.
    order_id_tag : str, optional
        The unique order ID tag for the strategy. Must be unique
        amongst all running strategies for a particular trader ID.
    use_uuid_client_order_ids : bool, default False
        If UUID4's should be used for client order ID values.
    use_hyphens_in_client_order_ids : bool, default True
        If hyphens should be used in generated client order ID values.
    oms_type : OmsType, optional
        The order management system type for the strategy. This will determine
        how the `ExecutionEngine` handles position IDs.
    external_order_claims : list[InstrumentId | str], optional
        The external order claim instrument IDs.
        External orders and reconciled position exposure for matching instrument IDs will be associated
        with (claimed by) the strategy.
        Each entry is an `InstrumentId`, a bare instrument string (for example ``"ETHUSDT-PERP.BINANCE"``),
        or an account-scoped string with a single ``@`` suffix. On a venue with a single execution client
        (account), a bare claim behaves as before. On a venue with multiple execution clients (accounts),
        there is no default account, so a bare claim is not applied there; use
        ``"ETHUSDT-PERP.BINANCE@BINANCE2"`` to claim that instrument for one account only, or
        ``"ETHUSDT-PERP.BINANCE@*"`` to claim it for every account of that venue.
    manage_contingent_orders : bool, default False
        If OTO, OCO, and OUO **open** contingent orders should be managed automatically by the strategy.
        Any emulated orders which are active local will be managed by the `OrderEmulator` instead.
    manage_gtd_expiry : bool, default False
        If all order GTD time in force expirations should be managed by the strategy.
        If True, then will ensure open orders have their GTD timers re-activated on start.
    manage_stop : bool, default False
        If the strategy should automatically perform a market exit when stopped.
        If True, calling stop() will first cancel all orders and close all positions
        before the strategy transitions to the STOPPED state.
    market_exit_interval_ms : int, default 100
        The interval in milliseconds to check for in-flight orders and open positions
        during a market exit.
    market_exit_max_attempts : int, default 100
        The maximum number of attempts to wait for orders and positions to close
        during a market exit before completing. Defaults to 100 attempts
        (10 seconds at 100ms intervals).
    market_exit_time_in_force : TimeInForce, default ``GTC``
        The time in force for closing market orders during a market exit.
    market_exit_reduce_only : bool, default True
        If closing market orders during a market exit should be reduce only.
    log_events : bool, default True
        If events should be logged by the strategy.
        If False, then only warning events and above are logged.
    log_commands : bool, default True
        If commands should be logged by the strategy.
    log_rejected_due_post_only_as_warning : bool, default True
        If order rejected events where `due_post_only` is True should be logged as warnings.

    """

    strategy_id: StrategyId | None = None
    order_id_tag: str | None = None
    use_uuid_client_order_ids: bool = False
    use_hyphens_in_client_order_ids: bool = True
    oms_type: str | None = None
    external_order_claims: list[InstrumentId] | None = None
    manage_contingent_orders: bool = False
    manage_gtd_expiry: bool = False
    manage_stop: bool = False
    market_exit_interval_ms: PositiveInt = 100
    market_exit_max_attempts: PositiveInt = 100
    market_exit_time_in_force: TimeInForce = TimeInForce.GTC
    market_exit_reduce_only: bool = True
    log_events: bool = True
    log_commands: bool = True
    log_rejected_due_post_only_as_warning: bool = True


class ImportableStrategyConfig(NautilusConfig, frozen=True):
    """
    Configuration for a trading strategy instance.

    Parameters
    ----------
    strategy_path : str
        The fully qualified name of the strategy class.
    config_path : str
        The fully qualified name of the config class.
    config : dict[str, Any]
        The strategy configuration.

    """

    strategy_path: str
    config_path: str
    config: dict[str, Any]


class StrategyFactory:
    """
    Provides strategy creation from importable configurations.
    """

    @staticmethod
    def create(config: ImportableStrategyConfig):
        """
        Create a trading strategy from the given configuration.

        Parameters
        ----------
        config : ImportableStrategyConfig
            The configuration for the building step.

        Returns
        -------
        Strategy

        Raises
        ------
        TypeError
            If `config` is not of type `ImportableStrategyConfig`.

        """
        PyCondition.type(config, ImportableStrategyConfig, "config")
        strategy_cls = resolve_path(config.strategy_path)
        config_cls = resolve_config_path(config.config_path)
        json = msgspec.json.encode(config.config, enc_hook=msgspec_encoding_hook)
        config = config_cls.parse(json)
        return strategy_cls(config=config)


class ImportableControllerConfig(NautilusConfig, frozen=True):
    """
    Configuration for a controller instance.

    Parameters
    ----------
    controller_path : str
        The fully qualified name of the controller class.
    config_path : str
        The fully qualified name of the config class.
    config : dict[str, Any]
        The controller configuration.

    """

    controller_path: str
    config_path: str
    config: dict
