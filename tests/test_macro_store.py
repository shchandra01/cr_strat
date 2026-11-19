"""The macro and global store: synthetic answers only (``macro_fixtures``), a fake transport and a fake sleep.

No network; the clock is an argument.
"""

import datetime as dt
import json
import socket
import subprocess
import sys
from pathlib import Path

import macro_fixtures as fx
import pandas as pd
import pytest

from cr_strat import macro_store as st
from cr_strat import mospi, rbi_store, storekit
from cr_strat.storekit import FeedResponse

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 18, 14, 0, tzinfo=UTC)  # 19:30 in India
HOUR = dt.timedelta(hours=1)
MINUTE = dt.timedelta(minutes=1)
EXAMPLE_EVENTS = Path(__file__).resolve().parents[1] / "data" / "index_events_example.csv"


def ist(*args) -> dt.datetime:
    return dt.datetime(*args)


# --- Synthetic answers (structure of the real ones, made-up values) -------------------------------------------

def secured(*rows) -> bytes:
    return fx.nyfed_json([fx.sofr_row(day, rate) for day, rate in rows])


def unsecured(*rows) -> bytes:
    return fx.nyfed_json([fx.effr_row(day, rate, revision=rev) for day, rate, rev in rows])


H10 = fx.h10_page([("12-NOV-26", "94.0400"), ("13-NOV-26", "94.2400"), ("16-NOV-26", "ND")])
CBPOL = fx.cbpol_v1([("IN", "2001-04-02", "7", "A"), ("IN", "2001-04-03", "8.5", "A"), ("IN", "2001-04-04", "8.5", "A"),
                     ("IN", "2001-04-07", "NaN", "M"), ("IN", "2026-10-06", "5.25", "A"),
                     ("IN", "2026-10-07", "5.5", "A"), ("US", "2026-09-17", "3.875", "A")])
XRU = fx.xru_v1([("2026-11-10", "93.928571", "A")])
UST = fx.ust_csv([("03/09/2026", ["4.10"] * 14), ("03/06/2026", ["4.08"] * 14)])
EIA = fx.eia_page([("2026 Nov- 9 to Nov-13", ["91.03", "90.16", "92.14", "", "95.17"]),
                   ("2026 Nov-16 to Nov-20", ["92.90", "92.94", "", "", ""])])


def cpi_month(month: str, combined: str = "110.05", yoy: str = "6.09", year: int = 2026, food: str = "113.23",
              housing: str = "104.67") -> list[dict]:
    rows = [fx.cpi24(year, month, s, index=v, inflation=y) for s, v, y in
            (("Rural", "110.73", "6.63"), ("Urban", "109.22", "5.41"), ("Combined", combined, yoy))]
    rows += [fx.cpi24(year, month, "Combined", division="Food and beverages", group="Food", code="01.1", index=food,
                      inflation="8.94"),
             fx.cpi24(year, month, "Combined", division="Housing, water, electricity, gas and other fuels", code="04",
                      index=housing, inflation="2.90"),
             fx.cpi24(year, month, "Combined", division="Food and beverages", group="Food",
                      klass="Cereals and products", sub_class="Rice", item="Rice, PDS", code="01.1.1.1.1",
                      index="120.00", inflation="1.00")]
    return rows


def cpi_answer(*months: list[dict]) -> bytes:
    rows = [r for m in months for r in m]
    return fx.mospi_answer(rows, per=100)


def answers() -> list[tuple[str, bytes]]:
    """One saved answer of every source, named as a user might have saved them."""
    return [("n1_secured.json", secured(("2026-11-13", "3.90"), ("2026-11-16", "3.93"), ("2026-11-17", "3.92"))),
            ("n2_unsecured.json", unsecured(("2026-11-13", "3.88", ""), ("2026-11-16", "3.88", "r"))),
            ("f1_h10.htm", H10), ("b1_cbpol.csv", CBPOL), ("b2_xru.csv", XRU), ("t1_ust.csv", UST),
            ("x1_eia.htm", EIA), ("m1_cpi.json", cpi_answer(cpi_month("October")))]


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _rows(path: Path) -> list[dict[str, str]]:
    return pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")


def _vintages(store: Path, source: str, series_id: str) -> list[dict[str, str]]:
    return [r for r in _rows(store / "observations" / f"{source}.csv") if r["series_id"] == series_id]


def _state(store: Path) -> dict:
    return json.loads((store / "state.json").read_text(encoding="utf-8"))


# --- Ingest -----------------------------------------------------------------------------------------------

def test_ingest_writes_the_store_and_the_same_answers_again_change_nothing(tmp_path):
    store = tmp_path / "s"
    report = st.ingest(store, answers(), now=NOW)
    assert (report.inputs, report.read, report.refused, report.unreadable) == (8, 8, [], [])
    # 3 SOFR days x 6 values, 2 EFFR days x 8, 2 H.10 days, 6 policy rates, 1 rupee rate, 2 curves x 14 tenors,
    # 6 Brent days, 5 CPI index-and-inflation pairs; the sixth, an item's, is not kept by default (--cpi-levels groups)
    assert report.new == 18 + 16 + 2 + 6 + 1 + 28 + 6 + 10 and report.changed == 0 and report.out_of_scope == 2
    files = _files(store)
    for name in ("series.csv", "revisions.csv", "fetches.csv", "derived/global_daily.csv", "derived/india_monthly.csv",
                 "derived/india_quarterly.csv", "derived/index_events.csv", "derived/policy_rates_bis.csv",
                 *(f"observations/{s}.csv" for s in ("nyfed", "h10", "bis", "ust", "eia", "mospi"))):
        assert name in files, name
    raw = sorted(n for n in files if n.startswith("raw/"))
    assert len(raw) == 7 and not any(n.startswith("raw/mospi/") for n in raw)  # MoSPI's copies only if asked
    assert all(n.split("/")[2].startswith("20261118T140000Z_") for n in raw)
    again = st.ingest(store, answers(), now=NOW + HOUR)
    assert (again.new, again.changed, again.unchanged, again.written) == (0, 0, 87, [])
    assert _files(store) == files  # byte for byte
    other = tmp_path / "t"
    st.ingest(other, list(reversed(answers())), now=NOW)
    assert _files(other) == files  # the order of the inputs does not matter


def test_two_inputs_that_disagree_give_the_same_store_in_either_order(tmp_path):
    first = ("a_old.json", secured(("2026-11-16", "3.93")))
    second = ("b_new.json", secured(("2026-11-16", "3.95")))
    one, two = tmp_path / "one", tmp_path / "two"
    report = st.ingest(one, [first, second], now=NOW)
    st.ingest(two, [second, first], now=NOW)
    assert _files(one) == _files(two)
    (conflict,) = report.conflicts
    assert conflict == "sofr 2026-11-16: 3.93 in a_old.json, 3.95 in b_new.json (the later kept)"  # the inputs named
    (sofr,) = _vintages(one, "nyfed", "sofr")
    assert sofr["value"] == "3.95" and sofr["notes"].startswith("two answers disagreed: 3.93 in ")


def test_unreadable_refused_and_out_of_scope_inputs_add_nothing(tmp_path):
    store = tmp_path / "s"
    rbi = fx.mospi_answer([fx.rbi_fx("2025", "July", "Euro", "Average", "100.64")], per=20)
    state_cpi = fx.mospi_answer([fx.cpi24(2026, "October", "Combined", state="Kerala", index="111.00")], per=100)
    report = st.ingest(store, [("a.json", fx.LIMIT_ERROR), ("b.htm", fx.AKAMAI_PAGE), ("c.json", rbi),
                               ("d.json", state_cpi), ("e.json", fx.NO_DATA), ("f.json", fx.CALENDAR_POINTER),
                               ("g.bin", b"\x00\xff")], now=NOW)
    assert [n for n, _ in report.refused] == ["a.json"] and "an error answer" in report.refused[0][1]
    assert [n for n, _ in report.unreadable] == ["b.htm", "g.bin"] and "refused" in report.unreadable[0][1]
    assert [n for n, _ in report.other] == ["f.json"] and "release calendar pointer" in report.other[0][1]
    assert (report.read, report.new, report.out_of_scope) == (3, 0, 2)  # RBI's tables and a state: not kept
    with_states = st.ingest(tmp_path / "t", [("d.json", state_cpi)], now=NOW, settings=st.Settings(states=True))
    assert with_states.new == 1  # a state's general index, with --states


def test_what_is_kept_by_default_and_with_other_settings():
    def kept(sid, settings=None, source="mospi"):
        return st.in_scope(fx_obs(sid, source), settings or st.Settings())
    items = st.Settings(cpi_levels="items")
    assert st.Settings().cpi_levels == "groups"  # the conservative default while MoSPI's terms are not settled
    for sid in ("cpi_2024_all_india_combined_general", "cpi_2024_all_india_rural_d01_yoy",
                "cpi_2024_all_india_urban_g01_1", "cpi_2024back_all_india_combined_general"):
        assert kept(sid) and kept(sid, items)
    for sid in ("cpi_2024_all_india_combined_c01_1_1", "cpi_2024_all_india_combined_i01_1_1_1_1",
                "cpi_2012_all_india_item_rice"):
        assert not kept(sid) and kept(sid, items)  # what macro_sources.stored_by_default keeps; items when asked
        from cr_strat import macro_sources
        assert not macro_sources.stored_by_default(sid)
    assert not kept("cpi_2024_kerala_combined_general") and kept("cpi_2024_kerala_combined_general",
                                                                 st.Settings(states=True))
    assert not kept("cpi_2024_kerala_combined_d01", st.Settings(states=True))  # a state's general index only
    assert kept("iip_2022_23_manufacturing") and not kept("iip_2022_23_manufacturing_food_products")
    assert kept("wpi_2022_23_food_index") and not kept("wpi_2022_23_primary__paddy")
    assert not kept("mospi_rbi_inr_per_usd_average")
    assert kept("anything", source="nyfed")


def fx_obs(series_id: str, source: str):
    from cr_strat.macro_base import Observation
    return Observation(source, series_id, "2026-10", "month", "2026-10-01", "2026-10-31", "1", "x")


# --- Vintages, revisions, as_of ---------------------------------------------------------------------------

def test_a_changed_value_is_a_new_vintage_logged_as_a_revision_and_as_of_sees_each_in_turn(tmp_path):
    store = tmp_path / "s"
    t1, t2 = NOW, NOW + 30 * HOUR
    st.ingest(store, [("a.json", secured(("2026-11-13", "3.90"), ("2026-11-16", "3.93")))], now=t1)
    report = st.ingest(store, [("b.json", secured(("2026-11-13", "3.90"), ("2026-11-16", "3.95")))], now=t2)
    assert (report.new, report.changed, report.unchanged) == (0, 1, 11)
    first, second = (v for v in _vintages(store, "nyfed", "sofr") if v["period"] == "2026-11-16")
    assert (first["vintage"], first["value"], first["public_from"], first["seen"]) == (
        "1", "3.93", "2026-11-18T19:30:00", "live")  # the latest row: no next date, dated by its sighting
    assert (second["vintage"], second["value"], second["public_from"], second["public_from_basis"],
            second["print"]) == ("2", "3.95", "2026-11-20T01:30:00", "first_seen", "revised")
    (revision,) = _rows(store / "revisions.csv")
    assert (revision["series_id"], revision["period"], revision["old_value"], revision["new_value"],
            revision["seen"], revision["old_first_seen"]) == ("sofr", "2026-11-16", "3.93", "3.95",
                                                              "2026-11-20T01:30:00", "2026-11-18T19:30:00")
    early = st.as_of(store, "sofr", ist(2026, 11, 17, 12, 0))
    assert list(early["period"]) == ["2026-11-13"]  # the 16th was not public yet
    assert early.loc[0, "public_from"] == pd.Timestamp("2026-11-16T20:30:00")  # 10:00 New York, the next date
    assert early.loc[0, "not_seen_at_publication"]  # read days later: may include later revisions
    middle = st.as_of(store, "sofr", ist(2026, 11, 19, 12, 0))
    assert middle.set_index("period")["value"].to_dict() == {"2026-11-13": 3.90, "2026-11-16": 3.93}
    late = st.as_of(store, "sofr", t2)  # an aware time
    assert late.set_index("period")["value_text"].to_dict() == {"2026-11-13": "3.90", "2026-11-16": "3.95"}
    assert list(late["vintage"]) == [1, 2]
    assert st.as_of(store, "sofr", ist(2026, 11, 17, 12, 0), seen_only=True).empty  # the store had read nothing
    with pytest.raises(ValueError, match="no series 'nope'"):
        st.as_of(store, "nope", NOW)


def test_an_ingest_does_not_take_a_superseded_value_as_a_change_back(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("a.json", secured(("2026-11-16", "3.93")))], now=NOW)
    st.ingest(store, [("b.json", secured(("2026-11-16", "3.95")))], now=NOW + HOUR)
    report = st.ingest(store, [("c.json", secured(("2026-11-16", "3.93")))], now=NOW + 2 * HOUR)  # an older file
    assert (report.older, report.changed) == (1, 0)
    assert [v["value"] for v in _vintages(store, "nyfed", "sofr")] == ["3.93", "3.95"]


