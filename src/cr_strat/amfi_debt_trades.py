"""AMFI's daily disclosure of every MF transaction in debt and money-market securities.

SEBI requires mutual funds to disclose each debt and money-market trade, and
AMFI publishes them by settlement date at amfiindia.com ("Transaction in Debt
and Money Market Securities"), from 21-Mar-2012, about 15 days behind. The
whole-day Excel export has one sheet, ``AMFI_Reports``, with a 23-column header
in row 1; the JSON API returns the same fields under snake_case keys. Both load
here into one row per transaction: which scheme bought or sold which ISIN, on
which day, at what price and yield.

Quirks the loader handles:

* Two schemas. Until about 18-Sep-2020 Transaction Type, Fund House, Type of
  Scheme, Type of Security, rating, face value, price, accrued interest and
  traded yield are blank or 0, and ``Yield at which Valued`` mixes fractions
  (0.0755) and percent (7.85). From 22-Sep-2020 every field is filled.
  ``direction_available`` is False for rows without a buy/sell flag. A price
  or yield of 0 means not reported, and a yield below 1 is read as a fraction.
* Free text everywhere: about 43 spellings of Type of Trade, 30 of Type of
  Security, ratings such as ``AAA``, ``CARE AAA``, ``SOV``, ``N.A.``, ``0``.
  They are mapped to ``DEAL_TYPES`` and ``SECURITY_TYPES``; ``mapping_report``
  lists every spelling and flags the ones no rule recognised. For old rows the
  security type is inferred from the ISIN and the security name.
* TREPS and repo rows have no ISIN, a pseudo code (``TREP28092026``,
  ``XXX000315820``, ``INTREP070619``, which passes the check digit) or the
  collateral's ISIN. ``isin`` is left blank for them (the text stays in
  ``isin_raw``), and they are dropped unless ``keep_no_isin`` is set.
* Quantity is in units whose face value varies by row (100, 1, 1 lakh, 5 lakh,
  10 lakh), so face traded is quantity x face value per unit. Old rows carry no
  face value and their quantity units are inconsistent (rupees of face in 2012,
  hundreds of rupees or bonds in 2019), so only ``value_inr`` compares there.
* Value of the Trade is in rupees and includes accrued interest (quantity x
  face x price / 100 + accrued interest on every ISIN row of a 2026 file).
  Dates look like ``28 Sept 2026`` in the export and ISO timestamps in JSON.
* Fund House strings are variants of the AMFI scheme master's AMC names
  (``EDEL MUTUAL FUND``, ``PGIM MUTUAL FUND``). ``attach_scheme_categories``
  maps them, and the blank fund house of old rows, onto the master's names
  before attaching SEBI categories, so ``holder_id`` joins to ``parse-mf``
  holdings parsed with the AMFI spelling of the AMC.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.isin import is_valid_isin
from cr_strat.rating_scale import normalize_agency, parse_rating
from cr_strat.schemes import attach_categories, normalize_amc, normalize_scheme_name, parse_amfi_navall
from cr_strat.textparse import cell_str, parse_date_column, read_rows, to_number
from cr_strat.trades import TRADE_COLUMNS

SCHEMA_CHANGE_DATE = pd.Timestamp("2020-09-22")
DEAL_TYPES = ("INTER_SCHEME", "MARKET", "OFF_MARKET", "PRIMARY", "BUYBACK", "REPO", "TREPS", "OTHER")
SECURITY_TYPES = (
    "CP", "CD", "CORPORATE_BOND", "DEBENTURE", "SECURITISED", "ZCB", "AT1",
    "GSEC", "TBILL", "CMB", "SDL", "REPO", "TREPS", "OTHER",
)
# Rows whose ISIN column is not the security bought or sold.
FUNDING_TYPES = ("REPO", "TREPS")
PRICE_VENUE = "AMFI_MF"

MF_TRADE_COLUMNS = (
    "trade_date", "settlement_date", "isin", "security_name", "security_type", "deal_type",
    "direction", "direction_available", "holder_type", "holder_id", "holder_name", "amc", "category",
    "scheme_type", "quantity", "face_value_per_unit", "face_traded_inr", "price", "value_inr",
    "accrued_interest_inr", "yield_traded_pct", "yield_valued_pct", "rating_grade", "rating_notch",
    "rating_scale", "rating_agency", "sovereign", "maturity_date", "deemed_maturity_date",
    "residual_days", "listing_status", "isin_raw", "security_type_raw", "security_type_source",
    "deal_type_raw", "transaction_type_raw", "rating_raw", "rating_agency_raw", "fund_house_raw",
    "source_file", "source_row",
)

# Excel headers and JSON keys, lower-cased with everything but letters and digits removed.
_HEADER_FIELDS = {
    "nameofthesecurity": "security_name", "nameofsecurity": "security_name",
    "isin": "isin_raw",
    "typeofsecurity": "security_type_raw",
    "securityrating": "rating_raw",
    "ratingagency": "rating_agency_raw",
    "transactiontype": "transaction_type_raw",
    "statusofsecurity": "listing_status_raw",
    "fundhouse": "fund_house_raw", "mfname": "fund_house_raw",
    "schemename": "holder_name",
    "typeofscheme": "scheme_type_raw",
    "maturitydate": "maturity_date",
    "residualdays": "residual_days",
    "deemedmaturitydate": "deemed_maturity_date",
    "tradedate": "trade_date",
    "settlementdate": "settlement_date",
    "quantitytraded": "quantity",
    "facevalueperunit": "face_value_per_unit",
    "priceatwhichtraded": "price",
    "totalinterestaccrued": "accrued_interest_inr",
    "valueofthetrade": "value_inr", "valueoftrade": "value_inr",
    "yieldatwhichtraded": "yield_traded_pct",
    "yieldatwhichvalued": "yield_valued_pct",
    "typeoftrade": "deal_type_raw",
}
_REQUIRED = ("isin_raw", "holder_name", "value_inr", "deal_type_raw")
_HEADER_SCAN_ROWS = 10

# First matching rule wins; text is lower-cased with punctuation turned into spaces.
_DEAL_RULES = tuple((label, re.compile(p)) for label, p in (
    ("INTER_SCHEME", r"\binter ?scheme\b|\bist\b"),
    ("TREPS", r"\btreps?\b|\bcblo\b|\btri ?party\b"),
    ("REPO", r"\brepo\b|\bcroms\b"),
    ("BUYBACK", r"\bbuy ?back\b|\b(?:put|call) option\b|\bearly redemption\b"),
    ("PRIMARY", r"\bprimary\b|\bprivate placement\b|\bauction\b"),
    ("OFF_MARKET", r"\boff ?market\b"),
    ("MARKET", r"\bmarket\b|\bsecondary\b|\botc\b|\brfq\b|\bnds\b"),
    ("OTHER", r"\bothers?\b|\bmatur|\bswaps?\b|\birs\b|\bt ?bill\b"),
))
_SECURITY_RULES = tuple((label, re.compile(p)) for label, p in (
    ("TREPS", r"\btreps?\b|\bcblo\b|\btri ?party\b"),
    ("REPO", r"\brepo\b"),
    ("CP", r"\bcommercial papers?\b|\bcps?\b"),
    ("CD", r"\bcert\w* of deposits?\b|\bcds?\b"),
    ("CMB", r"\bcash management\b|\bcmbs?\b"),
    ("TBILL", r"\bt\w*ry bills?\b|\bt ?bills?\b|\btb\b"),
    ("SDL", r"\bstate development\b|\bsdls?\b|\bstate gov|\bsgs\b"),
    ("SECURITISED", r"\bsecuriti[sz]|\bptcs?\b|\bpass through\b"),
    ("AT1", r"\bat ?1\b|\badditional tier\b|\bperpetual\b"),
    ("ZCB", r"\bzero coupon\b|\bzcbs?\b|\bdeep discount\b"),
    ("GSEC", r"\bgov|\bg ?secs?\b|\bsovereign\b|\bgoi\b|\bgilts?\b"),
    ("DEBENTURE", r"\bdebentures?\b|\bncds?\b"),
    ("CORPORATE_BOND", r"\bbonds?\b|\bcorporate\b|\bpsu\b|\btier ?(?:ii|2)\b"),
    ("OTHER", r"\bothers?\b|\bswaps?\b|\birs\b|\bderivatives?\b|\bfd\b|\bfixed deposits?\b"),
))
# Types a corporate ISIN (INE...) can carry when inferred from the security name.
_CORPORATE_TYPES = ("CP", "CD", "SECURITISED", "AT1", "ZCB", "DEBENTURE", "CORPORATE_BOND")
# Security-type code in characters 8-9 of an Indian corporate ISIN.
_ISIN_TYPE_CODES = {"07": "DEBENTURE", "08": "CORPORATE_BOND", "14": "CP", "16": "CD"}
_PLACEHOLDER_ISIN_RE = re.compile(r"^IN[A-Z0-9]0{9}")
_FUNDING_CODE_RE = re.compile(r"^(?:INTR|TREP|CBLO)")
_SOVEREIGN_TEXT_RE = re.compile(r"^SOV|SOVEREIGN|^GOI$|^GOV|GOVERNMENT")
_RATING_FIELDS = ("rating_grade", "rating_notch", "rating_scale", "rating_agency", "sovereign")
_NA_TEXT_RE = re.compile(r"^(?:0|-+|N\.?A\.?|NA|NOT APPLICABLE|NOT AVAILABLE|NIL|NONE)$")


def _norm_text(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", cell_str(value).lower()).split())


def _first_rule(rules: tuple, value: object, allowed: tuple[str, ...] | None = None) -> str | None:
    text = _norm_text(value)
    if not text:
        return None
    for label, pattern in rules:
        if (allowed is None or label in allowed) and pattern.search(text):
            return label
    return None


def classify_deal_type(value: object) -> str | None:
    """Map a Type of Trade spelling onto ``DEAL_TYPES``; blank is None, anything unknown OTHER."""
    if not _norm_text(value):
        return None
    return _first_rule(_DEAL_RULES, value) or "OTHER"


def classify_security_type(value: object) -> str | None:
    """Map a Type of Security spelling onto ``SECURITY_TYPES``; blank is None, anything unknown OTHER."""
    if not _norm_text(value):
        return None
    return _first_rule(_SECURITY_RULES, value) or "OTHER"


def infer_security_type(isin: object, name: object) -> str | None:
    """Security type of a row whose Type of Security is blank, from its ISIN and name."""
    code = cell_str(isin).upper()
    funding = _first_rule(_SECURITY_RULES, name, FUNDING_TYPES)
    if funding or _FUNDING_CODE_RE.match(code):
        return funding or "TREPS"
    if is_valid_isin(code):
        if code.startswith("IN00"):
            by_name = _first_rule(_SECURITY_RULES, name, ("TBILL", "CMB"))
            return by_name or ("TBILL" if code[8] in "XYZ" else "GSEC")
        if re.match(r"IN[1-9]", code):
            return "SDL"
        if code[7:9] in ("14", "16"):
            return _ISIN_TYPE_CODES[code[7:9]]
        return (_first_rule(_SECURITY_RULES, name, _CORPORATE_TYPES)
                or _ISIN_TYPE_CODES.get(code[7:9]))
    return _first_rule(_SECURITY_RULES, name) or _first_rule(_SECURITY_RULES, code)


def _direction(value: object) -> str | None:
    text = cell_str(value).upper()
    if re.match(r"BUY|PURCHASE|B$", text):
        return "BUY"
    if re.match(r"SELL|SALE|S$", text):
        return "SELL"
    return None


def _scheme_type(value: object) -> str | None:
    text = _norm_text(value)
    for label in ("open", "close", "interval"):
        if text.startswith(label):
            return label.upper()
    return None


def _listing_status(value: object) -> str | None:
    text = cell_str(value).upper()
    if not text or _NA_TEXT_RE.match(text):
        return None
    return "_".join(text.split())


def _na_text(value: object) -> str:
    text = cell_str(value)
    return "" if _NA_TEXT_RE.match(text.upper()) else text


def _rating_fields(rating: object, agency: object) -> dict:
    parsed = parse_rating(_na_text(rating))
    agency_text = _na_text(agency)
    return {
        "rating_grade": parsed.grade,
        "rating_notch": parsed.notch,
        "rating_scale": parsed.scale,
        "rating_agency": parsed.agency or normalize_agency(agency_text),
        "sovereign": bool(parsed.sovereign or _SOVEREIGN_TEXT_RE.search(cell_str(rating).upper())
                          or _SOVEREIGN_TEXT_RE.search(agency_text.upper())),
    }


def _date_text(value: object) -> str:
    text = cell_str(value)
    if re.match(r"\d{4}-\d{2}-\d{2}T", text):
        return text[:10]
    return re.sub(r"\bSept\b", "Sep", text, flags=re.I)


def _dates(values: pd.Series) -> pd.Series:
    return parse_date_column(values.map(_date_text))


def _numbers(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values.map(to_number), errors="coerce")


def _percent(values: pd.Series) -> pd.Series:
    """Yields in percent: 0 means not reported, and a fraction below 1 is scaled by 100."""
    pct = _numbers(values)
    pct = pct.where(pct != 0)
    return pct.where(~(pct.abs() < 1), pct * 100)


def holder_ids(amc: pd.Series, scheme: pd.Series) -> pd.Series:
    """``MF|<amc key>|<scheme key>``, built as ``parse_mf_portfolio`` builds it."""
    return pd.Series(
        [f"MF|{normalize_amc(a if isinstance(a, str) else '')}|{normalize_scheme_name(cell_str(s))}"
         for a, s in zip(amc, scheme)],
        index=amc.index, dtype=object,
    )


def _compact(header: object) -> str:
    return re.sub(r"[^a-z0-9]", "", cell_str(header).lower())


def _map_columns(headers: list) -> dict[int, str]:
    mapped = {}
    for j, header in enumerate(headers):
        field = _HEADER_FIELDS.get(_compact(header))
        if field and field not in mapped.values():
            mapped[j] = field
    return mapped


def _raw_frame(path: Path) -> pd.DataFrame:
    """Source rows under canonical raw field names, with their 1-based row number in the file."""
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("data", []) if isinstance(payload, dict) else payload
        frame = pd.DataFrame(records)
        colmap = _map_columns(list(frame.columns))
        raw = pd.DataFrame({field: frame.iloc[:, j] for j, field in colmap.items()}, index=frame.index)
        raw["source_row"] = np.arange(1, len(raw) + 1)
        return raw
    sheets = read_rows(path)
    order = sorted(sheets, key=lambda name: name != "AMFI_Reports")
    for name in order:
        rows = sheets[name]
        for i, row in enumerate(rows[:_HEADER_SCAN_ROWS]):
            colmap = _map_columns(list(row))
            if set(_REQUIRED) <= set(colmap.values()):
                body = pd.DataFrame([list(r) for r in rows[i + 1:]]).reindex(columns=range(len(row)))
                raw = pd.DataFrame({field: body[j] for j, field in colmap.items()}, index=body.index)
                raw["source_row"] = np.arange(i + 2, i + 2 + len(raw))
                return raw.dropna(how="all", subset=list(colmap.values()))
    raise ValueError(f"{path.name}: no AMFI debt-transaction header (ISIN, Scheme Name, Value, Type of Trade)")


def _security_types(raw: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    reported = raw["security_type_raw"].map(classify_security_type)
    inferred = pd.Series(
        [infer_security_type(i, n) for i, n in zip(raw["isin_raw"], raw["security_name"])],
        index=raw.index, dtype=object,
    )
    # Reported "Government Bonds" also covers state loans; the ISIN tells them apart.
    state = raw["isin_raw"].map(lambda v: bool(re.match(r"IN[1-9]", cell_str(v).upper())))
    reported = reported.where(~((reported == "GSEC") & state), "SDL")
    source = pd.Series(np.where(reported.notna(), "reported", np.where(inferred.notna(), "inferred", None)),
                       index=raw.index, dtype=object)
    return reported.fillna(inferred), source


def _clean_isin(raw_isin: pd.Series, security_type: pd.Series, deal_type: pd.Series) -> pd.Series:
    code = raw_isin.map(lambda v: cell_str(v).upper())
    ok = code.map(lambda c: is_valid_isin(c) and not _PLACEHOLDER_ISIN_RE.match(c)).astype(bool)
    ok &= ~security_type.isin(FUNDING_TYPES) & ~deal_type.isin(FUNDING_TYPES)
    return code.where(ok, None)


def load_amfi_debt_trades(path: str | Path, *, keep_no_isin: bool = False) -> pd.DataFrame:
    """One row per transaction from an AMFI debt-transaction file (``.xlsx`` export or ``.json`` API page).

    Rows without a usable ISIN (TREPS, repo, swaps) are dropped unless
    ``keep_no_isin`` is set. ``category`` stays blank; fill it with
    ``attach_scheme_categories``.
    """
    path = Path(path)
    raw = _raw_frame(path).reindex(columns=list(dict.fromkeys(_HEADER_FIELDS.values())) + ["source_row"])
    df = pd.DataFrame(index=raw.index)
    df["trade_date"] = _dates(raw["trade_date"])
    df["settlement_date"] = _dates(raw["settlement_date"])
    df["deal_type"] = raw["deal_type_raw"].map(classify_deal_type)
    df["security_type"], df["security_type_source"] = _security_types(raw)
    df["isin"] = _clean_isin(raw["isin_raw"], df["security_type"], df["deal_type"])
    df["security_name"] = raw["security_name"].map(cell_str)
    df["direction"] = raw["transaction_type_raw"].map(_direction)
    df["direction_available"] = df["direction"].notna()
    df["holder_type"] = "MF"
    df["holder_name"] = raw["holder_name"].map(cell_str)
    df["fund_house_raw"] = raw["fund_house_raw"].map(cell_str)
    df["amc"] = df["fund_house_raw"].where(df["fund_house_raw"] != "", None)
    df["holder_id"] = holder_ids(df["amc"], df["holder_name"])
    df["category"] = None
    df["scheme_type"] = raw["scheme_type_raw"].map(_scheme_type)
    df["quantity"] = _numbers(raw["quantity"])
    df["face_value_per_unit"] = _numbers(raw["face_value_per_unit"])
    df["face_traded_inr"] = (df["quantity"] * df["face_value_per_unit"]).where(df["face_value_per_unit"] > 0)
    df["price"] = _numbers(raw["price"]).where(lambda p: p != 0)
    df["value_inr"] = _numbers(raw["value_inr"])
    df["accrued_interest_inr"] = _numbers(raw["accrued_interest_inr"])
    df["yield_traded_pct"] = _percent(raw["yield_traded_pct"])
    df["yield_valued_pct"] = _percent(raw["yield_valued_pct"])

    pairs = list(zip(raw["rating_raw"].map(cell_str), raw["rating_agency_raw"].map(cell_str)))
    cache = {pair: _rating_fields(*pair) for pair in set(pairs)}
    ratings = pd.DataFrame([cache[pair] for pair in pairs], index=raw.index, columns=list(_RATING_FIELDS))
    for col in ratings.columns:
        df[col] = ratings[col]
    df["rating_notch"] = df["rating_notch"].astype("Int64")
    df["sovereign"] = df["sovereign"].astype(bool)

    df["maturity_date"] = _dates(raw["maturity_date"])
    df["deemed_maturity_date"] = _dates(raw["deemed_maturity_date"])
    df["residual_days"] = _numbers(raw["residual_days"])
    df["listing_status"] = raw["listing_status_raw"].map(_listing_status)
    for col in ("isin_raw", "security_type_raw", "deal_type_raw", "transaction_type_raw",
                "rating_raw", "rating_agency_raw"):
        df[col] = raw[col].map(cell_str)
    df["source_file"] = path.name
    df["source_row"] = raw["source_row"]
    if not keep_no_isin:
        df = df[df["isin"].notna()]
    return df.reindex(columns=list(MF_TRADE_COLUMNS)).reset_index(drop=True)


def load_amfi_debt_trade_files(paths: Iterable[str | Path], *, keep_no_isin: bool = False) -> pd.DataFrame:
    frames = [load_amfi_debt_trades(p, keep_no_isin=keep_no_isin) for p in paths]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=list(MF_TRADE_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def _match_amc_key(key: str, keys: list[str], cutoff: float = 0.8) -> tuple[str | None, str]:
    if not key:
        return None, "none"
    if key in keys:
        return key, "exact"
    compact = {k.replace(" ", ""): k for k in keys}
    if key.replace(" ", "") in compact:
        return compact[key.replace(" ", "")], "variant"
    prefixed = [k for k in keys if k.startswith(key) or key.startswith(k + " ")]
    if len(prefixed) == 1 and len(key) >= 3:
        return prefixed[0], "variant"
    close = difflib.get_close_matches(key, keys, n=1, cutoff=cutoff)
    return (close[0], "fuzzy") if close else (None, "none")


def _amc_from_scheme(scheme: str, keys: list[str]) -> str | None:
    """AMC whose name starts the scheme name ("ADITYA BIRLA SUN LIFE LIQUID FUND"); longest wins."""
    text = normalize_amc(scheme)
    hits = [k for k in keys if text == k or text.startswith(k + " ")]
    return max(hits, key=len) if hits else None


def match_fund_houses(trades: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """Scheme master AMC for each fund house and scheme in ``trades``.

    Returns ``fund_house_raw, holder_name, amc, amc_match`` where ``amc_match``
    is exact, variant (spacing or a shortened name such as "EDEL"), fuzzy,
    from_scheme (blank fund house; read from the start of the scheme name) or none.
    """
    amc_by_key = master.drop_duplicates("amc_key").set_index("amc_key")["amc"]
    keys = sorted(amc_by_key.index)
    rows = []
    for fund_house, scheme in trades[["fund_house_raw", "holder_name"]].drop_duplicates().itertuples(index=False):
        fund_house = fund_house if isinstance(fund_house, str) else ""
        key, kind = _match_amc_key(normalize_amc(fund_house), keys) if fund_house else (None, "none")
        if key is None and not fund_house:
            key = _amc_from_scheme(cell_str(scheme), keys)
            kind = "from_scheme" if key else "none"
        rows.append({"fund_house_raw": fund_house, "holder_name": scheme,
                     "amc": amc_by_key[key] if key else (fund_house or None), "amc_match": kind})
    return pd.DataFrame(rows, columns=["fund_house_raw", "holder_name", "amc", "amc_match"])


def attach_scheme_categories(
    trades: pd.DataFrame,
    master: pd.DataFrame | str | Path,
    overrides: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Map fund houses onto the scheme master's AMC names, rebuild ``holder_id`` and fill ``category``.

    ``master`` is ``parse_amfi_navall`` output or the path of a NAVAll.txt.
    Returns the trades and a per-scheme match report (``attach_categories``'
    columns plus the fund house as reported and how its AMC was matched).
    """
    if not isinstance(master, pd.DataFrame):
        master = parse_amfi_navall(master)
    trades = trades.assign(fund_house_raw=trades["fund_house_raw"].fillna(""),
                           holder_name=trades["holder_name"].fillna(""))
    amcs = match_fund_houses(trades, master)
    key = ["fund_house_raw", "holder_name"]
    out = trades.drop(columns=["amc"]).merge(amcs, on=key, how="left").drop(columns=["amc_match"])
    out["holder_id"] = holder_ids(out["amc"], out["holder_name"])
    # attach_categories keys on (amc, holder_name); blank AMCs must be "" so the lookup matches.
    tagged, report = attach_categories(out.assign(amc=out["amc"].fillna("")), master, overrides)
    out["category"] = tagged["category"].to_numpy()
    sources = amcs.assign(amc=amcs["amc"].fillna("")).rename(columns={"holder_name": "scheme_name"})
    sources = sources.groupby(["amc", "scheme_name"], as_index=False).agg(
        fund_house_raw=("fund_house_raw", lambda v: "; ".join(sorted(set(v)))),
        amc_match=("amc_match", lambda v: "; ".join(sorted(set(v)))),
    )
    if {"amc", "scheme_name"} <= set(report.columns):
        report = report.merge(sources, on=["amc", "scheme_name"], how="left")
    return out.reindex(columns=list(MF_TRADE_COLUMNS)), report


