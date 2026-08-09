"""
Tests for tools/market_trends.py — Redfin Data Center area metrics.

No network: refresh() is exercised against a gzipped in-memory fixture, and
lookups read a slice written into tmp_path. Values in the fixtures are real
rows observed in the upstream WA data (Seattle, Kirkland, Alger, Bay View),
including the small-sample and NA cases that motivated the guards.
"""
import gzip
import io
import json
from pathlib import Path

import pytest

import tools.market_trends as mt
from tools.market_trends import (
    MIN_SALES_FOR_RATES,
    MarketSnapshot,
    parse_city_state,
    snapshot_for_address,
    snapshot_for_city,
)


@pytest.fixture(autouse=True)
def _clear_index_cache():
    """The module memoises the loaded slice; reset it around every test."""
    mt._invalidate_cache()
    yield
    mt._invalidate_cache()


# Real observed values. Alger sold exactly 1 home and reports "100% above list",
# which is the case the sample threshold exists to suppress.
_ROWS = [
    {
        "PERIOD_BEGIN": "2026-05-01", "PERIOD_END": "2026-05-31",
        "REGION_TYPE": "place", "CITY": "Seattle", "STATE_CODE": "WA",
        "PROPERTY_TYPE": "All Residential", "MEDIAN_SALE_PRICE": "915000",
        "MEDIAN_LIST_PRICE": "899000", "MEDIAN_PPSF": "600",
        "HOMES_SOLD": "799", "NEW_LISTINGS": "1400", "PENDING_SALES": "830",
        "INVENTORY": "2111", "MONTHS_OF_SUPPLY": "2.6", "MEDIAN_DOM": "11",
        "AVG_SALE_TO_LIST": "1.008061848", "SOLD_ABOVE_LIST": "0.3091364205",
        "PRICE_DROPS": "0.21", "OFF_MARKET_IN_TWO_WEEKS": "0.55",
        "MEDIAN_SALE_PRICE_YOY": "0.03", "MEDIAN_DOM_YOY": "-0.1",
        "AVG_SALE_TO_LIST_YOY": "0.002", "INVENTORY_YOY": "0.12",
    },
    {
        "PERIOD_BEGIN": "2026-04-01", "PERIOD_END": "2026-04-30",
        "REGION_TYPE": "place", "CITY": "Seattle", "STATE_CODE": "WA",
        "PROPERTY_TYPE": "All Residential", "MEDIAN_SALE_PRICE": "900000",
        "MEDIAN_LIST_PRICE": "890000", "MEDIAN_PPSF": "590",
        "HOMES_SOLD": "815", "NEW_LISTINGS": "1350", "PENDING_SALES": "800",
        "INVENTORY": "2057", "MONTHS_OF_SUPPLY": "2.5", "MEDIAN_DOM": "13",
        "AVG_SALE_TO_LIST": "1.009212296", "SOLD_ABOVE_LIST": "0.3165644172",
        "PRICE_DROPS": "0.19", "OFF_MARKET_IN_TWO_WEEKS": "0.57",
        "MEDIAN_SALE_PRICE_YOY": "0.02", "MEDIAN_DOM_YOY": "-0.2",
        "AVG_SALE_TO_LIST_YOY": "0.001", "INVENTORY_YOY": "0.09",
    },
    {   # Same city/period, different property type — must not be picked by default.
        "PERIOD_BEGIN": "2026-05-01", "PERIOD_END": "2026-05-31",
        "REGION_TYPE": "place", "CITY": "Seattle", "STATE_CODE": "WA",
        "PROPERTY_TYPE": "Multi-Family (2-4 Unit)", "MEDIAN_SALE_PRICE": "1100000",
        "MEDIAN_LIST_PRICE": "1125000", "MEDIAN_PPSF": "425",
        "HOMES_SOLD": "24", "NEW_LISTINGS": "60", "PENDING_SALES": "30",
        "INVENTORY": "120", "MONTHS_OF_SUPPLY": "5.0", "MEDIAN_DOM": "28",
        "AVG_SALE_TO_LIST": "0.985", "SOLD_ABOVE_LIST": "0.12",
        "PRICE_DROPS": "0.30", "OFF_MARKET_IN_TWO_WEEKS": "0.20",
        "MEDIAN_SALE_PRICE_YOY": "0.01", "MEDIAN_DOM_YOY": "0.1",
        "AVG_SALE_TO_LIST_YOY": "-0.004", "INVENTORY_YOY": "0.15",
    },
    {   # A Seattle *neighbourhood* that appears as its own place row.
        "PERIOD_BEGIN": "2026-05-01", "PERIOD_END": "2026-05-31",
        "REGION_TYPE": "place", "CITY": "Beacon Hill", "STATE_CODE": "WA",
        "PROPERTY_TYPE": "All Residential", "MEDIAN_SALE_PRICE": "700000",
        "MEDIAN_LIST_PRICE": "699000", "MEDIAN_PPSF": "500",
        "HOMES_SOLD": "1", "NEW_LISTINGS": "3", "PENDING_SALES": "2",
        "INVENTORY": "8", "MONTHS_OF_SUPPLY": "4.0", "MEDIAN_DOM": "32",
        "AVG_SALE_TO_LIST": "1.000175469", "SOLD_ABOVE_LIST": "1",
        "PRICE_DROPS": "0", "OFF_MARKET_IN_TWO_WEEKS": "0",
        "MEDIAN_SALE_PRICE_YOY": "", "MEDIAN_DOM_YOY": "",
        "AVG_SALE_TO_LIST_YOY": "", "INVENTORY_YOY": "",
    },
    {   # 1 sale -> "100% above list". The sample-threshold case.
        "PERIOD_BEGIN": "2026-05-01", "PERIOD_END": "2026-05-31",
        "REGION_TYPE": "place", "CITY": "Alger", "STATE_CODE": "WA",
        "PROPERTY_TYPE": "All Residential", "MEDIAN_SALE_PRICE": "450000",
        "MEDIAN_LIST_PRICE": "449000", "MEDIAN_PPSF": "300",
        "HOMES_SOLD": "1", "NEW_LISTINGS": "2", "PENDING_SALES": "1",
        "INVENTORY": "5", "MONTHS_OF_SUPPLY": "8.0", "MEDIAN_DOM": "3",
        "AVG_SALE_TO_LIST": "1.000331754", "SOLD_ABOVE_LIST": "1",
        "PRICE_DROPS": "0", "OFF_MARKET_IN_TWO_WEEKS": "1",
        "MEDIAN_SALE_PRICE_YOY": "", "MEDIAN_DOM_YOY": "",
        "AVG_SALE_TO_LIST_YOY": "", "INVENTORY_YOY": "",
    },
    {   # NA median DOM — 19 such rows existed in WA in May 2026 alone.
        "PERIOD_BEGIN": "2026-05-01", "PERIOD_END": "2026-05-31",
        "REGION_TYPE": "place", "CITY": "Bay View", "STATE_CODE": "WA",
        "PROPERTY_TYPE": "All Residential", "MEDIAN_SALE_PRICE": "525000",
        "MEDIAN_LIST_PRICE": "525000", "MEDIAN_PPSF": "310",
        "HOMES_SOLD": "1", "NEW_LISTINGS": "1", "PENDING_SALES": "0",
        "INVENTORY": "3", "MONTHS_OF_SUPPLY": "NA", "MEDIAN_DOM": "NA",
        "AVG_SALE_TO_LIST": "1", "SOLD_ABOVE_LIST": "0",
        "PRICE_DROPS": "0", "OFF_MARKET_IN_TWO_WEEKS": "0",
        "MEDIAN_SALE_PRICE_YOY": "", "MEDIAN_DOM_YOY": "",
        "AVG_SALE_TO_LIST_YOY": "", "INVENTORY_YOY": "",
    },
]


