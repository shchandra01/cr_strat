import pandas as pd
import pytest

from cr_strat.rating_actions import classify_instrument, load_rating_actions


@pytest.mark.parametrize(
    "label, cls",
    [
        # CRA instrument labels
        ("BLR", "BANK_LOAN"),
        ("Bank loan facilities", "BANK_LOAN"),
        ("Long Term Bank Facilities", "BANK_LOAN"),
        ("Long Term Loan", "BANK_LOAN"),
        ("PPMLD", "MLD"),
        ("PP-MLD", "MLD"),
        ("Non-convertible debenture - PP-MLD - III", "MLD"),
        ("Market Linked Debentures", "MLD"),
        ("Securitised", "STRUCTURED"),
        ("Securitised Notes (SNs)", "STRUCTURED"),
        ("Alpha Trust 01 2024 SN Series A1", "STRUCTURED"),
        ("Beta 05 24 Series A2 SN", "STRUCTURED"),
        ("SDI", "STRUCTURED"),
        ("Second loss credit facility (SLCF)", "STRUCTURED"),
        ("Gamma Trust Mar 2024 - Tranche I Second Loss Facility", "STRUCTURED"),
        ("Pass Through Certificates (PTCs)", "STRUCTURED"),
        ("Sub-ordinated debt", "SUB"),
        ("Subordinated debt - IV", "SUB"),
        ("Bonds-Tier II Bonds", "SUB"),
        ("Bonds-Perpetual Bonds", "PERP"),
        ("Long-term market borrowing programme (FY13)", "BOND"),
        ("Non - Convertible Debentures (NCD) (Zero Coupon Bonds)", "BOND"),
        ("Debentures-Non Convertible Debentures", "BOND"),
        ("Infrastructure Bonds", "BOND"),
        ("Commercial Paper (Standalone)", "ST"),
        ("Short Term", "ST"),
        ("Fixed Deposit", "FD"),
        ("FD (Long-term)/ FD (Short-term)", "FD"),
        ("Compulsorily Convertible Debentures", "OTHER"),
        ("Optionally Convertible Debentures", "OTHER"),
        # CARE's bare scale labels do not say what the instrument is.
        ("Long Term", "OTHER"),
        ("LT/ST", "OTHER"),
        ("Issuer Ratings", "OTHER"),
        ("", "BOND"),
        (None, "BOND"),
        (float("nan"), "BOND"),
    ],
)
def test_cra_labels(label, cls):
    assert classify_instrument(label) == cls


@pytest.mark.parametrize(
    "label, cls",
    [
        ("BLR/Bond/CP", "BOND"),
        ("BLR/CP", "BANK_LOAN"),
        ("NCD & CP", "BOND"),
        ("Commercial Paper and Term Loan", "BANK_LOAN"),
        ("Non-convertible Debenture/Subordinate Debt", "BOND"),
        ("Tier II Bonds, Perpetual Bonds", "SUB"),
        ("Fund-based/Non-fund-based-Bank Facilities", "BANK_LOAN"),
        ("NCD/CCD", "BOND"),
    ],
)
def test_combined_labels_take_the_highest_priority_class(label, cls):
    assert classify_instrument(label) == cls


@pytest.mark.parametrize(
    "description, cls",
    [
        # NSDL short security descriptions: issuer, series, coupon, type code, maturity, face value
        ("DELTA HOME LOANS LIMITED SR TR A 9.25 NCD 30AG28 FVRS10LAC", "BOND"),
        ("EPSILON GOLD LOANS LIMITED SR II 10.50 NCD 10JN28 FVRS1000", "BOND"),
        ("ZETA POWER CORPORATION LIMITED SR I 8.50 BD 15MR30 FVRS1LAC", "BOND"),
        ("ETA STATE INFRA BOARD SR B 9.10 BDS 08OT34 FVRS1LAC", "BOND"),
        ("THETA TRANSMISSION LIMITED SR-I 8.62 LOA 23FB27 FVRS750000", "BOND"),
        ("IOTA SOLAR PRIVATE LIMITED 0.01 CCD 12NV36 FVRS10", "OTHER"),
        ("KAPPA REALTY PRIVATE LIMITED SR I 0.001 OCD PERPETUAL FVRS10", "OTHER"),
        ("LAMBDA SECURITIES LIMITED SR AS/MLD/A/06 BR LOA 20SP29 FVRS1LAC", "MLD"),
        ("LAMBDA SECURITIES LIMITED SR AS/MLD/A BR LOA 24FB31 FVRS1LAC", "MLD"),
        ("MU FINCORP LIMITED SR SDI/21-21 OP II 12 NCD 02NV27 FVRS10000", "BOND"),
        ("PTC INDIA FINANCIAL SERVICES LIMITED SR-2 9.15 BD 30MR27 FVRS5000", "BOND"),
        ("MUL 11.00 210330", "OTHER"),
    ],
)
def test_nsdl_descriptions(description, cls):
    assert classify_instrument(description) == cls


def _load(rows, columns=("date", "issuer_id", "agency", "instrument", "rating")):
    return load_rating_actions(pd.DataFrame(rows, columns=list(columns)))


def test_fund_ratings_never_feed_bond_mandates():
    actions = _load([
        ("2024-01-10", "F1", "CARE", "Corporate Bond Fund", "CARE AAA mfs"),
        ("2024-01-10", "F2", "IND", "Bonds", "IND AA-idf-mf"),
        ("2024-01-10", "F3", "IND", None, "IND A+mfs"),
        ("2024-01-10", "X1", "CARE", "NCD", "CARE AAA; Stable"),
    ])
    assert actions["instrument_class"].tolist() == ["OTHER", "OTHER", "OTHER", "BOND"]
    assert actions["fund_scale"].tolist() == [True, True, True, False]
    assert actions["grade"].tolist() == ["AAA", "AA-", "A+", "AAA"]


def test_fund_rating_overrides_given_class():
    actions = _load(
        [("2024-01-10", "F1", "CARE", "BOND", "CARE AAA mfs")],
        columns=("date", "issuer_id", "agency", "instrument_class", "rating"),
    )
    assert actions.loc[0, "instrument_class"] == "OTHER"


def test_multi_part_rating_rows():
    actions = _load([
        ("2024-02-01", "A1", "ACUITE", "NCD", "ACUITE A-(CE) & ACUITE BBB-/ACUITE A3"),
        ("2024-02-01", "B1", "BRICKWORK", "NCD", "BWR AA - (Short term rating withdrawn)"),
        ("2024-02-01", "C1", "ACUITE", "BLR", "ACUITE BB+/ACUITE A4+"),
        ("2024-02-01", "D1", "CARE", "LT/ST", "CARE A+ / CARE A1+ (Under Credit Watch)"),
    ])
    a1, b1, c1, d1 = (actions.iloc[i] for i in range(4))
    assert (a1["instrument_class"], a1["grade"], a1["credit_enhanced"]) == ("BOND", "BBB-", False)
    assert (b1["instrument_class"], b1["grade"], b1["withdrawn"]) == ("BOND", "AA-", False)
    assert (c1["instrument_class"], c1["grade"]) == ("BANK_LOAN", "BB+")
    assert (d1["instrument_class"], d1["watch"]) == ("OTHER", "UNSPECIFIED")
    assert actions["parse_ok"].all()
