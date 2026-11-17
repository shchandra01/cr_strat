# Data for the rates and credit strategies

What the trading strategies need, and where each item can be had, as probed
on 16 Nov 2026 with real requests: one sample per dataset, each site's terms
read first and quoted. [`data_sources.md`](data_sources.md) covers the
forced-selling research (S9) and its sources; this file covers the other
strategies and does not repeat it. Terms change, so re-read them before any
bulk download.

Status: an independent re-check of the entries a build or purchase decision
rests on is under way. Until it lands, treat prices and quoted terms here as
provisional.

| Id | Strategy |
|---|---|
| S1 | Sovereign asset swaps: SDLs or off-the-run G-secs against paid OIS, funded in repo or TREPS |
| S2 | Front-end AAA bonds, CDs and CP up to 3 years against OIS |
| S3 | Rich/cheap to a fitted G-sec curve |
| S4 | Supply and RBI-operation cycle: auctions, switches, buybacks, open market and liquidity operations |
| S5 | Long end: 10-year against 30- to 50-year G-secs |
| S6 | SDL relative value across states and against G-secs |
| S7 | Policy path against OIS |
| S8 | High-grade credit spreads and new issues |
| S9 | Forced selling after rating actions (see [`data_sources.md`](data_sources.md)) |
| S10 | Selective AA and A carry |

**Bottom line for a commercial firm.**

- **Free, and the terms allow storage:** US Treasury yields, New York Fed
  SOFR, EIA Brent and the Federal Reserve's H.10 rupee rate.
- **Free to read, but no reuse licence; ask before storing a history:** RBI
  (auctions, money-market operations, security lists, policy), NSDL's FPI
  Monitor, SEBI's statistics, MoSPI's statistics API, the Controller General
  of Accounts and FIMMDA's public files.
- **Readable, but storing or back-testing needs a paid licence or
  membership:** CCIL (prices, repo rates and OIS trades by security), FBIL
  (valuation prices, curves, MIBOR, OIS to 5 years), NSE and BSE (corporate
  bond, CP and CD trades), and the valuation matrices of FIMMDA, ICRA
  Analytics and CRISIL.
- **No source found at all:** a history of quotes or order-book depth; an
  official OIS curve beyond 5 years; machine-readable insurer and pension
  flows.

