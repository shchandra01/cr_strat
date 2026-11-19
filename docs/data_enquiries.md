# Data enquiries: draft letters

Twelve enquiry letters, one for each data owner in the "Enquiries to send"
table of [`rates_data_sources.md`](rates_data_sources.md#enquiries-to-send).
They ask for the quotes, licences and written permissions that the strategies
S1 to S10 wait for. **Nothing has been sent.** To use them: fill in the
placeholders, re-read the owner's current terms, and send each letter by the
route in the table below. For six owners the sources record no route, so one
has to be found first.

Every product name, fee, date and address in this file comes from
[`rates_data_sources.md`](rates_data_sources.md) (probed on 16 and 17 Nov
2026), [`data_sources.md`](data_sources.md) (13 Oct 2026) or the probe notes
behind them. Nothing was fetched or searched for this file, so no address,
fee or term has been confirmed with its owner since those dates. Whatever the
sources mark as unverified is asked as a question, not stated. Strategy
numbers are those of `rates_data_sources.md`. Each paragraph of a letter sits
on one line, so that it pastes cleanly.

Each letter asks every question in its owner's row of that table, then other
points the sources leave open for the same owner. The eight letters that seek
a paid licence also ask the file format, whether stored data may be kept when
the licence ends, and the lead time. Not covered: owners outside the table,
such as AMFI and the other rating agencies, whose permission
`data_sources.md` says to ask.

## Placeholders

| Placeholder | Fill in with | Letters |
|---|---|---|
| `[FIRM NAME]` | The firm's registered name | All |
| `[ENTITY TYPE AND REGISTRATION]` | Its legal form and the registrations it holds | All |
| `[CONTACT NAME]` | The person who signs | All |
| `[EMAIL]`, `[PHONE]` | Where the reply should go | All |
| `[HISTORY START DATE]` | The earliest date from which history is wanted; CCIL's form asks for a period | CCIL |

Beyond these, the letters say only how the data would be used: internal
research, back-testing and trading models on the firm's own capital. They do
not state its size, turnover, strategies or memberships. Where a fee depends
on one of these, the letter asks which category applies, so have the facts
ready for the reply. The tiers the sources record:

- **FBIL.** Each priced benchmark has a fee for valuation only and a higher
  fee for "other purposes": ₹50,000 or ₹2,50,000 a year for G-sec and SDL
  valuation, ₹1,00,000 or ₹2,50,000 for each money-market benchmark. The
  money-market fees apply once annual turnover or outstanding contracts in
  that benchmark reach ₹500 crore; the fee below that is not confirmed, so
  the letter asks. The Secured Overnight Rupee Rate costs ₹1,25,000 or
  ₹3,00,000, or ₹50,000 for turnover below ₹500 crore (FY2025-26 schedule;
  none is published for FY2026-27). The Modified MIFOR swap curve, which the
  letter does not ask for, costs ₹1,20,000 or ₹2,50,000, or ₹50,000 for small
  users. Recognised research and educational institutions pay ₹10,000 a
  benchmark. No fee is confirmed for the USD/INR reference rate. Delivery
  costs ₹50,000 once by API or ₹20,000 by SFTP. FBIL rules on which tier
  applies. The letter does not state the firm's turnover.
- **CCIL.** Members may use the website archive for internal research; any
  other commercial user needs a Data Agreement, at a price that is not
  published. Membership needs a net worth of ₹200 crore for NBFCs, primary
  dealers and other corporates. The letter is written for a firm that is not
  a member. If the firm is a member, add one question: whether back-testing
  on the archive counts as "internal research and analysis".
- **NSE.** Corporate Bond Market Data costs ₹3,40,000 a year per site, and
  50% more for each additional delivery channel. No price is published for
  NSE Fixed Income Valuations. The letter does not state the number of sites.
- **BSE.** In the price sheet of Feb 2025, which may have been revised:
  ₹50,000 to ₹1,50,000 a year per debt segment, by the delay of the feed;
  ₹1,50,000 for the bidding-platform end-of-day file; ₹3,00,000 for privately
  placed memoranda. Each legal entity is counted separately for historical
  data, for which the sheet gives no debt start date or price.
- **FIMMDA.** By category of website user in the notice of 21 Feb 2022:
  ₹1,00,000 a year for corporate users needing data for their own internal
  use, ₹75,000 for educational institutions, ₹2,00,000 for domestic data
  vendors, advisory and research firms, ₹2,80,000 for non-member insurers,
  ₹5,00,000 for rating agencies and exchanges, and ₹5,00,000 to ₹10,00,000
  for multi-country companies. Whether these fees still apply is not
  confirmed.

The sources record no published price for ICRA Analytics, CRISIL, Parameta or
AMC Repo Clearing. RBI's, NSDL's and MoSPI's data is free to read; whether
permission to store it carries a fee is asked in the letters.

## Whom to write to, and in what order

Prices first, because every back-test waits for them: FBIL, then CCIL. Then
RBI's permission to store its releases, then the exchanges, then the valuation
and swap sources, then the rest.

"How to send" gives only a channel that the sources record; none was
re-checked for this file, so confirm it before sending. For FBIL, CRISIL,
Parameta, NSDL, AMC Repo Clearing and MoSPI the sources record none. The
table says so and names the site the sources give; no contact page was looked
up.

"What the answer unlocks" follows "What each strategy needs" in
`rates_data_sources.md`, in that table's words. Where it draws instead on the
"Data items, field by field" table of the same file (called "Data items"
below), or on `data_sources.md`, it says so.

| Order | Recipient | How to send | What the answer unlocks |
|---|---|---|---|
| 1 | FBIL | None recorded. The sources give only the site, <https://www.fbil.org.in/>, which since 17 Nov 2026 loads behind reCAPTCHA. Find the subscription contact there | S1 (bond valuation prices; OIS to 5 years), S2 (OIS, CD and T-bill curves, overnight MIBOR), S3 (prices as a cross-check), S5 (long-end prices), S6 (SDL valuation), S7 (OIS, overnight MIBOR). Also the G-sec and OIS curves S9 uses (`data_sources.md`) |
| 2 | CCIL Data Services | Data Request Form: <https://www.ccilindia.com/web/ccil/data-request-form>. It asks for organisation, description, purpose (Research, Commercial or Others) and period of data; use the letter as the description | S1 (bond prices from trades; OIS beyond 5 years from trade prints; repo rate per security), S2 (F-TRAC CP and CD trades), S3 (trades by security, with odd-lot and when-issued flags), S4 (prices around each event; trading by participant category), S5 (long-end prices), S6 (SDL trades and the daily SDL spread file). Question 4: a history of the TREPS haircuts, of which S1 now has only the current file |
| 3 | RBI | The contact address shown on `www.rbi.org.in`, which the probe notes record as `helpdoc[at]rbi[dot]org[dot]in`. For question 4, the addresses shown on DBIE (<https://data.rbi.org.in>): `dbie@rbi.org.in` and `cimsportalspt@rbi.org.in`. The sources do not say which office grants permission to reuse | A stored history of what S1 to S7 can now only read: security lists (S1, S3), auction results and money-market rates (S1), CP and CD aggregates (S2), calendars and results of auctions, underwriting, switches, OMOs and liquidity operations (S4), auctions by tenor and quarterly ownership (S5), SDL auction results by state and state finances (S6), policy rates, MPC dates and minutes, liquidity (S7) |
| 4 | NSE Data & Analytics | Enquiry form on its data vending page: <https://www.nseindia.com/nse-data-and-analytics/data-information-vending>. The probe notes also record `marketdata@nse.co.in` | S2 (CP, CD and bond trades reported at NSE), S8 and S10 (bond trades; valuation prices). Also the exchange trades S9 uses (`data_sources.md`) |
| 5 | BSE | The address the probe notes record for BSE's information products: `datafeed.sales@bseindia.com` | S2 (CP, CD and bond trades reported at BSE), S8 and S10 (bond trades; issue-level bidding data). Also the exchange trades S9 uses (`data_sources.md`) |
| 6 | ICRA Analytics | The address the probe notes record: `sales@icraanalytics.com` | S8 and S10 (valuation prices and matrices; the sources call these the prices mutual funds mark to). The "Data items" table also lists corporate valuation for S2 and S9. Question 6: mutual fund holdings for S9 (`data_sources.md`) |
| 7 | CRISIL | None recorded. The sources name only the domains `crisil.com` and `crisilratings.com`, which the November probe did not fetch: CRISIL's terms forbid bots, scrapers and AI tools, and its robots.txt bars AI agents. No CRISIL product name is recorded either, so the letter describes the data and asks for the names. Find the contact in a browser | As ICRA Analytics: the other agency's prices and matrices. Question 6: rating actions for S9 and S10 (`data_sources.md`) |
| 8 | Parameta | None recorded. The sources give the site `www.parametasolutions.com` and say the data is also sold through Bloomberg and LSEG | S1 (OIS beyond 5 years, from a broker), if its curve reaches that far: the sources give no tenors, and the OIS products they list are non-deliverable. The package also holds MIFOR and cross-currency swaps |
| 9 | FIMMDA | The addresses on its Website Access Policy page: `vp@fimmda.org` and `accounts@fimmda.org`. Its registration form (<https://www.fimmda.org/cb/register/register>) offers only bank, primary dealer, insurance company and financial institution as institution types, so write first | S8 and S10 (valuation prices and matrices). The "Data items" table also lists corporate valuation for S2 and S9. Question 6: the compiled auction files S4 uses |
| 10 | NSDL | None recorded. The sources give only the sites: <https://www.fpi.nsdl.co.in/> for the FPI Monitor and `www.indiabondinfo.nsdl.com` for India Bond Info | Not named in "What each strategy needs". The "Data items" table lists foreign flows and holdings by ISIN for S4 to S6. Question 5: India Bond Info, the security master and rating history S9 uses (`data_sources.md`) |
| 11 | AMC Repo Clearing | None recorded. The sources give only the site, <https://www.arclindia.com/>, which could not be reached during the probe (TLS certificate error, HTTP 503). They say a browser or a call is needed, and record no number | Not named in "What each strategy needs". The "Data items" table lists funding terms, corporate bond repo eligibility among them, for S1 to S3 and S8 |
| 12 | MoSPI | None recorded. The sources name the sites `www.mospi.gov.in` and `esankhyiki.mospi.gov.in`, and no address | S7 (CPI, IIP and GDP from the MoSPI API), kept in a stored database |

## 1. FBIL

**Subject:** FBIL benchmark subscription for internal research and back-testing: fee tier, history and terms

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a subscription quote. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The benchmarks are G-sec, SDL and STRIPS valuation with the par and zero-coupon yield curves, MIBOR-OIS, overnight MIBOR, the T-bill and CD curves, the Secured Overnight Rupee Rate (SORR) and the USD/INR reference rate.

Please tell us:

1. Which fee applies to keeping an internal database, back-testing and fitting an in-house curve: the fee for valuation only, or the fee for "other purposes".
2. The fee for a money-market benchmark when the user's annual turnover or outstanding contracts in it are below ₹500 crore.
3. The SORR fees for FY2026-27.
4. Whether a subscription includes history, and from what date for each benchmark.
5. Whether an in-house curve fitted to FBIL prices needs a Derived Product License under clause 5.1 of your Terms of Use, and its fee.
6. Whether MIBOR-OIS starts at 1 month or 6 months.
7. The fee for the USD/INR reference rate.
8. The file format by API and SFTP, whether stored data may be kept after the subscription ends, and the lead time.

Please send a quote and the applicable agreement.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 2. CCIL Data Services

**Subject:** Data request for commercial use: sovereign trade, repo, TREPS and OIS history

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote for a Data Agreement under your Data Sharing Policy. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

Data wanted, from [HISTORY START DATE] onwards: the security-wise outright trade summary (Trading Platform Analysis) for G-secs, SDLs and T-bills; G-Sec Historical Trades; Security Wise Repo Market Summary; Money Market Rates and Volumes (call, TREPS, basket repo); MTM Prices; and interbank MIBOR-OIS and Modified MIFOR trade prints, all tenors.

Please tell us:

1. The price of each dataset, as history and as a daily feed.
2. How far back each goes, the delivery format and fields, and whether odd-lot and when-issued trades are flagged.
3. Whether a Data Agreement allows storage in an internal database, back-testing and an in-house curve fitted to the prices, and keeping stored data after it ends.
4. Whether we may keep each Tri-Party Repo collateral notification (eligible securities and haircuts), and whether past issues exist.
5. Whether the State Government Spread Analysis file, Tenorwise Indicative Yields, participant-category trading data and F-TRAC CP and CD trades can be added.
6. The lead time to first delivery.

Please send a quote and the Data Agreement.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 3. RBI

**Subject:** Permission to store data published by RBI and to back-test on it; bulk access to DBIE

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for RBI's written permission to store data that RBI publishes. We would use it only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The data is: press releases (auction, underwriting, switch, open market and liquidity operation results; issuance calendars; Money Market Operations; monetary policy resolutions and minutes); the lists of outstanding Government of India securities, STRIPS, State Government Securities and Fully Accessible Route securities; the Handbook of Statistics, the Bulletin, the Weekly Statistical Supplement and State Finances: A Study of Budgets; and the Database on Indian Economy (DBIE).

Please tell us:

1. Whether we may store this data in an internal database and back-test on it.
2. What request rate is acceptable for a program that reads www.rbi.org.in and the press-release RSS feed.
3. Whether a program may download the XLS, XLSX and PDF files on rbidocs.rbi.org.in.
4. Whether DBIE, or another route, offers an API or bulk download of the history, how far back its auction and money-market series go, whether they carry ISINs, and in what format.
5. Whether a fee, agreement or acknowledgement applies.

Please send the permission, or the agreement that applies.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 4. NSE Data & Analytics

**Subject:** Corporate Bond Market Data and NSE Fixed Income Valuations: history, rights and price

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The products are Corporate Bond Market Data (₹3,40,000 a year per site in your price list from 1 April 2026) and NSE Fixed Income Valuations.

Please tell us:

1. Whether history of Corporate Bond Market Data is sold, from what date and at what price.
2. Whether the licence allows storage in an internal database, back-testing and derived data such as an in-house spread curve.
3. Whether end-of-day files read by a program (non-display use) carry the same fee, and which delivery channels exist.
4. The price of NSE Fixed Income Valuations, the date its history starts, and whether the same rights apply.
5. The file format, whether stored data may be kept after the licence ends, and the lead time.
6. Whether NSE will permit us in writing to store, and back-test on, the daily debt reports offered for download on its website.

Please send a quote and the licence agreement.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 5. BSE

**Subject:** BSE debt data products: current tariff, history and terms

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The products, as named in your price sheet for information products dated February 2025, are the debt segment feeds (ICDM, WDM, RDM – Corporate, RDM – G Bonds), EBP (EOD) and Memorandum of Privately placed bonds (EOD), and historical data for each.

Please tell us:

1. Whether the February 2025 prices still apply, and which segment carries reported corporate bond, CP and CD trades.
2. The date from which historical debt data is available, and its price.
3. Whether the licence allows storage in an internal database, back-testing and derived data such as an in-house spread curve.
4. The fields and format of the end-of-day and EBP (EOD) files, whether stored data may be kept after the licence ends, and the lead time.
5. Which terms of use govern data shown on www.bseindia.com, and whether a commercial user may store it.

Please send the current tariff, a quote, the licence agreement and the website terms of use.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 6. ICRA Analytics

**Subject:** Bond Valuation and Yield Matrices: licence for internal research and back-testing

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The products are Bond Valuation (daily security-level valuation), Yield Matrices and Fixed Income Data Services (historical valuations with bond terms).

Please tell us:

1. Whether you license security-level prices and matrices for back-testing to an entity of our type.
2. The date from which historical valuations and matrices are available.
3. The price of the history and of the daily service.
4. Whether the Subscription Terms allow storage in an internal database, back-testing and derived data such as an in-house spread curve.
5. The delivery format (data feed or API), whether stored data may be kept after the agreement ends, and the lead time.
6. Whether MFI360 Explorer holds a history of mutual fund scheme portfolios by ISIN, from what date and on what terms.

Please send a quote and the Subscription Terms.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 7. CRISIL

**Subject:** Security-level valuation prices and matrices: licence for internal research and back-testing

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The data is daily security-level valuation prices for debt and money-market securities, of the kind mutual funds use to value their holdings, and yield or spread matrices by rating and tenor.

Please tell us:

1. Whether you supply these, under what product names, and whether you license them for back-testing to an entity of our type.
2. The date from which history is available.
3. The price of the history and of the daily service.
4. Whether the licence allows storage in an internal database, back-testing and derived data such as an in-house spread curve.
5. The delivery format, whether stored data may be kept after the licence ends, and the lead time.
6. Whether a history of CRISIL's rating actions by instrument, with dates, can be licensed for the same use.

Please send a quote and the licence agreement.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 8. Parameta

**Subject:** INR MIBOR OIS and MIFOR swap data for internal models: coverage, history and price

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The data is the INR instruments in the ICAP Interest Rate Swaps (Global) package, including INR MIBOR ND OIS, INR MIBOR OIS Swap Spread, INR MIFOR 6M IRS, INR vs SOFR ND CCS and INR vs 6M USD ND CCS.

Please tell us:

1. Whether the onshore MIBOR OIS curve to 10 years is included, or only the non-deliverable curve, and the tenor points of each instrument.
2. Whether "INR MIFOR 6M IRS" refers to Modified MIFOR.
3. The date from which INR history is available.
4. The price of a licence for model use (data stored in an internal database, back-tested and used to build in-house curves), for end-of-day and for intraday data.
5. The delivery format, whether stored data may be kept after the licence ends, and the lead time.
6. Whether your Tullett Prebon data adds INR instruments or tenors.

Please send a quote and the licence agreement.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 9. FIMMDA

**Subject:** Corporate bond matrices and valuation for a website user: current fees, history and storage rights

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for a quote as a paying website user. We would use the data only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The data is what your fee notice of 21 February 2022 covers (daily spread, yield and AT-1 fixed spread matrices, fortnightly spread and zero-coupon spread matrices, and last 15 days traded data), the Security Level Valuation, and Historical Data of Corporate Bond Market.

Please tell us:

1. Whether the 2022 fees are current, and which category and fee apply to us.
2. Whether the daily Security Level Valuation is included and, if not, its price.
3. The price and start date of historical matrices, security-level valuation and traded data.
4. Whether the Website User Agreement allows storage in an internal database, back-testing and derived data such as an in-house spread curve.
5. The file format, whether stored data may be kept after the agreement ends, and the lead time.
6. Whether a commercial user may store the free auction data files, and whether G-sec data for FY2024-25 and SDL data after January 2025 will be posted.

Please send a quote and the Website User Agreement.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 10. NSDL

**Subject:** Permission to store FPI Monitor data for internal research and back-testing; request for a bulk file

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for NSDL's written permission to store data from the FPI Monitor (www.fpi.nsdl.co.in). We would use it only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

The data is: daily FPI net investment by route (Debt-General Limit, Debt-VRR and Debt-FAR); the Debt Utilisation Status report, with FAR holdings by ISIN; and the trade-wise FPI debt files.

Please tell us:

1. Whether we may store this data in an internal database and back-test on it, given that the site's terms prohibit reproduction, redistribution and transmission.
2. Whether you can supply the daily and Debt Utilisation Status archives as bulk files, in what format, and from what date each starts.
3. Whether the trade-wise FPI debt files will be extended beyond March 2025.
4. Whether a fee or an agreement applies, and whether stored data may be kept if the permission ends.
5. Whether the same permission can cover NSDL India Bond Info (securities list, terms and rating history by ISIN), whose copyright notice allows reproduction only with formal permission, for non-commercial research.

Please send the permission, or the agreement that applies.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 11. AMC Repo Clearing

**Subject:** Tri-party repo in corporate bonds: eligible collateral, haircuts, membership and published data

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks for the terms of your tri-party repo in corporate bonds. We would use the information, and any data you publish, only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

Please tell us:

1. Which issuers and securities are eligible as collateral, by rating and type of security, and where the current list is published.
2. The haircuts and any concentration limits, and whether earlier versions with their effective dates are available.
3. Who may become a member, whether an entity of our type may join directly or must deal through a member, and the fees and conditions.
4. What rate and volume data you publish, from what date and in what format, and whether a commercial user may store it and keep it.

Please send the fee schedule, the applicable membership agreement, and the current collateral and haircut lists.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]

## 12. MoSPI

**Subject:** Licence terms for eSankhyiki API data stored in an internal database for commercial use

Dear Sir or Madam,

[FIRM NAME] ([ENTITY TYPE AND REGISTRATION]) asks which terms apply to data from the eSankhyiki API (api.mospi.gov.in): CPI, IIP, national accounts and WPI. We would use it only for internal research, back-testing and trading models on our own capital. We would not redistribute it or display it to third parties.

Please tell us:

1. Which licence governs this data when it is stored in an internal database for commercial use: the Government Open Data License – India, the website's copyright policy, or another.
2. If permission is needed, whether this letter can serve as the request, and what acknowledgement you require.
3. Whether the same terms cover the series eSankhyiki serves on behalf of others: the WPI compiled by the Office of the Economic Adviser (DPIIT), and the tables republished from the Reserve Bank of India.
4. Whether a rate limit applies to the API. We would retrieve data with a scheduled program at no more than 60 requests a day, at least ten seconds apart, mostly conditional requests that the API answers with "not modified"; is that acceptable, and is the REST API the intended route for such use?
5. Whether the history is available as a bulk file, and how far back each series goes, in particular the CPI base-2024 back series and WPI. The API serves only the latest value of each series and replaces a provisional figure when it is revised: are first prints and later revisions (vintages) kept anywhere we could obtain?
6. At what time of day CPI, IIP and GDP are released, and how soon the API carries them.

Please send the licence text, or your written permission.

Yours faithfully,

[CONTACT NAME], [FIRM NAME], [EMAIL], [PHONE]
