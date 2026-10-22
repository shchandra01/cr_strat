import datetime as dt

import openpyxl
import pandas as pd
import pytest

from cr_strat import cra_disclosures as cra
from cr_strat.cra_disclosures import (
    ACTION_COLUMNS,
    combine_rating_actions,
    disclosure_summary,
    load_cra_disclosure,
    load_cra_disclosures,
    normalize_issuer_name,
    read_issuer_map,
    unmapped_issuers,
)
from cr_strat.rating_actions import load_rating_actions

D = dt.datetime


def _workbook(path, sheets: dict[str, list[list]]):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


CARE_MOVE_HEADER = ["Sr. No.", "Name of the Issuer", "Sector", "Type of Instrument ", "Listing Status",
                    "Rating prior to revision", "Rating post revision", "Date of press release",
                    "Notch difference", "Trigger Event"]


@pytest.fixture
def care_movement(tmp_path):
    rows = [
        ["a. Securities"],
        ["Upgrades"],
        ["Rating upgrades between October 2025 – March 2026"],
        CARE_MOVE_HEADER,
        [1, "Alpha Finance Private Limited", "NBFC", "Non Convertible Debentures", "Listed",
         "CARE A; Stable", "CARE A+; Stable", D(2025, 11, 6), "1  ", "Annual Surveillance"],
        ["Downgrades"],
        ["Rating downgrades between October 2025 – March 2026"],
        CARE_MOVE_HEADER,
        [1, "Beta Infra Limited", "Infra", "Non Convertible Debentures", "Unlisted",
         "CARE AA; Stable", "CARE AA-; Negative", D(2026, 1, 12), 1, "Results"],
        ["b. Financial instruments other than securities"],
        ["Upgrades"],
        CARE_MOVE_HEADER,
        [1, "Gamma Steel Pvt. Ltd.", "Steel", "Term Loan", "Unlisted",
         "CARE BB+; Stable; ISSUER NOT COOPERATING*", "CARE BBB-; Stable", D(2026, 2, 3), 1, "Review"],
    ]
    return _workbook(tmp_path / "Movement-of-Ratings-H2-FY2025-26.xlsx", {"Sheet1": rows})


def test_movement_stacked_subtables(care_movement):
    df = load_cra_disclosure(care_movement, "CareEdge")
    assert list(df.columns) == list(ACTION_COLUMNS)
    assert df["issuer_name"].tolist() == ["Alpha Finance Private Limited", "Beta Infra Limited",
                                          "Gamma Steel Pvt. Ltd."]
    assert df["action"].tolist() == ["Upgraded", "Downgraded", "Upgraded"]
    assert df["segment"].tolist() == ["securities", "securities", "other"]
    assert df["date"].tolist() == [pd.Timestamp("2025-11-06"), pd.Timestamp("2026-01-12"), pd.Timestamp("2026-02-03")]
    assert df["rating"].tolist() == ["CARE A+; Stable", "CARE AA-; Negative", "CARE BBB-; Stable"]
    assert df["prior_rating"].iloc[2] == "CARE BB+; Stable; ISSUER NOT COOPERATING*"
    assert df["notch_difference"].tolist() == ["1", "1", "1"]
    assert (df["agency"] == "CARE").all() and (df["source_kind"] == "movement").all()
    assert df["source_row"].tolist() == [5, 9, 13]
    assert df["issuer_id"].tolist() == ["NAME:ALPHA FINANCE", "NAME:BETA INFRA", "NAME:GAMMA STEEL"]

    actions = load_rating_actions(df)
    assert actions["parse_ok"].all()
    assert actions["outlook"].iloc[1] == "NEGATIVE"
    assert actions["instrument_class"].tolist() == ["BOND", "BOND", "BANK_LOAN"]


