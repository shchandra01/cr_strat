import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from cr_strat.cli import main

CONFIG = Path(__file__).resolve().parents[1] / "config" / "mandates.toml"


def test_end_to_end(tmp_path, isin):
    bond = isin("INE111A0701")
    pd.DataFrame([
        {"date": "2026-01-05", "issuer_id": "X1", "agency": "CRISIL", "instrument": "NCD", "rating": "CRISIL AA+/Stable"},
        {"date": "2026-01-06", "issuer_id": "X1", "agency": "ICRA", "instrument": "NCD", "rating": "[ICRA]AA+ (Stable)"},
        {"date": "2026-09-20", "issuer_id": "X1", "agency": "CRISIL", "instrument": "NCD", "rating": "CRISIL AA/Negative"},
    ]).to_csv(tmp_path / "actions.csv", index=False)
    pd.DataFrame([{"isin": bond, "issuer_id": "X1", "instrument_class": "BOND", "face_value_inr": 1e5,
                   "outstanding_face_inr": 5e9}]).to_csv(tmp_path / "securities.csv", index=False)
    (tmp_path / "NAVAll.txt").write_text(
        "Open Ended Schemes(Debt Scheme - Corporate Bond Fund)\n\nABC Mutual Fund\n\n"
        "1;INF000A01011;-;ABC Corporate Bond Fund - Direct Plan - Growth;30.1;10-Oct-2026\n"
    )
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Scheme Name: ABC Corporate Bond Fund"])
    ws.append(["Portfolio as on 15-Sep-2026"])
    ws.append(["Name of the Instrument", "ISIN", "Rating", "Quantity", "Market Value (Rs. in Lakhs)", "% to Net Assets"])
    ws.append(["X1 NCD", bond, "CRISIL AA+", 1000, 1000.0, 5.0])
    wb.save(tmp_path / "abc.xlsx")

    out = tmp_path / "out"
    assert main(["rating-events", "--actions", str(tmp_path / "actions.csv"), "--mandates", str(CONFIG),
                 "--out-dir", str(out)]) == 0
    assert main(["parse-mf", str(tmp_path / "abc.xlsx"), "--amc", "ABC Mutual Fund", "--out", str(out / "holdings.csv"),
                 "--navall", str(tmp_path / "NAVAll.txt")]) == 0
    assert main(["ownership", "--events", str(out / "rating_events.csv"), "--holdings", str(out / "holdings.csv"),
                 "--securities", str(tmp_path / "securities.csv"), "--mandates", str(CONFIG),
                 "--out", str(out / "event_ownership.csv")]) == 0

    result = pd.read_csv(out / "event_ownership.csv")
    cbf = result[result["mandate"] == "mf_corporate_bond_fund"].iloc[0]
    assert cbf["constrained_share"] == 1000 * 1e5 / 5e9


def test_price_pipeline(tmp_path, isin):
    t = pd.Timestamp("2025-06-02")
    issuers = {"E": isin("INE500E0701"), "P1": isin("INE501P0701"), "P2": isin("INE502P0701"), "P3": isin("INE503P0701")}
    actions = [{"date": "2025-01-01", "issuer_id": k, "rating": "CRISIL AA+/Stable", "instrument": "NCD"} for k in issuers]
    actions.append({"date": "2025-06-02", "issuer_id": "E", "rating": "CRISIL AA/Negative", "instrument": "NCD"})
    pd.DataFrame(actions).to_csv(tmp_path / "actions.csv", index=False)
    pd.DataFrame([{"isin": v, "issuer_id": k, "instrument_class": "BOND", "maturity_date": "2027-06-30"}
                  for k, v in issuers.items()]).to_csv(tmp_path / "securities.csv", index=False)
    days = pd.date_range("2025-03-01", "2025-09-30", freq="D")
    pd.DataFrame([{"date": d.strftime("%Y-%m-%d"), "tenor_years": tenor, "yield_pct": y}
                  for d in days for tenor, y in ((1, 6.0), (5, 6.4))]).to_csv(tmp_path / "gsec.csv", index=False)

    lines = ["Trade Date,ISIN,Description,Deal Size (Rs. Lakhs),Price,Yield,Settlement Date"]
    for offset in (-20, -5, 2, 10, 60):
        d = t + pd.Timedelta(days=offset)
        for k, v in issuers.items():
            y = 8.0 if k != "E" else (8.2 if offset < 0 else 9.2)
            lines.append(f"{d:%d-%b-%Y},{v},{k} NCD,1000,100,{y},{d:%d-%b-%Y}")
    (tmp_path / "nse.csv").write_text("\n".join(lines))

    out = tmp_path / "out"
    assert main(["rating-events", "--actions", str(tmp_path / "actions.csv"), "--mandates", str(CONFIG),
                 "--out-dir", str(out)]) == 0
    assert main(["load-trades", str(tmp_path / "nse.csv"), "--venue", "NSE", "--out", str(out / "trades.csv")]) == 0
    assert main(["price-panel", "--trades", str(out / "trades.csv"), "--securities", str(tmp_path / "securities.csv"),
                 "--benchmark", str(tmp_path / "gsec.csv"), "--rating-actions", str(tmp_path / "actions.csv"),
                 "--out-dir", str(out)]) == 0
    assert main(["event-prices", "--events", str(out / "rating_events.csv"), "--panel", str(out / "price_panel.csv"),
                 "--out-dir", str(out)]) == 0

    events = pd.read_csv(out / "event_prices.csv")
    cbf = events[events["mandate"] == "mf_corporate_bond_fund"].iloc[0]
    assert cbf["issuer_id"] == "E" and cbf["n_bonds"] == 1
    assert cbf["first_post_abn_bps"] == pytest.approx(100, abs=1)
    assert pd.isna(cbf["p60_gap_cur_bps"])  # no AA-rated peers, so no current-rating benchmark
    assert (out / "peer_spreads_weekly.csv").exists()


def _fields(text: str) -> dict:
    rows = [re.split(r"\s{2,}", line.strip(), maxsplit=1) for line in text.splitlines() if line.strip()]
    return {r[0]: r[1] for r in rows if len(r) == 2}


