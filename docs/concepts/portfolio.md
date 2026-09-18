# Portfolio

The Portfolio is the central hub for managing and tracking all positions across active strategies for the trading node or backtest.
It consolidates position data from multiple instruments, providing a unified view of your holdings, risk exposure, and overall performance.

## Currency conversion

The Portfolio supports automatic currency conversion for PnL and exposure calculations,
allowing you to view results in your preferred currency. This is particularly useful when
trading across multiple instruments with different cost currencies or managing multiple
accounts with different base currencies.

### Supported conversions

Currency conversion is available for the following portfolio queries:

- `realized_pnl()` / `realized_pnls()` - Convert realized PnL to target currency.
- `unrealized_pnl()` / `unrealized_pnls()` - Convert unrealized PnL to target currency.
- `total_pnl()` / `total_pnls()` - Convert total PnL to target currency.
- `net_exposure()` / `net_exposures()` - Convert net exposure to target currency.

All methods accept an optional `target_currency` parameter to specify the desired output
currency.

### Single account behavior

When querying a single account without specifying `target_currency`, the Portfolio
automatically converts values to that account's base currency:

```python
# Returns exposure in the account's base currency (e.g., USD)
exposure = portfolio.net_exposures(venue=BINANCE, account_id=account_id)
```

### Multi-account behavior

A venue with more than one account (for example two Binance accounts trading under
separate API keys) has **no default account**. Querying `net_exposure(s)` or
`mark_values` by `venue` alone, with no `account_id`, never silently combines two
accounts' exposure: netting a long position on one account against a short on another
would cancel out and hide real risk, so these calls log an error and return `None`
instead of guessing. This holds even when both accounts share the same base currency.

```python
# A venue with two accounts, no account_id given
exposures = portfolio.net_exposures(venue=BINANCE)
# Returns None and logs an error - there is no default account to aggregate across
```

Query one account explicitly, or discover every account of a venue and query each in
turn:

```python
# Query one account's exposure explicitly
exposures = portfolio.net_exposures(venue=BINANCE, account_id=account_id)

# Discover every account of a venue, and query each one
for account_id in portfolio.account_ids(BINANCE):
    print(account_id, portfolio.net_exposures(venue=BINANCE, account_id=account_id))
```

`Portfolio.net_position_by_account(instrument_id)` gives the equivalent per-account
breakdown for net position, without querying each account individually.

PnL aggregation is unaffected by this rule: `realized_pnl(s)`, `unrealized_pnl(s)`, and
`total_pnl(s)` still sum across every account by default (summing profit and loss across
accounts is mathematically valid, unlike netting exposure, which can cancel opposite
positions):

```python
# Multiple accounts: PnL sums across them by default
pnls = portfolio.unrealized_pnls(venue=BINANCE)
# Returns {USDT: Money(...)}

# Query one account's PnL explicitly
pnl = portfolio.unrealized_pnl(instrument_id, account_id=account_id)

# Force a single currency across accounts (see "Conversion failures" below)
pnls = portfolio.unrealized_pnls(venue=BINANCE, target_currency=USD)
# Returns {USD: Money(...)}
```

### Conversion failures

When `target_currency` is provided and currency conversion fails, behavior depends on
the method type:

- **Single-value methods** (`realized_pnl`, `unrealized_pnl`, `total_pnl`, `net_exposure`):
  Return `None` and log an error to prevent incorrect values.
- **Dict-returning methods** (`realized_pnls`, `unrealized_pnls`, `total_pnls`, `net_exposures`):
  Omit instruments that fail conversion but return results for successful conversions.

:::warning
Exchange rate data must be available when using `target_currency` for cross-currency
aggregation.
:::

### Conversion price types

When converting exposures to a target currency, the Portfolio uses different price types
depending on the position composition:

- **All long positions**: Uses `BID` prices (conservative for long exposure).
- **All short positions**: Uses `ASK` prices (conservative for short exposure).
- **Mixed positions**: Uses `MID` prices (neutral when both long and short exist).

This ensures conversions reflect realistic market conditions where you would liquidate
long positions at bid and cover short positions at ask. For mixed positions, mid-pricing
provides a neutral valuation.

If `use_mark_xrates` is enabled in the portfolio configuration, `MARK` prices replace
`MID` prices for mixed positions and general conversions.

## Equity and mark-to-market

The Portfolio exposes pull-style queries for continuous portfolio valuation and
recorded snapshots. Per-currency results use the relevant account base currency
or native cost currency.

