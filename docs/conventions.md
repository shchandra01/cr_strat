# Pricing conventions

The conventions behind [`conventions.py`](../src/cr_strat/conventions.py),
[`calendars.py`](../src/cr_strat/calendars.py) and
[`bonds.py`](../src/cr_strat/bonds.py), with their sources, as of 17 Nov 2026.
The main source is FIMMDA's
[Handbook of Market Practices 2026](https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf)
("the handbook" below). RBI's own pages could not be read: www.rbi.org.in
returned HTTP 418 and rbidocs serves a bot challenge, so nothing here rests on
an RBI document read first-hand. The published numbers come from FIMMDA's
compiled RBI auction files. Where a rule was not found, the engine uses the
market-standard reading as a named parameter, listed
[below](#rules-not-found).

## Summary

| | G-sec, SDL | STRIPS | T-bill, CMB | CP | CD |
|---|---|---|---|---|---|
| Day count | 30E/360 | 30E/360 (assumed) | Actual/365 | Actual/365 | Actual/365 |
| Coupons | Semi-annual, on the maturity's day of the month | None | None | None | None |
| Yield | Semi-annual compounding | Semi-annual, compounded to the end | Simple, rear-ended | Simple, rear-ended | Simple, rear-ended |
| Price | Clean, per ₹100, 4 decimals, tick 0.0025 | Per ₹100 | Per ₹100, rounded to 4 decimals | Same | Same |
| Quoted in | Price | Yield | Yield | Yield | Yield |
| Settlement | T+1 (T+2 with an FPI) | T+1 (assumed) | T+1 | T+0 or T+1 | T+0 or T+1 |
| Payment on a holiday | Coupon: next working day. Redemption: previous working day | As the parent bond (assumed) | Previous working day | Previous business day | Previous business day |

Saturdays and Sundays are never settlement days for any of them. Amounts never
change when a payment date moves.

Pricing takes the settlement date as given. `calendars.settlement_date(trade_date,
conventions)` applies `settlement_lag` on the calendar that `calendar_name`
names. `quote`, and a bond's `price_decimals` and `price_tick`, only describe
how the market quotes: the engine's prices are unrounded, and `round_to_tick`
gives a quotable price. Bills, CP and CD round their prices as
`price_decimals` and `price_rounding` say.

## Sources

| Rule | Engine | Source |
|---|---|---|
| G-sec and SDL day count | `days_30e_360`: both day numbers capped at 30, February left alone (Excel basis 4) | Handbook 6.6.1(a): "Interest to be calculated on a European 30/360-day basis." Every row of its broken-period table is a unit test |
| SDLs share it | `SDL` equals `GSEC` but for its name | Handbook 6.6: "Government Securities are coupon-bearing debt instruments issued by the Government of India / State Government." |
| Accrued interest | `coupon × A / 360`, `A` the 30E/360 days from the last coupon (inclusive) to settlement (exclusive) | Follows from 6.6.1; the handbook prints no formula |
| Price quotes | Clean price; `round_to_tick` for 0.0025 | Handbook 6.6.1 note: "Prices in the secondary market to be quoted up to a maximum of four decimal places with a tick size of 0.0025." |
| Settlement amount | Clean price plus accrued interest | Handbook 7.3.6: "The settlement consideration would be the total of the clean price + accrued interest" |
| G-sec settlement lag | `settlement_lag = 1`, applied by `calendars.settlement_date` | Handbook 4.7.1: "All deals in Government securities will be settled on T+1 basis." (T+2 allowed with an FPI) |
| Redemption on a holiday | `redemption_roll = "preceding"`, last coupon too | Handbook 4.10.1: "the redemption proceeds (Redemption value and the coupon) will be paid on the previous Mumbai working day without any reduction in the amount." |
| Coupon on a holiday | `coupon_roll = "following"` | Handbook 4.10.1: "the coupon payment shall be made on the next working day without any increase in the amount." |
| G-sec price-yield formula | Excel PRICE/YIELD form, below | NSE Data & Analytics, [G-sec valuation methodology](https://nsearchives.nseindia.com//web/mediaattachment/2026-07/Government_Securities_Methodology_20260728161853.pdf) (Jul 2026): prices "are calculated using Excel based formula". Confirmed by the auction pairs |
| T-bill formula | `P = 100 / (1 + y d / 36500)`, `d` = maturity minus settlement | Handbook 6.7.1: "Discount is calculated on Actual / 365-day basis", "on a rear-ended basis", price "rounded off to the 4th decimal place" |
| CMB | As T-bills | Handbook 6.7.3: "CMBs have the generic character of T-Bills but are issued for maturities less than 91 days." |
| CP formula | As T-bills | Handbook 6.4.1; [CP guidelines](https://www.fimmda.org/UploadPopupPageFiles/FIMCIR2024_25_44_25Mar2025.pdf) (Mar 2025): "Day Count Convention: Actual / 365" |
| CP and CD price rounding | Half up, 4 decimals | Handbook 7.3.4: price "up to four decimals – rounded off" |
| CP settlement and maturity | T+1 default, T+0 allowed; maturity on the preceding Mumbai business day | CP guidelines: "T+0 or T+1-day basis"; "Mumbai Business Day means a Mumbai Banking Day, except any Saturday" |
| CD formula and maturity | As T-bills; maturity on the preceding business day | Handbook 6.5.2; [CD guidelines](https://www.fimmda.org/UploadPopupPageFiles/FIMCIR2023_24_36_FIMMDA_CD_Operational_GuidelinesDated%2029Dec23.pdf) (Dec 2023): "CDs maturing on any Saturday, Sunday will be payable on the immediately preceding Mumbai business day." |
| Holiday calendar | `src/cr_strat/data/holidays_mumbai.csv`, years declared in `holidays_mumbai_years.csv` | Handbook 4.10.1: "Holiday Calendar followed is as per Holiday Declared by Maharashtra State Government." Lists from [FIMMDA's holiday page](https://www.fimmda.org/PageContent.aspx?Iid=NjE=) |
| 1 April | A holiday | Handbook 4.10.3: "Bank Annual Account Closing Day as announced – will be treated as a normal holiday." |

## Price and yield

For dated G-secs and SDLs, with `f = 2`, `C` the coupon per period, `A` the
30E/360 days from the previous coupon to settlement, `E = 180` and
`w = DSC / E`:

```
dirty = sum over remaining flows k of CF_k / (1 + y/2)^(k - 1 + w)   two or more periods left
dirty = (100 + C) / (1 + w × y/2)                                   one period or less
clean = dirty - C × A / E
```

`DSC`, the days still to run in the current period, is Excel's `E - A` while
two or more periods are left. In the last period it is the 30E/360 days from
settlement to redemption, the `DSR` of Excel's one-period YIELD formula, so
price and yield invert exactly. It is never less than one day. The two
readings differ only in a half-year that is not 180 days of 30E/360. FIMMDA
leaves February alone, so 28 or 29 February to the 29th-31st of August is 181
or 182 days, and `E - A` reaches 0 or below in its last days. 30E/360 also
counts 0 days from the 30th to the 31st. Before this rule those dates put the
next payment at a time of zero or less: the bond priced at or above what it
still pays, with zero or negative duration and DV01, and no yield could be
solved.

Coupons fall every six months on the maturity's day of the month, counted back
from maturity. A buyer settling on a coupon date gets no accrued interest and
not that coupon; one settling the day before gets both. Flows are discounted
on their scheduled dates, not the holiday-adjusted ones. Durations, DV01 and
convexity are exact derivatives of whichever formula applies. Prices must be
positive. Yields must be finite and above the level where a discount factor
stops being defined: -200% on semi-annual compounding, `-2 / w` in a simple
last period. A price whose yield sits too close to that level to hold in a
double is refused rather than returned. In a sweep of every settlement day
around month ends, that happened only under compounding to the end, for
prices at least 10% above face within ten days of maturity.

Published pairs reproduced, all in [`tests/test_bonds.py`](../tests/test_bonds.py):

- **26 priced G-sec auction pairs**, FY2025-26 and FY2026-27, from FIMMDA's
  [2025-26](https://www.fimmda.org/UploadPopupPageFiles/Gsec_Auction_data_2025_2026.xlsx)
  and [2026-27](https://www.fimmda.org/UploadPopupPageFiles/Gsec_Auction_data_2026_2027.xlsx)
  files plus the 13 Nov 2026 auction (6.94% GS 2036, 97.43 for 7.3203%). The
  yield from the cut-off price matches RBI's to 4 decimals in every case, and
  the price at RBI's yield rounds to the cut-off price.
- **The handbook's bill-rediscounting example** (6.3.2): ₹10 crore for 45 days
  at 10.25% front-ended pays ₹9,87,36,301 (`discount="front"`).

Smoke tests only, which no day count can fail (at par on a coupon or issue
date, a price of 100 gives the coupon):

- The 6.94% GS 2036 new issue at par on 11 May 2026 (in the auction list).
- **2 SDL new issues** at par from FIMMDA's
  [SDL file](https://www.fimmda.org/uploadpopuppagefiles/SDL_Auction_Data_11Feb2025New.xlsx):
  07.25 GJ SGS 2026 (auctioned 6 Feb 2024, a 2-year loan) and 07.42 KL SGS
  2064 (19 Mar 2024). No priced SDL pair has been tested. One would need an
  SDL reissue's cut-off price, which the file does not give.

The same pairs reject the alternatives. Actual/Actual (ICMA) misses 13 of the
26 at the fourth decimal (6.36% GS 2031 on 15 May 2026: 6.8775 against
6.8779); Actual/365 misses the 13 Nov 2026 pair (7.3246). Settling on 15 Aug
2025, 3 Apr 2026 or 1 May 2026 instead of the next business day, or moving a
maturity by one day, also breaks the match. No pair separates 30E/360 from
30/360 US: none settles on the 31st or has a coupon at the end of February.

## Calendar

`src/cr_strat/data/holidays_mumbai.csv` ships inside the package, so any
install finds it. It has one row per date: `date`, `name`, `source` (a URL)
and `confirmed`. It holds the Maharashtra Government's NI Act holidays
for 2025 and 2026 from the gazette notifications FIMMDA hosts
([2025](https://www.fimmda.org/UploadPopupPageFiles/RBI_List_Of_Holidays_2025.pdf),
[2026](https://www.fimmda.org/UploadPopupPageFiles/Maharashtra_State_List_Of_Holidays_2026.pdf)),
plus:

- **8 Sep 2025, not 5 Sep.** Maharashtra moved Id-E-Milad for Mumbai to 8 Sep
  and RBI moved its money-market holiday to match (news reports only).
- **15 Jan 2026, `confirmed = no`.** An ad hoc NI Act holiday for municipal
  elections; NSE and BSE shut, RBI and CCIL settlement not confirmed. It counts
  as a holiday by default (an NI Act holiday in Mumbai normally closes them);
  `include_unconfirmed=False` drops it.

Coverage is declared, not inferred from the rows, so one early row (say
26 Jan 2027) cannot make a year look covered. `holidays_mumbai_years.csv`,
beside the list, has a row per year: `year`, `complete` and `source`. Only
complete years are covered. A year marked `no` can hold a partial list, but
those rows are ignored. A year with rows and no line here is an error. In code,
`BusinessCalendar` and `calendar_from_holidays` take the covered `years`
explicitly. A strict calendar (the default) raises `CalendarCoverageError` for
any weekday outside those years, so a back-test cannot take an unknown holiday
for a business day; use `calendar.lenient()` to project on weekends alone. A
bond's payment dates beyond the covered years need `cash_flows(..., until=)`
or a lenient calendar. The 2027 list should appear in early December 2026: add
its rows and a `2027,yes,<gazette URL>` line. Exchange
calendars differ and need their own file: going by holiday counts (NSE's
circulars were not opened), NSE traded but did not settle debt on 19 Feb,
19 Mar, 1 Apr and 26 Aug 2026.

## Rules not found

| Rule | Engine default | Parameter |
|---|---|---|
| Interest in the last coupon period of a G-sec | Simple, as Excel's PRICE does | `BondConventions.final_period` (`"compound"`) |
| 30E/360 or 30/360 US for G-secs | 30E/360 (the handbook's table) | `day_count` (`"30/360 US"`) |
| First-period fraction when a coupon falls on the 29th to 31st or at February's end | Excel's `(E - A) / E`; the last period on the days to redemption (Excel YIELD's `DSR`) | `fraction_rule` (`"days_to_next"`) |
| Days to a payment when 30E/360 gives none: the 30th before a payment on the 31st, or the last days of a 181-182 day half-year | One day (the actual count from the 30th to the 31st) | None |
| Accrued interest in a 181-182 day half-year (28 or 29 Feb to the 29th-31st of Aug) | Not capped: up to 2/180 of a coupon more than the coupon paid | None; see [open questions](#open-questions) |
| Discounting on scheduled or holiday-adjusted dates | Scheduled; the pairs reproduce on them | None |
| Shut period | None before coupons (the 29 Dec 2025 pair prices cum-coupon a day before its coupon); one day before redemption, from a [2014 report](https://www.business-standard.com/article/reuters/rbi-s-shut-period-change-to-improve-liquidity-114050800488_1.html) seen through a search summary | `shut_days_before_coupon`, `shut_days_before_redemption`; `in_shut_period()` flags, pricing ignores |
| Coupon dates of new issues; odd first coupons | Counted back from maturity; an odd first coupon pays the rate on the day count for its actual length | `issue_date`, `first_coupon_date` |
| STRIPS time and compounding | 30E/360 periods as the parent bond, compounded to maturity | `time_basis` (`"act/365"`: actual days / 182.5), `final_period` |
| "No. of days to maturity" for bills, CP and CD | Maturity minus settlement in calendar days | `days=` on each call |
| Days for a bill maturing on a holiday | To the scheduled maturity | Pass the payment date as `maturity`, or `days=` |
| Yield rounding | None; RBI prints 4 decimals | Round when comparing |
| Primary settlement lag for CMB and SDL auctions | T+1 | `settlement_lag` (applied by `calendars.settlement_date`) |
| Whether 15 Jan 2026 closed RBI settlement | Closed | `include_unconfirmed` |
| Money markets on working Saturdays (open in 2015, per a search summary of RBI's release; current status unknown) | Closed, as for G-secs | `BusinessCalendar.weekend` |

## Where the sources disagree

- **The handbook's table, row (b).** 18 Sep to 31 Oct prints "Total 14 Days"
  but its own lines (13 + 29) give 42, the 30E/360 count. Tested as 42.
- **RBI's primer against FIMMDA.** A search summary says the primer's Excel
  annex uses basis 0 (30/360 US); the handbook says European 30/360 and its
  table rules out the US variant (28 Feb to 17 Mar is 19 days, not 17). The
  engine follows FIMMDA. Read primer Annex 6 when RBI is reachable.
- **The 4 Sep 2025 auction.** T+1 on the calendar is 5 Sep, but at 5 Sep the
  cut-off price gives 6.5495, not RBI's 6.5497; 8 Sep and 9 Sep both fit.
  Probably the settlement date was fixed before Id-E-Milad moved (unconfirmed).
  The calendar keeps 5 Sep as a business day and the test settles on 9 Sep.
  Take auction settlement dates from RBI's release, not from T+1.
- **The shut-period pitfall.** [`rates_data_sources.md`](rates_data_sources.md)
  said G-secs cannot settle in a shut period before a coupon. The 29 Dec 2025
  pair, and the 2014 report, say otherwise; the pitfall is now corrected.
- **SDL coupon dates.** The rates doc says they follow each issue date; the
  engine counts back from maturity. For every SDL seen the two agree, because
  issue and maturity share day and month. One that did not would need an
  irregular last period, which the engine refuses.
- **FIMMDA's SDL file repeats 2024 rows as 2025.** Its "February 2025" and
  "March 2025" sheets hold the February and March 2024 rows with only the
  auction date moved, e.g. IN1520230252 on 6 Feb 2025, a Thursday. The ISIN's
  "2023" is FY2023-24, the "February" sheet dates it 06-02-2024 (a Tuesday),
  and a file dated 11 Feb 2025 cannot hold a 19 Mar 2025 auction. The tests use
  the 2024 dates.

## Open questions

- **Accrued interest past the coupon.** Under FIMMDA's 30E/360, 28 Feb to
  30 Aug is 182 days, so a 7% bond maturing 31 Aug accrues 3.5389 by 30 Aug
  against a 3.5 coupon. The engine does not cap it, so a clean price of 100 on
  29 Aug 2035 gives -6.76%. Whether CCIL or NDS-OM caps the accrued days at 180
  is not known. A cap would change the clean price and the yield on those few
  days a year for every bond paying on 29-31 August (common among SDLs, which
  settle on Wednesdays: 31 Aug 2022 and 30 Aug 2023 were Wednesdays).
- **Excel's PRICE and YIELD disagree in a 181-182 day last period.** For one
  period or less, Microsoft's help (read 17 Nov 2026) gives
  [PRICE](https://support.microsoft.com/en-us/office/price-function-3ea9deac-8dfa-436f-a7c8-17ea02c21b0a)
  with `DSR = E - A` and
  [YIELD](https://support.microsoft.com/en-us/office/yield-function-f5f5ca43-c4bd-434f-8bd2-ed3c9727a4fe)
  with `DSR` the days from settlement to redemption. They agree in a 180-day
  period. In a longer one PRICE's `E - A` falls to 0 or below in the last
  days, with the faults described [above](#price-and-yield), so the engine
  uses YIELD's reading. Both are simple interest in the last period, the
  engine's default. COUPDAYSNC, Excel's days to the next coupon, is `E - A`
  on its 30/360 bases in LibreOffice's Excel-compatible
  [code](https://github.com/LibreOffice/core/blob/master/scaddins/source/analysis/analysishelper.cxx);
  Microsoft's help says only "days to the next coupon". LibreOffice's PRICE
  compounds through the last period (`final_period="compound"`). Which
  reading CCIL and NDS-OM use is not known.

## Not built yet

Corporate bonds and floating-rate bonds. Each would be another `Bond`
subclass; everything is priced from one list of (time, amount) flows.
Amortisation and calls or puts change that list; record dates need
`ex_coupon_days` (now refused unless 0); annualised yields on actual days use
`compounding="annual", time_basis="act/365"`; a quarterly or monthly basis
needs an entry in `COMPOUNDING` and `COMPOUNDING_FREQUENCY` (`convert_yield`
converts any basis listed there); floating coupons need a fixing source. Also
missing: MIBOR fixing and exchange calendars. T+2 for FPI trades is
`settlement_date(trade_date, GSEC, lag=2)`.
