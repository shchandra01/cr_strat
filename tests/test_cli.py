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
             dict(title="Money Market Operations as on November 15, 2026", prid=63981,
                  pub="Mon, 16 Nov 2026 12:05:00", html=fx.body("Tables."))]
    (src / "pr_feed_20261116.xml").write_bytes(fx.feed(items))
    (src / "strips_3973.html").write_text(fx.blocked_page(), encoding="utf-8")
    return src


def test_rbi_releases_appends_idempotently(tmp_path, capsys):
    src = _release_files(tmp_path)
    out = tmp_path / "out"
    files = [str(p) for p in sorted(src.iterdir())]
    assert main(["rbi-releases", *files, "--out-dir", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "no parser for money_market_operations yet" in printed and "Support ID" in printed
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
