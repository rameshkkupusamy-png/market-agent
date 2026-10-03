# Data sources (checked 2026-10-02)

| Need | Source | Finding |
|---|---|---|
| Daily prices | yfinance `history(auto_adjust=True)` | 3206 rows for AAPL since 2014-01-02 (to 2026-10-01); dash for class shares (BRK-B also 3206 rows); SPY 3206 rows |
| Earnings dates | yfinance `get_earnings_dates(limit=100)` | earliest date for AAPL/MSFT/JPM/KO: 2002-04-17 / 2002-01-17 / 2002-01-16 / 2002-01-29 (100 rows each, includes upcoming dates) |
| Sectors | yfinance `info["sector"]` | Yahoo sector names, close to GICS (AAPL Technology, JPM Financial Services, XOM Energy) |
| Index membership | github.com/fja05680/sp500, file `S&P 500 Historical Components & Changes (Updated).csv` | 2720 rows, 1996-01-02 to 2026-08-18, 1209 distinct tickers |
| Removed members | yfinance | 24 of 40 sampled removed members have no data |

Consequences: none of the "If not" cases applied. Notes: the header is already `date,tickers`. The
repo also has an older file without "(Updated)" (ends 2019-01-11) whose removed tickers carry
`-YYYYMM` suffixes (e.g. `TMC-200006`); we used the "(Updated)" file, which has no suffixes, so
`clean_ticker` suffix stripping is not needed for it. Removed or delisted tickers TWTR, XLNX, FRC and
ATVI returned empty data (no exception raised; yfinance only logged errors). In the removed sample,
tickers that were later reused by another company (e.g. AAL, AAP, ALK) return data for the new
company, so "has data" does not guarantee it is the old member; survivorship bias is measurable,
not fixable. The ticker `AFS.A` (dot-class share) is mapped to a dash in the check.
News source for plan 2: not checked here.

Update (2026-10-03): prices are now fetched with `history(auto_adjust=False)`. Yahoo's `Close`
and `Volume` there are still adjusted for later splits (NVDA 2015-01-02: Close 0.503, i.e. the
traded $20.13 divided by the 4:1 and 10:1 splits), and `Adj Close` is also adjusted for dividends
and spin-offs (T 2015-01-02: Close 25.58, Adj Close 11.25). The fetch derives the adjusted bars
from `Adj Close` / `Close`, as `auto_adjust=True` does, and the as-traded close and volume by
undoing every later split in `Ticker.splits`. Yahoo records the 2022 AT&T/WarnerMedia spin-off as
a split, so T's as-traded price comes out right too. `Ticker.splits` costs one extra request per
ticker.