def test_movement_direction_from_header_sheet_and_strings(tmp_path):
    header = ["S.No", "Name of the Issuer", "Sector", "Security Type",
              "Listing Status \n(Listed/ Proposed to be listed)", "Rating prior to Revision",
              "Rating post Revision", "Date of Press Release for Rating Upgrades", "Notch Difference",
              "Trigger Event (Quarterly Results, Exchange Disclosures, etc.)"]
    plain = header[:7] + ["Date of Press Release", "Notch Difference", "Trigger Event"]
    newline = header[:7] + ["Date of Press\nRelease for Rating Downgrade", "Notch Difference", "Trigger Event"]
    path = _workbook(tmp_path / "movement.xlsx", {
        "Upgrades-Securities": [
            ["Movement* of Each Credit Rating"],
            header,
            [1, "Delta Housing Finance Limited", "Finance", "Non-convertible debentures", "Listed",
             "[ICRA]AA-(Stable)", "[ICRA]AA (Stable)", "05-Nov-25", 1, "Annual Surveillance"],
        ],
        "Sheet2": [
            newline,
            [1, "Epsilon Hotels Private Limited", "Leisure", "Bank Facilities", "Unlisted",
             "[ICRA]A (Positive)/[ICRA]A2+", "[ICRA]A- (Stable)/[ICRA]A2", "7-Jan-26", "1/1", "Results"],
        ],
        "Downgrades -FIOS": [
            plain,
            [1, "Zeta Mills", "Textiles", "Bank Facilities", "Unlisted",
             "[ICRA]BB+ (Stable)", "[ICRA]B+ (Stable) ISSUER NOT COOPERATING", "18-01-2026", 3, "Review"],
        ],
    })
    df = load_cra_disclosure(path, "ICRA")
    assert df["action"].tolist() == ["Upgraded", "Downgraded", "Downgraded"]
    assert df["source_sheet"].tolist() == ["Upgrades-Securities", "Sheet2", "Downgrades -FIOS"]
    assert df["segment"].fillna("").tolist() == ["securities", "", "other"]
    assert df["listing_status"].iloc[0] == "Listed"
    assert df["date"].tolist() == [pd.Timestamp("2025-11-05"), pd.Timestamp("2026-01-07"), pd.Timestamp("2026-01-18")]
    assert df["notch_difference"].iloc[1] == "1/1"
    assert load_rating_actions(df)["parse_ok"].all()


def test_movement_withdrawn_mark_and_continuation_rows(tmp_path):
    acuite = _workbook(tmp_path / "Annexure 22.xlsx", {"ANNEXURE 22": [
        ["Annexure 22 A – Upgrades"],
        ["a. Securities"],
        ["SI. No.", "Name of the Issuer", "Sector", "Securities Type",
         "Listing Status\n(Listed/Proposed to be listed )", "Rating Prior to Revision", "Rating Post Revision",
         "Date of Press Release for Rating Upgrade", "Notch Difference", "Trigger Event"],
        [1, "EPSILON TEXTILES PRIVATE LIMITED#", "Textiles", "NCD", "Listed", "ACUITE C", "ACUITE BB",
         D(2025, 10, 16), 6, "Curing of Default"],
        [None, "(#) Rating Withdrawn"],
    ]})
    df = load_cra_disclosure(acuite, "Acuité")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["issuer_name"] == "EPSILON TEXTILES PRIVATE LIMITED"
    assert row["issuer_id"] == "NAME:EPSILON TEXTILES"
    assert row["note"] == "rating withdrawn (#)"
    assert row["action"] == "Upgraded" and row["agency"] == "ACUITE"

    infomerics = _workbook(tmp_path / "Annx22_Movement_Downgrade_Securities_mar26.xlsx", {" Downgrades - Securities": [
        ["Annexure 22 - Downgrades - Securities"],
        ["Sr. No.", "Name of the Issuer", "Sector", "Security Type",
         "Listing Status (Listing / Proposed to be Listed)", "Rating Prior to Revision", "Rating Post Revision",
         "Date of Press Release for Rating Downgrade", "Notch Difference", "Trigger Event"],
        [1, "Zeta Green Energy Limited", "Construction", "Non Convertible Debentures", "Unlisted",
         "IVR C /Negative/INC", "IVR D /INC", D(2025, 10, 3), 2, "delayed debt servicing"],
        [None, None, None, "Proposed - Non Convertible Debentures", "Unlisted",
         "IVR C /Negative/INC", "IVR D /INC", None, 2, None],
    ]})
    df = load_cra_disclosure(infomerics, "Infomerics")
    assert df["issuer_name"].tolist() == ["Zeta Green Energy Limited"] * 2
    assert df["sector"].tolist() == ["Construction"] * 2
    assert df["date"].tolist() == [pd.Timestamp("2025-10-03")] * 2
    assert df["trigger"].tolist() == ["delayed debt servicing"] * 2
    assert df["instrument"].tolist() == ["Non Convertible Debentures", "Proposed - Non Convertible Debentures"]
    assert load_rating_actions(df)["parse_ok"].all()