# --- From when a value was public -------------------------------------------------------------------------

def test_us_and_bis_values_are_dated_by_their_rules_across_new_york_daylight_saving(tmp_path):
    store = tmp_path / "s"
    nyfed = secured(("2026-10-29", "4.10"), ("2026-10-30", "4.11"), ("2026-11-02", "4.12"))
    st.ingest(store, [("u.csv", UST), ("n.json", nyfed), ("x.htm", EIA), ("f.htm", H10), ("b.csv", CBPOL),
                      ("r.csv", XRU)], now=NOW)
    ust = {v["period"]: v["public_from"] for v in _vintages(store, "ust", "ust_par_10y")}
    assert ust == {"2026-03-06": "2026-03-07T10:29:59", "2026-03-09": "2026-03-10T09:29:59"}  # EST, then EDT
    sofr = {v["period"]: (v["public_from"], v["public_from_basis"]) for v in _vintages(store, "nyfed", "sofr")}
    assert sofr == {"2026-10-29": ("2026-10-30T19:30:00", "publication_rule"),  # 10:00 EDT on Friday
                    "2026-10-30": ("2026-11-02T20:30:00", "publication_rule"),  # 10:00 EST on Monday, after the change
                    "2026-11-02": ("2026-11-18T19:30:00", "first_seen")}  # the latest: no next date
    eia = {v["period"]: v["public_from"] for v in _vintages(store, "eia", "brent_spot_usd_bbl")}
    assert eia["2026-11-13"] == "2026-11-18T19:30:00"  # the rule's Thursday is after the sighting: first_seen
    assert eia["2026-11-09"] == "2026-11-13T10:29:59"  # Thursday 12 Nov, 23:59:59 New York
    h10 = {v["period"]: v["public_from"] for v in _vintages(store, "h10", "usdinr_h10_noon")}
    assert h10 == {"2026-11-12": "2026-11-18T02:45:00", "2026-11-13": "2026-11-18T02:45:00"}  # Tuesday 16:15 EST
    bis = {(v["series_id"], v["period"]): v["public_from"] for v in _rows(store / "observations" / "bis.csv")}
    assert bis[("policy_rate_in_bis", "2026-10-07")] == "2026-10-07T23:59:59"
    assert bis[("policy_rate_us_bis", "2026-09-17")] == "2026-09-18T09:29:59"
    # BIS's own rupee figure: public when BIS publishes it; the rule (23:59:59 Basel time a week after Thursday 12
    # Nov) is after this sighting, so the sighting dates it
    assert bis[("usdinr_bis_daily", "2026-11-10")] == "2026-11-18T19:30:00"


def test_a_value_seen_before_its_rule_time_is_dated_by_its_sighting(tmp_path):
    store = tmp_path / "s"
    seen = dt.datetime(2026, 11, 16, 23, 0, tzinfo=UTC)  # Monday 18:00 New York, before H.10's Tuesday rule
    st.ingest(store, [("f.htm", H10)], now=seen)
    for v in _vintages(store, "h10", "usdinr_h10_noon"):
        assert (v["public_from"], v["public_from_basis"], v["seen"]) == ("2026-11-17T04:30:00", "first_seen", "live")


def test_mospi_values_are_dated_by_the_release_that_set_them_and_seen_live_or_later(tmp_path):
    late, live = tmp_path / "late", tmp_path / "live"
    st.ingest(late, [("m.json", cpi_answer(cpi_month("October")))], now=NOW)  # six days after the release
    (v,) = _vintages(late, "mospi", "cpi_2024_all_india_combined_general")
    assert (v["public_from"], v["public_from_basis"], v["release_id"], v["stage"], v["print"], v["seen"]) == (
        "2026-11-12T16:00:00", "release_table_planned", "cpi_2026-10", "first", "first", "later")
    at_release = dt.datetime(2026, 11, 12, 10, 40, tzinfo=UTC)  # 16:10 in India
    st.ingest(live, [("m.json", cpi_answer(cpi_month("October")))], now=at_release)
    (v,) = _vintages(live, "mospi", "cpi_2024_all_india_combined_general")
    assert (v["public_from"], v["seen"]) == ("2026-11-12T16:00:00", "live")
    final = dt.datetime(2026, 12, 14, 10, 50, tzinfo=UTC)  # 16:20 on the Monday the final comes out
    st.ingest(live, [("m2.json", cpi_answer(cpi_month("October", combined="110.10", yoy="6.14")))], now=final)
    first, second = _vintages(live, "mospi", "cpi_2024_all_india_combined_general")
    assert (second["public_from"], second["stage"], second["print"], second["release_id"], second["seen"]) == (
        "2026-12-14T16:00:00", "final", "final", "cpi_2026-11", "live")
    assert first["print"] == "first"
    before_launch = tmp_path / "early"
    st.ingest(before_launch, [("m.json", cpi_answer(cpi_month("January", year=2025)))],
              now=dt.datetime(2026, 2, 1, tzinfo=UTC))  # read before base 2024's recorded launch
    (v,) = _vintages(before_launch, "mospi", "cpi_2024_all_india_combined_general")
    assert v["public_from_basis"] == "first_seen" and "launch" in v["public_from_rule"]
    history = tmp_path / "history"
    st.ingest(history, [("m.json", cpi_answer(cpi_month("January", year=2025)))], now=NOW)
    (v,) = _vintages(history, "mospi", "cpi_2024_all_india_combined_general")
    assert (v["public_from"], v["print"], v["seen"]) == ("2026-02-12T16:00:00", "final", "later")  # the launch


def test_a_release_table_given_later_redates_every_vintage_of_its_releases(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("m.json", cpi_answer(cpi_month("October")))], now=NOW)
    table = (",".join(mospi.RELEASE_DATE_COLUMNS) + "\ncpi,2026-10,first 2026-10; final 2026-09,2026-11-12,2026-11-12,"
             "2026-11-13,17:30,read on the release page,actual,a typed-in test row,\n").encode()
    report = st.ingest(store, [], now=NOW + HOUR, release_dates=table)
    assert "release_dates.csv" in report.written and "observations/mospi.csv" in report.written
    (v,) = _vintages(store, "mospi", "cpi_2024_all_india_combined_general")
    assert (v["public_from"], v["public_from_basis"]) == ("2026-11-13T17:30:00", "release_table")
    with pytest.raises(ValueError, match="bad.csv, line 2"):
        st.ingest(store, [], now=NOW, release_dates=_write(tmp_path / "bad.csv", table.replace(b"17:30", b"5pm")))
    assert (store / "release_dates.csv").read_bytes() == table  # a refused table changes nothing


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# --- The update: a fake server ------------------------------------------------------------------------------

def ok(body: bytes, **headers) -> FeedResponse:
    return FeedResponse(200, {"content-type": "x", **headers}, body)


class Server:
    """Answers by the first route whose text the URL contains (an answer, a list taken in turn, or a function);
    404 otherwise. Records every request with its headers."""

    def __init__(self, *routes):
        self.routes = [list(r) for r in routes]
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers)))
        for route in self.routes:
            if route[0] in url:
                answer = route[1]
                if isinstance(answer, list):
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                if callable(answer):
                    answer = answer(url, headers)
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        return FeedResponse(404, {}, b"")

    def urls(self, text: str = "") -> list[str]:
        return [u for u, _ in self.requests if text in u]


class Sleep:
    def __init__(self):
        self.waits: list[float] = []

    def __call__(self, seconds):
        self.waits.append(seconds)


def us_server(**over) -> Server:
    routes = {"unsecured/all": ok(unsecured(("2026-11-16", "3.88", ""), ("2026-11-17", "3.88", ""))),
              "secured/all": ok(secured(("2026-11-16", "3.93"), ("2026-11-17", "3.92"))),
              "dat00_in": ok(H10, **{"last-modified": "Mon, 16 Nov 2026 21:15:09 GMT"}),
              "dat96_in": ok(fx.h10_page_1990s([("2-Jan-90", "16.9600"), ("3-Jan-90", "ND")])),
              "WS_CBPOL": ok(fx.cbpol_v2([("IN", "2026-10-07", "5.5", "A"), ("US", "2026-11-10", "3.875", "A")])),
              "WS_XRU": ok(XRU), "treasury": lambda url, h: ok(fx.ust_csv([(f"11/16/{_year(url)}", ["4.10"] * 14)])),
              "eia.gov": ok(EIA, etag='"e1"')}
    routes.update(over)
    return Server(*routes.items())


def _year(url: str) -> int:
    return int(url.split("/all?")[0].rsplit("/", 1)[1])


def run(store, server, when=NOW, **kwargs) -> st.UpdateReport:
    kwargs.setdefault("sleep", Sleep())
    return st.update(store, now=when, transport=server, **kwargs)


def test_an_update_asks_the_recent_window_then_the_history_and_then_too_soon_asks_nothing(tmp_path):
    store, server = tmp_path / "s", us_server()
    sleep = Sleep()
    report = run(store, server, sleep=sleep)
    assert report.exit_code == 0 and [s.source for s in report.sources] == ["nyfed", "h10", "bis"]
    assert {s.source: s.outcome for s in report.sources} == {"nyfed": "updated", "h10": "updated", "bis": "updated"}
    assert server.urls() == [
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json?startDate=2026-11-04&endDate=2026-11-18",
        "https://markets.newyorkfed.org/api/rates/unsecured/all/search.json?startDate=2026-11-04&endDate=2026-11-18",
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json?startDate=2018-04-02&endDate=2026-11-18",
        "https://markets.newyorkfed.org/api/rates/unsecured/all/search.json?startDate=2000-01-01&endDate=2026-11-18",
        "https://www.federalreserve.gov/releases/h10/hist/dat00_in.htm",
        "https://www.federalreserve.gov/releases/h10/hist/dat96_in.htm",
        "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.IN+US?lastNObservations=60",
        "https://stats.bis.org/api/v1/data/WS_XRU/D.IN../all?format=csv&startPeriod=2026-09-19",
        "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.IN+US",
        "https://stats.bis.org/api/v1/data/WS_XRU/D.IN../all?format=csv"]
    assert sleep.waits == [10.0] * 7  # ten seconds between two requests to one host, none across hosts
    assert all(h["User-Agent"] == "Mozilla/5.0 (compatible; data-client/1.0)" and h["Accept-Encoding"] == "gzip"
               for _, h in server.requests)
    log = _rows(store / "fetches.csv")
    assert [r["outcome"] for r in log] == ["ok"] * 10 and log[0]["time"] == "2026-11-18T14:00:00Z"
    assert (log[0]["rows"], log[0]["kept"], log[0]["new"], log[2]["new"]) == ("12", "12", "12", "0")  # a value both
    # hold is first seen in the earlier answer (the history repeats the recent window)
    assert (log[0]["latest"], log[2]["latest"]) == ("2026-11-18T14:01:00Z", "2026-11-18T14:03:20Z")  # clock plus a
    # minute (the timeout) for each request of the run so far
    assert log[0]["raw"].startswith("raw/nyfed/20261118T140000Z_")
    history = _state(store)["sources"]["nyfed"]["backfill"]
    assert (len(history["done"]), history["queue"], history["finished"]) == (2, [], "2026-11-18T14:00:00Z")
    files = _files(store)
    again = run(store, server, when=NOW + 5 * MINUTE)
    assert {s.outcome for s in again.sources} == {"too_soon"} and again.exit_code == 0
    assert len(server.requests) == 10 and _files(store) == files  # nothing asked, nothing written
    assert again.sources[0].message.startswith("too soon: its last update began at 2026-11-18T14:00:00Z (5 min ago)")
    later = run(store, server, when=NOW + HOUR)
    assert len(server.requests) == 15  # the recent window only: the history is done
    assert {s.outcome for s in later.sources} == {"unchanged"}
    changed = {k for k, v in _files(store).items() if files.get(k) != v}
    assert changed == {"fetches.csv", "state.json"}  # the same content again: only the request log and the state
    assert server.requests[12][1]["If-Modified-Since"] == "Mon, 16 Nov 2026 21:15:09 GMT"  # H.10: conditional


def test_a_history_proceeds_in_steps_within_the_caps_and_the_status_says_how_far(tmp_path):
    store, server = tmp_path / "s", us_server()
    steps = []
    for day in range(5):
        when = NOW + dt.timedelta(days=day)
        for hour in (0, 1):
            report = run(store, server, when=when + hour * HOUR, sources=["ust"], max_per_host=6)
            steps.append(len(server.urls("treasury")))
            assert report.exit_code == 0
    assert steps == [6, 10, 16, 20, 26, 30, 36, 40, 45, 46]  # six a run, ten a day: Treasury's daily cap
    years = [_year(u) for u in server.urls("treasury")]
    history = [y for y in years if y != 2026]
    assert history == list(range(2025, 1989, -1))  # newest first, each once
    queue = _state(store)["sources"]["ust"]["backfill"]
    assert (len(queue["done"]), queue["queue"], queue["finished"]) == (36, [], "2026-11-22T14:00:00Z")
    lines = st.status(store, now=NOW).lines()
    assert "  history: 36 requests done, 0 to go, finished 2026-11-22T14:00:00Z (plan full, seeded " \
           "2026-11-18T14:00:00Z)" in lines
    capped = run(tmp_path / "t", us_server(), sources=["ust"], max_requests=3)
    assert "33 left for a later run (caps)" not in capped.sources[0].message  # the history is not one queue item
    assert "history: 2 requests done, 34 to go" in capped.sources[0].message