def _write_slice(data_dir, rows=None, state="WA"):
    """Write a local slice exactly as refresh() would."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"market_trends_{state}.tsv"
    lines = ["\t".join(mt._KEPT_COLUMNS)]
    for row in rows if rows is not None else _ROWS:
        lines.append("\t".join(row.get(col, "") for col in mt._KEPT_COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── slice location ────────────────────────────────────────────────────────────

def test_slice_defaults_to_data_dir(monkeypatch):
    monkeypatch.delenv("MARKET_TRENDS_DIR", raising=False)
    assert mt.slice_path("WA") == Path("data/market_trends_WA.tsv")


def test_market_trends_dir_env_overrides_default(monkeypatch, tmp_path):
    """A deployment may keep the slice on a mounted volume, not in the repo."""
    monkeypatch.setenv("MARKET_TRENDS_DIR", str(tmp_path))
    assert mt.slice_path("WA") == tmp_path / "market_trends_WA.tsv"


def test_explicit_data_dir_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_TRENDS_DIR", "/somewhere/else")
    assert mt.slice_path("WA", data_dir=tmp_path) == tmp_path / "market_trends_WA.tsv"


def test_env_override_is_read_lazily(monkeypatch, tmp_path):
    """Set after import must still take effect — load_dotenv() runs late."""
    _write_slice(tmp_path)
    monkeypatch.setenv("MARKET_TRENDS_DIR", str(tmp_path))
    assert snapshot_for_city("Seattle", "WA") is not None


def test_state_code_is_normalised_in_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_TRENDS_DIR", str(tmp_path))
    assert mt.slice_path("wa").name == "market_trends_WA.tsv"


# ── parse_city_state ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("address,expected", [
    ("4521 Rainier Ave S, Seattle, WA 98118", ("Seattle", "WA")),
    ("123 Main St, Kirkland, WA", ("Kirkland", "WA")),
    ("1 A St, Unit 4, Bellevue, WA 98004", ("Bellevue", "WA")),
    ("77 Ocean Dr, Mount Vernon, wa 98273", ("Mount Vernon", "WA")),
])
def test_parse_city_state_ok(address, expected):
    assert parse_city_state(address) == expected


@pytest.mark.parametrize("address", [
    None, "", "Seattle", "just a street name",
    "123 Main St, Seattle, Washington 98118",  # state must be a 2-letter code
    "123 Main St, , WA 98118",                 # empty city segment
])
def test_parse_city_state_unparseable(address):
    assert parse_city_state(address) is None


# ── numeric parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("11", 11.0), ('"11"', 11.0), ("1.008061848", 1.008061848),
    ("NA", None), ("na", None), ("", None), ("   ", None),
    (None, None), ("not-a-number", None), ("0", 0.0),
])
def test_num_parsing(raw, expected):
    assert mt._num(raw) == expected


def test_na_is_not_coerced_to_zero(tmp_path):
    """NA must read as missing, never 0 — a 0-day DOM would be a lie."""
    _write_slice(tmp_path / "data")
    snap = snapshot_for_city("Bay View", "WA", data_dir=tmp_path / "data")
    assert snap is not None
    assert snap.median_dom is None
    assert snap.months_of_supply is None


# ── lookup ────────────────────────────────────────────────────────────────────

def test_snapshot_returns_latest_period(tmp_path):
    snap = snapshot_for_city("Seattle", "WA", data_dir=_write_slice(tmp_path / "data").parent)
    assert snap is not None
    assert snap.period_end == "2026-05-31"
    assert snap.homes_sold == 799
    assert snap.sale_to_list == pytest.approx(1.008061848)
    assert snap.pct_above_list == pytest.approx(0.3091364205)


def test_snapshot_is_case_insensitive(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    assert snapshot_for_city("seattle", "wa", data_dir=data_dir) is not None


def test_snapshot_unknown_city_returns_none(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    assert snapshot_for_city("Nowhere", "WA", data_dir=data_dir) is None


def test_missing_slice_degrades_to_none(tmp_path):
    """No refresh yet must mean 'no context', not a crash."""
    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path / "absent") is None


def test_unreadable_slice_degrades_to_none(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "market_trends_WA.tsv").write_bytes(b"\xff\xfe not a tsv \x00")
    assert snapshot_for_city("Seattle", "WA", data_dir=data_dir) is None


def test_property_type_defaults_to_all_residential(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    default = snapshot_for_city("Seattle", "WA", data_dir=data_dir)
    multi = snapshot_for_city(
        "Seattle", "WA", property_type="Multi-Family (2-4 Unit)", data_dir=data_dir
    )
    assert default.property_type == "All Residential"
    assert default.homes_sold == 799
    assert multi.property_type == "Multi-Family (2-4 Unit)"
    assert multi.homes_sold == 24


def test_neighbourhood_row_does_not_shadow_its_city(tmp_path):
    """
    REGION_TYPE is `place`, so Seattle neighbourhoods appear as their own rows.
    Exact matching must keep a Seattle address on the Seattle row, and must not
    silently roll Beacon Hill up into Seattle either.
    """
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    seattle = snapshot_for_city("Seattle", "WA", data_dir=data_dir)
    beacon = snapshot_for_city("Beacon Hill", "WA", data_dir=data_dir)
    assert seattle.homes_sold == 799
    assert beacon.homes_sold == 1
    assert seattle.city == "Seattle"
    assert beacon.city == "Beacon Hill"


def test_snapshot_for_address(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    snap = snapshot_for_address("4521 Rainier Ave S, Seattle, WA 98118", data_dir=data_dir)
    assert snap is not None and snap.city == "Seattle"


def test_snapshot_for_unparseable_address(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    assert snapshot_for_address("no city here", data_dir=data_dir) is None


def test_history_returns_newest_first(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    history = mt.history_for_city("Seattle", "WA", data_dir=data_dir)
    assert [h.period_end for h in history] == ["2026-05-31", "2026-04-30"]


def test_history_respects_months_limit(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    assert len(mt.history_for_city("Seattle", "WA", months=1, data_dir=data_dir)) == 1


# ── sample threshold ──────────────────────────────────────────────────────────

def test_small_sample_flagged_insufficient(tmp_path):
    """Alger: 1 sale reporting '100% above list' — the case that must be hidden."""
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    snap = snapshot_for_city("Alger", "WA", data_dir=data_dir)
    assert snap.homes_sold == 1
    assert snap.pct_above_list == 1.0        # the raw value really is 100%
    assert snap.has_enough_sales is False    # ...and must not be rendered


def test_large_sample_is_sufficient(tmp_path):
    data_dir = tmp_path / "data"
    _write_slice(data_dir)
    assert snapshot_for_city("Seattle", "WA", data_dir=data_dir).has_enough_sales is True


@pytest.mark.parametrize("sold,expected", [
    (None, False), (0, False), (1, False),
    (MIN_SALES_FOR_RATES - 1, False), (MIN_SALES_FOR_RATES, True),
    (MIN_SALES_FOR_RATES + 1, True),
])
def test_has_enough_sales_boundaries(sold, expected):
    snap = MarketSnapshot(
        city="X", state="WA", period_end="2026-05-31",
        property_type="All Residential", homes_sold=sold,
        median_sale_price=None, median_list_price=None, median_ppsf=None,
        inventory=None, new_listings=None, pending_sales=None,
        months_of_supply=None, median_dom=None, sale_to_list=None,
        pct_above_list=None, pct_price_drops=None,
        pct_off_market_two_weeks=None, median_sale_price_yoy=None,
        median_dom_yoy=None, sale_to_list_yoy=None, inventory_yoy=None,
    )
    assert snap.has_enough_sales is expected


# ── market temperature ────────────────────────────────────────────────────────

@pytest.mark.parametrize("supply,expected", [
    (None, None), (0.5, "Seller's market"), (2.9, "Seller's market"),
    (3.0, "Balanced market"), (6.0, "Balanced market"),
    (6.1, "Buyer's market"), (14.0, "Buyer's market"),
])
def test_market_temperature(supply, expected):
    snap = MarketSnapshot(
        city="X", state="WA", period_end="2026-05-31",
        property_type="All Residential", homes_sold=100,
        median_sale_price=None, median_list_price=None, median_ppsf=None,
        inventory=None, new_listings=None, pending_sales=None,
        months_of_supply=supply, median_dom=None, sale_to_list=None,
        pct_above_list=None, pct_price_drops=None,
        pct_off_market_two_weeks=None, median_sale_price_yoy=None,
        median_dom_yoy=None, sale_to_list_yoy=None, inventory_yoy=None,
    )
    assert snap.market_temperature == expected


# ── refresh ───────────────────────────────────────────────────────────────────

def _gzipped_source(rows) -> bytes:
    header = "\t".join(mt._KEPT_COLUMNS)
    lines = [header] + ["\t".join(r.get(c, "") for c in mt._KEPT_COLUMNS) for r in rows]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


class _FakeHeadResponse(io.BytesIO):
    """Minimal stand-in for a urlopen HEAD response."""

    def __init__(self, last_modified: str | None):
        super().__init__(b"")
        self.headers = {"Last-Modified": last_modified} if last_modified else {}


def _patch_urlopen(monkeypatch, payload: bytes, last_modified: str | None = None):
    """
    Patch urlopen, answering HEAD with headers and GET with the gzip payload.

    Returns a counter dict so tests can assert a download was skipped rather
    than merely that the result looked right.
    """
    calls = {"HEAD": 0, "GET": 0}

    def fake_urlopen(request, timeout=None):
        method = getattr(request, "method", None) or "GET"
        calls[method] = calls.get(method, 0) + 1
        if method == "HEAD":
            return _FakeHeadResponse(last_modified)
        return io.BytesIO(payload)

    monkeypatch.setattr(mt.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_refresh_filters_to_state(tmp_path, monkeypatch):
    rows = _ROWS + [{**_ROWS[0], "CITY": "Portland", "STATE_CODE": "OR"}]
    _patch_urlopen(monkeypatch, _gzipped_source(rows))

    dest = mt.refresh(state="WA", data_dir=tmp_path)

    written = dest.read_text(encoding="utf-8").splitlines()
    assert written[0].split("\t") == mt._KEPT_COLUMNS
    assert len(written) == len(_ROWS) + 1
    assert "Portland" not in dest.read_text(encoding="utf-8")


def test_refresh_drops_rows_older_than_history_window(tmp_path, monkeypatch):
    stale = {**_ROWS[0], "PERIOD_BEGIN": "2005-01-01", "CITY": "Ancientville"}
    _patch_urlopen(monkeypatch, _gzipped_source(_ROWS + [stale]))

    dest = mt.refresh(state="WA", data_dir=tmp_path)
    assert "Ancientville" not in dest.read_text(encoding="utf-8")


def test_refresh_output_is_loadable(tmp_path, monkeypatch):
    """End-to-end: refresh then look up, with no hand-written slice."""
    _patch_urlopen(monkeypatch, _gzipped_source(_ROWS))
    mt.refresh(state="WA", data_dir=tmp_path)

    snap = snapshot_for_city("Seattle", "WA", data_dir=tmp_path)
    assert snap is not None and snap.homes_sold == 799


def test_failed_refresh_preserves_previous_slice(tmp_path, monkeypatch):
    """An interrupted refresh must not leave a truncated or empty slice."""
    _patch_urlopen(monkeypatch, _gzipped_source(_ROWS))
    dest = mt.refresh(state="WA", data_dir=tmp_path)
    before = dest.read_text(encoding="utf-8")

    def boom(request, timeout=None):
        raise OSError("connection reset")
    monkeypatch.setattr(mt.urllib.request, "urlopen", boom)

    with pytest.raises(OSError):
        mt.refresh(state="WA", data_dir=tmp_path)

    assert dest.read_text(encoding="utf-8") == before
    assert not (tmp_path / "market_trends_WA.tsv.partial").exists()


# ── conditional refresh ───────────────────────────────────────────────────────
#
# Redfin's monthly files do not publish on a dependable schedule — all five
# trackers were stamped 2026-06-02 within four minutes of each other and had
# not moved two months later. So refresh must check headers before spending a
# ~950 MB download on a byte-identical result.

_STAMP = "Tue, 02 Jun 2026 18:16:20 GMT"


def test_refresh_skips_download_when_upstream_unchanged(tmp_path, monkeypatch):
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path)
    assert calls["GET"] == 1

    mt.refresh(state="WA", data_dir=tmp_path)
    assert calls["GET"] == 1, "second refresh must not re-download unchanged data"
    assert calls["HEAD"] == 2


def test_refresh_downloads_when_upstream_changed(tmp_path, monkeypatch):
    _patch_urlopen(monkeypatch, _gzipped_source([_ROWS[1]]), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path)
    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path).period_end == "2026-04-30"

    calls = _patch_urlopen(
        monkeypatch, _gzipped_source(_ROWS), last_modified="Wed, 01 Jul 2026 10:00:00 GMT"
    )
    mt.refresh(state="WA", data_dir=tmp_path)
    assert calls["GET"] == 1
    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path).period_end == "2026-05-31"


def test_force_redownloads_unchanged_data(tmp_path, monkeypatch):
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path)
    mt.refresh(state="WA", data_dir=tmp_path, force=True)
    assert calls["GET"] == 2


def test_refresh_downloads_when_head_unavailable(tmp_path, monkeypatch):
    """No Last-Modified header must mean 'download', not 'silently skip'."""
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=None)
    mt.refresh(state="WA", data_dir=tmp_path)
    mt.refresh(state="WA", data_dir=tmp_path)
    assert calls["GET"] == 2


def test_refresh_records_upstream_stamp(tmp_path, monkeypatch):
    _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path)

    meta = json.loads((tmp_path / "market_trends_WA.meta.json").read_text(encoding="utf-8"))
    assert meta["last_modified"] == _STAMP
    assert meta["rows"] == len(_ROWS)


def test_missing_slice_forces_download_despite_matching_stamp(tmp_path, monkeypatch):
    """A stale meta file with no slice beside it must not suppress the download."""
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path)
    (tmp_path / "market_trends_WA.tsv").unlink()

    mt.refresh(state="WA", data_dir=tmp_path)
    assert calls["GET"] == 2


def test_refresh_invalidates_cached_index(tmp_path, monkeypatch):
    """A lookup made before a refresh must not be served stale afterwards."""
    _write_slice(tmp_path, rows=[_ROWS[1]])  # April only
    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path).period_end == "2026-04-30"

    _patch_urlopen(monkeypatch, _gzipped_source(_ROWS))
    mt.refresh(state="WA", data_dir=tmp_path)

    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path).period_end == "2026-05-31"


def test_truncated_slice_forces_download_despite_matching_stamp(tmp_path, monkeypatch):
    """
    A zero-byte slice must not be treated as current.

    Deleting the slice already forces a re-download, but a *truncated* one has
    the harder failure mode: it satisfies exists() and the stamp matches, so the
    skip path would return it forever while every lookup silently comes back
    empty and the CLI keeps printing "Already current".
    """
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    dest = mt.refresh(state="WA", data_dir=tmp_path)
    dest.write_text("", encoding="utf-8")

    mt.refresh(state="WA", data_dir=tmp_path)
    assert calls["GET"] == 2
    assert dest.stat().st_size > 0


def test_zero_row_slice_forces_download(tmp_path, monkeypatch):
    """
    A refresh that legitimately kept 0 rows (wrong state, upstream column
    rename, a date-format change tripping the cutoff compare) must retry rather
    than pin the empty result in place until someone passes --force.
    """
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="ZZ", data_dir=tmp_path)   # no rows match this state
    assert calls["GET"] == 1

    mt.refresh(state="ZZ", data_dir=tmp_path)
    assert calls["GET"] == 2, "an empty slice must not count as current"


def test_widening_history_window_forces_download(tmp_path, monkeypatch):
    """
    The skip check compares the upstream stamp; a caller asking for more history
    than the slice holds would otherwise be handed the narrow one and told it
    was current.
    """
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path, history_years=3)
    assert calls["GET"] == 1

    mt.refresh(state="WA", data_dir=tmp_path, history_years=10)
    assert calls["GET"] == 2


def test_changed_source_url_forces_download(tmp_path, monkeypatch):
    calls = _patch_urlopen(monkeypatch, _gzipped_source(_ROWS), last_modified=_STAMP)
    mt.refresh(state="WA", data_dir=tmp_path)
    mt.refresh(state="WA", data_dir=tmp_path, source_url="https://example.test/other.tsv.gz")
    assert calls["GET"] == 2


def test_lookup_picks_up_a_slice_rewritten_by_another_process(tmp_path):
    """
    The web app never calls refresh() — `scout.py --market-refresh` is a separate
    process, so in-process cache invalidation never fires there. Without an
    mtime check the server would serve its first-seen slice until restart.
    """
    _write_slice(tmp_path, rows=[_ROWS[1]])
    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path).period_end == "2026-04-30"

    _write_slice(tmp_path, rows=[_ROWS[0]])   # stands in for the other process
    assert snapshot_for_city("Seattle", "WA", data_dir=tmp_path).period_end == "2026-05-31"


def test_alternating_states_do_not_thrash_the_cache(tmp_path, monkeypatch):
    """
    A shortlist spanning two states re-parsed the whole slice on every lookup
    when the cache held a single path. The slice is ~43k rows in production and
    this runs inside the request path.
    """
    _write_slice(tmp_path, rows=_ROWS)
    _write_slice(tmp_path, rows=[{**_ROWS[0], "CITY": "Portland", "STATE_CODE": "OR"}],
                 state="OR")

    parses = {"n": 0}
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self.suffix == ".tsv":
            parses["n"] += 1
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    for _ in range(5):
        snapshot_for_city("Seattle", "WA", data_dir=tmp_path)
        snapshot_for_city("Portland", "OR", data_dir=tmp_path)

    assert parses["n"] == 2, "each slice should be parsed once, not once per lookup"