def _padded(*lines: bytes, pad: int = 4000) -> bytes:
    return b"\r\n".join(line + b"," * pad for line in lines) + b"\r\n"


def test_india_ratings_archive_movement_csv(tmp_path):
    header = (b"Sr. No.,Issuer,Sector,Instrument,Listing Status,Rating prior to Revision Long Term,"
              b"Rating prior to Revision Short Term,Rating Post Revision Long Term,"
              b"Rating Post Revision Short Term,Press Release Date,Notch Difference,Trigger Event")
    data = _padded(
        b"Upgrade", b"", header,
        b"1,Eta Foods Ltd.,\"Food, Beverage & Tobacco\",BLR,Unlisted,IND A-,A2,IND A,A1,08-Apr-19,1,"
        b"Annual Surveillance ",
        b"2,Theta\x92s Exim Private Limited,Services,Bond,Unlisted,IND BBB,-,IND BBB+,-,7-Jun-19,1,Review",
        b"", b"Downgrade", b"", header,
        b"1,Iota Power Limited,Power,Bond,Listed,IND C,-,IND D,-,30-Sep-19,1,Delay in debt servicing",
    )
    path = tmp_path / "Movement of each credit rating H1 2020.csv"
    path.write_bytes(data)
    assert max(len(r) for r in cra._iter_csv_rows(path)) == 12  # padding dropped while streaming

    df = load_cra_disclosure(path, "India Ratings")
    assert df["issuer_name"].tolist() == ["Eta Foods Ltd.", "Eta Foods Ltd.", "Theta’s Exim Private Limited",
                                          "Iota Power Limited"]
    assert df["rating"].tolist() == ["IND A", "IND A1", "IND BBB+", "IND D"]
    assert df["prior_rating"].tolist() == ["IND A-", "IND A2", "IND BBB", "IND C"]
    assert df["note"].fillna("").tolist() == ["", "short-term leg", "", ""]
    assert df["action"].tolist() == ["Upgraded", "Upgraded", "Upgraded", "Downgraded"]
    assert df["sector"].iloc[0] == "Food, Beverage & Tobacco"
    assert df["trigger"].iloc[0] == "Annual Surveillance"
    assert df["date"].tolist() == [pd.Timestamp("2019-04-08")] * 2 + [pd.Timestamp("2019-06-07"),
                                                                      pd.Timestamp("2019-09-30")]
    actions = load_rating_actions(df)
    assert actions["parse_ok"].all()
    assert actions["scale"].tolist() == ["LT", "ST", "LT", "LT"]


CARE_HIST_HEADER = ["Sr. No.", "Name of Issuer ", "Sector", "Industry", "Instrument Type",
                    "Outstanding Issue Size (Rs. Million)", "Listing Status", "Intial Rating", "Date of Intial Rating",
                    "Rating after 1st Revision", "Date of 1st Revision", "Rating after 2nd Revision",
                    "Date of 2nd Revision", "Current Outstanding Rating"]


@pytest.fixture
def care_history(tmp_path):
    rows = [
        ["Annexure V- History of Credit Rating of all Outstanding Securities"],
        CARE_HIST_HEADER,
        [1, "Kappa Realty Private Limited", "Realty", "Realty", "Non Convertible Debentures", 979.6, "Listed",
         "CARE BBB-; Stable", D(2024, 11, 21), "CARE BB+; Stable; ISSUER NOT COOPERATING*", D(2025, 12, 3),
         None, None, "CARE BB+; Stable; ISSUER NOT COOPERATING*"],
        [2, "Lambda Logistics Private Limited", "Services", "Transport", "Cash Credit", 100, "Unlisted",
         "CARE BB-; Stable / CARE A4", D(2022, 6, 14), "CARE BB-; Negative / CARE A4", "21-06-2023",
         "CARE BB+; Stable / CARE A4+", D(2024, 8, 21), "CARE BB+; Stable / CARE A4+"],
        [3, "Mu Textiles Limited", "Textiles", "Textiles", "Term Loan", 50, "Unlisted",
         "CARE B; Stable", D(2021, 1, 5), "CARE B-; Stable", "CARE B-; Stable; ISSUER NOT COOPERATING*",
         None, None, "CARE C; Stable"],
    ]
    return _workbook(tmp_path / "History-of-Credit-Rating.xlsx", {"Sheet1": rows})


