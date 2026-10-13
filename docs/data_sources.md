# Data sources

What each input to the pipeline can be built from, as found by probing the
official sites on 13 Oct 2026. Endpoints, history and formats below were
observed with real requests. Terms are quoted from each site. Re-check terms
before any bulk download: several sites changed theirs recently (FBIL posted
new terms on 13 Oct 2026).

**Bottom line for a commercial fund.** Almost every free source can be read
by a script, but almost none allows a commercial user to build a database
from it without permission or a licence:

- **No restriction found:** SEBI documents and OpenFIGI.
- **Allowed with permission or for non-commercial research:** NSDL India Bond
  Info, CARE, Acuité and Brickwork.
- **Explicitly prohibited** (scraping, building databases or back-testing):
  AMFI, ICRA, India Ratings, FBIL, CCIL and CRISIL. CRISIL's robots.txt also
  blocks AI agents by name.

Ask for written permission or buy the licence before bulk collection.

## Summary

| Input | Best free source | History | Terms | Route |
|---|---|---|---|---|
| Rating actions | CRA "movement of each credit rating" and "history of all outstanding securities" files (SEBI-mandated, half-yearly) | Varies by agency: 2009-10 (ICRA PDFs), 2016 (India Ratings archive), 2019 (CARE PDFs), 2022-23 onward (xlsx at most agencies) | ICRA and India Ratings forbid database building; CARE, Brickwork and Acuité need consent; Infomerics silent; CRISIL forbids bots and AI | Permission per agency, or licence (PRIME Acuité, CRISIL/S&P) |
| Per-ISIN rating history | NSDL India Bond Info, credit-ratings per ISIN | IL&FS rows from 2014 | Reproduction "only with formal permission for the purposes of non-commercial research" | Ask NSDL |
| MF holdings | AMC portfolio files (each AMC site; AMFI only links) | Monthly with ISIN from about 2012; fortnightly from Oct 2020 | Per AMC; HDFC's CDN blocked this sandbox | Vendor backfill (ACE MF, ICRA Analytics) plus forward collection |
| MF trades (first stage and prices) | AMFI "Transaction in Debt and Money Market Securities" | 21 Mar 2012 to about 15 days ago; buy/sell direction only from about 22 Sep 2020 | AMFI: personal, non-commercial; "shall not store electronically any significant portion" | Ask AMFI; until then use event windows only |
| Scheme master and categories | AMFI NAVAll.txt, scheme data CSV, NAV history | NAV from Apr 2006; categories are current only | AMFI terms as above | Archive NAVAll daily from now for as-of categories |
| Security master | NSDL India Bond Info bulk list and per-ISIN detail | Live and some matured ISINs; outstanding history only from Dec 2025 | As above | Ask NSDL, or vendor |
| Exchange trades | NSE/BSE trade repositories | Not probed (domains not allowed) | NSE terms forbid automated collection | NSE Data "Corporate Bond Market Data" licence, BSE data |
| G-sec curve | FBIL par yield and ZCYC (daily workbooks) | Par yields from 31 Mar 2018 | Free data is 7 days late, viewing only; archive or back-test needs a subscription (₹50k to ₹2.5 lakh a year; ₹10k per benchmark for recognised research institutions) | FBIL subscription |
| OIS curve | FBIL MIBOR-OIS | From 3 Apr 2018, 1M to 5Y only | As FBIL | FBIL subscription; vendor beyond 5Y |
| Rules | SEBI circulars, master circulars, regulations | All needed rules, 2012 onward | Reproduction with permission and attribution; no automated-access clause | Use freely for research |

## Sources in detail

### SEBI (www.sebi.gov.in)

Primary text for the mutual fund holder rules in `config/mandates.toml`:

- Scheme categorisation: Oct 6 and Dec 4, 2017; recategorisation Feb 26, 2026.
- Issuer limits by rating: Nov 29, 2022.
- Segregated portfolios: Dec 28, 2018 and later amendments.
- Potential Risk Class matrix: Jun 7, 2021.
- Rebalancing after passive breaches: Mar 30, 2022 and Jun 26, 2025.
- Disclosure rules: Feb 28, 2012; Jul 22, 2020; Sep 1, 2020.
- Master Circular for Mutual Funds: Mar 20, 2026, in force Apr 1, 2026.
- SEBI (Mutual Funds) Regulations, 2026, in force Apr 1, 2026. These replace the 1996 Regulations.
- Master Circular for CRAs: Jul 11, 2025.

Detail pages embed a single PDF. The circulars listing is an HTML page with a
search form. Website policy: material "may be reproduced free of charge after
taking proper permission"; there is no automated-access clause.

### AMFI (www.amfiindia.com, portal.amfiindia.com)

- **NAVAll.txt** (portal.amfiindia.com/spages/NAVAll.txt): the current
  snapshot. 8 columns since 2026, with plan and option split out. It carries
  both category families, "Debt Scheme - X" and the 2026 "Income/Debt
  Oriented Schemes - X". Categories are current only, so archive the file
  daily to get as-of categories.
- **Scheme data CSV** (DownloadSchemeData_Po.aspx?mf=0): all 16k plan codes,
  including matured close-ended plans. Categories are current.
- **NAV history** (DownloadNAVHistoryReport_Po.aspx): from 3 Apr 2006, at
  most 90 days per request in the UI. Category headers and names are current,
  not as-of.
- **Transaction in Debt and Money Market Securities**
  (www.amfiindia.com/otherdata/transaction-in-debt): every MF debt trade,
  settlement-date-wise, from 21 Mar 2012. It runs about 15 days behind. The
  page offers a full-day Excel export.
  - Before about 22 Sep 2020 the rows lack direction, price, traded yield,
    fund house and rating.
  - From then on all 23 columns are filled: buy/sell, price, traded yield and
    valued yield.