def test_mospi_is_asked_only_when_named_and_reminds_that_its_licence_is_not_settled(tmp_path):
    store, server = tmp_path / "s", us_server(**{"api.mospi.gov.in": ok(fx.NO_DATA)})
    report = run(store, server)
    assert not server.urls("mospi") and not any(s.reminder for s in report.sources)
    assert st.resolve_sources(None) == ("nyfed", "h10", "bis")
    assert st.resolve_sources(["all"]) == ("mospi", "ust", "nyfed", "eia", "h10", "bis")
    named = run(tmp_path / "t", server, sources=["mospi"])
    assert server.urls("mospi") and named.sources[0].reminder.startswith("reminder: MoSPI's licence for a stored "
                                                                         "history is not settled")
    assert named.lines()[0] == (False, "mospi: " + named.sources[0].reminder)
    with pytest.raises(ValueError, match="no source 'rbi'"):
        st.resolve_sources(["rbi"])


_MONTHS = {9: "September", 10: "October", 11: "November"}


def mospi_server(probe_value="110.05", detail_value="110.05", etag='W/"p1"', housing="104.67", november=False,
                 **over) -> Server:
    """A fake MoSPI: October's CPI (and November's first print with ``november``: a release brings a new month)."""
    def cpi(url, headers):
        if "division_code=0" in url:
            if headers.get("If-None-Match") == etag:
                return FeedResponse(304, {}, b"")
            months = [cpi_month("October", combined=probe_value)] + ([cpi_month("November")] if november else [])
            return ok(cpi_answer(*months), etag=etag)
        month = _MONTHS[int(url.split("month_code=")[1].split("&")[0])]
        rows = cpi_month(month, combined=detail_value, housing=housing)  # six rows: two pages of three
        page = int(url.split("page=")[1].split("&")[0])
        return ok(fx.mospi_answer(rows[3 * (page - 1):3 * page], page=page, per=3, total=6))
    routes = {"getCPIData": cpi, "getIIPMonthly": lambda url, h: ok(fx.mospi_answer([fx.iip(2026, "September")],
                                                                                     per=200)),
              "getWpiRecords": ok(fx.mospi_answer([fx.wpi(2026, "October", index="113.3")], per=200)),
              "frequency_code=Annually": ok(fx.mospi_answer([fx.nas("2025-26", None, frequency="Annual",
                                                                    revision="Provisional Estimates")], per=200)),
              "getNASData": ok(fx.mospi_answer([fx.nas("2026-27", "Q1")], per=200)), "api.mospi.gov.in": ok(fx.NO_DATA)}
    routes.update(over)
    return Server(*routes.items())


def test_a_changed_mospi_probe_brings_the_latest_months_and_their_pages_and_an_unchanged_one_nothing(tmp_path):
    store, server = tmp_path / "s", mospi_server()
    report = run(store, server, sources=["mospi"], settings=st.Settings(backfill=False))
    urls = server.urls()
    assert urls[:6] == [r.url for r, _ in st.recent_requests("mospi", NOW)]
    details = [u for u in urls if "month_code" in u and "getCPIData" in u]
    assert len(details) == 12  # October and September, three sectors, pages 1 and 2 of each
    assert any("month_code=9&state_code=1&sector_code=3&limit=100&page=2" in u for u in details)
    assert sum("getIIPMonthly" in u and "type=All" in u for u in urls) == 2  # September and August
    assert sum("major_group_code=1" in u and "1000000000" not in u for u in urls) == 4  # the four major groups
    assert report.exit_code == 0 and report.sources[0].outcome == "updated"
    sent = len(server.requests)
    later = run(store, server, when=NOW + HOUR, sources=["mospi"], settings=st.Settings(backfill=False))
    probe = [h for u, h in server.requests[sent:] if "division_code=0" in u]
    assert probe[0]["If-None-Match"] == 'W/"p1"'  # MoSPI honours its ETag: a 304, and no follow-up
    assert not [u for u in server.urls()[sent:] if "month_code" in u and "getCPIData" in u]
    assert later.sources[0].outcomes.get("not_modified") == 1
    assert not any(s.startswith("raw/mospi") for s in _files(store))  # no MoSPI copies unless asked


def test_follow_ups_left_by_the_caps_wait_in_pending_for_the_next_run(tmp_path):
    store, server = tmp_path / "s", mospi_server()
    first = run(store, server, sources=["mospi"], settings=st.Settings(backfill=False), max_per_host=8)
    assert len(server.requests) == 8 and "left for a later run (caps)" in first.sources[0].message
    pending = _state(store)["sources"]["mospi"]["pending"]
    assert len(pending) == 12 and {p["role"] for p in pending} == {"top", "all", "page"}  # 4 CPI, 2 IIP, 4 WPI, 2
    # pages; CPI's first pages ("top": --cpi-levels groups) have pages after them, as they lack top-level nodes
    run(store, server, when=NOW + HOUR, sources=["mospi"], settings=st.Settings(backfill=False), max_per_host=30)
    assert _state(store)["sources"]["mospi"]["pending"] == []
    follow_ups = [u for u in server.urls() if "month_code" in u or "major_group_code=1" in u and "1000000000" not in u]
    assert len(follow_ups) == len(set(follow_ups)) == 6 * 2 + 2 + 4  # each asked once, across the two runs


def test_a_mospi_change_seen_after_the_store_last_read_the_old_value_is_dated_by_its_sighting(tmp_path):
    store = tmp_path / "s"
    release = dt.datetime(2026, 12, 14, 10, 30, tzinfo=UTC)  # 16:00 in India: the planned final of October
    run(store, mospi_server(etag='W/"a"'), when=release - 6 * HOUR, sources=["mospi"],
        settings=st.Settings(backfill=False))
    run(store, mospi_server(etag='W/"b"'), when=release + 5 * MINUTE, sources=["mospi"],
        settings=st.Settings(backfill=False))  # 16:05: MoSPI's API still serves the first print
    run(store, mospi_server(probe_value="110.10", detail_value="110.10", etag='W/"c"'), when=release + 2 * HOUR,
        sources=["mospi"], settings=st.Settings(backfill=False))
    first, second = (v for v in _vintages(store, "mospi", "cpi_2024_all_india_combined_general")
                     if v["period"] == "2026-10")
    assert first["public_from"] == "2026-11-12T16:00:00"
    assert (second["value"], second["prior_seen"], second["public_from"], second["public_from_basis"]) == (
        "110.10", "2026-12-14T16:06:00", "2026-12-14T18:01:00", "first_seen")  # the probe read at 16:05 (by 16:06 at
    # the latest), after 16:00, still held the first print; the change came with the 18:00 run's first answer
    assert "after the store last read the earlier value" in second["public_from_rule"]


def test_a_block_cools_the_host_until_eight_the_next_india_day_and_a_second_stops_it(tmp_path):
    store = tmp_path / "s"
    server = us_server(dat00_in=[FeedResponse(403, {}, b"no"), FeedResponse(418, {}, b"no"), ok(H10)])
    blocked = run(store, server, sources=["h10"])
    assert (blocked.exit_code, blocked.sources[0].outcome) == (3, "blocked")
    assert "no request to www.federalreserve.gov before 2026-11-19T02:30:00Z" in blocked.sources[0].message
    host = _state(store)["hosts"]["www.federalreserve.gov"]
    assert (host["blocks"], host["cooldown_until"]) == (1, "2026-11-19T02:30:00Z")
    assert len(server.requests) == 1  # nothing more to that host in the run, not even the history
    cooling = run(store, server, when=NOW + 2 * HOUR, sources=["h10"])
    assert (cooling.exit_code, cooling.sources[0].outcome, len(server.requests)) == (3, "cooling_down", 1)
    stopped = run(store, server, when=dt.datetime(2026, 11, 19, 3, 0, tzinfo=UTC), sources=["h10"])
    assert (stopped.exit_code, stopped.sources[0].outcome, len(server.requests)) == (3, "blocked", 2)
    assert "2 blocks in a row, so www.federalreserve.gov is stopped" in stopped.sources[0].message
    still = run(store, server, when=dt.datetime(2026, 11, 21, 3, 0, tzinfo=UTC), sources=["h10"])
    assert (still.sources[0].outcome, len(server.requests)) == ("stopped", 2)
    assert any("stopped since 2026-11-19T03:00:00Z" in line for line in st.status(store).lines())
    resumed = run(store, server, when=dt.datetime(2026, 11, 21, 4, 0, tzinfo=UTC), sources=["h10"], resume=True)
    assert (resumed.exit_code, resumed.sources[0].outcome) == (0, "updated")
    host = _state(store)["hosts"]["www.federalreserve.gov"]
    assert (host["blocks"], host["stopped"], host["resumed"]) == (0, None, "2026-11-21T04:00:00Z")


@pytest.mark.parametrize("answer, why", [
    (ok(fx.AKAMAI_PAGE), "Access Denied"), (ok(fx.CHALLENGE_PAGE), "challenge"),
    (ok(fx.CONTACT_PAGE), "contact details"), (FeedResponse(429, {}, b""), "HTTP 429")])
def test_refusal_pages_and_demands_for_contact_details_are_blocks(tmp_path, answer, why):
    report = run(tmp_path / "s", us_server(dat00_in=answer), sources=["h10"])
    assert report.exit_code == 3 and why in report.sources[0].message
    if why == "contact details":
        assert any("none was given" in a for a in report.sources[0].attention)
        assert _state(tmp_path / "s")["hosts"]["www.federalreserve.gov"]["contact_demand"]


def test_errors_and_answers_that_need_a_look_have_their_exit_codes(tmp_path):
    down = run(tmp_path / "a", us_server(dat00_in=FeedResponse(503, {}, b"")), sources=["h10"])
    assert (down.exit_code, down.sources[0].outcome) == (1, "error")
    assert down.sources[0].message.startswith("server error: HTTP 503")
    assert down.lines()[0] == (True, "h10: error: " + down.sources[0].message)
    moved = run(tmp_path / "b", us_server(dat00_in=FeedResponse(301, {"location": "https://elsewhere/"}, b"")),
                sources=["h10"])
    assert moved.exit_code == 1 and "not followed" in moved.sources[0].message
    wrong = run(tmp_path / "c", us_server(WS_XRU=FeedResponse(404, {}, b"")), sources=["bis"])
    assert wrong.exit_code == 4 and any("HTTP 404" in a for a in wrong.sources[0].attention)
    refused = run(tmp_path / "d", us_server(dat96_in=ok(b"<html><body><table><tr><td>x</td></tr></table></body>"
                                                         b"</html>")), sources=["h10"])
    assert refused.exit_code == 4 and any("refused" in a for a in refused.sources[0].attention)
    failed = _state(tmp_path / "d")["sources"]["h10"]["failed"]
    assert len(failed) == 1 and failed[0]["request"]["url"].endswith("dat96_in.htm")
    assert failed[0]["raw"].startswith("raw/h10/")  # kept, to be read again after a fix
    assert _state(tmp_path / "d")["sources"]["h10"]["backfill"]["queue"] == []  # given up, not retried
    retried = run(tmp_path / "d", us_server(), when=NOW + HOUR, sources=["h10"], retry_failed=True)
    assert retried.exit_code == 0 and _state(tmp_path / "d")["sources"]["h10"]["failed"] == []


def test_the_attempt_is_committed_before_the_request_and_a_run_killed_during_it_is_logged(tmp_path):
    store = tmp_path / "s"

    class Killed(BaseException):
        pass

    server = us_server(dat00_in=Killed())
    with pytest.raises(Killed):
        run(store, server, sources=["h10"])
    state = _state(store)
    assert state["sources"]["h10"]["in_flight"] == [{"url": "https://www.federalreserve.gov/releases/h10/hist/"
                                                            "dat00_in.htm", "time": "2026-11-18T14:00:00Z"}]
    assert state["hosts"]["www.federalreserve.gov"]["last_request"] == "2026-11-18T14:00:00Z"
    report = run(store, us_server(), when=NOW + HOUR, sources=["h10"])
    assert report.exit_code == 4 and report.interrupted
    (killed, *rest) = _rows(store / "fetches.csv")
    assert (killed["outcome"], killed["time"]) == ("interrupted", "2026-11-18T14:00:00Z") and rest
    assert _state(store)["sources"]["h10"]["in_flight"] is None


