import pytest

from cr_strat.rating_scale import normalize_agency, normalize_liquidity, parse_outlook, parse_rating, parse_ratings

FLAGS = ("provisional", "credit_enhanced", "mld", "fixed_deposit", "hybrid", "issuer_not_cooperating",
         "withdrawn", "sovereign", "fund_scale")


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


@pytest.mark.parametrize(
    "raw, scale, grade, flags",
    [
        ("BWR BB + /Stable", "LT", "BB+", set()),
        ("BWR C -", "LT", "C-", set()),
        ("BWR A - (SO)", "LT", "A-", {"credit_enhanced"}),
        ("BWR AA - / BWR A1 +", "LT", "AA-", set()),
        ("BWR A1 +", "ST", "A1+", set()),
        ("Provisional BWR BBB - (SO)", "LT", "BBB-", {"provisional", "credit_enhanced"}),
        ("BWR PP-MLD AA -", "LT", "AA-", {"mld"}),
        ("BWR FAA +", "LT", "AA+", {"fixed_deposit"}),
    ],
)
def test_brickwork_spaced_modifier(raw, scale, grade, flags):
    p = parse_rating(raw)
    assert (p.agency, p.scale, p.grade) == ("BRICKWORK", scale, grade)
    assert {f for f in FLAGS if getattr(p, f)} == flags


def test_spaced_dash_before_a_word_is_not_a_modifier():
    assert parse_rating("BWR A - Reaffirmed").grade == "A"


@pytest.mark.parametrize(
    "raw, grade, watch",
    [
        ("IVR BBB / RWDI", "BBB", "DEVELOPING"),
        ("IVR BBB- /RWNI", "BBB-", "NEGATIVE"),
        ("IVR BBB+ /RWPI", "BBB+", "POSITIVE"),
        ("IVR B /CWDI", "B", "DEVELOPING"),
        ("IVR A /CWNI", "A", "NEGATIVE"),
        ("IVR A /CWPI", "A", "POSITIVE"),
        ("IVR Triple B Plus with Rating Watch with Developing Implications", "BBB+", "DEVELOPING"),
        ("IVR BBB / RWDI (IVR Triple B with Rating Watch with Developing Implications)", "BBB", "DEVELOPING"),
        ("IVR Double A Minus", "AA-", None),
        ("IVR Single A", "A", None),
    ],
)
def test_infomerics_watch_codes_and_word_grades(raw, grade, watch):
    p = parse_rating(raw)
    assert (p.agency, p.scale, p.grade, p.watch) == ("INFOMERICS", "LT", grade, watch)


def test_infomerics_word_short_term_grade():
    p = parse_rating("IVR A One Plus")
    assert (p.scale, p.grade) == ("ST", "A1+")


def test_watch_without_direction_is_unspecified():
    assert parse_rating("CARE A- (Under Credit Watch)").watch == "UNSPECIFIED"
    assert parse_rating("CARE BBB (SO) (Under Credit Watch)").watch == "UNSPECIFIED"
    assert parse_rating("CARE AA- (Under Credit watch with Developing Implications)").watch == "DEVELOPING"
    assert parse_outlook("Under Credit Watch") == (None, "UNSPECIFIED")
    assert parse_rating("IND AA/Off Rating Watch").watch is None


@pytest.mark.parametrize(
    "raw, scale, grade",
    [
        ("IND A+mfs", "LT", "A+"),
        ("IND AAAmfs", "LT", "AAA"),
        ("IND AAmfs", "LT", "AA"),
        ("IND A+idf-mf", "LT", "A+"),
        ("IND AA- idf-mf", "LT", "AA-"),
        ("CARE AAA mfs", "LT", "AAA"),
        ("Provisional CARE AAA mfs", "LT", "AAA"),
        ("CARE A1+ mfs", "ST", "A1+"),
    ],
)
def test_fund_scale(raw, scale, grade):
    p = parse_rating(raw)
    assert (p.scale, p.grade, p.fund_scale) == (scale, grade, True)
    assert not p.is_plain


def test_fund_scale_not_set_on_issuer_ratings():
    assert not parse_rating("CARE AAA; Stable").fund_scale


@pytest.mark.parametrize(
    "raw, grade",
    [("F1+(ind)", "A1+"), ("F1(ind)", "A1"), ("F2+(ind)", "A2+"), ("F2(ind)", "A2"),
     ("F3(ind)", "A3"), ("F3 (ind)", "A3"), ("F4(ind)", "A4")],
)
def test_legacy_fitch_short_term(raw, grade):
    p = parse_rating(raw)
    assert (p.agency, p.scale, p.grade) == ("IND", "ST", grade)