- **Monthly industry report** (spages/am<mon><yyyy>repo.xls): category-level
  flows and AUM with as-of labels. xls from Apr 2018; PDF from 2009.
- **Scheme-wise quarterly average AUM**: from FY2006-07; categories are
  restated to current.
- **Terms of use:** "for your personal and non-commercial use only"; "You
  shall not store electronically any significant portion of any part of the
  Site". robots.txt does not disallow the data paths.

### Credit rating agencies

Every agency publishes the SEBI-mandated half-yearly files:

- **Movement of each credit rating:** upgrades and downgrades with
  press-release date, notch difference and trigger.
- **History of credit rating of all outstanding securities:** initial rating
  plus up to 19 dated revisions per instrument.

The history files cover only instruments outstanding at the snapshot date, so
stack every snapshot to limit survivorship bias. None of the files carries
ISIN or CIN; ISINs appear in press-release annexures. Since 31 Mar 2023,
SEBI requires agencies to keep a 10-year machine-readable archive of website
disclosures.

| Agency | Bulk files | Per-action documents | Terms |
|---|---|---|---|
| ICRA | Movement and history xlsx from H2 FY2022-23; PDF back to H2 FY2009-10 | Rationale PDFs open without login; search from 2017 | Forbids scraping and "systematically extracting data ... to populate databases" |
| India Ratings | Current xlsx, plus an archive of CSV and xlsx for FY2016-FY2026 | JSON press releases from 2002, full text from about 2013 | Forbids bots, scrapers and storage without written consent; "no commercial exploitation" |
| CARE | History xlsx (Mar 2023 to Mar 2026; about 27k instruments, initial dates back to 2008); movement xlsx from Oct 2022, PDF from Oct 2019 | PR PDFs back to 2010 | Reproduction, derivative works and "use or reference" need written consent |
| Acuité | Annexure 22 and 24 xlsx, Mar 2023 to Mar 2026 | HTML PRs from mid-2022 | Distribution or publication needs written consent |
| Infomerics | Annexures 20 to 25 xlsx, Mar 2025 to Mar 2026 | Per-company action history from about 2019 | No reuse restriction found |
| Brickwork | Annexures 20 to 24 xlsx from Oct 2022, PDF from 2018 (revisions include reaffirmations) | PR PDFs and HTML | Reproduction needs written consent |
| CRISIL | No per-instrument history file found | Latest rationale per company only | Forbids bots, scrapers and AI tools; robots.txt blocks Claude agents. **Do not automate.** |

The liquidity descriptor (Superior/Strong, Adequate, Stretched, Poor) is
mandated in every press release; see CRA Master Circular para 10.3.

### NSDL India Bond Info (www.indiabondinfo.nsdl.com)

- **Bulk list of securities** (Active: about 29.5k ISINs; Matured: ISINs past
  maturity still in the depository).
- **Per-ISIN detail:**
  - CIN and LEI. CIN is null for statutory issuers such as SBI and NABARD.
  - Instrument terms, including secured/unsecured, seniority and perpetual.
  - Redemption and call/put text, and the coupon schedule.
  - Per-ISIN rating history with agency, outlook and watch.
  - Default and restructuring details.
  - Quarterly holdings by investor category, from Dec 2025 only.
- **Gaps:**
  - No point-in-time outstanding history before Dec 2025.
  - Perpetuals carry a maturity of 31-12-9999.
  - IN8-prefixed ISINs duplicate INE bonds.
- **Copyright:** reproduction "only with formal permission for the purposes of
  non-commercial research".

### FBIL (www.fbil.org.in)

- **G-sec par yields** (0.25 to 50y, semi-annual and annualised) and **ZCYC**,
  daily from 31 Mar 2018.
- **SDL ZCYC** from Jul 2023.
- **T-bill curve** from Aug 2017.
- **MIBOR-OIS** 1M to 5Y from Apr 2018. Rates up to 1Y are money-market
  simple rates; 2 to 5Y are semi-annual.
- **Overnight MIBOR** from Jul 2015.

The public website shows data 7 days late. Terms of Use (13 Oct 2026),
clause 9.2:

> Users are strictly prohibited from compiling, downloading, storing, or
> leveraging the delayed website Benchmark Data to construct an internal
> database, historical archive, or systematic tracking feed without acquiring
> a specific paid subscription.

Clause 2.3 counts back-testing as commercial use (in the context of vendor
terminal data). Subscriptions:

- ₹50,000 a year for valuation, ₹2,50,000 for other uses (G-sec/SDL
  valuation).
- ₹1,00,000 or ₹2,50,000 per money-market benchmark.
- ₹10,000 per benchmark for recognised research and educational institutions.

### CCIL (www.ccilindia.com) and RBI

- **CCIL:** free views show the latest two days only (NSS zero curve,
  interbank OIS trades, NDS-OM). Archives need membership or CCIL Data
  Services. Terms forbid "any systematic or automated data collection". Not
  used.
- **RBI DBIE:** the anonymous scripted session was rejected; the API needs
  registration.
- **RBI Handbook of Statistics:** month-end SGL yield tables (xlsx) as a
  coarse fallback.

### Not yet probed

These need their domains allowed:

- NSE and BSE trade repositories.
- IRDAI, PFRDA and EPFO rules for the insurer, NPS and EPFO mandates.
- Insurer and NPS pension-fund holdings.
- AMC websites for portfolio files.