def test_history_wide_to_long(care_history):
    df = load_cra_disclosure(care_history, "CARE")
    kappa = df[df["issuer_name"] == "Kappa Realty Private Limited"]
    assert kappa["source_kind"].tolist() == ["history_initial", "history_revision"]
    assert kappa["action"].tolist() == ["Assigned", "Downgraded"]
    assert kappa["date"].tolist() == [pd.Timestamp("2024-11-21"), pd.Timestamp("2025-12-03")]
    assert kappa["prior_rating"].tolist()[1] == "CARE BBB-; Stable"
    assert kappa["outstanding_size_inr_mn"].tolist() == [979.6, 979.6]
    assert kappa["listing_status"].iloc[0] == "Listed" and kappa["sector"].iloc[0] == "Realty"

    lam = df[df["issuer_name"] == "Lambda Logistics Private Limited"]
    assert lam["action"].tolist() == ["Assigned", "Revised", "Upgraded"]  # outlook change, then upgrade
    assert lam["date"].iloc[1] == pd.Timestamp("2023-06-21")  # dd-mm-yyyy text cell

    mu = df[df["issuer_name"] == "Mu Textiles Limited"]
    assert mu["source_kind"].tolist() == ["history_initial", "history_revision", "history_current"]
    assert pd.isna(mu["date"].iloc[1]) and mu["note"].iloc[1].startswith("unparsed date: CARE B-")
    # No snapshot date in the title: a changed current rating has no date until one is given.
    assert pd.isna(mu["date"].iloc[2])
    assert mu["note"].iloc[2] == "current rating differs from the last dated rating"
    dated = load_cra_disclosure(care_history, "CARE", as_of="2023-03-31")
    assert dated["date"].iloc[-1] == pd.Timestamp("2023-03-31")

    actions = load_rating_actions(df)
    assert (actions["failure"].dropna() == "bad date").all() and (~actions["parse_ok"]).sum() == 2


def test_history_icra_sections_sub_rating_and_prefix(tmp_path):
    path = _workbook(tmp_path / "History of all Outstanding Securities.xlsx", {"History.": [
        ["ICRA LIMITED"],
        ["History of Credit Rating of all Outstanding Securities on 31/03/2026 (Non-Structured)"],
        ["S.No", "Issuer Name", "Sector", "Type of Security", "Outstanding Issue Size (INR Millions)",
         "Listing Status (Listed/Proposed to be listed/Unlisted)", "Initial Rating", "Date of Initial Rating",
         "Rating after 1st revision", "Date of 1st revision", "Current rating"],
        [1, "Mu Power Limited", "Power", "NCD", 5000, "Listed", "AA", D(2019, 3, 1), "D", D(2024, 5, 2), "D INC"],
        [],
        ["ICRA LIMITED"],
        ["History of Credit Rating of all Outstanding Securities on 31/03/2026 (Structured)"],
        ["Sl. No", " Name of the Issuer ", "Sector", "Type of Security/Instrument Name",
         "Outstanding Issue Size (Rs. In Million)", "Listing Status", " Initial Rating  ", " Sub Rating ",
         " Date of Initial Rating   ", " Rating after 1st Revision    ", " Sub Rating after 1st Revision    ",
         " Date of 1st Revision    ", "Current Rating", "Sub-rating"],
        [1, "Nu Financiers Limited", "Finance", "Prime Home Loan Trust II PTC Series A", 243.5, "Unlisted",
         "AA ", "(SO)", D(2017, 10, 6), "AA+ ", "(SO)", D(2019, 3, 20), "AA+", "(SO)"],
    ]})
    df = load_cra_disclosure(path, "ICRA")
    assert df["rating"].tolist() == ["[ICRA]AA", "[ICRA]D", "[ICRA]D INC", "[ICRA]AA (SO)", "[ICRA]AA+ (SO)"]
    assert df["action"].tolist() == ["Assigned", "Downgraded", "Revised", "Assigned", "Upgraded"]
    current = df.iloc[2]
    assert current["source_kind"] == "history_current"
    assert current["date"] == pd.Timestamp("2026-03-31")  # "on 31/03/2026" in the title
    assert df["instrument"].iloc[3] == "Prime Home Loan Trust II PTC Series A"
    assert df["outstanding_size_inr_mn"].iloc[3] == 243.5
    actions = load_rating_actions(df)
    assert actions["parse_ok"].all()
    assert actions["issuer_not_cooperating"].iloc[2]
    assert actions["instrument_class"].iloc[3] == "STRUCTURED"