@pytest.mark.parametrize("raw, grade", [("BBB-(ind)", "BBB-"), ("D(ind)", "D"), ("AAA(ind)", "AAA")])
def test_legacy_fitch_long_term(raw, grade):
    p = parse_rating(raw)
    assert (p.agency, p.scale, p.grade) == ("IND", "LT", grade)


def test_legacy_fitch_f5_has_no_equivalent():
    p = parse_rating("F5(ind)")
    assert p.grade is None and p.agency == "IND"


def test_expected_rating_is_provisional():
    p = parse_rating("A(ind)(SO)(EXP)")
    assert (p.grade, p.provisional, p.credit_enhanced) == ("A", True, True)


@pytest.mark.parametrize("raw", ["WD", "IND WD"])
def test_wd_is_withdrawn(raw):
    p = parse_rating(raw)
    assert p.withdrawn and p.grade is None


def test_one_leg_withdrawn():
    p = parse_rating("BWR AA - (Short term rating withdrawn)")
    assert (p.grade, p.withdrawn) == ("AA-", False)
    assert parse_rating("BWR A1 + (Short term rating withdrawn)").withdrawn
    assert parse_rating("CARE AA (Long term rating withdrawn)").withdrawn


@pytest.mark.parametrize(
    "raw, agency, grade, outlook, watch, flags",
    [
        ("ACUITE C & ACUITE D", "ACUITE", "D", None, None, set()),
        ("ACUITE C/ACUITE A4 & ACUITE D", "ACUITE", "D", None, None, set()),
        ("ACUITE A-(CE) & ACUITE BBB-/ACUITE A3", "ACUITE", "BBB-", None, None, set()),
        ("ACUITE A-(CE) & BB+/ACUITE A4+", "ACUITE", "BB+", None, None, set()),
        ("ACUITE AA & ACUITE PP MLD AA", "ACUITE", "AA", None, None, set()),
        ("IND A+/IND AA-(SO)/IND A-(SO)", "IND", "A+", None, None, set()),
        ("IND D/C/IND PP-MLD Cemr", "IND", "D", None, None, set()),
        ("AA-/IND PP-MLD AA-emr", "IND", "AA-", None, None, set()),
        ("CARE BBB-; Stable / CARE A3 (Rating Watch with Negative Implications)", "CARE", "BBB-", "STABLE", None,
         set()),
        ("[ICRA]A+ (Stable)/[ICRA]A1+", "ICRA", "A+", "STABLE", None, set()),
        ("Crisil AA+/Watch Positive / Crisil A1+", "CRISIL", "AA+", None, "POSITIVE", set()),
        ("IND BB+/Stable; IND A4+", "IND", "BB+", "STABLE", None, set()),
        ("IVR A- /Stable & IVR A2+", "INFOMERICS", "A-", "STABLE", None, set()),
        # No plain segment: the worst of all segments, and the one with fewer special flags on a tie.
        ("IND AA(CE)/IND A+(CE)", "IND", "A+", None, None, {"credit_enhanced"}),
        ("ACUITE PP MLD Provisional AA (SO) & ACUITE Provisional AA (SO)", "ACUITE", "AA", None, None,
         {"credit_enhanced", "provisional"}),
        # A final rating beats a provisional one of the same grade.
        ("IND BBB-/Provisional IND BBB-", "IND", "BBB-", None, None, set()),
        ("ACUITE Provisional A & ACUITE A", "ACUITE", "A", None, None, set()),
        ("ACUITE Provisional AA & ACUITE A", "ACUITE", "A", None, None, set()),
        # The unsupported (standalone) rating is the plain one.
        ("Supported: IVR A- (CE) /Stable; Unsupported: IVR BB /Stable", "INFOMERICS", "BB", "STABLE", None, set()),
        # Several agencies: worst grade; another agency's withdrawal or status does not leak.
        ("ICRA A-/Stable ();IVR A-/Stable ();CARE WITHDRAWN/No Outlook ()", "ICRA", "A-", "STABLE", None, set()),
        ("IND AA+/Stable ();BWR BB-/Stable () (ISSUER NOT COOPERATING*)", "BRICKWORK", "BB-", "STABLE", None,
         {"issuer_not_cooperating"}),
        ("BWR A+/Positive ();ACUITE A/Stable ()", "ACUITE", "A", "STABLE", None, set()),
    ],
)
def test_multi_part(raw, agency, grade, outlook, watch, flags):
    p = parse_rating(raw)
    assert (p.agency, p.scale, p.grade, p.outlook, p.watch) == (agency, "LT", grade, outlook, watch)
    assert {f for f in FLAGS if getattr(p, f)} == flags
    assert p.raw == raw