| Method                             | Returns                                                |
| ---------------------------------- | ------------------------------------------------------ |
| `mark_values(venue, account_id)`   | Signed MTM totals for open positions.                  |
| `equity(venue, account_id)`        | Total equity combining balance and position valuation. |
| `build_snapshot(account_id)`       | Account‑wide MTM totals and valuation metadata.        |
| `snapshots(account_id)`            | Recorded account snapshots in emission order.          |
| `missing_price_instruments(venue)` | Instruments currently flagged as unpriceable.          |

Longs contribute positive notional, shorts contribute negative notional. Flat
positions are skipped.

### Equity formula

Equity combines the account balance with open-position valuation, using a different
second term depending on account type:

- **Cash accounts without a base currency**: Start with `balances_total`. For positions
  owned by that account, do not add a base-asset mark value when the balance already holds
  that asset and the instrument's cost currency differs from its base currency. Add mark
  values for inverse instruments and positions not represented by a credited balance asset.
- **Cash accounts with a base currency and betting accounts**:
  `balances_total + Σ mark_value(open positions)`.
- **Margin accounts**: `balances_total + Σ unrealized_pnl(open positions)`.

`mark_values()` always returns gross open-position values, including assets already
present in a multi-currency cash balance. The value-once rule means `equity()` and equity
snapshots count each non-inverse base asset either as a balance or a mark value, not both.
The margin path uses the same cached unrealized PnL pipeline that powers
`unrealized_pnls()`.

### Price fallback

Valuation asks `Cache` for a price in this order, stopping at the first match:

1. Mark price, if `use_mark_prices=true` (the v2 default) in `PortfolioConfig` and a mark price is cached.
2. Side-appropriate quote: `BID` for longs, `ASK` for shorts.
3. Last trade price.
4. Most recent cached bar close (populated when `bar_updates=true`).

In v2, set `use_mark_prices=false` to skip the mark tier and begin with the side-appropriate quote.

If none of the four yield a current price, the Portfolio carries the last valid price
for that instrument and position side. The next snapshot lists the instrument in
`stale_instruments`. If the position has never had a valid price, it goes into the
missing-price tracker, is listed in `unpriced_instruments`, and is excluded from the sum.

### Base currency conversion

When `convert_to_account_base_currency=true` (the default) and the account has a
`base_currency` set, cost-currency values are converted to the base currency
using `MID` xrates from `Cache.get_xrate()`. With `use_mark_xrates=true`, the cached
mark xrate from `Cache.get_mark_xrate()` is used first and falls back to `MID` if
unavailable. The output dictionary then has a single key matching the base currency.

When `convert_to_account_base_currency=false`, or the account has no `base_currency`,
results are keyed by each position's native cost currency and no xrate
conversion is applied.

If no current xrate is available for a required conversion, the Portfolio carries the
last valid rate and lists its source currency in `stale_currencies`. If no valid rate
has ever been available, the position is treated as unpriceable and flagged through
the missing-price tracker rather than silently valued at a 1.0 rate.

### Snapshot valuation metadata

`PortfolioSnapshot.total_equity` always provides the per-currency MTM breakdown.
When base-currency conversion is enabled and the account has a base currency,
`base_currency_equity` provides the headline scalar in that currency. It is `None`
when conversion is disabled or the account has no base currency.

`is_stale` is true when the snapshot uses a carried price or xrate, or excludes a
position that has never had all required valuation inputs. The related fields identify
the cause:

- `stale_instruments`: Instruments valued with carried prices.
- `stale_currencies`: Source currencies converted with carried xrates.
- `unpriced_instruments`: Instruments excluded because no complete valid valuation has
  ever been available.

Call `build_snapshot(account_id)` for an on-demand sample. Call `snapshots(account_id)`
to read the bounded recorded sequence. The methods are available from the Rust
Portfolio and Strategy API and from the Python v2 Portfolio binding.

### Automatic equity curve

In v2, `PortfolioConfig.equity_curve=true` (the default) records and publishes a
mark-to-market snapshot when each account registers, at every UTC midnight even while
the account is flat, and when the backtest or live node shuts down. Set
`equity_curve=false` for workloads such as optimizer runs that do not consume an equity
curve. On-demand `equity()` and `build_snapshot()` calculations remain available.

The separate `snapshot_interval_ms` setting remains opt-in. When set, it adds
fine-grained snapshots only while the account has an open position.

### Missing-price tracking

The tracker keeps the latest missing set for each account-filtered query scope
and the unfiltered venue scope. `missing_price_instruments(venue)` returns their
venue-wide union. Each observation remains authoritative until the same scope
runs again; a filtered result does not declare an earlier unfiltered result
resolved. It has two observable behaviors:

- A warning log fires once per instrument on the transition from no scope reporting
  it to at least one scope reporting it, not on every subsequent call. Once every
  reporting scope observes recovery, a future drop re-warns.
- When a venue goes flat (no open positions), its tracker entry is cleared so stale
  instruments do not remain flagged.

Call `missing_price_instruments(venue)` to inspect the current set.

:::tip
If `equity()` understates what you expect, check `missing_price_instruments(venue)`
before investigating the math. An empty quote, trade, and bar feed for one instrument
is the most common cause of silent gaps.
:::

### Venue and account scope

`mark_values` and `equity` accept an optional `account_id` to scope the
aggregation to a single account. With `account_id=None`, results aggregate
across every account on the venue.

An account-filtered valuation reconciles only that account's observation, so
flags raised by other accounts on the same venue survive.

## Portfolio statistics

There are a variety of built-in portfolio statistics in
`crates/analysis/src/statistics` which analyse a trading portfolio's performance
for both backtests and live trading.

The statistics are generally categorized as follows.

- PnLs based statistics (per currency)
- Returns based statistics
- Positions based statistics
- Orders based statistics

Backtest statistics are exposed after a run through `engine.get_result()`.

## Custom statistics

Custom metrics for post-run analysis can be computed from reports, snapshots, or
position data and added to the dictionaries passed to visualization APIs such as
`create_tearsheet_from_stats()`.

For example, calculate a win rate from realized PnLs:

```python
import pandas as pd


def calculate_win_rate(realized_pnls: pd.Series) -> float:
    if realized_pnls.empty:
        return 0.0

    winners = realized_pnls[realized_pnls > 0.0]
    return len(winners) / len(realized_pnls)
```

Then include the metric in offline tearsheet inputs:

```python
stats_general = {
    "Win Rate": calculate_win_rate(realized_pnls),
}
```

:::tip
Your metric should handle degenerate inputs such as empty series or insufficient data.
Return `None` for unknown or incalculable values, or a reasonable default like `0.0`
when semantically appropriate.
:::

## Returns: position vs portfolio

The analyzer tracks two distinct return series:

- **Position returns** (`analyzer.position_returns()`) measure realized return per position
  as a side-aware price return relative to the average open price. This reflects the
  instrument's price movement between entry and exit, independent of account size or
  leverage.
- **Portfolio returns** (`analyzer.portfolio_returns()`) measure daily percentage change in
  mark-to-market account equity. A $900 gain on a $100,000 account reports roughly 0.9%
  for that day.

When complete portfolio snapshots span at least two distinct UTC dates, the analyzer
uses the final snapshot from each date and computes portfolio returns automatically.
It uses them as the primary series for statistics, tearsheets, and the monthly returns
heatmap. A snapshot emitted exactly at UTC midnight closes the preceding date, keeping
the daily tier consistent with fine-grained samples. The first valid registration sample
anchors the opening value for a partial first date. Missing or unpriced account dates are
forward-filled after every account has an initial valid sample. Multiple snapshots on the
same date count as one date, so intra-day trading alone does not produce portfolio returns.
When portfolio returns are unavailable, the analyzer falls back to position returns;
Python tearsheets can also fall back to account reports.

The convenience accessor `analyzer.returns()` resolves this preference: portfolio returns
when present, position returns otherwise.

### Multi-currency accounts

Portfolio returns require every account's snapshot equity to resolve to one common
currency. Base-currency conversion normally provides that scalar. When snapshots expose
multiple currencies or accounts resolve to different currencies, the analyzer falls back
to position returns. An explicit tearsheet currency can select matching per-currency
equity where available.

If you need portfolio-level returns for a multi-currency account, compute them externally
by converting balances to a common currency before calculating percentage changes.

### Multi-account calculation

Backtest analysis aggregates all cached accounts after resolving them to a common
currency. The tearsheet follows the same account-wide aggregation rule for multi-venue
backtests.

## Backtest analysis

Following a backtest run, the engine passes realized PnLs, returns, positions, and orders data to each registered
statistic. Any output is then displayed in the tear sheet under the `Portfolio Performance` heading, grouped as:

- Realized PnL statistics (per currency)
- Returns statistics (for the entire portfolio)
- General statistics derived from position and order data (for the entire portfolio)

## Related guides

- [Positions](positions.md) - Position tracking within portfolios.
- [Reports](reports.md) - Generate portfolio analysis reports.
- [Visualization](visualization.md) - Visualize portfolio performance.