def test_history_two_row_header_and_segments(tmp_path):
    top = ["Sl.No.", "Name of the Issuer", "Sector", "Security Type", "Outstanding Issue Size", "Listing Status\n",
           "Initial Rating", None, "First Revision", None, "Second Revision", None, "Current Outstanding Rating"]
    sub = [None, None, None, None, "(INR\nmillions)", "(Listed/Proposed to be listed)",
           "Rating", "Date", "Rating", "Date", "Rating", "Date"]
    path = _workbook(tmp_path / "Annexure 24.xlsx", {"ANNEXURE 24": [
        ["ANNEXURE 24"],
        ["History of credit rating of all Outstanding Securities as on 31 March 2026"],
        ["a. Securities"],
        top, sub,
        [1, "AGRA NAGAR NIGAM", "Urban Local Bodies", "BOND", 500, "Listed",
         "ACUITE Provisional AA", D(2024, 3, 29), "ACUITE AA", D(2025, 5, 16), "-", "-", "ACUITE AA"],
        ["b. Financial Instruments other than securities"],
        top, sub,
        [1, "ZUHA LEATHER PRIVATE LIMITED", "Leather", "BLR", 147.5, "Unlisted",
         "ACUITE BB-/ACUITE A4+", D(2016, 1, 27), "ACUITE B+/ACUITE A4", D(2021, 3, 25), "-", "-",
         "ACUITE B+/ACUITE A4"],
    ]})
    df = load_cra_disclosure(path, "Acuite")
    assert df["segment"].tolist() == ["securities", "securities", "other", "other"]
    assert df["action"].tolist() == ["Assigned", "Revised", "Assigned", "Downgraded"]
    assert df["date"].tolist() == [pd.Timestamp(d) for d in ("2024-03-29", "2025-05-16", "2016-01-27", "2021-03-25")]
    assert df["outstanding_size_inr_mn"].tolist() == [500, 500, 147.5, 147.5]
    assert df["listing_status"].iloc[0] == "Listed"
    assert (df["source_kind"] != "history_current").all()


def test_history_continuation_rows_take_issuer_and_dates(tmp_path):
    path = _workbook(tmp_path / "Annx24.xlsx", {"Securities": [
        ["ANNEXURE 24 - HISTORY OF CREDIT RATING OF ALL OUTSTANDING SECURITIES March 26"],
        ["Sr. No.", "Name of the Issuer", "Sector", "Type of Security", "Outstanding Issue Size (INR millions)",
         "Listing Status (Listed / Proposed to be Listed)", "Initial Rating", "Date of Initial Rating",
         "Rating after 1st Revision", "Date of 1st Revision", "Rating after 2nd Revision", "Date of 2nd Revision",
         "Current Outstanding Rating"],
        [1, "Omicron Digital Limited", "Financial Services", "Non Convertible Debentures", 3807.5, "Listed",
         "IVR A /Positive", D(2020, 2, 22), "IVR A+ /Stable", D(2020, 8, 26), "IVR AA- /Stable", D(2021, 8, 26),
         "IVR AA- /Stable"],
        [None, None, None, "Non Convertible Debentures", 250, "Listed",
         None, None, "IVR A+ /Stable", None, "Withdrawn", None, None],
        [None, None, None, "Non Convertible Debentures", 510, "Listed",
         None, None, None, None, None, None, "IVR AA- /Stable"],
        ["Note:", "Listing status is for the disclosure period"],
    ]})
    df = load_cra_disclosure(path, "Infomerics")
    assert (df["issuer_name"] == "Omicron Digital Limited").all()
    assert (df["sector"] == "Financial Services").all()
    second = df[df["source_row"] == 4]
    assert second["rating"].tolist() == ["IVR A+ /Stable", "IVR Withdrawn"]
    assert second["date"].tolist() == [pd.Timestamp("2020-08-26"), pd.Timestamp("2021-08-26")]
    assert second["action"].tolist() == ["Assigned", "Withdrawn"]
    third = df[df["source_row"] == 5].iloc[0]
    assert third["source_kind"] == "history_current"
    assert third["date"] == pd.Timestamp("2026-03-31")  # "March 26" in the title
    assert third["note"] == "only the current rating is given"
    actions = load_rating_actions(df)
    assert actions["parse_ok"].all() and actions["withdrawn"].sum() == 1


