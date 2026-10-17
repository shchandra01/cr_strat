# Input data

Put downloaded files under `data/raw/` and generated files under `data/out/`.
Both directories are git-ignored. Dates are ISO (`YYYY-MM-DD`) everywhere: in the
security master and the curve, other formats are not guessed. The curve then fails
with an error, and the security master warns how many dates it ignored. Re-save
exports from Excel or FBIL with ISO dates.

## Rating actions: `rating_actions.csv`

One row per rating action. Build it from CRA press releases (CRISIL, ICRA,
CARE, India Ratings, Acuité, Infomerics, Brickwork all publish rating rationales
with the instrument table) or from rating-change disclosures that listed issuers
file with NSE/BSE. A vendor feed works too.

| Column | Required | Notes |
|---|---|---|
| `date` | yes | First **public** date of the action (press release or exchange filing), not the rating committee date. Using the committee date leaks information. |
| `issuer_id` | yes | Stable key shared with the security master. CIN is a good choice. |
| `rating` | yes | Rating string as published, e.g. `CRISIL AA+/Stable`, `[ICRA]AA (Negative)`, `Withdrawn` |
| `issuer_name` | no | |
| `agency` | when the rating string does not name it | Read from the rating string when blank. A bare `Withdrawn` names no agency, so withdrawals need this column filled or the row is rejected. |
| `instrument` | no | e.g. `Non-convertible debentures`, `Commercial paper`, `Tier II bonds`. Blank is taken to mean bonds. |
| `instrument_class` | no | Overrides the inferred class: `BOND`, `BANK_LOAN`, `ST`, `SUB`, `PERP`, `STRUCTURED`, `MLD`, `FD`, `OTHER` |
| `outlook` | no | If the outlook or watch is in a separate column |
| `action` | no | `Upgraded`, `Downgraded`, `Withdrawn`... (any text containing "withdraw" marks a withdrawal) |
| `liquidity` | no | CRA liquidity descriptor: Superior, Strong, Adequate, Stretched, Poor |
| `isin`, `source_url` | no | Carried through |

Keep withdrawals and outlook-only changes. Withdrawals change effective ratings,
and outlook/watch changes feed the `prior_signal` flag. Rows that cannot be
parsed go to `rating_parse_failures.csv`.

## MF portfolio disclosures: AMC `.xlsx` / `.xls`

Each AMC website's statutory disclosures section has monthly portfolios for all
schemes and fortnightly portfolios for debt schemes. Download one AMC's files
into one folder and run `cr-strat parse-mf` on them with `--amc` set to the AMC
name as AMFI spells it. `.xls` files need the `xls` extra (`xlrd`).

If a layout hides the date or value unit, pass `--as-of` or `--mv-unit`. Check
`<out>_report.csv` after every run.

## AMFI scheme master: `NAVAll.txt`

`https://www.amfiindia.com/spages/NAVAll.txt` lists every live scheme under its
SEBI category heading. Pass it as `--navall` to attach categories. Schemes it
cannot match land in `<out>_scheme_matches.csv` with `match = none`. Map those
in a CSV of `amc, scheme_name, category` and pass it as `--scheme-overrides`.
Closed or merged schemes are missing from the current file, so older periods
need overrides or an archived copy.

## Security master: `securities.csv`

| Column | Required | Notes |
|---|---|---|
| `isin` | yes | |
| `issuer_id` | yes | Same key as the rating actions |
| `instrument_class` | yes | Same vocabulary as above; plain NCDs are `BOND` |
| `face_value_inr` | no | Per unit. Needed to value holdings at face (quantity × face value); otherwise market value is used. Note the denomination changed over time (₹10 lakh, ₹1 lakh, ₹10,000). |
| `outstanding_face_inr` | no | Needed for ownership shares |
| `issue_date`, `maturity_date` | no | Used to drop ISINs not live at the event or maturing within a horizon. `maturity_date` is required for the price panel (tenor and spread). |
| `sector` | no | e.g. PSU, NBFC, HFC, bank, corporate. Add `sector` to `--match-on` to match peers on it. |
| `coupon_pct` | for backtests | Annual coupon rate in percent; `0` for zero-coupon bonds. Bonds without it are marked by yield only and cannot be traded by the backtester. |
| `coupon_freq` | no | Payments a year: 1 (default, most Indian corporate bonds), 2, 4 or 12 |
| `redemption_pct` | no | Redemption per 100 of face; default 100 |