def test_short_term_only_takes_first_segment():
    p = parse_rating("CARE A1+ / CARE A1")
    assert (p.scale, p.grade) == ("ST", "A1+")


def test_parse_ratings_segments():
    parts = parse_ratings("CRISIL AAA/Stable ();CARE AAA/Stable ()")
    assert [(p.agency, p.grade, p.outlook) for p in parts] == [("CRISIL", "AAA", "STABLE"), ("CARE", "AAA", "STABLE")]
    assert [p.raw for p in parts] == ["CRISIL AAA/Stable ()", "CARE AAA/Stable ()"]

    parts = parse_ratings("ACUITE A-(CE) & BB+/ACUITE A4+")
    assert [(p.agency, p.scale, p.grade, p.credit_enhanced) for p in parts] == [
        ("ACUITE", "LT", "A-", True), ("ACUITE", "LT", "BB+", False), ("ACUITE", "ST", "A4+", False)]

    parts = parse_ratings("ICRA A-/Stable ();CARE WITHDRAWN/No Outlook ()")
    assert [(p.agency, p.grade, p.withdrawn) for p in parts] == [("ICRA", "A-", False), ("CARE", None, True)]


def test_parse_ratings_single_and_blank():
    assert parse_ratings(None) == []
    assert parse_ratings("  ") == []
    [p] = parse_ratings("CARE AA; Stable")
    assert (p.raw, p.grade, p.outlook) == ("CARE AA; Stable", "AA", "STABLE")
    [p] = parse_ratings("NR")
    assert p.grade is None


def test_segments_do_not_split_outlooks_or_from_clauses():
    assert len(parse_ratings("CARE BB; Stable; ISSUER NOT COOPERATING*")) == 1
    assert len(parse_ratings("Downgraded from CARE AA+ to CARE AA; Negative")) == 1
    [p] = parse_ratings("CRISIL A1 (Downgraded from CRISIL AA/CRISIL A1+)")
    assert (p.scale, p.grade) == ("ST", "A1")
    # A piece without a grade stays with the rating before it.
    [p] = parse_ratings("IND A-/IND tA")
    assert p.grade == "A-"


def test_issuer_not_cooperating_is_shared_within_an_agency():
    lt, st = parse_ratings("CARE BB+; Stable / CARE A4; ISSUER NOT COOPERATING*")
    assert lt.issuer_not_cooperating and st.issuer_not_cooperating
    assert parse_rating("CARE D / CARE D; ISSUER NOT COOPERATING*").issuer_not_cooperating
    ind, bwr = parse_ratings("IND AA+/Stable ();BWR BB-/Stable () (ISSUER NOT COOPERATING*)")
    assert not ind.issuer_not_cooperating and bwr.issuer_not_cooperating


def test_trailing_watch_covers_long_term_leg_without_its_own():
    p = parse_rating("CARE A+ / CARE A1+ (Rating Watch with Developing Implications)")
    assert (p.grade, p.outlook, p.watch) == ("A+", None, "DEVELOPING")
    assert parse_rating("CARE BBB- / CARE A3 (Under Credit Watch)").watch == "UNSPECIFIED"
    p = parse_rating("CARE A; Stable / CARE A1 (Under Credit watch with Developing Implications)")
    assert (p.outlook, p.watch) == ("STABLE", None)
    # Two equal grades: the one that states a watch is kept.
    assert parse_rating("CARE D / CARE D (Rating Watch with Positive Implications)").watch == "POSITIVE"


def test_market_linked_suffix_without_prefix():
    p = parse_rating("CARE A+r(SO)")
    assert (p.grade, p.mld, p.credit_enhanced) == ("A+", True, True)
    assert parse_rating("CARE MLD AA-; Stable").mld
    p = parse_rating("CARE AA; Stable (Rs. 100 Cr)")
    assert (p.grade, p.mld) == ("AA", False)


def test_liquidity_takes_the_first_descriptor():
    assert normalize_liquidity("Superior / Strong") == "SUPERIOR"
    assert normalize_liquidity("Adequate: supported by strong parentage") == "ADEQUATE"
    assert normalize_liquidity("Liquidity Indicator - Poor") == "POOR"
    assert normalize_liquidity("no descriptor") is None