def test_history_brickwork_reaffirmations_and_current_date(tmp_path):
    path = _workbook(tmp_path / "Annx24_History_Securities.xlsx", {"Securities": [
        [],
        [None, None, "BRICKWORK RATINGS INDIA PRIVATE LIMITED,BANGALORE"],
        [None, None, "DETAILS OF OUTSTANDING RATINGS OF FINANCIAL INSTRUMENTS SECURITIES AS OF 31.03.2026"],
        ["S.No", "Name of the Issuer", "Sector", "Type of security etc.)", "Outstanding Issue Size (INR millions)",
         "Listing Status (Listed/Proposedto be listed)", "Initial Rating", "Initial Rating Date",
         "Rating After First Revision", "Date After First Revision", "Rating After Second Revision",
         "Date After Second Revision", "RatingAfterThirdRevision", "Date After Third Revision",
         "CurrentRatingDate", "CurrentRating"],
        [1, "Pi Infra & Utilities Pvt. Ltd.", "Finance", "Non Convertible Debentures", 3.1, "Unlisted",
         "BWR A + (SO)", D(2016, 5, 9), "BWR A + (SO)", D(2017, 5, 9), "Not Reviewed", D(2018, 5, 9),
         "BWR A - (CE)", D(2018, 6, 1), D(2018, 6, 1), "BWR A - (CE)"],
        [2, "Rho Corp Ltd", "Capital Goods", "Non Convertible Debentures", 2000, "Listed",
         "BWR AA", D(2020, 8, 24), "BWR AA", D(2021, 7, 7), "NULL", "NULL", "NULL", "NULL",
         D(2022, 7, 1), "BWR AA"],
    ]})
    df = load_cra_disclosure(path, "Brickwork")
    pi = df[df["issuer_name"] == "Pi Infra & Utilities Pvt. Ltd."]
    assert pi["rating"].tolist() == ["BWR A + (SO)", "BWR A + (SO)", "BWR A - (CE)"]  # as published
    assert pi["action"].tolist() == ["Assigned", "Reaffirmed", "Downgraded"]
    rho = df[df["issuer_name"] == "Rho Corp Ltd"]
    assert rho["source_kind"].tolist() == ["history_initial", "history_revision", "history_current"]
    assert rho["action"].tolist() == ["Assigned", "Reaffirmed", "Reaffirmed"]
    assert rho["date"].iloc[-1] == pd.Timestamp("2022-07-01")
    assert df["issuer_id"].iloc[0] == "NAME:PI INFRA AND UTILITIES"