def test_a_clock_that_moved_back_or_a_damaged_state_sends_nothing(tmp_path):
    store, server = tmp_path / "s", us_server()
    run(store, server, sources=["h10"])
    sent = len(server.requests)
    back = run(store, server, when=NOW - HOUR, sources=["h10"])
    assert (back.exit_code, back.sources[0].outcome, len(server.requests)) == (1, "clock_error", sent)
    state = _state(store)
    state["hosts"]["www.federalreserve.gov"]["blocks"] = "two"
    (store / "state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(st.StoreError, match="blocks must be a whole number"):
        run(store, server, when=NOW + 2 * HOUR, sources=["h10"])
    assert len(server.requests) == sent


@pytest.mark.parametrize("kwargs, message", [
    (dict(sources=["rbi"]), "no source 'rbi'"), (dict(max_requests=0), "max_requests"),
    (dict(max_per_host=501), "max_per_host"), (dict(min_interval=dt.timedelta(days=8)), "min_interval"),
    (dict(user_agent="bot (me@example.com)"), "names someone"), (dict(timeout=0), "timeout"),
    (dict(now=dt.datetime(2026, 11, 18)), "timezone-aware")])
def test_bad_arguments_are_refused_before_anything_is_recorded(tmp_path, kwargs, message):
    store, server = tmp_path / "s", us_server()
    kwargs.setdefault("now", NOW)
    with pytest.raises(ValueError, match=message):
        st.update(store, transport=server, sleep=Sleep(), **kwargs)
    assert not store.exists() and not server.requests


# --- Atomic writes and the lock -------------------------------------------------------------------------

class _Crash(Exception):
    pass


def _crash_on(monkeypatch, name, call):
    """Make ``os.<name>`` (which storekit's writes call) raise on its ``call``-th use, as a crash would stop there."""
    real = getattr(storekit.os, name)
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        if len(calls) == call:
            raise _Crash(f"{name} call {call}")
        return real(*args, **kwargs)

    monkeypatch.setattr(storekit.os, name, fake)


def test_a_crash_at_any_rename_of_an_ingest_ends_in_the_same_store(tmp_path, monkeypatch):
    clean = tmp_path / "clean"
    st.ingest(clean, answers()[:3], now=NOW)
    renames = len(_files(clean)) + 1  # every file, and the journal first
    for call in range(1, renames + 1):
        crashed = tmp_path / f"crash{call}"
        _crash_on(monkeypatch, "replace", call)
        with pytest.raises(_Crash):
            st.ingest(crashed, answers()[:3], now=NOW)
        monkeypatch.undo()
        assert (crashed / "journal.json").exists() == (call > 1)  # the journal's rename is the commit point
        assert not (crashed / "store.lock").exists()
        if call > 1:
            assert st.status(crashed).interrupted
        st.ingest(crashed, [], now=NOW + HOUR)  # completes the commit, or finds the store as it was
        st.ingest(crashed, answers()[:3], now=NOW)  # then the same ingest again
        assert _files(crashed) == _files(clean), call


def test_a_crash_while_temporary_files_are_written_leaves_the_store_as_it_was(tmp_path, monkeypatch):
    store = tmp_path / "s"
    st.ingest(store, answers()[:2], now=NOW)
    before = _files(store)
    _crash_on(monkeypatch, "fsync", 2)
    with pytest.raises(_Crash):
        st.ingest(store, answers()[2:4], now=NOW + HOUR)
    monkeypatch.undo()
    assert {k: v for k, v in _files(store).items() if not k.endswith(".tmp-macrostore")} == before
    report = st.ingest(store, answers()[2:4], now=NOW + HOUR)
    assert report.new == 8 and not any(p.name.endswith(".tmp-macrostore") for p in store.rglob("*"))


def test_an_update_killed_after_a_sources_commit_keeps_that_source_and_says_the_tables_are_out_of_date(tmp_path,
                                                                                                    monkeypatch):
    store = tmp_path / "s"
    real = st.derive

    class Killed(BaseException):  # not an error the rebuild catches: the process ends there
        pass

    def broken(data):
        raise Killed("killed while the tables were rebuilt")

    monkeypatch.setattr(st, "derive", broken)
    with pytest.raises(Killed):
        run(store, us_server(), sources=["h10"])
    monkeypatch.setattr(st, "derive", real)
    assert _vintages(store, "h10", "usdinr_h10_noon")  # the source's commit came first
    assert _state(store)["derived_stale"] == "2026-11-18T14:00:00Z"
    assert any("out of date since 2026-11-18T14:00:00Z" in line for line in st.status(store).lines())
    st.ingest(store, [], now=NOW + HOUR)
    assert _state(store)["derived_stale"] is None and (store / "derived" / "global_daily.csv").exists()


def test_a_failed_rebuild_of_the_tables_keeps_the_values_and_the_previous_tables(tmp_path, monkeypatch):
    store = tmp_path / "s"
    st.ingest(store, answers()[:1], now=NOW)
    tables = {k: v for k, v in _files(store).items() if k.startswith("derived/")}

    def bug(data):
        raise ValueError("a bug")

    monkeypatch.setattr(st, "derive", bug)
    report = st.ingest(store, answers()[1:2], now=NOW + HOUR)
    assert report.derive_error == "ValueError: a bug" and report.new == 16
    assert {k: v for k, v in _files(store).items() if k.startswith("derived/")} == tables
    assert _state(store)["derived"] == {"error": "ValueError: a bug", "since": "2026-11-18T15:00:00Z"}
    monkeypatch.undo()
    st.ingest(store, [], now=NOW + 2 * HOUR)
    assert _state(store)["derived"] is None


def _lock(store: Path, **info):
    store.mkdir(parents=True, exist_ok=True)
    (store / "store.lock").write_text(json.dumps({"command": "update", "pid": 4242, "host": "another-machine",
                                                  "since": "2026-11-18T13:30:00Z", **info}), encoding="utf-8")


def test_a_held_lock_refuses_every_command_and_is_never_broken(tmp_path):
    store, server = tmp_path / "s", us_server()
    _lock(store)
    with pytest.raises(st.StoreLockedError) as held:
        run(store, server)
    assert str(held.value) == (f"store {store} is locked by update (pid 4242 on another-machine) since "
                               f"2026-11-18T13:30:00Z. Wait for that command to end; if none is running, delete "
                               f"{store / 'store.lock'}.")
    assert not server.requests
    with pytest.raises(st.StoreLockedError, match=r"has a stale lock .*: it is 3\.5 hours old\. If no macro-store "
                                                  r"command is running, delete .*store\.lock and run again"):
        st.ingest(store, answers()[:1], now=NOW + 3 * HOUR)
    assert (store / "store.lock").exists() and not (store / "series.csv").exists()
    other = tmp_path / "t"
    st.ingest(other, answers()[:1], now=NOW)
    _lock(other)
    assert st.status(other, now=NOW).lock.startswith(f"store {other} is locked by update")
    assert any(line.startswith("locked: ") for line in st.status(other, now=NOW).lines())


def test_a_lock_left_by_a_process_that_ended_is_reported_as_stale(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    store = tmp_path / "s"
    _lock(store, pid=child.pid, host=socket.gethostname(), since="2026-11-18T13:59:00Z")
    with pytest.raises(st.StoreLockedError, match=f"process {child.pid} is not running"):
        st.ingest(store, [], now=NOW)


def test_the_store_refuses_files_that_are_not_its_own_or_do_not_agree(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, answers()[:1], now=NOW)
    path = store / "observations" / "nyfed.csv"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("sofr,2026-11-16,", "sofr,2026-11-16-x,", 1), encoding="utf-8")
    with pytest.raises(st.StoreError, match=r"nyfed\.csv, line \d+: period '2026-11-16-x'"):
        st.ingest(store, [], now=NOW)
    path.write_text(text.replace(",1,3.93,", ",2,3.93,", 1), encoding="utf-8")
    with pytest.raises(st.StoreError, match="vintages must run 1, 2"):
        st.status(store)
    path.write_text(text, encoding="utf-8")
    (store / "state.json").write_text('{"format": 9}', encoding="utf-8")
    with pytest.raises(st.StoreError, match="not a macro store state of format 1"):
        st.ingest(store, [], now=NOW)


# --- Derived tables --------------------------------------------------------------------------------------

def test_global_daily_has_a_row_per_date_with_each_groups_public_from_and_the_rows(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, answers(), now=NOW)
    st.ingest(store, [("n3.json", secured(("2026-11-13", "3.91")))], now=NOW + 2 * HOUR)  # a revision
    rows = {r["date"]: r for r in _rows(store / "derived" / "global_daily.csv")}
    assert list(_rows(store / "derived" / "global_daily.csv")[0]) == list(st.GLOBAL_DAILY_COLUMNS)
    day = rows["2026-11-13"]
    assert (day["sofr"], day["effr"], day["fomc_target_low"], day["fomc_target_high"]) == ("3.91", "3.88", "3.75",
                                                                                         "4.00")
    assert (day["usdinr_h10"], day["usdinr_h10_public_from"]) == ("94.2400", "2026-11-18T02:45:00")
    assert day["nyfed_secured_public_from"] == "2026-11-18T21:30:00"  # the revision: dated by its sighting
    assert day["nyfed_unsecured_public_from"] == "2026-11-16T21:30:00"  # 11:00 New York on the next date
    assert day["brent_usd_bbl"] == "95.17"
    assert day["public_from"] == "2026-11-18T21:30:00" and day["revised"] == "2026-11-18T21:30:00"
    assert day["seen"] == "later"
    assert rows["2026-11-16"]["notes"] == ("marked revised (r) by the New York Fed: effr, fomc_target_low, "
                                           "fomc_target_high")  # the target comes from the EFFR row
    assert rows["2026-10-07"]["policy_rate_in_bis"] == "5.5" and rows["2026-10-07"]["sofr"] == ""
    assert "2001-04-07" not in rows  # a missing value (NaN) is no row, and nothing is filled forward


def test_india_monthly_has_a_row_per_print_with_every_measure_as_known_then(tmp_path):
    store = tmp_path / "s"
    first = dt.datetime(2026, 11, 12, 10, 40, tzinfo=UTC)
    st.ingest(store, [("m1.json", cpi_answer(cpi_month("October")))], now=first)
    final = dt.datetime(2026, 12, 14, 10, 50, tzinfo=UTC)
    st.ingest(store, [("m2.json", cpi_answer(cpi_month("October", combined="110.10", yoy="6.14")))], now=final)
    rows = [r for r in _rows(store / "derived" / "india_monthly.csv") if r["month"] == "2026-10"]
    assert [(r["print"], r["public_from"]) for r in rows] == [("first", "2026-11-12T16:00:00"),
                                                              ("final", "2026-12-14T16:00:00")]
    one, two = rows
    assert (one["cpi_combined_index"], one["cpi_combined_yoy"], one["cpi_food_index"]) == ("110.05", "6.09", "113.23")
    assert (two["cpi_combined_index"], two["cpi_combined_yoy"], two["cpi_food_index"]) == ("110.10", "6.14", "113.23")
    assert two["cpi_food_public_from"] == "2026-11-12T16:00:00"  # unchanged since the first print
    assert two["first_public_from"] == "2026-11-12T16:00:00" and two["revised"] == "2026-12-14T16:20:00"
    assert "public earlier than the row: cpi_food_public_from" in two["notes"]
    assert (one["seen"], one["release_id"], one["stage"], one["family"], one["base"]) == (
        "live", "cpi_2026-10", "first", "cpi", "2024")
    assert one["cpi_housing_index"] == "104.67" and one["cpi_rent_index"] == ""


def test_india_monthly_computes_wpi_inflation_on_one_base_and_notes_iips_missing_growth(tmp_path):
    store = tmp_path / "s"
    wpi = fx.mospi_answer([fx.wpi(2025, "October", index="100.9"), fx.wpi(2026, "October", index="113.3")], per=200)
    iip = fx.mospi_answer([fx.iip(2023, "April", index="100.4", growth="0.4"), fx.iip(2026, "September")], per=200)
    st.ingest(store, [("w.json", wpi), ("i.json", iip)], now=NOW)
    rows = {(r["family"], r["month"]): r for r in _rows(store / "derived" / "india_monthly.csv")}
    october = rows[("wpi", "2026-10")]
    assert (october["wpi_index"], october["wpi_yoy"], october["print"]) == ("113.3", "12.29", "first")
    assert "wpi_yoy computed here from the indices of 2026-10 and 2025-10 on base 2022-23" in october["notes"]
    assert rows[("wpi", "2025-10")]["wpi_yoy"] == ""
    april = rows[("iip", "2023-04")]
    assert (april["iip_index"], april["iip_yoy"]) == ("100.4", "")  # the index minus 100, dropped by the parser
    assert "iip_yoy: none published for FY2023-24 on base 2022-23" in april["notes"]


def test_india_quarterly_computes_growth_from_the_year_ago_quarter_as_known_then(tmp_path):
    store = tmp_path / "s"
    gdp = fx.mospi_answer([fx.nas("2025-26", "Q1", current="900", constant="1000"),
                           fx.nas("2026-27", "Q1", current="980", constant="1078.2")], per=200)
    st.ingest(store, [("g.json", gdp)], now=NOW)
    rows = {r["quarter"]: r for r in _rows(store / "derived" / "india_quarterly.csv")}
    q1 = rows["2026-27Q1"]
    assert (q1["gdp_current_cr"], q1["gdp_constant_cr"], q1["gdp_constant_yoy"], q1["base"]) == (
        "980", "1078.2", "7.82", "2022-23")
    assert (q1["print"], q1["stage"], q1["public_from"]) == ("first", "quarter", "2026-08-31T16:00:00")
    assert rows["2025-26Q1"]["gdp_constant_yoy"] == "" and rows["2025-26Q1"]["stage"] == "revised"


def test_index_events_come_only_from_the_table_the_user_gives(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, answers()[:1], now=NOW)
    assert _rows(store / "derived" / "index_events.csv") == []  # nothing shipped as a default
    assert any("index events: none given" in line for line in st.status(store).lines())
    st.ingest(store, [], now=NOW + HOUR, index_events=EXAMPLE_EVENTS)
    rows = {r["event_id"]: r for r in _rows(store / "derived" / "index_events.csv")}
    assert len(rows) == 13 and (store / "index_events.csv").read_bytes() == EXAMPLE_EVENTS.read_bytes()
    ftse = rows["idx_ftse_emgbi_2024_announce"]
    assert (ftse["status"], ftse["public_from"], ftse["public_from_basis"]) == ("verified", "2024-10-09T04:29:59",
                                                                                "publication_rule")
    assert "unverified" in rows["idx_jpm_gbiem_2023_announce"]["public_from_rule"]
    assert any("index events: 13 in the table given (3 verified" in line for line in st.status(store).lines())
    bad = _write(tmp_path / "bad.csv", EXAMPLE_EVENTS.read_bytes().replace(b",verified,", b",rumoured,", 1))
    with pytest.raises(ValueError, match="bad.csv, line .*status 'rumoured'"):
        st.ingest(store, [], now=NOW, index_events=bad)


def test_bis_policy_rates_export_in_rbi_stores_columns_with_the_corridor_left_blank(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("b.csv", CBPOL)], now=NOW)
    rows = _rows(store / "derived" / "policy_rates_bis.csv")
    assert [(r["effective_date"], r["repo_pct"], r["sdf_pct"], r["msf_pct"]) for r in rows] == [
        ("2001-04-03", "8.5", "", ""), ("2026-10-06", "5.25", "", ""), ("2026-10-07", "5.5", "", "")]
    assert "fill them in from RBI's resolution" in rows[0]["source"]
    export = store / "derived" / "policy_rates_bis.csv"
    with pytest.raises(ValueError, match="sdf_pct '' is not a rate"):
        rbi_store.read_policy_rates(export)  # as documented: refused until the corridor is filled in
    filled = export.read_text(encoding="utf-8").replace(",8.5,,,", ",8.5,7.5,9.5,").replace(",5.25,,,", ",5.25,5,5.5,")
    filled = filled.replace(",5.5,,,", ",5.5,5.25,5.75,")
    assert [r["repo_pct"] for r in rbi_store.read_policy_rates(filled.encode())] == ["8.5", "5.25", "5.5"]


