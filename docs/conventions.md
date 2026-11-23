# Pricing conventions

The conventions behind [`conventions.py`](../src/cr_strat/conventions.py),
[`calendars.py`](../src/cr_strat/calendars.py) and
[`bonds.py`](../src/cr_strat/bonds.py), with their sources, as of 17 Nov 2026; corporate bonds and
floating-rate bonds ([`corporates.py`](../src/cr_strat/corporates.py),
[`floating.py`](../src/cr_strat/floating.py)) as of 19 Nov 2026, in
[their own section](#corporate-bonds-and-floating-rate-bonds).
The main source is FIMMDA's
[Handbook of Market Practices 2026](https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf)
("the handbook" below). Of RBI's own documents only a few saved pages were
read (Nov 2026 auction results, Handbook Table 221, the GoI list): www.rbi.org.in
rate-limits, rbidocs serves a bot challenge, and no RBI rule book was read.
The published numbers come from FIMMDA's compiled RBI auction files and those
pages. Where a rule was not found, the engine uses the
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
- **The whole auction table** (`auctions.build_auctions` on FIMMDA's
  2015-2027 files, Handbook Table 221 and the Nov 2026 releases, saved on
  16-17 Nov 2026): the yield from the cut-off price at T+1 on the calendar
  reproduces the printed yield on 1,337 of 1,448 priced G-sec, green-bond
  and SDL reissues (three after rolling over a holiday declared after the
  auction). Of the rest, 95 settle in an odd first coupon period
  ([open questions](#open-questions)), 11 are conversion lines, 2 fit only on
  or before their auction date and 3 look mistyped in FIMMDA's file.
  Reproduced in FY2022-23 and FY2025-27 without exception. The 2 are lines
  of 29 Sep 2016, four small lines (100 to 425 crore) that all fit settlement
  on the auction day and none at T+1: a special operation settled that day,
  not an auction, so all four are flagged `auction_day_pricing` and do not
  count as issuance. Lines priced on another settlement basis cannot date a
  maturity either: under 30E/360 a price fixed for the auction day fits a
  maturity one day late exactly as T+1 fits the true one. Searched at T+1,
  the conversion legs of 8.24% GS 2018 put its maturity on 23 Apr 2018, where
  RBI's notifications (seen through search excerpts) give 22 Apr. Such lines
  are therefore left out of maturity inference.
- **12 SDL reissues** of RBI's 9 Nov 2026 result (7.83% Assam SGS 2046 at
  97.55 for 8.0814% and eleven more), settling on 11 Nov after the Diwali
  holiday, with each maturity on the anniversary of the first issue date the
  result prints: all 12 to 4 decimals, on the G-sec conventions.

Smoke tests only, which no day count can fail (at par on a coupon or issue
date, a price of 100 gives the coupon):

- The 6.94% GS 2036 new issue at par on 11 May 2026 (in the auction list).
- **2 SDL new issues** at par from FIMMDA's
  [SDL file](https://www.fimmda.org/uploadpopuppagefiles/SDL_Auction_Data_11Feb2025New.xlsx):
  07.25 GJ SGS 2026 (auctioned 6 Feb 2024, a 2-year loan) and 07.42 KL SGS
  2064 (19 Mar 2024). FIMMDA's file gives no SDL prices; the priced SDL
  reissues above come from RBI's result.

The same pairs reject the alternatives. Actual/Actual (ICMA) misses 13 of the
26 at the fourth decimal (6.36% GS 2031 on 15 May 2026: 6.8775 against
6.8779); Actual/365 misses the 13 Nov 2026 pair (7.3246). Settling on 15 Aug
2025, 3 Apr 2026 or 1 May 2026 instead of the next business day, or moving a
maturity by one day, also breaks the match. No pair separates 30E/360 from
30/360 US: none settles on the 31st or has a coupon at the end of February.

## Calendar

`src/cr_strat/data/holidays_mumbai.csv` ships inside the package, so any
install finds it. It has one row per date: `date`, `name`, `source` (a URL)
and `confirmed`. It covers 2015 to 2026.

**2025 and 2026** are the Maharashtra Government's NI Act holidays from the
gazette notifications FIMMDA hosts
([2025](https://www.fimmda.org/UploadPopupPageFiles/RBI_List_Of_Holidays_2025.pdf),
[2026](https://www.fimmda.org/UploadPopupPageFiles/Maharashtra_State_List_Of_Holidays_2026.pdf)),
plus:

- **8 Sep 2025, not 5 Sep.** Maharashtra moved Id-E-Milad for Mumbai to 8 Sep
  and RBI moved its money-market holiday to match (news reports only).
- **15 Jan 2026.** An ad hoc NI Act holiday for municipal elections, declared
  on 6 Jan 2026. CCIL's notification CCIL/Legal/26/01 (9 Jan 2026) closed all
  its settlement segments that day.

**2015 to 2024** come from the clearing houses' settlement-holiday lists, which
follow the NI Act list (for 2025 NSE's currency-segment list matched the
gazette exactly): NSCCL for 2015 and 2016, the NSE currency segment for 2017
to 2021 and 2023, NSE Clearing for 2022, and CCIL's own NI Act list for 2024.
The source of each year is in `holidays_mumbai_years.csv`, each row's in
the list. Only weekday holidays are listed; weekend ones change nothing.
Ad hoc and moved days are in: municipal polls on 21 Feb 2017, the general and
assembly elections on 29 Apr 2019, 21 Oct 2019, 20 May 2024 and 20 Nov 2024,
7 Feb 2022 (declared on 6 Feb), Bakri Id moved to 29 Jun 2023, 22 Jan 2024
(declared on 19 Jan) and Id-E-Milad moved to 18 Sep 2024. Left out on
purpose: 9 Nov 2018 (dropped from NSE's revised list), 16 Sep 2024 (the
holiday moved), and 28 and 29 Sep 2023. NSE's 2023 list (CD54832, issued in
December 2022), the year's source, has 28 Sep 2023 (Id-E-Milad) as a
settlement holiday. Maharashtra later cancelled that holiday and declared
29 Sep instead, and RBI kept the G-sec, forex, money and rupee derivative
markets open and settling on 29 Sep for the half-year end. The
dated-securities auction moved from 29 to 28 Sep and settled on 29 Sep; the
T-bill auction of 27 Sep settled on 29 Sep, not at T+1 (RBI press release
2023-2024/1005 of 27 Sep 2023,
[prid=56455](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=56455),
read through web-search excerpts and news reports; the page was not
fetched). The 28 Sep auction's yields agree: three of its four securities
fit settlement on 29 Sep and no other day. Exchange segments followed their
own notices and need their own calendar.

Two days are `confirmed = no`, holidays by default and dropped by
`include_unconfirmed=False`: **1 Jul 2015**, "Annual Closing of RBI", an NSCCL
settlement holiday but not an NI Act holiday; and **21 Feb 2017**, the
municipal polls, known only from NSCCL. No auction settles near either.

**Checked against the auctions.** `auctions.settlement_evidence` compares
the calendar with what 2,500 G-sec, SDL and T-bill auctions of 2015-2026 say
about their settlement: the days on which the cut-off price reproduces the
printed yield, RBI's dates of issue (Handbook Table 221, the first issue
dates in RBI's list) and the issue dates of FIMMDA's SDL new issues (each
matures on the anniversary of its issue). Of 1,991 pieces of evidence, none
shows a listed holiday on which an auction settled or a business day the
list misses, apart from holidays declared or moved after the auction: the
yields of 19 Jan 2024 and 4 Feb 2022 were fixed for 22 Jan and 7 Feb, which
became holidays, and those auctions settled the next business day (RBI's list
dates the green bond auctioned on 19 Jan 2024 from 23 Jan); the 4 Sep 2025
auction settled on 9 Sep (below). Fifty of the 192 listed weekday holidays of
2015-2024 are confirmed outright, an auction's evidence ruling out the
holiday and fitting the next business day; the three polling days of 2019
and 2024 are among them, so they are marked confirmed. T+1 on the calendar
matched all 74 new issues' first issue dates in RBI's list, 89 of the 91
Handbook dates of issue and every auction whose yield could be checked,
except those three.

**Saturdays.** G-secs, SDLs, T-bills, forex and OTC rupee derivatives never
settle on a Saturday: RBI's August 2015 release (seen through press copies)
kept those markets closed "on all Saturdays, as hitherto" when the 2nd and
4th Saturdays became bank holidays, and no auction yield of 2015-2026 fits a
Saturday where no weekday fits (the one hit is a conversion line priced
otherwise). The calendar's weekend is right for G-sec, SDL, T-bill and OIS
work. It is wrong for call, notice and term money, market repo and CBLO (now
TREPS), which deal on the 1st, 3rd and 5th Saturdays from 1 Sep 2015, and
probably on every non-holiday Saturday before (not confirmed). A
money-market calendar would need `weekend={6}` with the 2nd and 4th
Saturdays as holidays from September 2015; it is not built, and when such
Saturday dealing stopped, if it did, was not found.

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
| Interest in the last coupon period of a G-sec | Simple, as Excel's PRICE does. Evidence, not a rule: RBI's yields for the three conversion legs in their last period (7.83% GS 2018 on 22 Nov 2017, 8.24% GS 2018 on 8 and 22 Mar 2018) reproduce to 4 decimals with simple interest and miss by 2 to 8 bp compounded, if they settled on the auction day at the true maturities (22 Apr 2018 per RBI's notifications; 11 Apr 2018 recalled, not checked) | `BondConventions.final_period` (`"compound"`) |
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
| Primary settlement lag for CMB auctions | T+1 | `settlement_lag` (applied by `calendars.settlement_date`) |
| SDL auctions settle T+1 | T+1 | Not a rule found, but every one of FIMMDA's 791 SDL new issues of 2024-25 matures on the anniversary of T+1, and RBI's 9 Nov 2026 reissues reprice at T+1 |
| Whether 1 Jul 2015 and 21 Feb 2017 closed RBI settlement | Closed | `include_unconfirmed` |
| Rule for a reissue settling before the first coupon of a security issued off its coupon schedule | Odd first period accrued from the issue date (Excel ODDFPRICE-like) | None; see [open questions](#open-questions) |
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
  Probably the settlement date was fixed before Id-E-Milad moved (unconfirmed);
  the Handbook dates the issue 9 Sep. The calendar keeps 5 Sep as a business
  day and the test settles on 9 Sep. The auctions of 19 Jan 2024 and 4 Feb
  2022 are the same case: their yields fit 22 Jan and 7 Feb, holidays
  declared after the auction, and they settled the next business day.
  `auctions.reprice` rolls such a date forward (`yield_rolled`). Take auction
  settlement dates from RBI's release, not from T+1.
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

- **Reissues in an odd first coupon period.** A security issued off its
  coupon schedule (7.16% GS 2050 was issued on 20 Apr 2020 with coupons on
  20 Mar and 20 Sep) and reissued before its first coupon does not reproduce
  RBI's yields: on 96 such FIMMDA rows of 2015-2021 the printed yield is 4 to
  15 hundredths of a basis point below the engine's (median 7). The engine's
  odd first period (coupon and accrual from the issue date, compounded on
  the regular fraction, as Excel's ODDFPRICE) is wrong for them, and so are
  a full first coupon, a regular bond, simple interest to the first coupon,
  and actual/actual or actual/365 for the broken period: none reproduces
  more than 15 of the 90 priced rows. Reissues after the first coupon
  reproduce. Every security in RBI's list first issued since 2022 (59) was
  issued on its coupon schedule, against 22 of 41 of those first issued in 2015-2021.

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

## Corporate bonds and floating-rate bonds

Built in [`corporates.py`](../src/cr_strat/corporates.py) (listed non-convertible debentures, NCDs) and
[`floating.py`](../src/cr_strat/floating.py) (GoI floating-rate bonds and benchmark-linked corporate FRBs),
on the conventions `NCD`, `NCD_2013`, `NCD_PRE2013` and `GOI_FRB` in `conventions.py`
(`CORPORATE_CONVENTIONS`; `CONVENTIONS` still lists only what `bonds` prices). Both are `bonds.Bond`
subclasses: every price, yield, duration, DV01 and convexity comes from one list of (time, amount) flows,
and the code of `bonds.py` is unchanged (its docstring now points to them; a GoI FRB composes an unchanged
`FixedRateBond` with a 1% coupon). No
licensed price was used: the tests are SEBI's and issuers' worked examples (numbers only) and synthetic
bonds with answers computed independently.

Sources read for these rules:

- SEBI circular CIR/IMD/DF/18/2013 of 29 Oct 2013, Annex A, read in a
  [copy](https://corporatelawreporter.com/issues-pertaining-primary-issuance-debt-securities/) ("SEBI 2013").
- SEBI circular CIR/IMD/DF-1/122/2016 of 11 Nov 2016,
  [PDF](https://www.sebi.gov.in/sebi_data/attachdocs/aug-2022/1659352777649.pdf) ("SEBI 2016").
- SEBI circular SEBI/HO/DDHS/DDHSPOD1/P/CIR/2025/72 of 13 May 2025,
  [PDF](https://www.sebi.gov.in/sebi_data/attachdocs/may-2025/1747410005828.pdf) ("SEBI 2025").
- SEBI's master circular for non-convertible securities of 15 Oct 2025, read in a
  [copy](https://taxguru.in/wp-content/uploads/2025/10/SEBI-Master-Circular-for-Non-convertible-Securities-Listing.pdf)
  ("NCS master circular"), with its cash-flow illustration.
- SEBI's master circular for mutual funds of 20 Mar 2026,
  [PDF](https://www.sebi.gov.in/sebi_data/attachdocs/mar-2026/1774024028162.pdf), chapter 10 ("MF master circular").
- Term sheets of Power Finance Corporation's public issue of Jan 2026
  ([copy](https://media.thefixedincome.com/uploads/products/term_sheet_upload/17704579531770122643_power-finance-c-ts.pdf))
  and Capri Global Capital's of Oct 2025
  ([copy](https://media.thefixedincome.com/uploads/products/term_sheet_upload/1760601335CAPRI%20GLOBAL%20CAPITAL%20TS.pdf)),
  and Kosamattam Finance's [illustrative cash flows](https://www.kosamattam.com/media/fxxjxeot/illustrative-cash-flow.pdf)
  of Nov 2025.
- RBI's release on the FRB 2034 coupon of 28 Oct 2022, read in a
  [copy](https://www.simpliance.in/India/FCC/govt_notification/Central/press-release-rbi-announces-rate-of-interest-on-government-of-india-floating-rate-bond-2034dated-28102022),
  RBI's G-sec primer (a saved copy) and FIMMDA's handbook. No RBI page was requested.

Status: **V** verified in a primary document or a saved copy of one; **P** partly verified (a secondary
source, a search summary, or a published number reproduced within a tolerance); **U** UNVERIFIED, the
standard or conservative reading, a named parameter.

| | Listed NCD (`NCD`) | GoI FRB (`GOI_FRB`) |
|---|---|---|
| Day count | Actual/actual, a year of 365 or 366 days (`ACT/ACT SEBI`) | 30E/360, as G-secs |
| Coupons | Annual, semi-annual, quarterly or monthly, counted forward from the deemed date of allotment; zero-coupon and cumulative bonds | Semi-annual on the maturity's day, the rate reset each half-year (FRB 2035: every five years, at a five-year G-sec yield) |
| Yield | Annual compounding on actual days / 365 (XIRR); simple under a year with one flow left | Semi-annual, as G-secs; discount margin over the index |
| Holidays | Coupon paid the next working day, redemption the previous one | As G-secs |
| Record date | 15 days before each due date; ex-interest after it | None |
| Settlement | T+1 (T+0 allowed) | T+1 |

| Rule | Engine | Source | Status |
|---|---|---|---|
| Interest on listed debt is actual/actual | `ACT/ACT SEBI`, `corporates.year_fraction_sebi`; `year_fraction` and `coupon_period_days` refuse it | SEBI 2013 para I, SEBI 2016 para 3(b) (para 1 only recounts the earlier circulars), NCS master circular Ch. III; handbook 7.3.8 | V |
| A year holding 29 February counts 366 days for every coupon in it, at any frequency (issues from 1 Jan 2017) | `leap_year_rule` | SEBI 2016 para 3(b), 4 | V |
| Which one-year period: between anniversaries of the deemed allotment | `leap_year_rule="anniversary"` (default); `calendar`, `fixed365` | Reproduces the NCS master circular's illustration (89,500 a year); the calendar split gives 89,487.94 and 89,512.06 there. Kosamattam's monthly table follows calendar years (all 98 coupons) against its own text | U (decision) |
| An allotment on 29 February: anniversaries on 28 February in common years, each year 366 days when it holds a 29 February, else 365 (29 Feb 2024 to 28 Feb 2025 pays 365/366 of the rate, 28 Feb 2027 to 29 Feb 2028 366/365) | `corporates._year_days` | SEBI 2016 para 3(b) read literally | U (decision) |
| Issues before Dec 2013: each issue's terms | `NCD_PRE2013`: actual/365 fixed, and a postponed coupon or early redemption accruing to its payment date (`to_payment`, as `NCD_2013`; the corporates spec had `scheduled`: 9.073973 against 9.024658 for a 9% coupon due Sun 13 Nov 2016, paid Tue 15 Nov) | earlier research notes only | U (decision) |
| Schedule counted forward from the deemed allotment, maturity last | `schedule="forward"`; `backward`, `month_start` (monthly on the 1st, `first_period="clubbed"`), `explicit` (from the term sheet or the database, with payment and record dates) | SEBI 2016 para 3(a) example; PFC, Capri, Kosamattam | V |
| Each listed ISIN's schedule sits in SEBI's centralised database from 18 Aug 2025 | `schedule="explicit"`, `corporate_schedule.csv` (amounts typed there are checked against the rules) | SEBI 2025 paras 2-4 | V |
| Coupon on a holiday paid the next working day; later dates unchanged | `coupon_roll="following"` | SEBI 2013, 2016 para 3(a); NCS master circular Ch. III | V |
| Issues from 2017: a late coupon's interest only to the due date | `postponed_coupon="scheduled"` | SEBI 2016 para 3(a); the NCS illustration (Sat 14 Dec 2024 paid Mon 16 Dec at the full 89,500) | V |
| Issues of Dec 2013 to Dec 2016: interest to the payment date, the next period from there; redemption with interest to its early payment | `NCD_2013` (`to_payment` both) | SEBI 2013 Annex A (copy): 367 and 364 days, printed as Rs 89,745.00 and 89,255.00 (rounded to the rupee); the redemption's interest to the payment date reads the note that redemption and accrued interest are paid on the previous working day | V (copy) for the postponed coupon; P/U for the redemption's interest (an interpretation) |
| Redemption on a holiday paid the previous working day | `redemption_roll="preceding"` | SEBI 2013; NCS master circular Ch. III | V |
| Interest on an early redemption: the full scheduled coupon | `redemption_interest="scheduled"`; `"to_payment"` per issue (PFC, Capri: 89,009.59 in the illustration) | NCS illustration; term sheets | V per document; U as a market rule (decision) |
| Payments on Mumbai money-market days; working Saturdays pay | `calendar` (payment dates roll on it; none roll without one) | SEBI 2016 para 3(c); PFC | V |
| Default payment calendar: the settlement calendar, every Saturday off | `payment_calendar_name="mumbai_settlement"` | no banking calendar with Saturday holidays exists here | U (decision) |
| Record date 15 calendar days before each due date | `NCD.ex_coupon_days=15` (`record_days` per issue; `bonds` still refuses it) | NCS Regulations amendment of Jul 2024 (summaries); PFC, Capri | P; V for the two issues |
| Record date on a holiday: the preceding day | `record_roll="preceding"` (Capri: `following`) | PFC | U (decision) |
| Ex-interest: a settlement after the record date and before the due date leaves out that coupon, accrued interest `-F r yf(s, due)`; the record date itself is cum | `CorporateBond._flows` | no exchange or FIMMDA rule found | U (decision) |
| A record date never falls before the allotment (a first period shorter than the offset: the allottee is on record) | `CorporateBond._record` | inference | U |
| A record date always falls before its payment date: with `record_days=0` a redemption due Sun 15 Dec 2024 and paid Fri 13 Dec is on record at the close of 12 Dec | `CorporateBond._record` (within `RECORD_PAYMENT_DAYS` of the due date) | inference | U |
| An explicit schedule's due dates are one period of the frequency apart (a first or last stub allowed, 3 days' slack for month ends); payment and record dates increase with them, each record date before its payment date | `CorporateBond._generate_dues`, `_check_aligned` (refused otherwise) | inference: the matched OIS rate and coupons follow the frequency | U |
| Trading stops from the redemption's record date to the redemption | settlement there refused; `in_shut_period` | PFC, Capri; NCS master circular Ch. XVI 6.1 (no shut period for interest) | V (issues) |
| A partial redemption's record date: ex-principal | `amortisation` | none | U |
| Prices per 100, 4 decimals; yields 4 decimals | `price_decimals=4`, `price_tick=0.0001` (U) | handbook 6.9.1(b) | V |
| Yields annual on actual/365 (XIRR) | `compounding="annual"`, `time_basis="act/365"`; `with_compounding` for any `COMPOUNDING` basis (quarterly and monthly added) | PFC's annual series within 0.6 bp, its cumulative amounts within Rs 0.02 per Rs 1,000, its zero-coupon yields round to the printed ones; Capri's annual and 18-month series within 0.5 bp | P |
| Under a year with no interim interest: simple, actual/365 | `final_period="simple"`, `mm_final_days=365` | handbook 6.9.1(c) | V |
| Due dates for yield-to-price from 18 Aug 2025; payment dates before | `flow_dates="by_date"`, `due_dates_from` | SEBI 2025 paras 1.2-1.3, 4 (para 1.2 sets it for securities traded on the RFQ platform); the working group's description of earlier practice | V for RFQ trades; U for OTC reports, valuation and the return engine, which use it too; P before |
| Accrued interest `F r yf(last due, s)`; none on zero and cumulative bonds | `CorporateBond` | follows from the day count; handbook 7.3.8 | P; U for zeros |
| Cash rounded per security, valuation unrounded | `cash_schedule(decimals=)`, `amount_decimals` | SEBI 2013 note (rupee); Kosamattam (paisa) | P |
| Callable bonds valued at the lowest of the values to each call and maturity; puttable at the highest over puts and maturity; with both, the earlier trigger; a put and call on one date at one price is a deemed maturity | `price_to_worst`, `price_to_best_put`, `sebi_valuation` | MF master circular 10.3.1 | V |
| A put favours the holder when the yield exceeds the coupon by 30 bp | `SebiValuation.put_favours_holder` (reported, not enforced) | MF master circular 10.3.2-10.3.4 | V |
| AT1 bonds valued to call; perpetuals deemed to mature 100 years after issue | `tier="at1"` (first call ahead), `perpetual_bond` | MF master circular 10.3.1(a), 10.4.2 | V |
| Yield to worst: lowest of the yields to maturity and to each call | `yield_to_worst` (puts with `include_puts`) | standard definition | U (choice) |
| An exercise counts from its notice, a decision not to exercise likewise; a date alone from 23:59:59 | `CallPut.exercised_public_from`, `effective_bond`, `workouts(as_of=)`, `as_known` | term sheets list notice times; rule 10 (nothing before it is public) | V; U for the time |
| A batch of prices, prints or valuations takes the terms as known at 00:00 on each row's trade date (its `trade_date`, else the settlement less T+1); `as_of` overrides | `corporates.default_as_of` | a trade's time is rarely known: a notice made public during the trade day counts from the next day | U (conservative) |
| An exercise public after the record date of its redemption (15 days before the exercise date by the bond's rule) is refused; the notice's own record date can be given | `CorporateBond.check_exercises`, `CallPut.record_date` (`record_date` in `corporate_options.csv`) | inference: the holders to be redeemed are those on record | U |
| Reported prices of a partly redeemed bond are per 100 of the face still outstanding | `price_basis="outstanding"` (trade check and recomputed panel; `original` elsewhere by default, and for engine marks) | exchange part-redemption notices: trading continues on the reduced face value (BSE notices through a search summary, not read) | P; U for this package's prints and marks (decision) |
| Reported corporate bond prices are clean | the trade check's standard reading; a print matching only on its dirty reading is `dirty_price` and its yield read so | the corporates spec's decision 7; NSE's order-matching platform may print dirty prices (unverified) | U (decision) |
| Exercise between coupon dates pays interest to it | `to_workout` | none | U |
| Coupon steps on ratings, from the next period when the terms are silent | `StepRule`, `apply_rating_steps` (`effective="next_period"`; `from_event` the day after the action is public) | NCS master circular Annex XIV-A item 43 (disclosure) | V; U for the default |
| Amortisation by face; coupons on the outstanding face; a partial redemption due on a holiday paid the previous working day, as a redemption | `amortisation` (on due dates; quantity redemptions valued pro rata) | Annex XIV-A items 51-52 | V; U (pro rata, the holiday roll) |
| GoI FRB base: mean of the WAYs of the last three 182-day auctions before the fixing day, plus the spread | `floating.goi_frb_fixing` (`yield_kind="cutoff"` for FRB 2024's rule) | FRB 2034 release (copy); primer | V (copy) |
| Base rounded half up to 2 decimals; each WAY taken to 4 decimals; an auction on the fixing day left out | `decimals=2`, `yield_decimals=4`, `include_fixing_day=False` | primer (FRB 2024's rounding) | P; U |
| WAY = (100/WAP - 1) x 365/182 x 100 | `way_from_wap` | reproduces RBI's 6.5059 from WAP 96.8579 | V |
| A coupon is public from RBI's release; else from the latest of its three results dated before the fixing day, once no weekly auction can still fall before it or a week after it; an unknown release time is 00:00 on the fixing day; an announcement more than 14 days before the fixing day, or a result public before its auction, is refused | `goi_frb_rates` (`announced`, `computed`, `projected`, `unfixed`, `unknown`), `ANNOUNCE_LEAD_DAYS`, `AUCTION_WEEK_DAYS` | release dates (2022 copy; 2026 via search) | V/P; U for the rules |
| A passed fixing is projected only from the results before it, or the previous period's announced coupon; a period ended unprojected keeps its projection (`unfixed`) | `goi_frb_rates` (`public_from_source`: `before_fixing`, `previous_coupon`) | the coupon rule fixes the coupon on its fixing day | V for the rule; U for the flags |
| Projection flat at the latest computable base (or the base the last announced coupon implies); forward from a curve as an option; a base more than one period old is refused unless `allow_stale` | `projection`, `allow_stale` | none | U (decision) |
| Discount margin over the projection's index, on the G-sec half-year times; curve form as an option | `discount_margin_bp`, `price_from_dm`, `spread_dv01`, `index_dv01`, `spread_duration` | standard definitions | U (choice) |
| FRB spreads 0.64 / 1.00 / 1.22 / 0.98 and the coupons known | `data/goi_frb_terms.csv`, `data/goi_frb_coupons.csv`, each row with its source and status | RBI's GoI list (saved); RBI releases via search; FRB 2034's 0.98 and 7.69 from the 2022 copy | P; V for FRB 2034 |
| Corporate FRB terms: benchmark, spread, floor, cap, reset process | `FloatingTerms`, `corporate_frb_rates` (fixings with `public_from`; fixing lag 0 and a one-fixing look-back when the terms are silent, U) | Annex XIV-A item 42 | V |
| A corporate FRB reset is fixed only on its own published fixing: the fixing date's (the last business day's before a holiday) for a daily benchmark, one within one and a half usual intervals for a periodic one, none still to be published; else projected and noted | `corporate_frb_rates` | inference | U |

Worked examples reproduced in [`tests/test_corporates.py`](../tests/test_corporates.py) and
[`tests/test_floating.py`](../tests/test_floating.py):

- **NCS master circular illustration** (8.95% on Rs 10 lakh, 14 Dec 2020 to 14 Dec 2025): five coupons of
  Rs 89,500.00, the Saturday coupon of 14 Dec 2024 paid on Monday 16 Dec, the Sunday maturity paid on
  Friday 12 Dec 2025 with the full coupon, Rs 14,47,500.00 in all.
- **SEBI 2013 Annex A** (8.95%, 13 Nov 2013 to 13 Nov 2018): 367 days and 364 days, which SEBI prints as
  Rs 89,745.00 and 89,255.00 (rupee-rounded; total Rs 14,47,500). The unrounded Rs 89,745.21 and 89,254.79 are
  the engine's reading of the years (anniversaries: 366/366 + 1/365); counting 367/366 gives 89,744.54, which
  rounds the same, so the illustration does not confirm the split.
- **SEBI 2016 examples**: 182/366 and 184/366 for the half-years of 2016; at 10% from 1 Jul 2016 to
  30 Jun 2018, 5.041096, 4.958904, 5.041096 and 4.931507 on a forward schedule.
- **Kosamattam's monthly table** (Series II and VII): all 37 and 61 coupons to the paisa on the calendar
  reading; the anniversary reading misses rows 25-26 (II) and 25, 26, 37, 38 (VII) by two paisa.
- **PFC and Capri** effective yields (6.8461 to 7.2948% for PFC's annual series against 6.85 to 7.29;
  8.8929 to 9.6943% for Capri), PFC's cumulative amounts (2,780.5090 against 2,780.50) and zero-coupon
  yields (6.8009, 6.8502, 6.9505%). Capri's printed 9.27% for its 36-month monthly series is the nominal
  8.90% compounded monthly; the flows' XIRR is 9.2639%.
- **RBI's WAY** 6.5059 from the WAP of 11 Nov 2026, and the cut-off rule's 7.72 against RBI's 7.69 for
  FRB 2034 from 30 Oct 2022 (cut-off yields from search summaries).

Built on these rules elsewhere: the trade-yield check and the price panel's recomputed yields (`trades`,
`price_panel`), G-, par and z-spreads (`credit_spreads`), asset swaps and FRB discount margins (`asset_swaps`),
corporate bonds and FRBs in the return engine (`returns`) and their cost rows (HS20-HS25, CS06-CS07, PF04-PF05,
SD06-SD07 in `cost_defaults.csv`): [`analytics.md`](analytics.md).

Not built yet: a banking calendar in which first, third and fifth Saturdays pay (it needs a list of the NI
Act holidays falling on Saturdays, which the shipped list leaves out), exchange and depository calendars
(record dates roll on the payment calendar), corporate bonds and FRBs in the event ledger (FRBs have no DV01
there), a parser for RBI's FRB coupon releases (no saved sample), the meaning of RBI's printed FRB auction
yield, MIBOR fixings (FBIL, licensed), coupon discretion and write-down of AT1 bonds (left in the spread),
default interest and TDS, partial exercises of options, amortisation between due dates and irregular explicit
schedules. Each choice marked U above is a named parameter, listed with
its options in the build's decisions for the owner. Exchange calendars are also missing for G-secs. T+2 for
FPI trades is `settlement_date(trade_date, GSEC, lag=2)`. MIBOR fixings and OIS dates use the Mumbai
settlement calendar (the handbook's MIBOR business days taken as Mumbai business days, an equivalence
reasoned, not confirmed: [`analytics.md`](analytics.md)).