def test_history_india_ratings_archive_csv(tmp_path):
    header = (b"Sr. no.,Name of the Issuer,Sector,Instrument Type, Outstanding Issue Size (INR Million) ,"
              b"Listing Status (Listed or Unlisted),Initial Rating,Date of initial rating,Rating after 1st revision,"
              b"Date of 1st revision,Rating after 2nd revision,Date of 2nd revision,Current & Outstanding Rating,")
    path = tmp_path / "History H2 2020.csv"
    path.write_bytes(_padded(
        header,
        b"1,Sigma Infra LLP,Others,BLR,1210,Unlisted, Provisional BB+ ,25-Sep-17,-,-,-,-,Provisional BB+,BB+,11,BB+",
        b"2,Tau Spintex India Private Limited,Diversified,BLR,169.5,Unlisted, BB- ,27-Feb-18,B+,7-Jun-19,-,-,"
        b"BB-,BB-,13,B+",
    ))
    df = load_cra_disclosure(path, "IND")
    assert df["rating"].tolist() == ["IND Provisional BB+", "IND BB-", "IND B+", "IND BB-"]
    assert df["source_kind"].tolist() == ["history_initial", "history_initial", "history_revision",
                                          "history_current"]
    assert df["outstanding_size_inr_mn"].tolist() == [1210, 169.5, 169.5, 169.5]
    assert pd.isna(df["date"].iloc[3])
    dated = load_cra_disclosure(path, "IND", as_of="2020-03-31")
    assert dated["date"].iloc[3] == pd.Timestamp("2020-03-31")
    actions = load_rating_actions(dated)
    assert actions["parse_ok"].all()
    assert actions["provisional"].iloc[0] and actions["grade"].tolist() == ["BB+", "BB-", "B+", "BB-"]


def test_issuer_names_and_map(care_movement, tmp_path):
    radiant = "Radiant Life Care Private Limited (formerly, Halcyon Finance & Capital Advisors Pvt. Ltd.)"
    assert normalize_issuer_name(radiant) == "radiant life care"
    assert normalize_issuer_name("ELEGANZ INTERIORS LIMITED (ERSTWHILE ELEGANZ INTERIORS PRIVATE LIMITE") == \
        "eleganz interiors"
    assert normalize_issuer_name("Zyphar's Pharmaceutics Pvt. Ltd.") == "zyphars pharmaceutics"
    assert normalize_issuer_name("The Il&Fs Transportation Networks Ltd") == "il and fs transportation networks"
    assert normalize_issuer_name("Acuité Ratings & Research Limited") == "acuite ratings and research"
    assert normalize_issuer_name("M/s. Foo Traders") == "foo traders"
    assert normalize_issuer_name(None) == ""

    with pytest.raises(ValueError, match="several issuer_ids"):
        read_issuer_map({"Alpha Finance Ltd": "A1", "ALPHA FINANCE PRIVATE LIMITED": "A2"})
    with pytest.raises(ValueError, match="name, issuer_id"):
        read_issuer_map(pd.DataFrame({"issuer": ["x"], "id": ["y"]}))

    map_path = tmp_path / "issuer_map.csv"
    map_path.write_text("name,issuer_id\nAlpha Finance Pvt Ltd,U65910MH2000PTC000001\n")
    df = load_cra_disclosure(care_movement, "CARE", issuer_map=map_path)
    assert df["issuer_id"].tolist() == ["U65910MH2000PTC000001", "NAME:BETA INFRA", "NAME:GAMMA STEEL"]
    report = unmapped_issuers(df)
    assert report["issuer_id"].tolist() == ["NAME:BETA INFRA", "NAME:GAMMA STEEL"]
    assert report["agencies"].tolist() == ["CARE", "CARE"] and report["rows"].tolist() == [1, 1]
    assert unmapped_issuers(df.iloc[:1]).empty


def _actions(rows, kind):
    df = pd.DataFrame(rows, columns=["date", "issuer_id", "agency", "instrument", "rating"])
    return df.assign(date=pd.to_datetime(df["date"]), source_kind=kind)


def test_combine_drops_repeats_across_snapshots():
    h1 = _actions([("2024-01-05", "NAME:X", "CARE", "NCD", "CARE A; Stable"),
                   ("2025-02-01", "NAME:X", "CARE", "NCD", "CARE A-; Stable")], "history_revision")
    h2 = _actions([("2024-01-05", "NAME:X", "CARE", "NCD", "CARE A; Stable"),
                   ("2025-02-01", "NAME:X", "CARE", "NCD", "CARE A-; Stable"),
                   ("2025-11-01", "NAME:X", "CARE", "NCD", "CARE BBB+; Stable"),
                   ("2025-11-01", "NAME:X", "CARE", "Term Loan", "CARE BBB+; Stable")], "history_revision")
    mv = _actions([("2025-11-01", "NAME:X", "CARE", "ncd", "CARE  BBB+;  Stable")], "movement")
    out = combine_rating_actions([h1, h2, mv])
    assert len(out) == 4
    assert out["source_kind"].tolist() == ["history_revision"] * 2 + ["history_revision", "movement"]
    assert out["instrument"].tolist() == ["NCD", "NCD", "Term Loan", "ncd"]
    assert combine_rating_actions([]).columns.tolist() == list(ACTION_COLUMNS)