# --- Reading back, status, plans ---------------------------------------------------------------------------

def test_read_table_types_every_table_and_status_reports_what_the_store_holds(tmp_path):
    store = tmp_path / "s"
    run(store, us_server())
    st.ingest(store, answers(), now=NOW + HOUR)
    obs = st.read_table(store, "observations")
    assert str(obs["value"].dtype) == "float64" and str(obs["vintage"].dtype) == "Int64"
    assert obs["public_from"].dtype.kind == "M" and obs["period_start"].dtype.kind == "M"
    assert obs["period"].map(type).eq(str).all()
    fetches = st.read_table(store, "fetches")
    assert str(fetches["time"].dt.tz) == "UTC" and str(fetches["http_status"].dtype) == "Int64"
    daily = st.read_table(store, "global_daily")
    assert daily["sofr"].dtype == float and daily["date"].dtype.kind == "M"
    assert st.read_table(store, "india_monthly")["cpi_combined_index"].notna().any()
    with pytest.raises(ValueError, match="no store table"):
        st.read_table(store, "nope")
    lines = st.status(store, now=NOW).lines()
    assert lines[0] == f"store {store}"
    assert any(line.startswith("nyfed (on by default): last update 2026-11-18T14:00:00Z: updated") for line in lines)
    assert any(line.startswith("mospi (named only: licence not settled): never updated") for line in lines)
    assert any("host markets.newyorkfed.org: last request 2026-11-18T14:00:30Z: ok (HTTP 200)" in line
               for line in lines)
    assert any(line.startswith("  sofr: ") for line in lines)
    assert st.status(store).lines("^usdinr")[-3:] == [
        "series matching '^usdinr':",
        "  usdinr_bis_daily: 1 period 2026-11-10 to 2026-11-10 (1 vintage); last 2026-11-10 = 93.928571, public from "
        "2026-11-18T19:38:50",  # its sighting: BIS's weekly release rule comes later
        "  usdinr_h10_noon: 3 periods 1990-01-02 to 2026-11-13 (3 vintages); last 2026-11-13 = 94.2400, public from "
        "2026-11-18T02:45:00"]
    assert "  14 series, 42 periods (50 vintages, 8 periods revised), 2026-11-13 to 2026-11-17" in lines  # the EFFR
    # rows' revision mark (r) arrived with the saved answer: a change of mark is a new vintage
    with pytest.raises(st.StoreError, match="no macro store here"):
        st.status(tmp_path / "none")


def test_series_csv_keeps_what_llms_txt_asks_and_the_citation_each_publisher_wants(tmp_path):
    store = tmp_path / "s"
    run(store, mospi_server(), sources=["mospi"], settings=st.Settings(backfill=False))
    st.ingest(store, answers()[:2], now=NOW + HOUR)
    series = {r["series_id"]: r for r in _rows(store / "series.csv")}
    cpi = series["cpi_2024_all_india_combined_general"]
    assert (cpi["dataset"], cpi["unit"], cpi["frequency"], cpi["period_format"]) == (
        "eSankhyiki CPI, base 2024, Current series", "index, 2024=100", "monthly", "YYYY-MM")
    assert cpi["indicator"] == "Consumer Price Index, All India, Combined, CPI (General)"
    assert cpi["source_url"] == "https://api.mospi.gov.in/api/cpi/getCPIData"  # the endpoint, kept once recorded
    assert cpi["citation"].startswith("Source: Ministry of Statistics and Programme Implementation (MoSPI), "
                                      "Government of India, eSankhyiki (eSankhyiki CPI, base 2024, Current series), "
                                      "https://api.mospi.gov.in/api/cpi/getCPIData, retrieved ")
    assert cpi["citation"].endswith(", retrieved 2026-11-18")
    assert cpi["update_info"].startswith("monthly: the 12th of the next month")  # its family's own rule
    sofr = series["sofr"]
    assert sofr["citation"].startswith("The Secured Overnight Financing Rate is subject to the Terms of Use posted "
                                       "at newyorkfed.org.")
    assert (sofr["in_catalogue"], sofr["terms_url"]) == ("True", "https://www.newyorkfed.org/privacy/termsofuse")


def test_the_history_plans_and_a_changed_plan_adds_what_it_lacks(tmp_path):
    derived = [r.url for r, _ in st.history_requests("mospi", NOW)]
    full = [r.url for r, _ in st.history_requests("mospi", NOW, st.Settings(mospi_history="full", cpi_levels="items"))]
    states = [r.url for r, _ in st.history_requests("mospi", NOW, st.Settings(states=True))]
    assert len(derived) == 1 + 20 * 3 + 13 + 4 + 3 + 1  # back series; Jan 2025 to Aug 2026 by sector; ...
    assert len(full) == len(derived) + 13 + 2 and set(derived) <= set(full)  # base-2012 items, IIP use-based
    assert len(states) == len(derived) + 36
    roles = dict(st.history_requests("mospi", NOW))
    assert {roles[r] for r in roles if "month_code" in r.url} == {"top"}  # the first page, checked
    assert [_year(r.url) for r, _ in st.history_requests("ust", NOW)] == list(range(2025, 1989, -1))
    january = dt.datetime(2027, 1, 3, 12, tzinfo=UTC)
    assert [_year(r.url) for r, _ in st.recent_requests("ust", january)] == [2027, 2026]
    store, server = tmp_path / "s", mospi_server()
    run(store, server, sources=["mospi"], max_per_host=6)
    seeded = _state(store)["sources"]["mospi"]["backfill"]
    assert (seeded["plan"], seeded["planned"]) == ("derived/groups/all_india", len(derived))
    run(store, server, when=NOW + HOUR, sources=["mospi"], max_per_host=6, settings=st.Settings(states=True))
    grown = _state(store)["sources"]["mospi"]["backfill"]
    assert grown["plan"] == "derived/groups/states" and grown["planned"] == len(derived) + 36


def test_a_first_cpi_page_with_every_top_level_node_needs_no_other_page(tmp_path):
    complete = [fx.cpi24(2026, "October", "Combined")]
    complete += [fx.cpi24(2026, "October", "Combined", division=f"d{i}", code=f"{i:02d}") for i in range(1, 13)]
    complete += [fx.cpi24(2026, "October", "Combined", division="d1", group=f"g{i}", code=f"01.{i}")
                 for i in range(1, 44)]
    page1 = fx.mospi_answer(complete, per=56, total=112)
    request = mospi.cpi_2024_request(year=2026, month=10, sector_code=3)
    result = mospi.parse_mospi(page1, "p1", kind=mospi.CPI_2024)
    assert st.follow_ups(request, "top", result, NOW) == []
    assert [(r.url.endswith("page=2&Format=JSON"), role) for r, role in st.follow_ups(request, "all", result, NOW)] \
        == [(True, "page")]
    short = mospi.parse_mospi(fx.mospi_answer(complete[:-1], per=55, total=110), "p1", kind=mospi.CPI_2024)
    assert len(st.follow_ups(request, "top", short, NOW)) == 1  # a group missing: every other page


def test_raw_copies_are_kept_once_and_mospis_only_when_asked(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, answers(), now=NOW, settings=st.Settings(mospi_raw=True))
    assert len([n for n in _files(store) if n.startswith("raw/mospi/")]) == 1
    st.ingest(store, answers(), now=NOW + HOUR, settings=st.Settings(mospi_raw=True))
    assert len([n for n in _files(store) if n.startswith("raw/")]) == 8  # the same bytes are not saved twice
    bare = tmp_path / "t"
    st.ingest(bare, answers(), now=NOW, settings=st.Settings(keep_raw=False))
    assert not (bare / "raw").exists()
    sources = {r["source_file"] for r in _rows(bare / "observations" / "nyfed.csv")}
    assert sources == {"n1_secured.json", "n2_unsecured.json"}


def test_a_mospi_revision_is_not_dated_before_the_probe_last_saw_the_old_release(tmp_path):
    store = tmp_path / "s"
    first = dt.datetime(2026, 11, 12, 11, 0, tzinfo=UTC)  # 16:30 in India on the day of October's first print
    release = dt.datetime(2026, 12, 14, 10, 30, tzinfo=UTC)  # 16:00: October's final is due
    off = st.Settings(backfill=False)
    run(store, mospi_server(etag='W/"a"'), when=first, sources=["mospi"], settings=off)
    run(store, mospi_server(etag='W/"a"'), when=release + 5 * MINUTE, sources=["mospi"], settings=off)  # 304
    run(store, mospi_server(probe_value="110.10", detail_value="110.10", housing="104.90", etag='W/"b"', november=True),
        when=release + 2 * HOUR, sources=["mospi"], settings=off)
    old, new = (v for v in _vintages(store, "mospi", "cpi_2024_all_india_combined_d04") if v["period"] == "2026-10")
    assert old["public_from"] == "2026-11-12T16:00:00" and old["seen"] == "live"
    # the housing index came from a table page last read a month ago (prior_seen), but the CPI probe answered 304 at
    # 16:05 without November, the month the release of October's final brings: that release had not reached the API
    # then, so the change is dated by its sighting at 18:00, not by 16:00
    assert (new["value"], new["prior_seen"], new["public_from"], new["public_from_basis"]) == (
        "104.90", "2026-11-12T16:52:00", "2026-12-14T18:25:30", "first_seen")  # the table's page read at 18:25
    assert "the store read the family's headline at 2026-12-14T16:06:00 without the release" in new["public_from_rule"]
    (november,) = _vintages(store, "mospi", "cpi_2024_all_india_rural_general")[-1:]
    assert (november["period"], november["prior_seen"], november["public_from"], november["public_from_basis"]) == (
        "2026-11", "2026-12-14T16:06:00", "2026-12-14T18:01:00", "first_seen")  # the new month itself, too: the
    # probe read at 16:05 lacked it, so the release came after 16:00


def test_no_backfill_leaves_the_history_queue_for_later_and_files_written_are_counted(tmp_path):
    store, server = tmp_path / "s", us_server()
    first = run(store, server, sources=["ust"], max_per_host=3)
    queue = _state(store)["sources"]["ust"]["backfill"]["queue"]
    assert len(queue) == 34 and "observations/ust.csv" in first.written  # a source's commit counts
    run(store, server, when=NOW + HOUR, sources=["ust"], settings=st.Settings(backfill=False))
    assert len(server.urls("treasury")) == 4  # this year's file only
    assert _state(store)["sources"]["ust"]["backfill"]["queue"] == queue  # kept for a run with the history on