def test_bond_price(capsys):
    # 6.94% GS 2036, RBI auction of 13 Nov 2026: cut-off price 97.43, yield 7.3203%, settled 16 Nov.
    args = ["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "2026-11-16"]
    assert main([*args, "--price", "97.43"]) == 0
    out = _fields(capsys.readouterr().out)
    assert float(out["yield (%)"]) == pytest.approx(7.320279, abs=1e-6)
    assert float(out["accrued interest"]) == pytest.approx(6.94 * 5 / 360, abs=1e-6)
    assert out["previous coupon"] == "2026-11-11" and out["next coupon"] == "2027-05-11"
    for key in ("clean price", "dirty price", "modified duration", "dv01 (per 100)", "convexity"):
        assert float(out[key]) > 0

    assert main([*args, "--yield", "7.3203", "--cash-flows"]) == 0
    text = capsys.readouterr().out
    assert f"{float(_fields(text)['clean price']):.4f}" == "97.4299"
    assert "2036-05-11  redemption" in text

    assert main(["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "2036-05-11",
                 "--yield", "7"]) == 2
    assert "on or after maturity" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([*args])  # neither a yield nor a price


@pytest.mark.parametrize("extra, message", [
    (["--coupon", "7", "--maturity", "2036-05-11", "--yield", "7", "--conventions", "strips"],
     "STRIPS have no coupon; pass --coupon 0"),
    (["--coupon", "0", "--maturity", "2036-05-11", "--yield", "7", "--conventions", "strips",
      "--issue-date", "2025-09-01", "--first-coupon-date", "2026-05-11"], "does not apply to a STRIP"),
    (["--coupon", "6.94", "--maturity", "2036-13-01", "--yield", "7"], "--maturity: month must be in 1..12"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--yield", "7", "--issue-date", "2026-02-30"],
     "--issue-date: day is out of range for month"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--price", "0"], "clean price must be a positive number"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--price", "inf"], "clean price must be a positive number"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--yield", "nan"], "yield must be a finite number"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--yield", "-250"], "yield must be above -200%"),
])
def test_bond_price_rejects_bad_input(capsys, extra, message):
    assert main(["bond-price", "--settlement", "2026-11-16", *extra]) == 2
    assert message in capsys.readouterr().err


def test_bond_price_settlement_flag_is_named(capsys):
    assert main(["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "16-11-2026",
                 "--yield", "7"]) == 2
    assert "--settlement: Invalid isoformat string" in capsys.readouterr().err


def test_bond_price_options(capsys):
    # A STRIP is priced as a zero-coupon bond.
    assert main(["bond-price", "--coupon", "0", "--maturity", "2036-05-11", "--settlement", "2026-11-16",
                 "--yield", "7", "--conventions", "strips"]) == 0
    out = _fields(capsys.readouterr().out)
    assert float(out["dirty price"]) == pytest.approx(100 / 1.035 ** (18 + 175 / 180), abs=1e-6)
    assert "STRIPS conventions" in out["bond"] and float(out["accrued interest"]) == 0
    # A long first coupon, from the issue date to the first coupon date.
    assert main(["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "2025-10-01",
                 "--yield", "7.1", "--issue-date", "2025-09-01", "--first-coupon-date", "2026-05-11"]) == 0
    out = _fields(capsys.readouterr().out)
    assert out["previous coupon"] == "2025-09-01" and out["next coupon"] == "2026-05-11"
    assert float(out["accrued interest"]) == pytest.approx(6.94 * 30 / 360, abs=1e-6)
    assert main(["bond-price", "--coupon", "5.91", "--maturity", "2028-06-30", "--settlement", "2028-02-15",
                 "--yield", "6", "--final-period", "compound"]) == 0
    assert "last period compound" in capsys.readouterr().out
    # 31 Aug maturity two days out: was exit 2 ("no yield prices these cash flows").
    assert main(["bond-price", "--coupon", "7", "--maturity", "2035-08-31", "--settlement", "2035-08-29",
                 "--price", "100"]) == 0
    out = _fields(capsys.readouterr().out)
    assert float(out["yield (%)"]) == pytest.approx(-6.762015, abs=1e-6)
    assert float(out["modified duration"]) > 0 and float(out["dv01 (per 100)"]) > 0


def _release_files(tmp_path):
    import rbi_fixtures as fx

    src = tmp_path / "rbi"
    src.mkdir()
    goi = fx.release_page("Government Stock - Full Auction Results", "Nov 13, 2026",
                          fx.body("", fx.results_table([fx.GOI_6_94])), prid=63970)
    (src / "goi_full_63970.html").write_text(goi, encoding="utf-8")
    switch = fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504")
    items = [dict(title="Result: Conversion/Switch Auction of Government of India Securities", prid=63982,
                  pub="Mon, 16 Nov 2026 14:55:00", html=switch),
             dict(title="Government Stock - Auction Results: Cut-off", prid=63969,
                  pub="Fri, 13 Nov 2026 13:35:00", html=fx.body("Tables."))]
    (src / "pr_feed_20261116.xml").write_bytes(fx.feed(items))
    (src / "strips_3973.html").write_text(fx.blocked_page(), encoding="utf-8")
    return src


