import pytest

from cr_strat.rating_scale import normalize_agency, normalize_liquidity, parse_outlook, parse_rating


@pytest.mark.parametrize(
    "raw, agency, grade, outlook, watch",
    [
        ("CRISIL AA+/Stable", "CRISIL", "AA+", "STABLE", None),
        ("[ICRA]AA (Negative)", "ICRA", "AA", "NEGATIVE", None),
        ("CARE AA; Stable", "CARE", "AA", "STABLE", None),
        ("IND AA/RWN", "IND", "AA", None, "NEGATIVE"),
        ("ACUITE AA- | Stable | Reaffirmed", "ACUITE", "AA-", "STABLE", None),
        ("Acuité A+", "ACUITE", "A+", None, None),
        ("IVR AA-/Stable", "INFOMERICS", "AA-", "STABLE", None),
        ("BWR AA+ (CE) (Stable)", "BRICKWORK", "AA+", "STABLE", None),
        ("[ICRA]AA (Rating Watch with Negative Implications)", "ICRA", "AA", None, "NEGATIVE"),
        ("CRISIL AA/Watch Developing", "CRISIL", "AA", None, "DEVELOPING"),
        ("CARE A+; Credit watch with positive implications", "CARE", "A+", None, "POSITIVE"),
        ("CRISIL AA+/Stable (Downgraded from CRISIL AAA/Stable)", "CRISIL", "AA+", "STABLE", None),
        ("Downgraded from CARE AA+ to CARE AA; Negative", "CARE", "AA", "NEGATIVE", None),
        ("CRISIL D", "CRISIL", "D", None, None),
        ("India Ratings BBB-", "IND", "BBB-", None, None),
    ],
)
def test_long_term(raw, agency, grade, outlook, watch):
    p = parse_rating(raw)
    assert (p.agency, p.scale, p.grade, p.outlook, p.watch) == (agency, "LT", grade, outlook, watch)


def test_notches_order_best_first():
    assert parse_rating("CRISIL AAA").notch < parse_rating("CRISIL AA+").notch < parse_rating("CRISIL BBB-").notch
    assert parse_rating("CRISIL D").is_default


def test_short_term():
    p = parse_rating("CRISIL A1+")
    assert (p.scale, p.grade, p.notch) == ("ST", "A1+", 1)
    assert parse_rating("CARE A1+ (CE)").credit_enhanced


def test_flags():
    p = parse_rating("Provisional CARE AA (CE); Stable")
    assert p.provisional and p.credit_enhanced and p.grade == "AA"
    p = parse_rating("CARE BB; Stable; ISSUER NOT COOPERATING*")
    assert p.issuer_not_cooperating and p.grade == "BB"
    p = parse_rating("[ICRA]AA(hyb) (Stable)")
    assert p.hybrid and p.grade == "AA"
    assert parse_rating("CRISIL AA+/Stable; Withdrawn").withdrawn


@pytest.mark.parametrize("raw", ["PP-MLD[ICRA]AA+ (Stable)", "CRISIL PPMLD AA+r/Stable", "IND PP-MLD AA+emr/Stable"])
def test_market_linked(raw):
    p = parse_rating(raw)
    assert p.mld and p.grade == "AA+"


@pytest.mark.parametrize("raw", ["CRISIL FAA+/Stable", "MAA+ (Stable)", "CARE AA+ (FD)"])
def test_fixed_deposit(raw):
    p = parse_rating(raw)
    assert p.fixed_deposit and p.grade == "AA+"


@pytest.mark.parametrize("raw", ["N.A.", "Unrated", "NR", "-", "", None, float("nan"), "Withdrawn", "Sovereign"])
def test_no_grade(raw):
    assert parse_rating(raw).grade is None


def test_helpers():
    assert normalize_agency("India Ratings & Research") == "IND"
    assert normalize_agency("CareEdge Ratings") == "CARE"
    assert normalize_agency("ICRA Limited") == "ICRA"
    assert normalize_agency("Moody's") is None
    assert normalize_liquidity("Liquidity: Stretched") == "STRETCHED"
    assert parse_outlook("Rating Watch with Negative Implications") == (None, "NEGATIVE")
    assert parse_outlook("Negative") == ("NEGATIVE", None)