# --- Never early: what the store's own reads say, per answer -----------------------------------------------------

def test_a_new_value_absent_from_the_stores_read_after_its_rule_time_is_dated_by_its_sighting(tmp_path):
    # EIA's release of Wednesday 25 Nov 2026 slipped: the page read on Friday 27 Nov (after the rule's Thursday
    # 23:59:59) still ended at 17 Nov; the prices of 18 to 24 Nov came with the read of Monday 30 Nov
    store = tmp_path / "s"
    early = fx.eia_page([("2026 Nov-16 to Nov-20", ["92.90", "92.94", "", "", ""])])
    late = fx.eia_page([("2026 Nov-16 to Nov-20", ["92.90", "92.94", "93.00", "93.10", "93.20"]),
                        ("2026 Nov-23 to Nov-27", ["93.30", "93.40", "", "", ""])])
    server = Server(("eia.gov", [ok(early, etag='"a"'), ok(late, etag='"b"')]))
    run(store, server, when=dt.datetime(2026, 11, 27, 15, 0, tzinfo=UTC), sources=["eia"])
    run(store, server, when=dt.datetime(2026, 11, 30, 15, 0, tzinfo=UTC), sources=["eia"])
    v = {r["period"]: r for r in _vintages(store, "eia", "brent_spot_usd_bbl")}
    assert v["2026-11-17"]["public_from"] == "2026-11-20T10:29:59"  # read in time: the rule
    assert (v["2026-11-18"]["prior_seen"], v["2026-11-18"]["public_from"], v["2026-11-18"]["public_from_basis"]) == (
        "2026-11-27T20:31:00", "2026-11-30T20:31:00", "first_seen")  # the rule said 27 Nov 10:29:59
    assert "without this value" in v["2026-11-18"]["public_from_rule"]
    assert st.as_of(store, "brent_spot_usd_bbl", ist(2026, 11, 28, 12, 0))["period"].max() == "2026-11-17"


def test_a_mospi_release_later_than_its_planned_date_is_dated_by_its_sighting(tmp_path):
    # GDP of 2026-27 Q2 is planned for 30 Nov 2026 16:00; the store's reads at 16:05 and the next morning lack it,
    # the one at 16:05 on 1 Dec has it: public from that sighting, not from 30 Nov
    store, off = tmp_path / "s", st.Settings(backfill=False)
    gdp = [fx.nas("2026-27", "Q1", current="980", constant="900")]
    with_q2 = gdp + [fx.nas("2026-27", "Q2", current="990", constant="910")]
    quarterly = [ok(fx.mospi_answer(gdp, per=200), etag='W/"1"'), FeedResponse(304, {}, b""),
                 ok(fx.mospi_answer(with_q2, per=200), etag='W/"2"')]
    base = mospi_server()
    server = Server(("frequency_code=Quarterly&indicator_code=5", quarterly), *base.routes)
    for when in (dt.datetime(2026, 11, 30, 10, 35, tzinfo=UTC), dt.datetime(2026, 12, 1, 4, 0, tzinfo=UTC),
                 dt.datetime(2026, 12, 1, 10, 35, tzinfo=UTC)):
        run(store, server, when=when, sources=["mospi"], settings=off)
    (q2,) = [v for v in _vintages(store, "mospi", "gdp_2022_23_q_constant_cr") if v["period"] == "2026-27Q2"]
    # the GDP query is the fourth request of a run: sent 30 s after its start, by four timeouts later at the latest
    assert (q2["prior_seen"], q2["public_from_basis"]) == ("2026-12-01T09:34:30", "first_seen")
    assert q2["public_from"] == q2["first_seen"] == "2026-12-01T16:09:30"
    assert st.as_of(store, "gdp_2022_23_q_constant_cr", ist(2026, 11, 30, 20, 0))["period"].tolist() == ["2026-27Q1"]


def test_a_value_is_first_seen_when_its_own_answer_can_have_come_not_at_the_runs_start(tmp_path):
    # the run starts at 07:59:45 New York time; its first answer has no SOFR of 17 Nov (published at 08:00), the
    # history's, two requests later, has it: first seen at the latest that answer can have come
    store = tmp_path / "s"
    start = dt.datetime(2026, 11, 18, 12, 59, 45, tzinfo=UTC)
    server = Server(("secured/all/search.json?startDate=2026-11-04", ok(secured(("2026-11-16", "3.93")))),
                    ("secured/all/search.json?startDate=2018", ok(secured(("2026-11-16", "3.93"),
                                                                          ("2026-11-17", "3.92")))),
                    *us_server().routes)
    run(store, server, when=start, sources=["nyfed"])
    (v,) = [r for r in _vintages(store, "nyfed", "sofr") if r["period"] == "2026-11-17"]
    log = _rows(store / "fetches.csv")
    assert (log[2]["time"], log[2]["latest"]) == ("2026-11-18T13:00:05Z", "2026-11-18T13:03:05Z")
    assert v["first_seen"] == v["public_from"] == "2026-11-18T18:33:05"  # 08:03:05 New York, after 08:00
    assert st.as_of(store, "sofr", dt.datetime(2026, 11, 18, 13, 0, 1, tzinfo=UTC))["period"].tolist() == [
        "2026-11-16"]


def test_a_refused_answer_keeps_no_validator_so_the_retry_reads_it_whole(tmp_path, monkeypatch):
    store = tmp_path / "s"
    page = fx.h10_page_1990s([("2-Jan-90", "16.9600")])
    server = us_server(dat96_in=ok(page, **{"last-modified": "Fri, 05 Jan 2001 21:15:00 GMT"}))
    real = st.ms.parse
    monkeypatch.setattr(st.ms, "parse", lambda request, body, name=None: (_ for _ in ()).throw(
        st.SourceError("a layout not sampled (simulated)")) if "dat96" in request.url else real(request, body, name))
    first = run(store, server, sources=["h10"])
    assert first.exit_code == 4 and "dat96_in.htm" not in json.dumps(_state(store)["sources"]["h10"]["validators"])
    history = _state(store)["sources"]["h10"]["backfill"]
    assert history["done"] == [] and len(_state(store)["sources"]["h10"]["failed"]) == 1  # given up, not done
    assert "1 given up" in first.sources[0].message
    monkeypatch.setattr(st.ms, "parse", real)  # the package fixed
    run(store, server, when=NOW + HOUR, sources=["h10"], retry_failed=True)
    retried = [h for u, h in server.requests if "dat96" in u][-1]
    assert "If-Modified-Since" not in retried  # asked whole: not "if changed since" the refused answer
    assert [v["value"] for v in _vintages(store, "h10", "usdinr_h10_noon") if v["period"] == "1990-01-02"] == [
        "16.9600"]
    assert _state(store)["sources"]["h10"]["failed"] == []


def test_a_refused_answer_keeps_the_time_of_the_last_good_read_so_a_late_value_is_not_dated_before_it(tmp_path):
    # H.10's rate of Fri 13 Nov 2026 is due by 16:15 New York time on Tue 17 Nov (02:45 on the 18th in India): the
    # page read that evening still ends at 12 Nov, the next answer is cut short (refused), the third holds 13 Nov
    store, off = tmp_path / "s", st.Settings(backfill=False)
    rows = [("11-NOV-26", "94.1500"), ("12-NOV-26", "94.2000")]
    after = fx.h10_page(rows + [("13-NOV-26", "94.2400")], release="Wednesday, November 18, 2026")
    server = Server(("dat00_in", [ok(fx.h10_page(rows, release="Monday, November 16, 2026"),
                                     **{"last-modified": "Tue, 17 Nov 2026 21:15:00 GMT"}),
                                  ok(after[:-40], **{"last-modified": "Wed, 18 Nov 2026 13:00:00 GMT"}),
                                  ok(after, **{"last-modified": "Wed, 18 Nov 2026 21:15:00 GMT"})]))
    run(store, server, when=dt.datetime(2026, 11, 18, 1, 0, tzinfo=UTC), sources=["h10"], settings=off)
    cut = run(store, server, when=dt.datetime(2026, 11, 18, 14, 0, tzinfo=UTC), sources=["h10"], settings=off)
    (held,) = _state(store)["sources"]["h10"]["validators"].values()
    assert cut.exit_code == 4 and held == {"etag": "", "last_modified": "", "last_ok": "2026-11-18T01:01:00Z"}
    run(store, server, when=dt.datetime(2026, 11, 19, 1, 0, tzinfo=UTC), sources=["h10"], settings=off)
    assert "If-Modified-Since" not in server.requests[-1][1]  # asked whole after the refusal
    (v,) = [r for r in _vintages(store, "h10", "usdinr_h10_noon") if r["period"] == "2026-11-13"]
    assert (v["prior_seen"], v["public_from"], v["public_from_basis"]) == (
        "2026-11-18T06:31:00", "2026-11-19T06:31:00", "first_seen")  # the read before the refused one lacked it
    assert st.as_of(store, "usdinr_h10_noon", ist(2026, 11, 18, 12, 0))["period"].max() == "2026-11-12"


def test_a_refused_mospi_probe_keeps_the_time_of_its_last_good_read_for_the_month_it_brings(tmp_path, monkeypatch):
    # CPI's November is planned for 14 Dec 2026 16:00 (the 12th is a Saturday): the probe answered 304 at 16:05
    # without it, was refused the next morning and brought November that afternoon, dated by that sighting
    store, off = tmp_path / "s", st.Settings(backfill=False)
    real = st.ms.parse
    refuse = {"on": False}
    monkeypatch.setattr(st.ms, "parse", lambda request, body, name=None: (_ for _ in ()).throw(
        st.SourceError("a layout not sampled (simulated)")) if refuse["on"] and "division_code=0" in request.url
        else real(request, body, name))
    planned = dt.datetime(2026, 12, 14, 10, 30, tzinfo=UTC)  # 16:00 in India
    run(store, mospi_server(), when=planned - 6 * HOUR, sources=["mospi"], settings=off)
    run(store, mospi_server(), when=planned + 5 * MINUTE, sources=["mospi"], settings=off)  # 304: no November
    refuse["on"] = True
    refused = run(store, mospi_server(etag='W/"p1b"'), when=planned + 18 * HOUR, sources=["mospi"], settings=off)
    assert refused.exit_code == 4
    refuse["on"] = False
    server = mospi_server(etag='W/"p2"', november=True)
    run(store, server, when=planned + 24 * HOUR, sources=["mospi"], settings=off)
    assert "If-None-Match" not in [h for u, h in server.requests if "division_code=0" in u][0]  # asked whole
    (nov,) = [v for v in _vintages(store, "mospi", "cpi_2024_all_india_combined_general") if v["period"] == "2026-11"]
    assert (nov["prior_seen"], nov["public_from_basis"], nov["public_from"]) == (
        "2026-12-14T16:06:00", "first_seen", nov["first_seen"])  # the 304 at 16:05, before the refused answer
    assert "2026-11" not in set(st.as_of(store, "cpi_2024_all_india_combined_general",
                                         ist(2026, 12, 14, 20, 0))["period"])


def _lagging_cpi(ready: dict):
    """CPI answers whose October tables lack October until ``ready["on"]`` (the probe shows it from the start)."""
    def cpi(url, headers):
        if "division_code=0" in url:
            if headers.get("If-None-Match") == 'W/"p"':
                return FeedResponse(304, {}, b"")
            rows = [fx.cpi24(2026, m, s, index="110.00", inflation="5.00") for m in ("October", "September")
                    for s in ("Rural", "Urban", "Combined")]
            return ok(fx.mospi_answer(rows, per=100), etag='W/"p"')
        month = "October" if "month_code=10" in url else "September"
        if month == "October" and not ready["on"]:
            return ok(fx.NO_DATA, etag='W/"empty"')
        return ok(fx.mospi_answer(cpi_month(month), per=100), etag=f'W/"{month}{ready["on"]}"')
    return cpi


def test_a_retried_request_is_asked_whole_and_keeps_the_time_of_its_last_good_read(tmp_path):
    # October's CPI tables lag their headline: six reads, one a day, lack the month and the tables are given up;
    # --retry-failed asks them again whole and they hold it: dated by that sighting, not by the release of 12 Nov
    store, off, ready = tmp_path / "s", st.Settings(backfill=False), {"on": False}
    server = mospi_server(getCPIData=_lagging_cpi(ready))
    start = dt.datetime(2026, 11, 12, 10, 40, tzinfo=UTC)  # 16:10 in India, after the release at 16:00
    for day in range(st.MAX_LAG_TRIES):
        run(store, server, when=start + dt.timedelta(days=day), sources=["mospi"], settings=off)
    src = _state(store)["sources"]["mospi"]
    url = next(f["request"]["url"] for f in src["failed"] if "month_code=10&" in f["request"]["url"]
               and "sector_code=1" in f["request"]["url"])  # the first table to hold the combined food index
    assert src["validators"][url]["last_ok"] == "2026-11-17T10:48:00Z"  # its sixth read, without October
    ready["on"] = True
    sent = len(server.requests)
    run(store, server, when=start + dt.timedelta(days=7), sources=["mospi"], settings=off, retry_failed=True)
    assert "If-None-Match" not in [h for u, h in server.requests[sent:] if u == url][0]  # asked whole
    (food,) = [v for v in _vintages(store, "mospi", "cpi_2024_all_india_combined_g01_1") if v["period"] == "2026-10"]
    assert (food["prior_seen"], food["public_from_basis"], food["public_from"]) == (
        "2026-11-17T16:18:00", "first_seen", food["first_seen"])  # that read, in India time
    assert _state(store)["sources"]["mospi"]["failed"] == []