def test_rbi_releases_appends_idempotently(tmp_path, capsys):
    src = _release_files(tmp_path)
    out = tmp_path / "out"
    files = [str(p) for p in sorted(src.iterdir())]
    assert main(["rbi-releases", *files, "--out-dir", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "no parser for gsec_cutoff: not sampled; the full result follows" in printed and "Support ID" in printed
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    assert set(first) == {"gsec_result.csv", "switch_result.csv", "releases.csv"}
    assert main(["rbi-releases", *files, "--out-dir", str(out)]) == 0
    assert main(["rbi-releases", files[1], "--out-dir", str(out)]) == 0  # one file again: no duplicates
    assert {p.name: p.read_bytes() for p in out.iterdir()} == first
    goi = pd.read_csv(out / "gsec_result.csv")
    assert len(goi) == 1 and goi.loc[0, "comp_accepted_cr"] == 33947.718 and goi.loc[0, "cutoff_yield"] == 7.3203
    switch = pd.read_csv(out / "switch_result.csv")
    assert len(switch) == 3 and switch["published"].eq("2026-11-16T14:55:00").all()
    index = pd.read_csv(out / "releases.csv")
    assert sorted(index["status"]) == ["parsed", "parsed", "skipped", "skipped"]


def test_rbi_releases_keeps_the_feed_time_when_the_page_is_read_alone(tmp_path):
    import rbi_fixtures as fx

    src = _release_files(tmp_path)
    out = tmp_path / "out"
    assert main(["rbi-releases", str(src / "pr_feed_20261116.xml"), "--out-dir", str(out)]) == 0
    switch = pd.read_csv(out / "switch_result.csv")
    assert switch["published"].eq("2026-11-16T14:55:00").all() and switch["source_file"].str.endswith("#1").all()
    page = src / "switch_63982.html"  # the same release saved as a page: it has no publication time
    page.write_text(fx.release_page(
        "Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026",
        fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504"), prid=63982), encoding="utf-8")
    assert main(["rbi-releases", str(page), "--out-dir", str(out)]) == 0
    again = pd.read_csv(out / "switch_result.csv")
    assert again["source_file"].eq("switch_63982.html").all() and len(again) == 3
    assert again["published"].eq("2026-11-16T14:55:00").all()  # not blanked by the page
    index = pd.read_csv(out / "releases.csv").set_index("source_file")
    assert pd.isna(index.loc["switch_63982.html", "published"])  # the index says what each copy holds
    assert index.loc["pr_feed_20261116.xml#1", "published"] == "2026-11-16T14:55:00"


def test_rbi_releases_uses_stored_greenshoe(tmp_path):
    import rbi_fixtures as fx

    out = tmp_path / "out"
    result = fx.release_page("Government Stock - Full Auction Results", "Nov 20, 2026", fx.body("", fx.results_table(
        [dict(fx.GOI_SECOND, notified="6,000", uw="6,000", uw_acc="6,000")])), prid=64001)
    (tmp_path / "result.html").write_text(result, encoding="utf-8")
    # 14,000 accepted on 6,000 notified: refused while no announcement gives the greenshoe
    assert main(["rbi-releases", str(tmp_path / "result.html"), "--out-dir", str(out)]) == 0
    assert not (out / "gsec_result.csv").exists()
    announcement = fx.release_page("Auction of Government of India Dated Securities", "Nov 16, 2026",
                                   fx.body(fx.GSEC_ANNOUNCEMENT_TEXT.replace("2,000", "8,000"),
                                           fx.gsec_announcement_table()), prid=63983)
    (tmp_path / "announcement.html").write_text(announcement, encoding="utf-8")
    assert main(["rbi-releases", str(tmp_path / "announcement.html"), "--out-dir", str(out)]) == 0
    assert main(["rbi-releases", str(tmp_path / "result.html"), "--out-dir", str(out)]) == 0
    goi = pd.read_csv(out / "gsec_result.csv")
    assert goi["greenshoe_cr"].tolist() == [8000.0] and goi["total_accepted_cr"].tolist() == [14000.0]
    index = pd.read_csv(out / "releases.csv").set_index("release_id")
    assert index.loc[64001, "status"] == "parsed" and index.loc[63983, "rows"] == 2


def test_sovereign_master_keeps_dated_snapshots(tmp_path):
    import rbi_fixtures as fx

    (tmp_path / "goi_1116.html").write_text(fx.goi_list_page(), encoding="utf-8")
    (tmp_path / "goi_1113.html").write_text(fx.goi_list_page(as_of="November 13, 2026", rows=fx.GOI_LIST_ROWS[:3]),
                                            encoding="utf-8")
    (tmp_path / "far.html").write_text(fx.far_page(), encoding="utf-8")
    snapshots, out = tmp_path / "goi_outstanding.csv", tmp_path / "master.csv"
    args = ["sovereign-master", "--far", str(tmp_path / "far.html"), "--snapshots", str(snapshots),
            "--out", str(out)]
    assert main([*args, "--goi-list", str(tmp_path / "goi_1113.html")]) == 0
    assert main([*args, "--goi-list", str(tmp_path / "goi_1116.html"), str(tmp_path / "goi_1113.html")]) == 0
    first = snapshots.read_bytes(), out.read_bytes()
    assert main([*args, "--goi-list", str(tmp_path / "goi_1116.html")]) == 0
    assert (snapshots.read_bytes(), out.read_bytes()) == first
    stacked = pd.read_csv(snapshots)
    assert stacked.groupby("as_of").size().to_dict() == {"2026-11-13": 3, "2026-11-16": len(fx.GOI_LIST_ROWS)}
    master = pd.read_csv(out).set_index("isin")
    assert len(master) == len({r[2] for r in fx.GOI_LIST_ROWS} | {r[1] for r in fx.FAR_ROWS})
    assert master.loc["IN0020140060", "first_listed"] == "2026-11-13" and master.loc["IN0020260025", "far"]
    assert master.loc["IN0020180488", "matured"] and not master.loc["IN0020180488", "in_latest_list"]


def _ledger_inputs(tmp_path):
    """Saved RBI pages and lists (synthetic), FIMMDA workbooks and a Handbook page, and the CSVs they give."""
    import datetime as dt

    import auction_fixtures as af
    import rbi_fixtures as fx

    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "goi_63970.html").write_text(fx.release_page(
        "Government Stock - Full Auction Results", "Nov 13, 2026", fx.body("", fx.results_table([fx.GOI_6_94]))),
        encoding="utf-8")
    (pages / "switch_63982.html").write_text(fx.release_page(
        "Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026",
        fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504"), prid=63982), encoding="utf-8")
    assert main(["rbi-releases", *map(str, sorted(pages.iterdir())), "--out-dir", str(tmp_path / "rbi")]) == 0
    (tmp_path / "goi.html").write_text(fx.goi_list_page(), encoding="utf-8")
    (tmp_path / "far.html").write_text(fx.far_page(), encoding="utf-8")
    assert main(["sovereign-master", "--goi-list", str(tmp_path / "goi.html"), "--far", str(tmp_path / "far.html"),
                 "--snapshots", str(tmp_path / "snapshots.csv"), "--out", str(tmp_path / "master.csv")]) == 0
    gsec = af.gsec_workbook(tmp_path / "Gsec_Auction_data_2026_2027.xlsx", {"2026-2027": [
        (dt.datetime(2026, 5, 8), "6.94% GS 2036", 32000, 31990.5, 100, 6.94)]})
    sdl = af.sdl_workbook(tmp_path / "SDL_Auction_Data.xlsx", {"January": [
        ("02-01-2024", "IN1020230679", "07.74 AP SGS 2032 03 JAN", "03-01-2032", 7.74, 7.7151, "NEW", 7.74, "AP",
         "#REF!", 1000, 7740)]})
    (tmp_path / "hbs.html").write_text(af.handbook_page([
        ("08-May-2026", "11-May-2026", "32000", "10.00", "300", "60000", "3", "9", "120", "31991", "3", "9", "0",
         "100/6.94", "6.94% GS 2036")], fy="2026-27"), encoding="utf-8")
    return ["--fimmda-gsec", str(gsec), "--fimmda-sdl", str(sdl), "--handbook", str(tmp_path / "hbs.html"),
            "--releases", str(tmp_path / "rbi"), "--master", str(tmp_path / "master.csv"),
            "--snapshots", str(tmp_path / "snapshots.csv")]


def test_auctions_command_writes_table_ledger_and_report(tmp_path, capsys):
    from cr_strat.auctions import read_auctions
    from cr_strat.issuance import read_ledger

    args = _ledger_inputs(tmp_path)
    out = tmp_path / "out"
    assert main(["auctions", *args, "--out-dir", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "3 auction rows" in printed and "validation:" in printed
    assert {p.name for p in out.iterdir()} == {
        "auctions.csv", "switches.csv", "ledger.csv", "ledger_coverage.csv", "supply_monthly.csv",
        "settlement_evidence.csv", "reconciliation.csv", "validation.csv"}
    a = read_auctions(out / "auctions.csv").set_index("auction_date")
    new, nov = a.loc[pd.Timestamp("2026-05-08")], a.loc[pd.Timestamp("2026-11-13")]
    assert (new["isin"], new["issue_type"], new["settlement_source"], new["accepted_cr"]) == (
        "IN0020260025", "new", "handbook", 31999.5)  # FIMMDA competitive plus the Handbook's non-competitive
    assert (nov["sources"], nov["repricing"], nov["settlement_date"], nov["accepted_cr"]) == (
        "rbi_result", "reproduced", pd.Timestamp("2026-11-16"), 34000.0)
    ledger = read_ledger(out / "ledger.csv")
    mine = ledger[ledger["key"] == "IN0020260025"]
    assert mine["event"].tolist() == ["issue", "reissue", "maturity"]
    assert mine["amount_cr"].tolist()[:2] == [31999.5, 34000.0]
    assert mine["amount_basis"].iloc[-1] == "snapshot" and mine["amount_cr"].iloc[-1] == -272000
    assert set(ledger.loc[ledger["source"] == "rbi_switch", "event"]) == {"switch_in", "switch_out"}
    rec = pd.read_csv(out / "reconciliation.csv").set_index("key")
    assert rec.loc["IN0020260025", "ledger_cr"] == 65999.5 and rec.loc["IN0020260025", "rbi_cr"] == 272000
    report = pd.read_csv(out / "validation.csv")
    assert {"reconciliation", "fimmda_file"} <= set(report["check"])
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    assert main(["auctions", *args, "--out-dir", str(out)]) == 0  # the same inputs give the same files
    assert {p.name: p.read_bytes() for p in out.iterdir()} == first


def test_outstanding_command(tmp_path, capsys):
    args = _ledger_inputs(tmp_path)
    out = tmp_path / "out"
    assert main(["auctions", *args, "--out-dir", str(out)]) == 0
    capsys.readouterr()
    ledger, snap = str(out / "ledger.csv"), str(tmp_path / "snapshots.csv")
    assert main(["outstanding", "--ledger", ledger, "--date", "2026-11-13", "--snapshot", snap, "--by-bucket",
                 "--out", str(tmp_path / "o.csv")]) == 0
    printed = capsys.readouterr().out
    assert "2026-11-13: " in printed and "with a complete history" in printed
    o = pd.read_csv(tmp_path / "o.csv").set_index("key")
    assert o.loc["IN0020260025", "outstanding_cr"] == 272000 - 34000 and o.loc["IN0020260025", "method"] == "backward"
    assert (tmp_path / "o_by_bucket.csv").exists()
    assert main(["outstanding", "--ledger", ledger, "--date", "2026-06-30", "--out", str(tmp_path / "f.csv")]) == 0
    forward = pd.read_csv(tmp_path / "f.csv").set_index("key")
    assert forward.loc["IN0020260025", "outstanding_cr"] == 31999.5 and forward.loc["IN0020260025", "method"] == \
        "forward"
    assert main(["outstanding", "--ledger", ledger, "--date", "13/11/2026", "--out", str(tmp_path / "x.csv")]) == 2
    assert "--date" in capsys.readouterr().err


def test_rbi_releases_output_does_not_depend_on_the_file_order(tmp_path):
    import rbi_fixtures as fx

    src = _release_files(tmp_path)
    (src / "switch_63982.html").write_text(fx.release_page(  # the feed's switch release, also saved as a page
        "Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026",
        fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504"), prid=63982), encoding="utf-8")
    files = [str(p) for p in sorted(src.iterdir())]
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["rbi-releases", *files, "--out-dir", str(a)]) == 0
    assert main(["rbi-releases", *reversed(files), "--out-dir", str(b)]) == 0
    first = {p.name: p.read_bytes() for p in a.iterdir()}
    assert {p.name: p.read_bytes() for p in b.iterdir()} == first
    assert main(["rbi-releases", *reversed(files), "--out-dir", str(a)]) == 0  # a rerun in the other order
    assert {p.name: p.read_bytes() for p in a.iterdir()} == first
    switch = pd.read_csv(a / "switch_result.csv")
    assert switch["source_file"].eq("switch_63982.html").all()  # the saved page, with the feed's time
    assert switch["published"].eq("2026-11-16T14:55:00").all()
    index = pd.read_csv(a / "releases.csv").set_index("source_file")
    assert index.loc["pr_feed_20261116.xml#1", "status"] == "duplicate"


def test_sovereign_master_names_an_extra_row_without_an_isin(tmp_path, capsys):
    extra = tmp_path / "sdl_isins.csv"
    extra.write_text("isin,name\nIN2020230123,7.74% KL SGS 2033\n,8.00% KL SGS 2034\n", encoding="utf-8")
    assert main(["sovereign-master", "--extra", str(extra), "--out", str(tmp_path / "m.csv")]) == 2
    err = capsys.readouterr().err
    assert "extra listing sdl_isins.csv" in err and "row 3 ('8.00% KL SGS 2034')" in err
    assert not (tmp_path / "m.csv").exists()


def test_auctions_command_reads_competitive_only_rows(tmp_path):
    import datetime as dt

    import auction_fixtures as af
    from cr_strat.issuance import read_ledger

    gsec = af.gsec_workbook(tmp_path / "g.xlsx", {"2025-2026": [  # no Handbook line: competitive amount only
        (dt.datetime(2025, 7, 4), "New GS 2040", 16000, 15990.554, 100, 6.68)]})
    for choice, amount, basis in (("notified", 16000.0, "notified_assumed_full"),
                                  ("accepted", 15990.554, "competitive_only")):
        out = tmp_path / choice
        assert main(["auctions", "--fimmda-gsec", str(gsec), "--competitive-only", choice, "--out-dir", str(out)]) == 0
        issue = read_ledger(out / "ledger.csv").iloc[0]
        assert (issue["amount_cr"], issue["amount_basis"]) == (amount, basis)


def _fixed_clock(monkeypatch, start):
    """Make the commands read ``start`` from the clock; the returned list's first item moves it."""
    import cr_strat.cli as cli

    clock = [start]
    monkeypatch.setattr(cli, "_clock", lambda: clock[0])
    return clock


def test_rbi_store_poll_exit_codes_and_one_line_messages(tmp_path, capsys, monkeypatch):
    import datetime as dt

    import rbi_fixtures as fx

    from cr_strat import rbi_store

    feed = fx.feed([fx.mmo_item(fx.mmo_saturday()), fx.mmo_item(fx.mmo_friday())])
    answers = [rbi_store.FeedResponse(200, {"etag": 'W/"a:0"'}, feed), rbi_store.FeedResponse(418, {}, b""),
               rbi_store.FeedResponse(503, {}, b"")]
    sent = []

    def transport(url, headers, timeout):  # stands in for the network
        sent.append((url, headers))
        return answers.pop(0)

    monkeypatch.setattr(rbi_store, "urllib_transport", transport)
    clock = _fixed_clock(monkeypatch, dt.datetime(2026, 11, 16, 4, 0, tzinfo=dt.timezone.utc))
    store = str(tmp_path / "store")
    expected = [  # (minutes later, extra arguments, exit code, stream, message)
        (0, [], 0, "out", "new: 2 new releases (63977, 63980), 0 revised, 0 repaired, 0 failed; 2 items in the feed"),
        (10, [], 0, "out", "too soon: the last request was at 2026-11-16T04:00:00Z (10 min ago); the minimum interval "
                           "is 25 min"),
        (30, [], 3, "err", "blocked: HTTP 418; no request before 2026-11-17T02:30:00Z (08:00 India time the next day); "
                           "a block of the next request stops the poller"),
        (60, [], 3, "err", "cooling down after HTTP 418 at 2026-11-16T04:30:00Z: no request before "
                           "2026-11-17T02:30:00Z"),
        (400, ["--resume"], 1, "err",  # the operator's decision
         "error: server error: HTTP 503; the next poll tries again"),
    ]
    start = clock[0]
    for minutes, extra, code, stream, message in expected:
        clock[0] = start + dt.timedelta(minutes=minutes)
        assert main(["rbi-store-poll", "--store", store, *extra]) == code
        captured = capsys.readouterr()
        assert getattr(captured, stream) == f"rbi-store-poll: {message}\n"
        assert getattr(captured, "err" if stream == "out" else "out") == ""
    assert len(sent) == 3 and all(url == rbi_store.FEED_URL for url, _ in sent)
    assert sent[1][1]["If-None-Match"] == 'W/"a:0"'

    page = tmp_path / "mmo_63981.html"
    page.write_text(fx.mmo_page(fx.mmo_sunday()), encoding="utf-8")
    assert main(["rbi-store-ingest", str(page), "--store", store]) == 0
    assert capsys.readouterr().out == ("rbi-store-ingest: 1 release in 1 copy from 1 input, 1 new, 0 revised, "
                                       "0 repaired, 0 failed; 4 files written\n")  # with the page's raw copy
    assert main(["rbi-store-status", "--store", store]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "releases: 3 (3 parsed); ids 63977 to 63981; last change 2026-11-16T16:10:00" in lines
    assert "possible gaps, days without Money Market Operations: none" in lines
    assert any(line.startswith("poller: last request 2026-11-16T10:40:00Z: server_error (HTTP 503)") for line in lines)
    daily = pd.read_csv(tmp_path / "store" / "derived" / "money_market_daily.csv")
    assert daily["as_on_date"].tolist() == ["2026-11-13", "2026-11-14", "2026-11-15"]
    assert daily["public_from_source"].tolist() == ["feed", "feed", "release_date"]


def _documented_grep(heading):
    """The one ``grep -E '...'`` pattern of the section of data/README.md that starts with ``heading``."""
    readme = (Path(__file__).resolve().parents[1] / "data" / "README.md").read_text(encoding="utf-8")
    section = readme.split(f"\n{heading}", 1)[1].split("\n## ", 1)[0]
    patterns = re.findall(r"grep -E\s+'([^']+)'", section)
    assert len(patterns) == 1, (heading, patterns)
    return patterns[0]


def test_the_documented_daily_grep_flags_every_poll_that_exits_non_zero_and_no_other(tmp_path, capsys, monkeypatch):
    import datetime as dt
    import json

    import rbi_fixtures as fx

    from cr_strat import rbi_store

    documented = re.compile(_documented_grep("## RBI operations store"))
    feed = fx.feed([fx.mmo_item(fx.mmo_saturday()), fx.mmo_item(fx.mmo_friday())])
    broken = fx.mmo_friday()
    broken["net_total"] = "-1,70,000.00"
    good = rbi_store.FeedResponse(200, {"etag": 'W/"a:0"'}, feed)
    page = b"<html><head><title>Maintenance</title></head><body><p>Back soon</p></body></html>"
    start = dt.datetime(2026, 11, 16, 4, 0, tzinfo=dt.timezone.utc)
    clock = _fixed_clock(monkeypatch, start)
    seen = {}

    def poll(name, answer, hours=0.0, *extra):
        monkeypatch.setattr(rbi_store, "urllib_transport", lambda url, headers, timeout: answer)
        clock[0] = start + dt.timedelta(hours=hours)
        code = main(["rbi-store-poll", "--store", str(tmp_path / name), *extra])
        captured = capsys.readouterr()
        lines = (captured.out + captured.err).splitlines()
        seen.setdefault(code, []).append(lines[0])
        assert bool(documented.search("\n".join(lines))) == (code != 0), (code, lines)

    poll("ok", good)  # new
    poll("ok", good, 0.2)  # too soon
    poll("ok", good, 1)  # unchanged
    poll("ok", rbi_store.FeedResponse(304, {}, b""), 2)  # not modified
    poll("look", rbi_store.FeedResponse(200, {}, fx.feed([fx.mmo_item(broken)])))  # a release that did not parse
    poll("block", rbi_store.FeedResponse(418, {}, b""))
    poll("block", good, 1)  # cooling down
    poll("block", rbi_store.FeedResponse(418, {}, b""), 24)  # the second block in a row: stopped
    poll("block", good, 25)
    for n, answer in enumerate([rbi_store.FeedResponse(503, {}, b""), rbi_store.FeedResponse(None, error="URLError"),
                                rbi_store.FeedResponse(301, {"location": "https://www.rbi.org.in/new/feed.xml"}, b""),
                                rbi_store.FeedResponse(404, {}, b""), rbi_store.FeedResponse(200, {}, page),
                                rbi_store.FeedResponse(200, {}, feed[:500]),
                                rbi_store.FeedResponse(200, {}, b"<rss><channel><title>x</title></channel></rss>"),
                                rbi_store.FeedResponse(200, {"content-encoding": "gzip"}, b"not gzip")]):
        poll(f"error{n}", answer)
    poll("ok", good, -3)  # the clock moved back
    (tmp_path / "ok" / "store.lock").write_text("{}", encoding="utf-8")
    poll("ok", good, 3)  # locked
    (tmp_path / "ok" / "store.lock").unlink()
    state = json.loads((tmp_path / "ok" / "state.json").read_text(encoding="utf-8"))
    state["poller"]["blocks"] = "two"
    (tmp_path / "ok" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    poll("ok", good, 4)  # a damaged state
    assert {code: len(lines) for code, lines in seen.items()} == {0: 4, 1: 11, 3: 4, 4: 1}
    assert all(line.startswith("rbi-store-poll: error: ") for line in seen[1])
    assert ("rbi-store-poll: error: HTTP 301 to https://www.rbi.org.in/new/feed.xml, not followed; the next poll "
            "tries again") in seen[1]


def test_rbi_store_ingest_reruns_without_change_and_its_tables_feed_the_auctions_command(tmp_path, capsys, monkeypatch):
    import datetime as dt

    src = _release_files(tmp_path)
    files = [str(p) for p in sorted(src.iterdir())]
    store = tmp_path / "store"
    clock = _fixed_clock(monkeypatch, dt.datetime(2026, 11, 17, 4, 0, tzinfo=dt.timezone.utc))
    assert main(["rbi-store-ingest", *files, "--store", str(store)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == ("rbi-store-ingest: 3 releases in 3 copies from 3 inputs, 3 new, 0 revised, 0 repaired, "
                        "0 failed, 1 unreadable; 12 files written")  # the page's raw copy and switch_operations too
    assert lines[1].startswith("unreadable strips_3973.html: strips_3973.html: the site refused the request")
    first = {str(p.relative_to(store)): p.read_bytes() for p in store.rglob("*") if p.is_file()}
    clock[0] += dt.timedelta(days=1)
    assert main(["rbi-store-ingest", *reversed(files), "--store", str(store)]) == 0
    assert capsys.readouterr().out.splitlines()[0].endswith("; no file changed")
    assert {str(p.relative_to(store)): p.read_bytes() for p in store.rglob("*") if p.is_file()} == first
    index = pd.read_csv(store / "releases.csv").set_index("release_id")
    assert index.loc[63969, "status"] == "no_parser" and index.loc[63982, "published"] == "2026-11-16T14:55:00"

    assert main(["auctions", "--releases", str(store / "tables"), "--out-dir", str(tmp_path / "ledger")]) == 0
    auctions = pd.read_csv(tmp_path / "ledger" / "auctions.csv")
    assert "6.94% GS 2036" in set(auctions["security"])


def test_rbi_store_commands_report_errors_in_one_line(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    (store / "store.lock").write_text("{}", encoding="utf-8")
    assert main(["rbi-store-ingest", "--store", str(store)]) == 1
    err = capsys.readouterr().err
    assert err == (f"rbi-store-ingest: error: store {store} is locked by a command. Wait for that command to end; if "
                   f"none is running, delete {store / 'store.lock'}.\n")
    assert main(["rbi-store-status", "--store", str(tmp_path / "none")]) == 1
    assert capsys.readouterr().err == f"rbi-store-status: error: {tmp_path / 'none'}: no store here (neither " \
                                      "releases.csv nor state.json)\n"
    assert main(["rbi-store-ingest", str(tmp_path / "missing.html"), "--store", str(tmp_path / "s")]) == 1
    assert capsys.readouterr().err.count("\n") == 1 and not (tmp_path / "s").exists()
    for argv, message in ((["--url", "https://rbidocs.rbi.org.in/x.xml"], "bot challenge"),
                          (["--min-interval", "0.5"], "at least 1 minute"), (["--min-interval", "inf"], "finite"),
                          (["--timeout", "-1"], "positive, finite"), (["--timeout", "inf"], "positive, finite"),
                          (["--timeout", "nan"], "positive, finite"),
                          # values the poll would overflow on, or the standard library refuse once under way
                          (["--min-interval", "1e20"], "at most 1440"), (["--min-interval", "1441"], "at most 1440"),
                          (["--timeout", "1e10"], "at most 600"), (["--timeout", "601"], "at most 600"),
                          (["--user-agent", ""], "one non-empty line"), (["--user-agent", "a\tb"], "visible ASCII"),
                          (["--user-agent", "feed reader ₹"], "visible ASCII"),
                          (["--url", "https://www.rbi.org.in/a b.xml"], "without spaces"),
                          (["--url", "https://www.rbi.org.in:99999/x.xml"], "port must be a number"),
                          (["--url", "https://www.rbi.org.in:abc/x.xml"], "port must be a number")):
        with pytest.raises(SystemExit) as stop:  # a bad argument is a usage error, before anything is recorded
            main(["rbi-store-poll", "--store", str(tmp_path / "p"), *argv])
        assert stop.value.code == 2 and message in capsys.readouterr().err, argv
    assert not (tmp_path / "p").exists()


def test_rbi_store_poll_exits_4_with_what_needs_a_look_on_stderr(tmp_path, capsys, monkeypatch):
    import datetime as dt

    import rbi_fixtures as fx

    from cr_strat import rbi_store

    broken = fx.mmo_friday()
    broken["net_total"] = "-1,70,000.00"  # line F no longer adds up: the release is refused
    feed = fx.feed([fx.mmo_item(fx.mmo_saturday()), fx.mmo_item(broken)])
    monkeypatch.setattr(rbi_store, "urllib_transport", lambda url, headers, timeout: rbi_store.FeedResponse(
        200, {}, feed))
    _fixed_clock(monkeypatch, dt.datetime(2026, 11, 16, 4, 0, tzinfo=dt.timezone.utc))
    assert main(["rbi-store-poll", "--store", str(tmp_path / "store")]) == 4
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert captured.out == "" and lines[0] == ("rbi-store-poll: new: 2 new releases (63977, 63980), 0 revised, "
                                               "0 repaired, 1 failed; 2 items in the feed")
    assert lines[1].startswith("rbi-store-poll: needs a look: release 63977 (money_market_operations) did not parse: "
                               "failed check: ") and len(lines) == 2


def test_rbi_store_ingest_takes_policy_rates_and_listings_and_reports_a_failed_rebuild(tmp_path, capsys,
                                                                                         monkeypatch):
    import datetime as dt

    import rbi_fixtures as fx

    from cr_strat import rbi_store

    _fixed_clock(monkeypatch, dt.datetime(2026, 11, 17, 4, 0, tzinfo=dt.timezone.utc))
    store, table, listing = tmp_path / "store", tmp_path / "policy.csv", tmp_path / "pr_list.html"
    table.write_text("effective_date,repo_pct,sdf_pct,msf_pct,source\n2026-10-07,5.50,5.25,5.75,MPC\n",
                     encoding="utf-8")
    listing.write_text(fx.listing_page(), encoding="utf-8")
    page = tmp_path / "mmo_63977.html"
    page.write_text(fx.mmo_page(fx.mmo_friday()), encoding="utf-8")
    assert main(["rbi-store-ingest", str(page), str(listing), "--store", str(store), "--policy-rates",
                 str(table)]) == 0
    assert capsys.readouterr().out.startswith("rbi-store-ingest: 1 release in 1 copy from 2 inputs, 1 new, 0 revised, "
                                              "0 repaired, 0 failed, 6 listed (title and date only); ")
    daily = pd.read_csv(store / "derived" / "money_market_daily.csv", dtype=str, keep_default_na=False)
    assert daily[["repo_rate_pct", "policy_check"]].values.tolist() == [["5.5", "agrees"]]

    def broken(*args, **kwargs):
        raise ValueError("a bug")

    monkeypatch.setattr(rbi_store, "derive", broken)
    assert main(["rbi-store-ingest", "--store", str(store)]) == 1
    assert capsys.readouterr().err == ("rbi-store-ingest: error: derived tables not rebuilt: ValueError: a bug (the "
                                       "previous ones are kept; the parsed rows and raw copies were saved)\n")


def _macro_transport(answers):
    """A fake transport for macro-store-update: the first route whose text the URL contains answers; 404 else."""
    from cr_strat.storekit import FeedResponse

    sent = []

    def transport(url, headers, timeout, **kwargs):
        sent.append(url)
        for key, answer in answers.items():
            if key in url:
                return answer
        return FeedResponse(404, {}, b"")
    return transport, sent


def test_macro_store_update_exit_codes_one_line_messages_and_the_mospi_reminder(tmp_path, capsys, monkeypatch):
    import macro_fixtures as mfx

    from cr_strat import macro_store
    from cr_strat.storekit import FeedResponse

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    good = {"unsecured/all": FeedResponse(200, {}, mfx.nyfed_json([mfx.effr_row("2026-11-16")])),
            "secured/all": FeedResponse(200, {}, mfx.nyfed_json([mfx.sofr_row("2026-11-16")])),
            "dat00_in": FeedResponse(200, {}, mfx.h10_page([("13-NOV-26", "94.2400")])),
            "dat96_in": FeedResponse(200, {}, mfx.h10_page_1990s([("2-Jan-90", "16.9600")])),
            "WS_CBPOL": FeedResponse(200, {}, mfx.cbpol_v2([("IN", "2026-10-07", "5.5", "A")])),
            "WS_XRU": FeedResponse(200, {}, mfx.xru_v1([("2026-11-10", "93.928571", "A")])),
            "api.mospi.gov.in": FeedResponse(200, {}, mfx.NO_DATA)}
    transport, sent = _macro_transport(good)
    monkeypatch.setattr(macro_store, "urllib_transport", transport)
    import datetime as dt
    _fixed_clock(monkeypatch, dt.datetime(2026, 11, 18, 14, 0, tzinfo=dt.timezone.utc))
    run = "macro-store-update: 2026-11-18T14:00:00Z "  # every line carries the run's time: the log is appended to
    store = str(tmp_path / "store")
    assert main(["macro-store-update", "--store", store]) == 0
    out = capsys.readouterr()
    assert out.err == "" and out.out.splitlines()[0].startswith(run + "nyfed: 4 requests (4 ok): ")
    assert out.out.splitlines()[-1].startswith(run + "done: 10 requests, ")
    assert not [u for u in sent if "mospi" in u]  # MoSPI only when named
    assert main(["macro-store-update", "--store", str(tmp_path / "m"), "--sources", "mospi"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith(run + "mospi: reminder: MoSPI's licence for a stored history is not settled")
    for answer, code, text in ((FeedResponse(403, {}, b""), 3, run + "h10: blocked: HTTP 403;"),
                               (FeedResponse(503, {}, b""), 1, run + "h10: error: server error: HTTP 503"),
                               (FeedResponse(404, {}, b""), 4, run + "needs a look: h10: ")):
        transport, sent = _macro_transport({**good, "dat00_in": answer})
        monkeypatch.setattr(macro_store, "urllib_transport", transport)
        assert main(["macro-store-update", "--store", str(tmp_path / f"s{code}"), "--sources", "h10"]) == code
        err = capsys.readouterr().err
        assert any(line.startswith(text) for line in err.splitlines()), (code, err)
    assert main(["macro-store-update", "--store", str(tmp_path / "s3"), "--sources", "h10"]) == 3  # cooling down
    assert "cooling down after HTTP 403" in capsys.readouterr().err


def test_macro_store_commands_refuse_bad_arguments_before_anything_is_recorded(tmp_path, capsys):
    for command, argv, message in (
            ("macro-store-update", ["--sources", "rbi"], "no source 'rbi'"),
            ("macro-store-update", ["--sources", "mospi,fred"], "no source 'fred'"),
            ("macro-store-update", ["--max-requests", "0"], "from 1 to 500"),
            ("macro-store-update", ["--max-per-host", "x"], "not a whole number"),
            ("macro-store-update", ["--min-interval", "-1"], "from 0 to 10080"),
            ("macro-store-update", ["--min-interval", "nan"], "finite"),
            ("macro-store-update", ["--user-agent", "client (me@example.com)"], "names someone"),
            ("macro-store-update", ["--timeout", "0"], "positive, finite"),
            ("macro-store-update", ["--cpi-levels", "all"], "invalid choice"),
            ("macro-store-update", ["--index-events", str(tmp_path / "missing.csv")], "no such file"),
            ("macro-store-ingest", ["--release-dates", str(tmp_path / "missing.csv")], "no such file"),
            ("macro-store-status", ["--series", "("], "not a regular expression")):
        with pytest.raises(SystemExit) as stop:
            main([command, "--store", str(tmp_path / "s"), *argv])
        assert stop.value.code == 2 and message in capsys.readouterr().err, argv
    assert not (tmp_path / "s").exists()


def test_macro_store_ingest_and_status_print_one_line_each_and_errors_in_one_line(tmp_path, capsys):
    import macro_fixtures as mfx

    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "n.json").write_bytes(mfx.nyfed_json([mfx.sofr_row("2026-11-16"), mfx.sofr_row("2026-11-17")]))
    (saved / "e.json").write_bytes(mfx.LIMIT_ERROR)
    (saved / "x.bin").write_bytes(b"\x00")
    store = tmp_path / "store"
    files = [str(p) for p in sorted(saved.iterdir())]
    assert main(["macro-store-ingest", *files, "--store", str(store)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("macro-store-ingest: 3 inputs: 1 read, 1 refused, 1 not recognised, 12 new values, ")
    assert lines[1].startswith(f"refused {saved / 'e.json'}: ") and lines[2].startswith(f"not recognised {saved}")
    assert main(["macro-store-ingest", *reversed(files), "--store", str(store)]) == 0
    assert capsys.readouterr().out.splitlines()[0].endswith("; no file changed")
    assert main(["macro-store-status", "--store", str(store), "--series", "^sofr$"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"store {store}" and out[-1].startswith("  sofr: 2 periods 2026-11-16 to 2026-11-17")
    (store / "store.lock").write_text("{}", encoding="utf-8")
    assert main(["macro-store-ingest", "--store", str(store)]) == 1
    assert capsys.readouterr().err == (f"macro-store-ingest: error: store {store} is locked by a command. Wait for "
                                       f"that command to end; if none is running, delete {store / 'store.lock'}.\n")
    assert main(["macro-store-status", "--store", str(tmp_path / "none")]) == 1
    assert capsys.readouterr().err.startswith("macro-store-status: error: ")


def test_the_documented_daily_grep_of_the_macro_store_flags_every_update_that_exits_non_zero_and_no_other(
        tmp_path, capsys, monkeypatch):
    import datetime as dt
    import json

    import macro_fixtures as mfx

    from cr_strat import macro_store
    from cr_strat.storekit import FeedResponse

    documented = re.compile(_documented_grep("## Macro and global store"))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    good = {"unsecured/all": FeedResponse(200, {}, mfx.nyfed_json([mfx.effr_row("2026-11-16")])),
            "secured/all": FeedResponse(200, {}, mfx.nyfed_json([mfx.sofr_row("2026-11-16")])),
            "dat00_in": FeedResponse(200, {}, mfx.h10_page([("13-NOV-26", "94.2400")])),
            "dat96_in": FeedResponse(200, {}, mfx.h10_page_1990s([("2-Jan-90", "16.9600")])),
            "WS_CBPOL": FeedResponse(200, {}, mfx.cbpol_v2([("IN", "2026-10-07", "5.5", "A")])),
            "WS_XRU": FeedResponse(200, {}, mfx.xru_v1([("2026-11-10", "93.928571", "A")])),
            "api.mospi.gov.in": FeedResponse(200, {}, mfx.NO_DATA)}
    start = dt.datetime(2026, 11, 18, 14, 0, tzinfo=dt.timezone.utc)  # 19:30 in India
    clock = _fixed_clock(monkeypatch, start)
    seen = {}

    def update(name, answers=None, hours=0.0, *extra):
        transport, _ = _macro_transport({**good, **(answers or {})})
        monkeypatch.setattr(macro_store, "urllib_transport", transport)
        clock[0] = start + dt.timedelta(hours=hours)
        code = main(["macro-store-update", "--store", str(tmp_path / name), *extra])
        captured = capsys.readouterr()  # cron appends both streams to macro_store.log
        lines = (captured.out + captured.err).splitlines()
        seen.setdefault(code, []).append(lines[0])
        assert bool(documented.search("\n".join(lines))) == (code != 0), (code, lines)
        return code

    h10 = ("--sources", "h10")
    assert update("ok") == 0  # new values
    assert update("ok", None, 0.2) == 0  # too soon
    assert update("ok", None, 1) == 0  # the same answers again
    assert update("ok", {"dat00_in": FeedResponse(304, {}, b"")}, 2) == 0  # not modified
    assert update("mospi", None, 0, "--sources", "mospi") == 0  # the reminder is not a problem line
    assert update("block", {"dat00_in": FeedResponse(403, {}, b"")}, 0, *h10) == 3
    assert update("block", None, 1, *h10) == 3  # cooling down
    assert update("block", {"dat00_in": FeedResponse(418, {}, b"")}, 24, *h10) == 3  # a second block: stopped
    assert update("block", None, 25, *h10) == 3  # stopped until resumed
    denied = b"<html><head><title>Access Denied</title></head><body><p>Access denied</p></body></html>"
    assert update("denied", {"dat00_in": FeedResponse(200, {}, denied)}, 0, *h10) == 3
    for n, answer in enumerate([FeedResponse(503, {}, b""), FeedResponse(None, error="URLError"),
                                FeedResponse(301, {"location": "https://www.federalreserve.gov/new/"}, b"")]):
        assert update(f"error{n}", {"dat00_in": answer}, 0, *h10) == 1
    assert update("wrong", {"WS_XRU": FeedResponse(404, {}, b"")}, 0, "--sources", "bis") == 4
    assert update("refused", {"dat96_in": FeedResponse(200, {}, b"<html><body><table><tr><td>x</td></tr></table>"
                                                                b"</body></html>")}, 0, *h10) == 4
    state_path = tmp_path / "ok" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sources"]["h10"]["in_flight"] = {"url": "https://www.federalreserve.gov/releases/h10/hist/dat00_in.htm",
                                            "time": "2026-11-18T16:00:00Z"}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert update("ok", None, 3, *h10) == 4  # a run that was killed during a request
    assert update("ok", None, -3, *h10) == 1  # the clock went back: a clock error
    (tmp_path / "ok" / "store.lock").write_text("{}", encoding="utf-8")
    assert update("ok", None, 4) == 1  # locked
    events = tmp_path / "events.csv"
    events.write_text("not,an,index,event,table\n", encoding="utf-8")
    assert update("events", None, 0, "--index-events", str(events)) == 1
    assert set(seen) == {0, 1, 3, 4}


def test_macro_store_ingest_needs_the_series_of_base_2012_cpi_and_status_checks_policy_rates(tmp_path, capsys):
    import macro_fixtures as mfx

    saved = tmp_path / "cpi2012.json"
    saved.write_bytes(mfx.mospi_answer([mfx.cpi_group(2025, "December", "Combined", "General", "General-Overall")],
                                       per=200))
    store = tmp_path / "store"
    assert main(["macro-store-ingest", str(saved), "--store", str(store)]) == 0
    out = capsys.readouterr().out
    assert "1 refused" in out and "--cpi-series" in out
    assert main(["macro-store-ingest", str(saved), "--store", str(store), "--cpi-series", "Current"]) == 0
    assert "2 new values" in capsys.readouterr().out
    bis = tmp_path / "b.csv"
    bis.write_bytes(mfx.cbpol_v1([("IN", "2026-10-06", "5.25", "A"), ("IN", "2026-10-07", "5.5", "A")]))
    assert main(["macro-store-ingest", str(bis), "--store", str(store)]) == 0
    capsys.readouterr()
    table = tmp_path / "policy.csv"
    table.write_text("effective_date,repo_pct,sdf_pct,msf_pct,source\n2026-10-06,5.25,5,5.5,typed in\n"
                     "2026-10-07,5.5,5.25,5.75,typed in\n", encoding="utf-8")
    assert main(["macro-store-status", "--store", str(store), "--policy-rates", str(table)]) == 0
    assert "policy rates: BIS and the table agree on 1 change" in capsys.readouterr().out