For backtests, the engine prices each bond from `coupon_pct`, `coupon_freq`,
`issue_date` and `maturity_date` as a plain fixed-coupon bullet. Leave
callable, puttable, floating-rate and amortising instruments without a
`coupon_pct` until their terms are modelled.

Sources: NSDL ISIN search, the information memorandum or key information
document filed with NSE/BSE, or a vendor (Bloomberg, LSEG, CMIE Prowess).

## Other holders: same CSV format as `parse-mf` output

`as_of_date, holder_type, holder_id, isin` plus optional `category, quantity,
market_value_inr`. Use `holder_type` `INSURER`, `NPS` or `EPFO` to match
`config/mandates.toml`. Insurers' quarterly public disclosures include a
statement of downgraded investments. NPS pension fund managers publish monthly
scheme portfolios.

## Reported trades: NSE / BSE files

OTC corporate bond trades are reported on the NSE and BSE reporting platforms,
and both exchanges publish them daily, as individual trades or as ISIN-day
summaries. Download each venue's files into its own folder and run
`cr-strat load-trades` per venue.

The loader finds the header row by its ISIN and price/yield columns, and prefers
weighted-average columns when a summary file has several. It reads the value
unit from the value header ("Rs. Lakhs", "Rs. Cr"). If a file has no date
column, pass `--trade-date`. If the value header has no unit, pass
`--amount-unit`. The `_report.csv` lists the header it mapped to each field;
check it on the first file from each venue.

Prices are per 100 of face value and yields are in percent, as reported. The
price panel is built from **yields**: trades without a yield column load but are
dropped by `price-panel`, which says how many. Without a traded-value column (or its
unit, see `--amount-unit`), fills in a backtest are not capped by participation;
`load-trades` prints a note when it cannot find the unit.

## Benchmark curve: `gsec_par_curve.csv` (or an OIS curve)

Long format, one row per date and tenor:

| Column | Notes |
|---|---|
| `date` | ISO date |
| `tenor_years` | e.g. 0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 30 |
| `yield_pct` | Percent |

FBIL publishes daily G-sec par yields; an OIS curve can come from a vendor or
dealer marks. A curve date is used for up to 7 days. Set `--benchmark-compounding`
to the curve's convention (G-sec yields are semi-annual) and `--bond-compounding`
to the trade yields' convention. Spreads are computed on the bond's basis.

## Cash rate (optional): `treps.csv`

`date, rate_pct`: the rate earned on uninvested cash, e.g. CCIL's TREPS weighted
average rate or FBIL overnight MIBOR. Pass it to `backtest` or `sweep` as
`--cash-rates`. Without it, cash earns the benchmark curve's shortest tenor.

## Synthetic data: `cr-strat synth` / `cr-strat demo`

`cr-strat synth --out-dir data/demo/raw` writes a simulated market in these formats:
`rating_actions.csv`, `securities.csv` (with coupons), `gsec_par_curve.csv`,
`trades/nse/*.csv`, `trades/bse/*.csv` and `holdings_mf.csv`. The holdings are already
in the canonical format, so there are no AMC workbooks, no `NAVAll.txt` and no
`parse-mf` step. There is no cash-rate file either. It also writes `truth_events.csv`
with the forced-selling overshoot planted at each downgrade below AA+, and a
`SYNTHETIC.txt` marker. Use it to check the pipeline and research code, never as
evidence about the market.