def test_a_query_asked_again_from_page_1_keeps_the_time_of_page_1s_last_read(tmp_path):
    # a release reaches October's tables between their pages 1 and 2: page 2 does not fit its query, so page 1 is
    # asked again whole the next run; the row it then brings is dated by that sighting, not by the release
    store, off = tmp_path / "s", st.Settings(backfill=False)
    phase = {"probe": 'W/"a"', "page1": False, "page2": False}  # False: six rows; True: a fuel row added on top

    def cpi(url, headers):
        if "division_code=0" in url:
            if headers.get("If-None-Match") == phase["probe"]:
                return FeedResponse(304, {}, b"")
            value = "110.05" if phase["probe"] == 'W/"a"' else "110.10"
            return ok(cpi_answer(cpi_month("October", combined=value)), etag=phase["probe"])
        month = _MONTHS[int(url.split("month_code=")[1].split("&")[0])]
        page = int(url.split("page=")[1].split("&")[0])
        rows = cpi_month(month)
        if phase["page1" if page == 1 else "page2"] and "sector_code=3" in url:  # a group of the combined index
            rows = [fx.cpi24(2026, month, "Combined", division="Housing, water, electricity, gas and other fuels",
                             group="Electricity, gas and other fuels", code="04.5", index="101.10", inflation="1.20"),
                    *rows]
        return ok(fx.mospi_answer(rows[3 * (page - 1):3 * page], page=page, per=3, total=len(rows)),
                  etag=f'W/"{month}{page}-{len(rows)}"')  # (a fake that never answers 304)
    server = mospi_server(getCPIData=cpi)
    start = dt.datetime(2026, 11, 13, 4, 0, tzinfo=UTC)  # 09:30 in India, the day after October's release
    run(store, server, when=start, sources=["mospi"], settings=off)
    phase.update(probe='W/"b"', page2=True)  # a revision changes the probe; the tables change between their pages
    second = run(store, server, when=start + HOUR, sources=["mospi"], settings=off)
    assert any("size changed" in a for a in second.sources[0].attention)
    page1 = next(p["url"] for p in _state(store)["sources"]["mospi"]["pending"]
                 if "month_code=10&" in p["url"] and "sector_code=3" in p["url"])
    assert _state(store)["sources"]["mospi"]["validators"][page1] == {
        "etag": "", "last_modified": "", "last_ok": "2026-11-13T05:10:20Z"}  # asked whole, its read of this run kept
    phase["page1"] = True
    sent = len(server.requests)
    run(store, server, when=start + 2 * HOUR, sources=["mospi"], settings=off)
    assert "If-None-Match" not in [h for u, h in server.requests[sent:] if u == page1][0]
    (fuel,) = [v for v in _vintages(store, "mospi", "cpi_2024_all_india_combined_g04_5") if v["period"] == "2026-10"]
    assert (fuel["prior_seen"], fuel["public_from_basis"], fuel["public_from"]) == (
        "2026-11-13T10:40:20", "first_seen", fuel["first_seen"])  # page 1's read of the second run lacked it


def test_a_refused_mospi_probe_is_asked_again_whole_and_brings_its_tables(tmp_path, monkeypatch):
    store, off = tmp_path / "s", st.Settings(backfill=False)
    server = mospi_server()
    real = st.ms.parse
    refuse = {"on": True}

    def parse(request, body, name=None):
        if refuse["on"] and "division_code=0" in request.url:
            raise st.SourceError("a layout not sampled (simulated)")
        return real(request, body, name)
    monkeypatch.setattr(st.ms, "parse", parse)
    first = run(store, server, sources=["mospi"], settings=off)
    src = _state(store)["sources"]["mospi"]
    assert first.exit_code == 4 and [f["origin"] for f in src["failed"]] == ["recent"]
    assert not any("division_code=0" in u for u in src["validators"])
    refuse["on"] = False
    sent = len(server.requests)
    second = run(store, server, when=NOW + HOUR, sources=["mospi"], settings=off)
    probe = [h for u, h in server.requests[sent:] if "division_code=0" in u][0]
    assert "If-None-Match" not in probe and second.exit_code == 0
    assert any("month_code=10" in u for u in server.urls()[sent:])  # the follow-ups came
    assert _state(store)["sources"]["mospi"]["failed"] == []  # answered now: no longer failing


def test_a_refused_recent_window_is_listed_once_and_leaves_the_list_when_a_later_window_reads(tmp_path, monkeypatch):
    # the New York Fed's window moves every day (startDate, endDate): a refused one is never asked again by its URL
    store, off = tmp_path / "s", st.Settings(backfill=False)
    real = st.ms.parse
    refuse = {"on": True}
    monkeypatch.setattr(st.ms, "parse", lambda request, body, name=None: (_ for _ in ()).throw(
        st.SourceError("simulated")) if refuse["on"] and "secured/all" in request.url and "/unsecured/" not in
        request.url else real(request, body, name))
    for day in range(3):
        run(store, us_server(), when=NOW + dt.timedelta(days=day), sources=["nyfed"], settings=off)
    failed = _state(store)["sources"]["nyfed"]["failed"]
    assert [f["origin"] for f in failed] == ["recent"] and "endDate=2026-11-20" in failed[0]["request"]["url"]
    refuse["on"] = False
    run(store, us_server(), when=NOW + dt.timedelta(days=3), sources=["nyfed"], settings=off)
    assert _state(store)["sources"]["nyfed"]["failed"] == []  # the later window covers it: no longer failing


def test_every_refused_history_request_stays_retryable_and_out_of_done(tmp_path, monkeypatch):
    store = tmp_path / "s"
    real = st.ms.parse
    monkeypatch.setattr(st.ms, "parse", lambda request, body, name=None: (_ for _ in ()).throw(
        st.SourceError("simulated")) if "treasury" in request.url and "/2026/" not in request.url
        else real(request, body, name))
    for day in range(5):
        run(store, us_server(), when=NOW + dt.timedelta(days=day), sources=["ust"], max_per_host=10)
    src = _state(store)["sources"]["ust"]
    assert len(src["failed"]) == 36 and src["backfill"]["done"] == []  # none dropped (once capped at 50)
    assert any("36 given up" in line for line in [src["last_message"]])
    monkeypatch.setattr(st.ms, "parse", real)
    for day in range(5, 10):
        run(store, us_server(), when=NOW + dt.timedelta(days=day), sources=["ust"], retry_failed=day == 5)
    src = _state(store)["sources"]["ust"]
    assert (len(src["backfill"]["done"]), src["failed"], src["backfill"]["queue"]) == (36, [], [])


def test_an_ingest_orders_inputs_by_their_full_names_and_names_them_in_its_conflicts(tmp_path):
    store = tmp_path / "s"
    old = (str(tmp_path / "2026-11-17" / "secured.json"), secured(("2026-11-16", "3.93")))
    new = (str(tmp_path / "2026-11-19" / "secured.json"),
           fx.nyfed_json([fx.sofr_row("2026-11-16", "3.95", revision="r")]))
    report = st.ingest(store, [new, old], now=NOW)
    (sofr,) = _vintages(store, "nyfed", "sofr")
    assert (sofr["value"], sofr["revision"]) == ("3.95", "r")  # the one saved later, by its folder
    conflict = next(c for c in report.conflicts if c.startswith("sofr 2026-11-16"))
    assert "2026-11-17/secured.json" in conflict and "2026-11-19/secured.json" in conflict


H10_START = dt.datetime(2026, 11, 18, 1, 0, tzinfo=UTC)  # 20:00 New York time on Tue 17 Nov, 06:30 in India
H10_ROWS = [("11-NOV-26", "94.1500"), ("12-NOV-26", "94.2000")]


def _h10(*extra: tuple[str, str]) -> bytes:
    return fx.h10_page(H10_ROWS + list(extra), release="Wednesday, November 18, 2026")


def _h10_runs(store: Path, monkeypatch, pages: list[bytes], refused: set[int] = frozenset()) -> list[str]:
    """One H.10 update per page, a day apart from ``H10_START``; the parser refuses the runs numbered in ``refused``
    (a layout it does not know, simulated). The raw copy each run kept, in order."""
    real = st.ms.parse
    on = {"refuse": False}
    monkeypatch.setattr(st.ms, "parse", lambda request, body, name=None: (_ for _ in ()).throw(
        st.SourceError("a layout not sampled (simulated)")) if on["refuse"] else real(request, body, name))
    server, raws = Server(("dat00_in", [ok(p) for p in pages])), []
    for n in range(len(pages)):
        on["refuse"] = n in refused
        run(store, server, when=H10_START + dt.timedelta(days=n), sources=["h10"], settings=st.Settings(backfill=False))
        raws.append(_rows(store / "fetches.csv")[-1]["raw"])
    monkeypatch.setattr(st.ms, "parse", real)  # the package fixed
    return raws


def test_a_refused_answers_raw_copy_ingested_later_is_dated_as_its_update_would_have_dated_it(tmp_path,
                                                                                              monkeypatch):
    # data/README's repair: ingest the raw copy of a refused answer after an update of this package. H.10's 13 Nov
    # is due at 02:45 on 18 Nov in India; the read at 06:31 lacked it, the read a day later held it but was refused
    pages = [_h10(), _h10(("13-NOV-26", "94.2400"))]
    _h10_runs(tmp_path / "control", monkeypatch, pages)  # the same reads, the second one parsed
    for name in ("s", "t", "u"):
        raws = _h10_runs(tmp_path / name, monkeypatch, pages, refused={1})
    later = H10_START + dt.timedelta(days=1, hours=13)
    copy = _write(tmp_path / "saved.htm", (tmp_path / "t" / raws[1]).read_bytes())  # the raw file copied elsewhere
    other = pages[1].replace(b"</body>", b" </body>")  # the same page saved another way: other bytes
    for name, inputs in (("s", [tmp_path / "s" / raws[1]]), ("t", [copy]), ("u", [("u.htm", other)])):
        report = st.ingest(tmp_path / name, inputs, now=later)
        assert (report.received, report.new) == ((name != "u") * 1, 1)
    for name in ("s", "t"):
        (v,) = [r for r in _vintages(tmp_path / name, "h10", "usdinr_h10_noon") if r["period"] == "2026-11-13"]
        assert (v["first_seen"], v["prior_seen"], v["public_from"], v["public_from_basis"]) == (
            "2026-11-19T06:31:00", "2026-11-18T06:31:00", "2026-11-19T06:31:00", "first_seen")
        for table in ("observations/h10.csv", "series.csv", "derived/global_daily.csv"):
            assert (tmp_path / name / table).read_bytes() == (tmp_path / "control" / table).read_bytes(), table
    (v,) = [r for r in _vintages(tmp_path / "u", "h10", "usdinr_h10_noon") if r["period"] == "2026-11-13"]
    assert (v["first_seen"], v["prior_seen"]) == ("2026-11-19T19:30:00", "")  # not the store's own: dated by the ingest


def test_raw_copies_are_read_in_the_order_received_and_never_over_a_newer_vintage(tmp_path, monkeypatch):
    # two answers in a row refused (13 Nov 94.2400, then 94.2500), their raw copies saved under names that sort the
    # other way: the later one received wins; in a second store a parsed read then has 94.2600: nothing older taken
    pages = [_h10(), _h10(("13-NOV-26", "94.2400")), _h10(("13-NOV-26", "94.2500")), _h10(("13-NOV-26", "94.2600"))]
    first, second = tmp_path / "s", tmp_path / "t"
    raws = _h10_runs(first, monkeypatch, pages[:3], refused={1, 2})
    copies = [("b_first.htm", (first / raws[1]).read_bytes()), ("a_second.htm", (first / raws[2]).read_bytes())]
    report = st.ingest(first, copies, now=H10_START + dt.timedelta(days=3))
    (v,) = [r for r in _vintages(first, "h10", "usdinr_h10_noon") if r["period"] == "2026-11-13"]
    assert (v["value"], v["first_seen"], v["prior_seen"]) == ("94.2500", "2026-11-20T06:31:00", "2026-11-18T06:31:00")
    assert report.conflicts == ["usdinr_h10_noon 2026-11-13: 94.2400 in b_first.htm, 94.2500 in a_second.htm (the "
                                "later kept)"]
    _h10_runs(second, monkeypatch, pages, refused={1, 2})
    again = st.ingest(second, copies, now=H10_START + dt.timedelta(days=4))
    assert (again.received, again.new, again.changed, again.superseded) == (2, 0, 0, 2)
    assert [v["value"] for v in _vintages(second, "h10", "usdinr_h10_noon") if v["period"] == "2026-11-13"] == [
        "94.2600"]
    assert "2 older than what the store holds or read since (not taken)" in again.summary()