Published prices for the smallest licensed set come to about ₹8.9 lakh to
₹21.4 lakh a year before GST, plus a CCIL quote ([below](#smallest-licensed-set)).
Everything RBI publishes on supply, policy and liquidity can be collected now,
slowly, from its RSS feed. Prices cannot.

## What each strategy needs

| Strategy | Free now | Needs a licence or permission |
|---|---|---|
| S1 | Security lists and auction results (RBI); money-market rates (RBI); TREPS haircuts by security (CCIL, current file only) | Bond prices (CCIL trades or FBIL valuation); OIS to 5 years (FBIL); OIS beyond 5 years (CCIL trade prints or a broker); repo rate per security (CCIL) |
| S2 | CP and CD aggregates (RBI) | CP, CD and bond trades (NSE, BSE; CCIL F-TRAC); OIS, CD and T-bill curves and overnight MIBOR (FBIL) |
| S3 | Security lists (RBI) | Trades by security, with odd-lot and when-issued flags (CCIL); FBIL valuation as a cross-check |
| S4 | Calendars and results of auctions, underwriting, switches, OMOs and liquidity operations (RBI); compiled auction files from 2015-16 (FIMMDA) | Prices around each event (CCIL) |
| S5 | Auctions by tenor (RBI); quarterly ownership (RBI Bulletin) | Long-end prices (CCIL, FBIL); pension and insurer data (PFRDA, IRDAI, by hand or by permission) |
| S6 | Weekly SDL auction results by state; state finances (RBI) | SDL trades and CCIL's daily SDL spread file; SDL valuation (FBIL) |
| S7 | Policy rates, MPC dates and minutes, liquidity (RBI); CPI, IIP, GDP (MoSPI API) | OIS and overnight MIBOR (FBIL) |
| S8 | Issuance totals (SEBI) | Bond trades (NSE, BSE); spread matrices and security-level prices (ICRA Analytics, CRISIL or FIMMDA); issue-level bidding data (BSE file) |
| S10 | Rating actions (see [`data_sources.md`](data_sources.md)) | As S8, plus issuer financials |

## Data items, field by field

| Item | Fields | Used by |
|---|---|---|
| Sovereign security master and issuance ledger | One row per ISIN per event: new issue, reissue, green-shoe, devolvement, non-competitive allotment, switch in or out (with the paired ISIN), buyback, maturity. Dates, amounts, cut-off and weighted-average price and yield, auction method. Coupon, coupon dates, maturity and outstanding stock on any date. Dated flags: Fully Accessible Route (FAR), TREPS eligibility, special security. Matured ISINs kept. | S1, S3 to S6 |
| Benchmark table | Benchmark ISIN per tenor bucket and date, and the date it took over; FIMMDA's monthly list of liquid securities for short selling | S1, S3 to S5 |
| Sovereign prices | Per trade: time, segment (standard lot, odd lot, when-issued, negotiated), settlement date, price, yield, amount. Per ISIN and day: volume, trade count, last-trade time, and a traded flag beside the valuation price | S1, S3 to S6 |
| Valuation prices and curves | FBIL G-sec, SDL and STRIPS prices; par and zero curves; T-bill and CD curves | S1 to S3, S5, S6 |
| Swap curves | MIBOR-OIS from 1 month to 10 years: closes, trade prints (time, start and end, rate, notional) and conventions; overnight MIBOR with its method changes; Modified MIFOR | S1, S2, S7 |
| Funding rates | Daily TREPS, call, basket repo and special repo rates and volumes; repo rate per security; RBI facility rates and auction cut-offs; flags for quarter-ends and 31 March | S1 to S3, S5, S6, S8, S10 |
| Funding terms | Haircuts and concentration limits by security, with effective dates; CCIL margin factors; corporate bond repo eligibility; short-sale limits | S1 to S3, S8 |
| Supply and RBI operations | Calendars as first published and as revised; announcements and results with publication time; underwriting commission; switches, buybacks, OMOs, liquidity auctions | S4 to S7 |
| Policy and liquidity | Policy corridor with announcement and effective dates; MPC dates, votes and minutes; net liquidity; government cash | S4, S7 |
| Macro | CPI (item level), WPI, IIP, GDP, fiscal accounts, GST, trade: release time, first print and revisions; consensus forecasts | S7 |
| Demand and positioning | Ownership by holder type; foreign flows and holdings by ISIN; insurer, pension and provident fund data; mutual fund flows | S4 to S6 |
| State data | State master with name changes; auction results by state; calendar against actual borrowing; redemption ladder; fiscal indicators | S6 |
| Corporate security master | ISIN, issuer and group, coupon schedule, calls and puts, partial redemptions, step-ups, seniority, security, guarantees, tax status, face value through time; CP and CD issue data | S2, S8 to S10 |
| Corporate trades | Per trade: price, yield, size, platform and deal type; end-of-day summaries; request-for-quote best bid and ask | S2, S8 to S10 |
| Corporate valuation | Rating-by-tenor spread matrices by sector; the security-level prices mutual funds use | S2, S8 to S10 |
| Corporate primary | Issue, size, cut-off and bids from the electronic bidding platforms; CP and CD primary rates | S8 |
| Conventions and calendars | Per instrument: day count, coupon frequency, yield compounding, settlement lag, lot sizes, shut periods and record dates, holiday rules. Holiday calendars per market | All |
| Costs | Clearing, platform and exchange fees; stamp duty; tax deducted at source on coupons; custody and brokerage | All |
| Global and index events | US yields and SOFR, Brent, USD/INR; index inclusion decisions | S5, S7 |
| The firm's own access | What the firm's legal form allows: repo, short sales, OIS purpose, bidding platforms, data entitlements | S1 to S3, S7 |

## Sources

### Summary

| Item | Best source | History | Terms | Route |
|---|---|---|---|---|
| Sovereign security master | RBI lists of outstanding and FAR securities; CCIL haircut list for ISIN, coupon and maturity | Snapshots only | RBI: no reuse licence. CCIL: no commercial download without consent | Store dated snapshots from now; ask RBI |
| Issuance ledger | RBI auction, switch and OMO releases; FIMMDA auction files | RBI press-release archive from 1990; FIMMDA files from 2015-16 | RBI as above; FIMMDA states no terms | RSS feed going forward; FIMMDA files for back history |
| Sovereign prices | CCIL trades by security | CCIL members' archive, depth unknown | Commercial use needs a Data Agreement | CCIL quote |
| Valuation and curves | FBIL | From 31 Mar 2018 | Storing or back-testing needs a subscription | FBIL subscription |
| OIS to 5 years | FBIL MIBOR-OIS | From Apr 2018 | As FBIL | FBIL subscription |
| OIS beyond 5 years | CCIL trade prints; Parameta (ICAP and Tullett Prebon) | CCIL site: current day only | Licence needed | CCIL or Parameta quote |
| Funding rates | RBI Money Market Operations; CCIL repo by security | RBI from 2000; CCIL two days free | RBI: no reuse licence. CCIL: Data Agreement | RSS feed; CCIL quote |
| Funding terms | CCIL TREPS haircut notifications | Current file only | No commercial download without consent | Keep each file from now; ask CCIL |
| Supply and operations | RBI press releases | Archive from 1990 | No reuse licence | RSS feed; ask RBI |
| Macro | MoSPI API | CPI from 2011, IIP from 2012 | Not readable | Use; confirm the licence |
| Foreign flows | NSDL FPI Monitor | Holdings by ISIN from 30 Sep 2015 | Reproduction prohibited | Ask NSDL |
| Corporate trades | NSE and BSE feeds | History not on the price lists | Licence | ₹3.4 lakh (NSE); ₹0.5 to 1.5 lakh (BSE) |
| Corporate valuation | ICRA Analytics, CRISIL, FIMMDA | FIMMDA security level from Jul 2019 | Licence | Quote; FIMMDA ₹1 lakh |
| Corporate primary | BSE bidding-platform file; Prime Database | Not stated | Licence | ₹1.5 lakh (BSE); Prime quote |
| Global | US Treasury, New York Fed, EIA, Federal Reserve | Decades | Storage allowed | Use |

### RBI (www.rbi.org.in)

**Terms.** RBI's
[Disclaimer](https://www.rbi.org.in/Scripts/Disclaimer.aspx) has no clause on
automated access, databases, commercial use or back-testing, and grants no
reuse licence. It reserves the right "to block access from a particular
Internet address ... without ascribing any reasons", and says "caching and
links to, and the framing of this Web Site or any of the contents are
prohibited" without permission. Pages carry "(c) Reserve Bank of India. All
Rights Reserved." Ask RBI in writing before storing a history.

**Access in practice.**

- HTML pages on www.rbi.org.in are readable. After about 25 to 30 pages in a
  session the site returns HTTP 418; robots.txt itself returned 418.
- Every XLS, XLSX and PDF attachment sits on rbidocs.rbi.org.in, which serves
  a JavaScript bot challenge. Do not automate past it.
- The [press-release RSS feed](https://www.rbi.org.in/pressreleases_rss.xml)
  carries the 10 latest releases with their full tables. Polling it a few
  times a day collects auction results and money-market operations going
  forward with very few requests. Backfilling years of releases page by page
  will be blocked.

**Datasets.**

| Dataset | Contents | Frequency | Notes |
|---|---|---|---|
| [Money Market Operations](https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx) | Volume, weighted average rate and range for call, TREPS, market repo and corporate bond repo, overnight and term; RBI operations (repo, reverse repo, MSF, SDF, SLF); net liquidity; bank reserves | Daily, every calendar day, next working day | Archive lists 2000 to 2026. Layout changed in Apr 2022, Dec 2023 and Sep 2025. Market repo is an aggregate: no special rates |
| GoI dated auction results ([sample](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63970)) | Per security: notified amount, bids, cut-off price and yield, accepted, weighted average, non-competitive, underwriting, devolvement | Weekly, same day | Names the security, not the ISIN: join to the outstanding list |
| T-bill auction results ([sample](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63956)) | Same fields for 91, 182 and 364 days | Weekly | Non-competitive amounts are large |
| SDL auction results ([sample](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63950)) | Per state security: notified, bids, cut-off yield and price, accepted, reissue details | Weekly | Most lines are reissues under the Benchmark Issuance Strategy begun in Q1 FY2026-27 |
| Underwriting results ([sample](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63967)) | Minimum and additional underwriting, commission cut-off in paise per ₹100 | Weekly, morning of the auction | Not in the Handbook tables |
| Switch, OMO and variable-rate repo results | Amounts offered and accepted, cut-offs | As held | No buyback or OMO sale release was sampled |
| [Issuance calendar](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63668) | Weekly amounts by tenor bucket | GoI half-yearly; T-bills and states quarterly | Oct 2026 to Mar 2027: ₹7,86,000 crore in 23 auctions. State annexes are PDFs on rbidocs |
| [GoI securities outstanding](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=1956) | ISIN, name, issue and maturity dates, outstanding stock | Updated after auctions | 122 securities on 16 Nov 2026. Snapshot only: keep dated copies. No coupon dates, reissue history or T-bills |
| [FAR securities](https://www.rbi.org.in/Scripts/bs_viewcontent.aspx?Id=3876) | ISIN, name, issue and maturity dates | On designation | 57 securities since Apr 2020 |
| [SDLs outstanding](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=4433) | Not seen | After auctions | XLS on rbidocs, not opened |
| [Handbook of Statistics](https://www.rbi.org.in/Scripts/AnnualPublications.aspx?head=Handbook%20of%20Statistics%20on%20Indian%20Economy) | Table 221: one year of GoI auctions per edition; tables on ownership, OMOs, call rates, CP, CD, T-bill auctions, month-end SGL yields | Annual | Names, not ISINs |
| [Bulletin Table 47](https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24571) | Ownership of central, state and T-bill debt by 12 holder types | Quarterly, about four months late | Consistent from Jun 2023 |
| Bulletin current statistics | Liquidity operations, CP, CD, call rates, state borrowing (Tables 49 to 51), CPI, WPI, IIP | Monthly | CPI now base 2024; WPI and IIP base 2022-23 |
| [Weekly Statistical Supplement](https://www.rbi.org.in/Scripts/BS_ViewWSS.aspx) | Rates, liquidity, CP, CD, borrowings | Weekly | Table list not confirmed |
| [DBIE](https://data.rbi.org.in) | Time series for auctions, money markets, rates | Not verified | No documented public API was found, which corrects [`data_sources.md`](data_sources.md). Depth and reuse terms unknown |
| [MPC](https://www.rbi.org.in/scripts/Annualpolicy.aspx) | Schedule, resolutions, minutes with votes, current rates | Six meetings a year | Repo 5.50%, SDF 5.25%, MSF 5.75% on 16 Nov 2026 |

RBI publishes no secondary-market prices, curves, swaps, haircuts or depth.

### CCIL (www.ccilindia.com)

**Terms.** The [Terms of Use](https://www.ccilindia.com/documents/d/ccil/terms-of-use)
(Apr 2024) forbid downloading content "for any commercial uses, except with
the express written consent of CCIL" (cl. 4.1) and "any systematic or
automated data collection activities" (cl. 4.8). The
[Data Sharing Policy](https://www.ccilindia.com/documents/d/ccil/ccil_datasharing_policy_oct25)
(version 6.0, Oct 2025) allows website data for non-commercial use only. A
trading firm is a "Commercial User" and needs a paid Data Agreement; prices
follow a Board-approved policy that is not published. Members may use the
archive "only for their internal research and analysis and regulatory
reporting". Guests see the latest two reports.

**Membership.** One-time fee ₹1 lakh for the securities segment and ₹25,000
each to CCIL and Clearcorp for TREPS, plus GST. Minimum net worth ₹200 crore
for NBFCs, primary dealers and other corporates since 4 Oct 2023.

**Datasets.**

| Dataset | Contents | Free access |
|---|---|---|
| [Trades by security](https://www.ccilindia.com/web/ccil/trading-platform) | Per ISIN and day: trades, volume, high, low, last price and yield, weighted average price and yield, platform | Latest two reports |
| [Repo by security](https://www.ccilindia.com/web/ccil/security-wise-repo-market-summary) | Per ISIN and day: trades, volume, open, high, low, last and weighted average rate. The only source found for special repo rates | Latest two reports |
| [G-sec trade by trade](https://www.ccilindia.com/web/ccil/g-sec-historical-trades) | Every outright trade | Latest two, after sign-in |
| [Money-market rates](https://www.ccilindia.com/web/ccil/money-market-rates-and-volumes-most-liquid-tenor-) | Call, TREPS and basket repo: open, high, low, last, weighted average, volume | Latest two days |
| [OIS trade prints](https://www.ccilindia.com/web/ccil/interbank-inr-interest-rate-swaps-real-time-trade-by-trade-dissemination) | Interbank MIBOR-OIS and Modified MIFOR trades: dates, tenor, notional, fixed rate, time | Current day |
| [TREPS haircuts](https://www.ccilindia.com/web/ccil/security-settlement1) | About 1,800 ISINs with coupon, maturity, liquidity class and haircut (T-bills 1%, SDLs 10%) | Free PDF, about weekly, no archive |
| [Zero curve](https://www.ccilindia.com/web/ccil/zcyc-parameters) | Daily Nelson-Siegel-Svensson parameters | Latest two days |
| [SDL spread file](https://www.ccilindia.com/web/ccil/state-government-spread-analysis) | SDL trades over ₹5 crore with spreads to G-secs | Latest two, after sign-in |
| [Liquidity indicators](https://www.ccilindia.com/web/ccil/bid-ask-spread) | Monthly bid-ask by liquidity class; order-book counts | Latest two months |
| [Tenor-wise yields](https://www.ccilindia.com/web/ccil/tenorwise-indicative-yields) | The benchmark security per tenor bucket | Latest date |
| [Foreign holdings](https://www.ccilindia.com/web/ccil/fpi-home-page) | FPI limits and holdings by ISIN | Current snapshot |
| [F-TRAC](https://www.ccilindia.com/web/ccil/primary-and-secondary-market) | CP and CD primary and secondary trades; corporate bond repo | Not confirmed |

Settlement charges ([schedule](https://www.ccilindia.com/web/ccil/securitystlfeescharges),
from Oct 2019, per counterparty, plus GST): outright dated securities ₹95 per
crore of face value (minimum ₹20, maximum ₹4,750 a trade); T-bills ₹45 per
crore; repo ₹10 per crore a leg; TREPS ₹10 per crore of repayment value.
Clearcorp's site, which holds platform charges, returned HTTP 403.

### FBIL (www.fbil.org.in)

**Terms.** The
[Terms of Use](https://www.fbil.org.in/uploads/FBIL_Terms_of_Use_and_Subscription_Disclaimer_for_FBIL_Benchmarks_97d336023d.pdf)
of 13 Oct 2026 are unchanged: free data is 7 days late and "solely for
delayed, non-commercial viewing" (cl. 9.1); building "an internal database,
historical archive, or systematic tracking feed" needs a paid subscription
(cl. 9.2); back-testing on vendor data counts as commercial use (cl. 2.3);
FBIL may "retroactively bill" unauthorised users (cl. 3.1); a "derived
benchmark, index, financial instrument, or valuation model" needs a Derived
Product License (cl. 5.1).

**Benchmarks beyond those in [`data_sources.md`](data_sources.md).** Secured
Overnight Rupee Rate, from TREPS and basket repo trades (from 7 Jul 2025;
subscription only from 1 Oct 2025); Market Repo Overnight Rate (from
12 Dec 2017); Term MIBOR (14 days, 1 and 3 months); CD curve; STRIPS
valuation; Modified MIFOR and its swap curve (from 10 Nov 2025, with 7- and
10-year points from 8 Jun 2026). That swap curve reaches 10 years but prices
swaps on Modified MIFOR, not overnight MIBOR, so it is not an OIS curve.
MIBOR-OIS still stops at 5 years. There is no CP curve and no corporate bond
valuation.

**Prices** (per year, end users, before GST):

| Benchmark | Valuation only | Other uses, including pricing and curves |
|---|---|---|
| G-sec and SDL valuation (one benchmark) | ₹50,000 | ₹2,50,000 |
| Each money-market benchmark (overnight MIBOR, market repo, Term MIBOR, MIBOR-OIS, CD curve, T-bill curve and others) | ₹1,00,000 (total capped at ₹5,00,000 above five) | ₹2,50,000 |
| Secured Overnight Rupee Rate | ₹1,25,000 | ₹3,00,000 (₹50,000 below ₹500 crore turnover) |
| Modified MIFOR swap curve | ₹1,20,000 | ₹2,50,000 (₹50,000 small users) |
| Delivery, one time | API ₹50,000; SFTP ₹20,000 | |

Sources: [money-market pricing](https://www.fbil.org.in/uploads/Comprehensive_Policy_Document_on_Pricing_of_benchmarks_e17db29459_2_cbeed36175.pdf),
[G-sec and SDL pricing](https://www.fbil.org.in/uploads/Pricing_of_FBIL_G_Sec_Valuation_and_SDL_Valuation_benchmarks_c4832dcaaa.pdf),
[SORR pricing](https://www.fbil.org.in/uploads/FBIL_Notification_on_Pricing_of_FBIL_SORR_Benchmark_September_9_2025_a7ca3a635f.pdf),
[MMFOR pricing](https://www.fbil.org.in/uploads/Notification_No_13_Pricing_of_MMFOR_Swap_Rates_Curve_823523aaee_c6d26a4977.pdf).
Which tier fits back-testing by a trading firm is our reading ("other uses"),
not FBIL's ruling.

**Method changes to mark in any history:** SDL valuation revised 15 Apr 2019
and realigned Jun 2025; long-end change Jun to Jul 2025; floating-rate bonds
15 Sep 2025; T-bill curve method revised 2 Nov 2026. FIMMDA stopped mirroring
FBIL data on 1 Oct 2026.

[`fbil.py`](../src/cr_strat/fbil.py) already reads subscribed par-curve,
zero-curve, MIBOR-OIS and T-bill files.

### FIMMDA (www.fimmda.org)

- **Paid login:** daily corporate bond spread and yield matrices (PSU banks
  and FIs, NBFCs, corporates; AAA to BBB-; 0.5 to 15 years), AT-1 matrix,
  daily security-level valuation (about 2,900 bonds in a May 2023 sample,
  firm from 1 Jul 2019) and 15 days of traded data. The 2022 fee notice puts
  corporate users at ₹1,00,000 a year and says "Historical Data ... is not
  included in the scope of Website User Agreement". Membership is limited to
  banks, financial institutions, primary dealers and insurers.
- **Free, no stated terms:** the
  [Handbook of Market Practices](https://www.fimmda.org/PageContent.aspx?Iid=MTAy)
  (Apr 2026); [holiday lists](https://www.fimmda.org/PageContent.aspx?Iid=NjE=);
  valuation methods; [auction data files](https://www.fimmda.org/PageContent.aspx?Iid=MTQ3)
  (G-secs from 2015-16; SDLs for 2011 to 2017 and 2020 to 2023, not updated
  since Feb 2025; not opened); circulars, including a monthly list of liquid
  securities for short selling.

### NSE and BSE

**NSE** answers again (www.nseindia.com redirects to www.nseindia.in), but
tables load by JavaScript. Its
[terms](https://www.nseindia.com/nse-terms-of-use) prohibit "any systematic or
automated data collection activities" and storing content "without prior
written permission of NSE".

| Product | Contents | Price a year, before tax |
|---|---|---|
| [Corporate Bond Market Data](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Corp_Bonds_Product_v1.4.pdf) | Per ISIN: weighted average and last price and yield, trades, value, source (reporting or RFQ), deal type, category (bond, CP, CD, securitised, G-sec) | ₹3,40,000 (from 1 Apr 2026). History not on the price list |
| Wholesale Debt Market | NSE's own debt segment, not NDS-OM | End of day ₹44,000; history ₹30,000 |
| Currency derivatives segment | Where interest rate futures appear to sit (confirm) | End of day ₹44,000 |
| [Fixed Income Valuations](https://www.nseindia.com/nse-data-and-analytics/nse-fixed-income-valuations) | Daily prices for over 10,000 securities; methods published Jul 2026 | Not published |

Free pages show trade repository data for both exchanges, request-for-quote
best bid and ask, tri-party repo and bidding-platform issues, each with CSV
export in a browser. NSE Indices' terms limit its site to "personal,
non-commercial use".

Exchange interest rate futures are too thin to hedge with: ₹19,379 crore
traded at NSE in all of FY2025-26, about ₹80 crore a day; BSE had none
([SEBI Bulletin](https://www.sebi.gov.in/reports-and-statistics/publications/jul-2026/sebi-bulletin-jun-2026_102586.html)).

**BSE** pages returned only a JavaScript shell, so its terms are unread. Its
[price sheet](https://www.bseindia.com/downloads1/Information_Products_Pricing_Sheet.pdf)
(Feb 2025) lists debt-segment feeds at ₹50,000 (15-minute delayed and end of
day) to ₹1,50,000 (1-minute) a year per segment, a bidding-platform
end-of-day file at ₹1,50,000 and privately placed memoranda at ₹3,00,000. It
names back-testing as a use of its historical data but gives no debt start
date or price.

### SEBI

- [Master Circular for Mutual Funds](https://www.sebi.gov.in/legal/master-circulars/mar-2026/master-circular-for-mutual-funds_100491.html)
  (20 Mar 2026), para 10.2.3: debt securities are valued at the average of
  valuation agencies' security-level prices; the waterfall is left to AMFI
  and the agencies. It defines no matrix.
- Statistics, free: corporate bond trades from FY2010; private placements
  monthly from Jan 2007; public issues from 2008-09; outstanding bonds
  quarterly from Sep 2020; corporate bond repo monthly from Jan 2010. Totals
  only.
- Monthly Bulletin tables: interest rate futures, AMC Repo Clearing volumes,
  private placements, FPI and mutual fund flows.

### AMC Repo Clearing (corporate bond tri-party repo)

Unreachable from here (TLS certificate chain error; HTTP 503). Volumes from
the SEBI Bulletin: ₹3,195 crore a day in FY2025-26 and ₹6,416 crore a day in
Apr to May 2026. Eligible collateral, haircuts and membership need a browser
or a call.

### NSDL FPI Monitor (www.fpi.nsdl.co.in)

[Daily flows](https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx) by route
(general limit, VRR, FAR); [debt utilisation](https://www.fpi.nsdl.co.in/web/Reports/ReportDetail.aspx?RepID=1)
with FAR holdings by ISIN, archive from 30 Sep 2015;
[trade-wise FPI debt files](https://www.fpi.nsdl.co.in/web/StaticReports/FIITradeWiseDebt/FIITradeWiseDebt.htm)
from Aug 2002 to Mar 2025. Terms: "Reproduction, redistribution and
transmission of any information contained on the website are strictly
prohibited." Ask NSDL before storing.

### Macro

- **MoSPI** [API](https://api.mospi.gov.in/api/cpi/getCPIData?base_year=2024&series=Current&level=Item&limit=10):
  free, no key. CPI by item (bases 2012 and 2024), IIP, national accounts,
  WPI by item. October 2026 CPI, released on 12 Nov, was in it by 16 Nov. An
  official MCP server and a Python client exist; the client's default
  address is a private test server, so override it. The site's terms could
  not be read.
- **[Advance release calendar](https://www.mospi.gov.in/release-calendar)**:
  CPI on the 12th, IIP on the 28th, with actual dates.
- **Controller General of Accounts**
  [monthly accounts](https://cga.nic.in/MonthlyReport/Published/9/2026-2027.aspx):
  about a month late; terms cover liability only.
- **GST** statistics files were listed but not downloaded.
- **PIB** permits reproduction without prior approval, with acknowledgement.
- **Insurers and pensions:** IRDAI's and PFRDA's robots.txt bar all crawlers;
  NPS Trust and EPFO returned 403. Monthly at best; download by hand or ask.
- **Ministry of Finance** public debt reports (dea.gov.in) were unreachable
  from here.

### Global series

| Series | Source | Terms |
|---|---|---|
| US Treasury par curve | [Treasury CSV](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv) | No reuse clause; robots allow all |
| SOFR | [New York Fed API](https://markets.newyorkfed.org/api/rates/secured/sofr/last/2.json) | Licence to "Download, store, and use Content", by automated process; notice required |
| Brent | [EIA](https://www.eia.gov/dnav/pet/hist/RBRTED.htm) | Public domain; published weekly |
| USD/INR | [Federal Reserve H.10](https://www.federalreserve.gov/releases/h10/hist/dat00_in.htm) | Public domain; a New York rate, not FBIL's fixing |

Do not build a stored history from FRED: its terms prohibit storing, caching
or archiving and commercial use without consent.

Index events: FTSE Russell publishes country classification results openly
(6 Oct 2026: no fixed income changes; next update Apr 2027). J.P. Morgan
publishes no announcements page, and Bloomberg's pages refused automated
access; its deferrals of Global Aggregate inclusion (13 Jan and 31 Jul 2026)
are known only from news.

### Commercial vendors

| Vendor | What it adds | Price |
|---|---|---|
| Parameta (ICAP, Tullett Prebon) | Rupee non-deliverable OIS, MIFOR 6M swaps, rupee cross-currency swaps; tenors not stated | Quote |
| ICRA Analytics | Daily security-level valuation and yield matrices by sector, rating and duration; one of the two agencies mutual funds use | Quote |
| CRISIL | The other agency; not fetched (robots.txt bars AI agents) | Quote |
| Prime Database | Private placements, public issues, CP and CD issuance | Quote |
| Bloomberg, LSEG, NSE Cogencis | Terminals; a seat is not a licence to store or back-test | Bloomberg about USD 32,000 a seat a year (third-party figure); others quote |
| CEIC | CCIL-sourced yields at fixed tenors, daily from 2008 | Quote |
| EODHD, Trading Economics, Cbonds | Benchmark yields only, or FBIL's delayed data | Low, but not for commercial use or not the data needed |

## Smallest licensed set

| Item | Covers | ₹ a year, before GST |
|---|---|---|
| FBIL G-sec and SDL valuation | Valuation prices and curves | 2,50,000 |
| FBIL MIBOR-OIS | OIS to 5 years | 2,50,000 |
| NSE Corporate Bond Market Data | Corporate bond, CP and CD trades reported at NSE | 3,40,000 |
| BSE corporate debt, end of day | Trades reported at BSE | 50,000 |
| **Minimum** | | **8,90,000** |
| FBIL overnight MIBOR, T-bill curve, CD curve | Money-market curves | 7,50,000 |
| FBIL Secured Overnight Rupee Rate | Funding benchmark | 3,00,000 |
| BSE 1-minute feed instead of end of day | Intraday trades | +1,00,000 |
| FIMMDA website user (2022 list) | Spread matrices, security-level valuation | 1,00,000 |
| **With all of the above** | | **21,40,000** |
| CCIL Data Agreement | Sovereign trade history, repo by security, OIS prints at all tenors | Quote |
| ICRA Analytics or CRISIL | Prices mutual funds mark to | Quote |

Add ₹50,000 once for FBIL API delivery. No terminal is needed for
back-testing.

## Enquiries to send

| To | Ask |
|---|---|
| CCIL Data Services ([form](https://www.ccilindia.com/web/ccil/data-request-form)) | Price, depth and format of trade history by security, repo by security, TREPS and OIS prints; whether a member's back-testing counts as internal research |
| FBIL | Which tier applies to back-testing and curve fitting; whether a subscription includes history and from when; whether an in-house curve built on FBIL prices needs a Derived Product License; whether MIBOR-OIS starts at 1 or 6 months; price of the USD/INR reference rate |
| NSE Data & Analytics | Whether corporate bond history is sold, from when and at what price; storage and back-testing rights; which segment carries interest rate futures; price of its valuations |
| BSE | Current debt tariff and history depth; website terms |
| RBI | Written permission to store press-release data and back-test on it; an acceptable request rate; bulk access to DBIE |
| FIMMDA | Current fees; whether the security-level file is included; price of history; whether storage is allowed |
| NSDL | Permission to store FPI Monitor history; bulk archive |
| ICRA Analytics, CRISIL | Licence for security-level prices and matrices for back-testing; start date; price |
| Parameta | Whether the onshore MIBOR OIS curve to 10 years is included; history start; price for model use |
| AMC Repo Clearing | Eligible collateral, haircuts, membership |
| MoSPI | Which licence governs API data in a stored commercial database |

## Build order

What can be built now, with no licence:

1. **Calendars and conventions.** Business-day calendars for Mumbai
   settlement, MIBOR fixing and the exchanges; day counts (30/360,
   actual/365, actual/actual); settlement lags, shut periods and holiday rules
   for coupons and redemptions. Sources: FIMMDA's handbook and holiday lists.
2. **Sovereign security master and issuance ledger.** One row per ISIN per
   issue, reissue, switch, buyback and maturity, so outstanding stock is known
   on any past date. Sources: RBI's lists kept as daily snapshots from now;
   auction results from the RSS feed going forward; FIMMDA's auction files for
   back history.
3. **Bond pricing engine.** Cash flows, price and yield, accrued interest,
   DV01 and convexity for G-secs, SDLs and T-bills, tested against RBI's
   auction price and yield pairs.
4. **Daily store of RBI operations and money-market rates** from the RSS
   feed.
5. **Macro and global store** from MoSPI's API and the US publishers.

Then, as licences arrive: the sovereign price panel (extend
[`fbil.py`](../src/cr_strat/fbil.py) to the security sheets; a CCIL trade
loader on the pattern of [`trades.py`](../src/cr_strat/trades.py)); the curve
fit for S3; OIS bootstrapping, asset-swap spreads and funding for S1; a return
engine with carry, roll-down, funding, haircuts and costs; S2 on the existing
trade and price-panel code; the event ledger for S4; the state panel for S6
and implied policy paths for S7; corporate cash-flow terms, primary data and
fundamentals for S8 and S10.

## Pitfalls

- **Yield bases differ.** G-secs and SDLs quote semi-annual yields; T-bills,
  CP and CD simple actual/365 yields; OIS up to 1 year a simple rate and
  semi-annual beyond; corporate bonds mostly annualised. Mixing bases costs
  10 to 20 basis points, the whole signal in S1, S2 and S8. Compute spreads
  from cash flows on one basis.
- **Day counts and holidays.** G-secs and SDLs accrue 30/360; corporate bonds
  actual/actual under SEBI's 2016 rule, older issues often actual/365. Coupons
  and redemptions falling on holidays move by rule (to verify per
  instrument).
- **Shut periods and record dates.** G-secs cannot settle in the shut period
  before a coupon; corporate bonds have record dates about 15 days ahead, and
  tax deducted at source falls on the record-date holder. Reported yields in
  those windows are unreliable.
- **Valuation prices are not trades.** FBIL prices for untraded bonds, and
  agency prices and matrices for corporate bonds, are model outputs. A curve
  fitted to them recovers the model, and back-tests on them show smoothed
  returns. Keep a traded flag; never assume a fill at a valuation price.
- **Method breaks.** FBIL took over from FIMMDA on 31 Mar 2018; SDL valuation
  changed in Apr 2019 and Jun 2025; MIBOR was polled before Jul 2015. Mark
  each break; do not splice across it silently.
- **SDL identity.** Names are not unique, coupon dates follow each issue date,
  and states have been reorganised. Key on ISIN.
- **Thin trading.** Most SDLs and off-the-run G-secs trade on few days a year;
  odd lots and negotiated deals are off-market. Compare each trade with the
  benchmark at the same time of day.
- **Benchmark rolls.** The 10-year changes about twice a year and trades rich
  to neighbours; generic series jump at rolls.
- **Funding regimes change.** Haircuts, margins, the policy corridor, bank
  investment accounting (1 Apr 2024), the FAR (Apr 2020) and index inclusion
  (from Jun 2024) all changed behaviour. Back-test on the terms in force on
  each date. Quarter-end and 31 March funding spikes are real.
- **Survivorship.** Current lists drop matured, switched and bought-back
  securities; stack snapshots. T-bill ISINs live under a year.
- **Reported corporate trades.** The same trade can appear on both exchanges;
  inter-scheme transfers print at valuation prices; reported yields may be to
  call or put.

## Not resolved

- Fields and history of CCIL's script-loaded tables and FBIL's tables: no
  data rows were seen.
- Whether MIBOR-OIS starts at 1 or 6 months.
- Contents of RBI's SDL and STRIPS lists, DBIE's depth and terms, and the
  Ministry of Finance debt reports.
- BSE's terms and debt data; AMC Repo Clearing's collateral rules; F-TRAC's
  fields; Clearcorp's charges.
- MoSPI's licence; the FRED terms, which were read only through a summarising
  tool.