def build_mf_trades(
    paths: Iterable[str | Path],
    *,
    navall: str | Path | None = None,
    scheme_overrides: str | Path | pd.DataFrame | None = None,
    keep_no_isin: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Load several files and, with a NAVAll.txt, attach categories; returns ``(trades, scheme_matches)``."""
    trades = load_amfi_debt_trade_files(paths, keep_no_isin=keep_no_isin)
    if navall is None:
        return trades, None
    overrides = scheme_overrides
    if overrides is not None and not isinstance(overrides, pd.DataFrame):
        overrides = pd.read_csv(overrides)
    return attach_scheme_categories(trades, navall, overrides)


def mapping_report(trades: pd.DataFrame) -> pd.DataFrame:
    """Every Type of Trade / Type of Security spelling seen, what it mapped to, and whether a rule matched.

    ``rule_matched`` False marks a spelling that fell through to OTHER; add a rule for it.
    Blank Type of Security rows are listed by their inferred type.
    """
    parts = []
    for field, raw_col, rules in (("deal_type", "deal_type_raw", _DEAL_RULES),
                                  ("security_type", "security_type_raw", _SECURITY_RULES)):
        counts = (trades.assign(raw=trades[raw_col].fillna(""), mapped=trades[field].fillna(""))
                  .groupby(["raw", "mapped"]).size().rename("rows").reset_index())
        counts.insert(0, "field", field)
        counts["rule_matched"] = [not raw or _first_rule(rules, raw) is not None for raw in counts["raw"]]
        parts.append(counts)
    report = pd.concat(parts, ignore_index=True)
    return report.sort_values(["field", "rows"], ascending=[True, False]).reset_index(drop=True)


def read_mf_trades(source) -> pd.DataFrame:
    """Read MF trade CSVs written from ``load_amfi_debt_trades`` output (one path, several, or a frame)."""
    if isinstance(source, pd.DataFrame):
        df = source.copy()
    elif isinstance(source, (str, Path)):
        df = pd.read_csv(source, dtype=str, keep_default_na=False, na_values=[""])
    else:
        return pd.concat([read_mf_trades(s) for s in source], ignore_index=True)
    df = df.reindex(columns=list(MF_TRADE_COLUMNS) + [c for c in df.columns if c not in MF_TRADE_COLUMNS])
    for col in ("trade_date", "settlement_date", "maturity_date", "deemed_maturity_date"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", errors="coerce")
    for col in ("quantity", "face_value_per_unit", "face_traded_inr", "price", "value_inr",
                "accrued_interest_inr", "yield_traded_pct", "yield_valued_pct", "residual_days"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["rating_notch"] = pd.to_numeric(df["rating_notch"], errors="coerce").astype("Int64")
    df["sovereign"] = df["sovereign"].map(lambda v: str(v).strip().lower() in ("true", "1"))
    for col in ("isin", "direction", "deal_type", "security_type", "category", "amc", "holder_type"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    df["holder_type"] = df["holder_type"].fillna("MF")
    df["direction_available"] = df["direction"].notna()
    return df


def to_price_trades(trades: pd.DataFrame, *, include_inter_scheme: bool = False) -> pd.DataFrame:
    """MF trades in the canonical trades table (``cr_strat.trades.TRADE_COLUMNS``), venue ``AMFI_MF``.

    Keeps market and off-market trades in securities (not repo or TREPS) that
    report a traded yield, so old rows, whose traded yield is 0, drop out.
    Inter-scheme transfers are not arm's length and are left out unless
    ``include_inter_scheme`` is set. ``amount_inr`` is the value of the trade.
    """
    deals = ["MARKET", "OFF_MARKET"] + (["INTER_SCHEME"] if include_inter_scheme else [])
    keep = (trades["isin"].map(is_valid_isin)
            & trades["deal_type"].isin(deals)
            & ~trades["security_type"].isin(FUNDING_TYPES + ("OTHER",))
            & trades["yield_traded_pct"].between(-5, 100)
            & trades["trade_date"].notna())
    df = trades[keep.fillna(False).astype(bool)]
    out = pd.DataFrame({
        "trade_date": df["trade_date"],
        "isin": df["isin"],
        "price": df["price"],
        "yield_pct": df["yield_traded_pct"],
        "amount_inr": df["value_inr"],
        "n_trades": 1.0,
        "venue": PRICE_VENUE,
        "settlement_date": df["settlement_date"],
        "source_file": df["source_file"],
    })
    return out.reindex(columns=list(TRADE_COLUMNS)).reset_index(drop=True)