def test_a_raw_copy_is_not_taken_over_a_later_read_and_brings_a_change_back_at_its_own_time(tmp_path, monkeypatch):
    # 13 Nov was 94.2400; a refused answer had 94.2500, the read after it 94.2400 again: the copy is older than that
    # read. In a second store 94.2400 became 94.2500, then a refused answer (the last read) went back to 94.2400:
    # the change back is taken as the update would have taken it, at that answer's time
    first, second = tmp_path / "s", tmp_path / "t"
    v1, v2 = ("13-NOV-26", "94.2400"), ("13-NOV-26", "94.2500")
    raws = _h10_runs(first, monkeypatch, [_h10(v1), _h10(v2), _h10(v1)], refused={1})
    report = st.ingest(first, [first / raws[1]], now=H10_START + dt.timedelta(days=3))
    assert (report.superseded, report.changed) == (1, 0)
    assert [v["value"] for v in _vintages(first, "h10", "usdinr_h10_noon") if v["period"] == "2026-11-13"] == [
        "94.2400"]
    raws = _h10_runs(second, monkeypatch, [_h10(v1), _h10(v2), _h10(v1)], refused={2})
    report = st.ingest(second, [second / raws[2]], now=H10_START + dt.timedelta(days=3))
    assert (report.older, report.changed) == (0, 1)
    values = [(v["value"], v["first_seen"]) for v in _vintages(second, "h10", "usdinr_h10_noon")
              if v["period"] == "2026-11-13"]
    assert values == [("94.2400", "2026-11-18T06:31:00"), ("94.2500", "2026-11-19T06:31:00"),
                      ("94.2400", "2026-11-20T06:31:00")]


def test_a_clock_behind_the_store_is_refused_and_vintages_never_go_back_in_time(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("a.json", secured(("2026-11-16", "3.95")))], now=NOW)
    with pytest.raises(st.StoreError, match="clock error"):
        st.ingest(store, [("b.json", secured(("2026-11-16", "3.93")))], now=NOW - dt.timedelta(days=1))
    assert [v["value"] for v in _vintages(store, "nyfed", "sofr")] == ["3.95"]
    back = run(store, us_server(), when=NOW - HOUR, sources=["h10"])
    assert (back.exit_code, back.sources[0].outcome) == (1, "clock_error") and "the store has recorded" in \
        back.sources[0].message
    path = store / "observations" / "nyfed.csv"
    st.ingest(store, [("c.json", secured(("2026-11-16", "3.90")))], now=NOW + HOUR)
    text = path.read_text(encoding="utf-8").replace("2026-11-18T20:30:00", "2026-11-17T20:30:00")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(st.StoreError, match="before the vintage it replaces"):
        st.status(store)


def test_windows_reach_back_to_the_latest_date_held_so_an_outage_leaves_no_gap(tmp_path):
    store = tmp_path / "s"  # the store's last SOFR is of 1 Oct; the next update is on 26 Oct
    st.ingest(store, [("n.json", secured(("2026-09-30", "4.10"), ("2026-10-01", "4.11")))],
              now=dt.datetime(2026, 10, 2, 3, 0, tzinfo=UTC))
    server = us_server()
    run(store, server, when=dt.datetime(2026, 10, 26, 14, 0, tzinfo=UTC), sources=["nyfed"],
        settings=st.Settings(backfill=False))
    assert server.urls("secured/all")[0].endswith("startDate=2026-10-01&endDate=2026-10-26")  # not 12 Oct
    held = {"secured": D(2026, 10, 1), "unsecured": D(2026, 10, 1), "xru": D(2026, 8, 1), "cbpol": D(2026, 7, 1),
            "ust": D(2025, 12, 20)}
    urls = [r.url for s in ("nyfed", "bis", "ust") for r, _ in st.recent_requests(s, NOW, held=held)]
    assert "https://markets.newyorkfed.org/api/rates/secured/all/search.json?startDate=2026-10-01&endDate=2026-11-18" \
        in urls
    assert "https://stats.bis.org/api/v1/data/WS_XRU/D.IN../all?format=csv&startPeriod=2026-08-01" in urls
    assert "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.IN+US?startPeriod=2026-07-01" in urls
    assert [u.split("/all?")[0][-4:] for u in urls if "treasury" in u] == ["2026", "2025"]  # December's file too


def D(*args):
    return dt.date(*args)


def test_mospi_tables_are_asked_for_every_month_since_the_latest_they_hold(tmp_path):
    request = mospi.cpi_2024_request(series="Current", year=[2025, 2026], division_code=0)
    result = mospi.parse_mospi(cpi_answer(cpi_month("October")), "probe", kind=mospi.CPI_2024)
    months = [(dict(r.query)["year"], dict(r.query)["month_code"])
              for r, _ in st.follow_ups(request, "probe_cpi", result, NOW, held="2026-05")]
    assert months[::3] == [("2026", "10"), ("2026", "9"), ("2026", "5"), ("2026", "6"), ("2026", "7"), ("2026", "8")]
    assert len(st.follow_ups(request, "probe_cpi", result, NOW, held="2026-09")) == 6  # no gap: two months


def test_a_table_that_lacks_its_headlines_month_is_asked_again_with_its_release(tmp_path):
    store, off = tmp_path / "s", st.Settings(backfill=False)
    state = {"details": False}

    def cpi(url, headers):
        if "division_code=0" in url:  # the probe: the general index only
            if headers.get("If-None-Match") == 'W/"p"':
                return FeedResponse(304, {}, b"")
            rows = [fx.cpi24(2026, m, s, index="110.00", inflation="5.00") for m in ("October", "September")
                    for s in ("Rural", "Urban", "Combined")]
            return ok(fx.mospi_answer(rows, per=100), etag='W/"p"')
        month = "October" if "month_code=10" in url else "September"
        if month == "October" and not state["details"]:
            return ok(fx.NO_DATA, etag='W/"empty"')
        return ok(fx.mospi_answer(cpi_month(month, food="113.50" if month == "October" else "112.17"), per=100),
                  etag=f'W/"{month}{state["details"]}"')
    server = mospi_server(getCPIData=cpi)
    run(store, server, when=dt.datetime(2026, 11, 12, 10, 40, tzinfo=UTC), sources=["mospi"], settings=off)
    pending = _state(store)["sources"]["mospi"]["pending"]
    assert {p["url"].split("month_code=")[1][:2] for p in pending if "getCPIData" in p["url"]} == {"10", "9&"}
    assert all(p.get("tries") == 1 for p in pending if "getCPIData" in p["url"])
    state["details"] = True
    run(store, server, when=dt.datetime(2026, 11, 12, 11, 10, tzinfo=UTC), sources=["mospi"], settings=off)
    assert not [p for p in _state(store)["sources"]["mospi"]["pending"] if "getCPIData" in p["url"]]
    (food,) = [v for v in _vintages(store, "mospi", "cpi_2024_all_india_combined_g01_1") if v["period"] == "2026-10"]
    assert (food["value"], food["public_from_basis"]) == ("113.50", "first_seen")  # its table read at 16:10 lacked it
    assert any("tables behind" not in line for line in st.status(store).lines())


def test_a_mospi_page_that_is_not_the_one_asked_or_does_not_fit_its_query_is_refused(tmp_path):
    store, off = tmp_path / "s", st.Settings(backfill=False)
    rows = [fx.iip(2026, "September", kind="Sectoral", category="Mining & Quarrying", sub=f"s{i}", index="90.0",
                   growth="1.0") for i in range(4)]

    def iip(url, headers):
        if "type=General" in url:
            return ok(fx.mospi_answer([fx.iip(2026, "September")], per=200))
        return ok(fx.mospi_answer(rows[:2], page=1, per=2, total=4))  # page 1 again, whatever was asked
    report = run(store, mospi_server(getIIPMonthly=iip), sources=["mospi"], settings=off)
    assert report.exit_code == 4
    assert any("answered page 1 to a request for page 2" in a for a in report.sources[0].attention)
    assert any("page=2" in f["request"]["url"] for f in _state(store)["sources"]["mospi"]["failed"])


def test_a_run_killed_during_a_source_logs_every_request_it_sent(tmp_path):
    store = tmp_path / "s"

    class Killed(BaseException):
        pass

    server = Server(("secured/all/search.json?startDate=2018", Killed()), *us_server().routes)
    with pytest.raises(Killed):
        run(store, server, sources=["nyfed"])
    assert len(_state(store)["sources"]["nyfed"]["in_flight"]) == 3
    report = run(store, us_server(), when=NOW + HOUR, sources=["nyfed"])
    interrupted = [r for r in _rows(store / "fetches.csv") if r["outcome"] == "interrupted"]
    assert len(interrupted) == 3 and len(report.interrupted) == 3
    assert interrupted[0]["message"].startswith("answered, but the run ended before")
    assert interrupted[-1]["message"] == "no answer recorded: the run ended during the request"


def test_revisions_seen_reads_back_as_a_time(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("a.json", secured(("2026-11-16", "3.93")))], now=NOW)
    st.ingest(store, [("b.json", secured(("2026-11-16", "3.95")))], now=NOW + HOUR)
    revisions = st.read_table(store, "revisions")
    assert revisions["seen"].dtype.kind == "M" and st.read_table(store, "nyfed")["seen"].map(type).eq(str).all()


def test_base_2012_cpi_answers_must_say_their_series(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("n.json", secured(("2026-11-16", "3.93")))], now=NOW)
    rows = [fx.cpi_group(2025, "December", "Combined", "General", "General-Overall", index="148.0")]
    body = fx.mospi_answer(rows, per=200)
    report = st.ingest(store, [("back.json", body)], now=NOW)
    assert report.read == 0 and "--cpi-series" in report.refused[0][1]  # not taken for the Current series
    back = st.ingest(store, [("back.json", body)], now=NOW, cpi_series="Back")
    assert (back.read, back.new, back.out_of_scope) == (1, 0, 2)  # base 2012's back series is not kept
    with pytest.raises(ValueError, match="cpi_series"):
        st.ingest(store, [], now=NOW, cpi_series="current")
    # a raw copy of the store's own request: its URL says the series
    raw = f"raw/mospi/20261118T140000Z_{st._sha(body)[:12]}.json"
    fetches = store / "fetches.csv"
    fetches.write_text(fetches.read_text(encoding="utf-8") + "2026-11-18T14:00:00Z,2026-11-18T14:01:00Z,mospi,"
                       "api.mospi.gov.in,https://api.mospi.gov.in/api/cpi/getCPIIndex?base_year=2012&series=Current"
                       f"&year=2025,200,ok,10,1,1,1,0,{raw},,,HTTP 200\n", encoding="utf-8")
    current = st.ingest(store, [("copy.json", body)], now=NOW + HOUR)
    assert (current.read, current.new) == (1, 2)
    assert _vintages(store, "mospi", "cpi_2012_all_india_combined_general")[0]["value"] == "148.0"


def test_global_daily_marks_a_change_of_definition_on_its_first_row(tmp_path):
    store = tmp_path / "s"
    body = fx.cbpol_v1([("IN", "2001-04-02", "7", "A"), ("IN", "2001-04-03", "8.5", "A")]).replace(
        b"a compilation note", b"From 3 Apr 2001 onwards: repo rate; from 1 Jan 1946 to 2 Apr 2001: bank rate")
    st.ingest(store, [("b.csv", body)], now=NOW)
    rows = {r["date"]: r for r in _rows(store / "derived" / "global_daily.csv")}
    assert rows["2001-04-03"]["notes"].startswith("policy_rate_in_bis: a new definition from 2001-04-03")
    assert rows["2001-04-02"]["notes"] == ""


def test_eias_citation_carries_the_release_date_its_page_printed(tmp_path):
    store = tmp_path / "s"
    page = fx.eia_page([("2026 Dec-28 to Jan- 1", ["91.03", "", "", "", ""])], release="12/30/2026")
    st.ingest(store, [("x.htm", page)], now=dt.datetime(2027, 1, 1, 3, 0, tzinfo=UTC))
    series = {r["series_id"]: r for r in _rows(store / "series.csv")}
    assert series["brent_spot_usd_bbl"]["citation"].startswith("Source: U.S. Energy Information Administration "
                                                               "(Dec 2026)")
    assert series["brent_spot_usd_bbl"]["last_release"] == "2026-12-30"


def test_bis_policy_rates_are_checked_against_a_table_kept_by_hand(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("b.csv", CBPOL)], now=NOW)
    table = (b"effective_date,repo_pct,sdf_pct,msf_pct,source\n2001-04-03,8.5,7.5,9.5,typed in for the test\n"
             b"2026-10-05,5.25,5,5.5,typed in\n2026-10-07,5.5,5.25,5.75,typed in\n")
    lines = st.policy_rate_check(store, table)
    assert lines[0] == ("policy rates: BIS and the table agree on 1 change from 2001-04-03 to 2026-10-07 (BIS's "
                        "latest date)")
    assert "policy rates: BIS dates the change to 5.25% 2026-10-06, the table 2026-10-05" in lines