def test_load_several_files_and_summary(care_movement, care_history, tmp_path):
    later = _workbook(tmp_path / "History-later.xlsx", {"Sheet1": [
        ["Annexure V- History of Credit Rating of all Outstanding Securities as on March 31, 2026"],
        CARE_HIST_HEADER,
        [1, "Kappa Realty Private Limited", "Realty", "Realty", "Non Convertible Debentures", 900, "Listed",
         "CARE BBB-; Stable", D(2024, 11, 21), "CARE BB+; Stable; ISSUER NOT COOPERATING*", D(2025, 12, 3),
         "CARE BB; Stable; ISSUER NOT COOPERATING*", D(2026, 2, 2), "CARE BB; Stable; ISSUER NOT COOPERATING*"],
    ]})
    combined = load_cra_disclosures([care_history, later, care_movement], "CARE")
    kappa = combined[combined["issuer_id"] == "NAME:KAPPA REALTY"]
    assert kappa["date"].tolist() == [pd.Timestamp(d) for d in ("2024-11-21", "2025-12-03", "2026-02-02")]

    summary = disclosure_summary(combined).set_index(["source_file", "source_kind"])
    move = summary.loc[(care_movement.name, "movement")]
    assert move["rows"] == 3 and move["parse_ok_share"] == 1.0 and move["unmapped_issuers"] == 3
    assert move["date_min"] == pd.Timestamp("2025-11-06")
    assert summary.loc[(later.name, "history_revision"), "rows"] == 1
    assert disclosure_summary(combined.iloc[:0]).empty


def test_errors(care_history, tmp_path):
    with pytest.raises(ValueError, match="no movement table"):
        load_cra_disclosure(care_history, "CARE", kind="movement")
    with pytest.raises(ValueError, match="unknown agency"):
        load_cra_disclosure(care_history, "Moody's")
    with pytest.raises(ValueError, match="kind must be"):
        load_cra_disclosure(care_history, "CARE", kind="defaults")
    defaults = _workbook(tmp_path / "Details_of_Defaults.xlsx", {"Sheet1": [
        ["Sr. No.", "Name of the Issuer", "Sector", "Instrument Type", "Rating prior to\nDefault", "Trigger Event",
         "Date of Default\nRecognition"],
        [1, "Upsilon Ltd", "Realty", "NCD", "CARE BB; Stable", "Delay", D(2026, 1, 1)],
    ]})
    with pytest.raises(ValueError, match="no movement or history table"):
        load_cra_disclosure(defaults, "CARE")
    assert len(load_cra_disclosure(care_history, "CARE", kind="history")) == 8


def test_date_and_snapshot_parsing():
    assert cra._to_date(D(2026, 3, 31, 15, 0)) == (pd.Timestamp("2026-03-31"), None)
    assert cra._to_date(46112) == (pd.Timestamp("2026-03-31"), None)  # Excel serial
    assert cra._to_date("7-Jun-19") == (pd.Timestamp("2019-06-07"), None)
    assert cra._to_date("31.03.2026") == (pd.Timestamp("2026-03-31"), None)
    assert cra._to_date("-") == (None, None) and cra._to_date(None) == (None, None)
    assert cra._to_date("CARE B; Stable") == (None, "CARE B; Stable")
    assert cra._as_of(["DETAILS OF OUTSTANDING RATINGS AS OF 31.03.2026"]) == pd.Timestamp("2026-03-31")
    assert cra._as_of(["S.No", "Outstanding as on 31 Mar 2026"]) == pd.Timestamp("2026-03-31")
    assert cra._as_of(["HISTORY OF CREDIT RATING OF ALL OUTSTANDING SECURITIES March 26"]) == \
        pd.Timestamp("2026-03-31")
    assert cra._as_of(["Annexure V- History of Credit Rating of all Outstanding Securities"]) is None
