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

from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId


def make_venue_position_id(
    instrument_id: InstrumentId,
    position_side: str,
    account_id: AccountId,
    is_multi_account: bool,
) -> PositionId:
    """
    Return the venue position ID for a Binance Futures hedge mode position.

    Where the venue has more than one account, the ID is suffixed with the account's
    issuer so that the positions of multiple accounts for the same instrument and side
    remain distinct. A venue with a single account never gets a suffix, whatever that
    one client happens to be named (`is_multi_account` reflects the venue's actual
    client count, from `ExecutionClient.is_multi_account_venue`, rather than comparing
    the account issuer to the venue's name).

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument ID for the position.
    position_side : str
        The Binance position side (LONG or SHORT).
    account_id : AccountId
        The account ID for the position.
    is_multi_account : bool
        If the venue currently has more than one account (execution client)
        registered with the execution engine.

    Returns
    -------
    PositionId

    """
    if not is_multi_account:
        return PositionId(f"{instrument_id}-{position_side}")

    return PositionId(f"{instrument_id}-{position_side}-{account_id.get_issuer()}")
