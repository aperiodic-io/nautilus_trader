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
) -> PositionId:
    """
    Return the venue position ID for a Binance Futures hedge mode position.

    Where the account is not the primary account for the venue (its issuer differs from
    the venue), the ID is suffixed with the issuer so that the positions of multiple
    accounts for the same instrument and side remain distinct.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument ID for the position.
    position_side : str
        The Binance position side (LONG or SHORT).
    account_id : AccountId
        The account ID for the position.

    Returns
    -------
    PositionId

    """
    issuer = account_id.get_issuer()
    if issuer == instrument_id.venue.value:
        return PositionId(f"{instrument_id}-{position_side}")

    return PositionId(f"{instrument_id}-{position_side}-{issuer}")
