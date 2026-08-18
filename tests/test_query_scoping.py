"""
Tests for how much of our screening criteria reaches the backend as a query.

The point is not tidiness. Both backends ignore parameters they don't
recognise instead of erroring, so a wrong name is indistinguishable from a
filter that matched everything — and both cap what one call returns, so a
listing that only fails screening later has taken a slot from a real candidate.

Redfin slugs asserted here were verified against live Seattle results on
2026-08-18; the Rentcast names come from its API reference (the key on hand
returns 403 subscription-inactive, so it can't be checked live).
"""
import pytest
from pytest_httpx import HTTPXMock

from tools import fetch as fetch_module
from tools.fetch import _rentcast_query_params, redfin_filter_string, resolve_city_urls
from tools.models import FetchConfig, ScreeningCriteria


def criteria(**overrides) -> ScreeningCriteria:
    base = {"max_price": 900_000, "min_beds": 3, "max_dom": 9999}
    return ScreeningCriteria(**{**base, **overrides})


# ── Redfin URL filters ────────────────────────────────────────────────────────

def test_beds_and_price_are_always_pushed():
    f = redfin_filter_string(criteria())
    assert "min-beds=3" in f
    assert "max-price=900000" in f


def test_min_price_is_pushed():
    assert "min-price=500000" in redfin_filter_string(criteria(min_price=500_000))


def test_min_baths_is_pushed():
    assert "min-baths=2.5" in redfin_filter_string(criteria(min_baths=2.5))


def test_whole_baths_are_not_written_with_a_trailing_zero():
    """Redfin writes 2, not 2.0 — keep our URL identical to one from the site."""
    f = redfin_filter_string(criteria(min_baths=2.0))
    assert "min-baths=2" in f
    assert "min-baths=2.0" not in f


def test_no_hoa_uses_redfins_hoa_slug():
    """
    max-hoa= is silently ignored by Redfin (a $1,556/mo listing came back under
    it). hoa=0 is the real "No HOA fee" option.
    """
    f = redfin_filter_string(criteria(max_hoa_fee=0))
    assert "hoa=0" in f
    assert "max-hoa" not in f


def test_hoa_ceiling_is_pushed_as_a_monthly_dollar_cap():
    assert "hoa=400" in redfin_filter_string(criteria(max_hoa_fee=400))


def test_unset_criteria_produce_no_filter_at_all():
    """An empty value would read as a filter, not as 'no preference'."""
    f = redfin_filter_string(criteria())
    for absent in ("min-price", "min-baths", "hoa=", "property-type"):
        assert absent not in f


def test_home_types_are_mapped_to_redfin_slugs():
    f = redfin_filter_string(criteria(preferred_home_types=["Single Family", "Townhouse"]))
    assert "property-type=house+townhouse" in f


def test_days_on_market_is_left_to_local_screening():
    """Redfin only takes coarse buckets; any mapping over-filters or does nothing."""
    assert "days" not in redfin_filter_string(criteria(max_dom=30))


def test_filters_are_comma_separated_with_no_blanks():
    f = redfin_filter_string(criteria(min_price=500_000, min_baths=2, max_hoa_fee=0))
    assert "" not in f.split(",")


@pytest.mark.asyncio
async def test_resolved_city_url_carries_the_filters():
    """A table city resolves with no network, so the URL is fully checkable."""
    urls = await resolve_city_urls(
        ["Seattle"], criteria(min_price=500_000, min_baths=2, max_hoa_fee=0)
    )
    assert urls == [
        "https://www.redfin.com/city/16163/WA/Seattle/filter/"
        "min-beds=3,max-price=900000,min-price=500000,min-baths=2,hoa=0"
    ]


# ── Rentcast query params ─────────────────────────────────────────────────────

def rentcast_config(**overrides) -> FetchConfig:
    base = {
        "data_source": "rentcast",
        "rentcast_cities": ["Seattle"],
        "rentcast_state": "WA",
        "rentcast_max_price": 900_000,
        "rentcast_min_beds": 3,
    }
    return FetchConfig(**{**base, **overrides})


def test_rentcast_never_sends_the_price_names_it_does_not_have():
    """
    minPrice/maxPrice are not parameters of /listings/sale. Sent anyway, they
    were dropped on the floor and every active listing in the city came back —
    paged 500 at a time, against a 50-call monthly allowance.
    """
    params = _rentcast_query_params(rentcast_config(rentcast_min_price=500_000))
    assert "minPrice" not in params
    assert "maxPrice" not in params


def test_price_is_a_min_max_range():
    params = _rentcast_query_params(rentcast_config(rentcast_min_price=500_000))
    assert params["price"] == "500000:900000"


def test_price_range_starts_at_zero_when_only_a_ceiling_is_set():
    assert _rentcast_query_params(rentcast_config())["price"] == "0:900000"


def test_price_is_omitted_when_there_is_no_ceiling():
    """A documented range needs both ends — don't invent one."""
    params = _rentcast_query_params(rentcast_config(rentcast_max_price=None))
    assert "price" not in params


def test_bedrooms_is_a_range_not_an_exact_count():
    """
    A bare bedrooms=3 matches exactly three, so a 3-bed *minimum* silently threw
    away every 4- and 5-bed home.
    """
    assert _rentcast_query_params(rentcast_config())["bedrooms"] == "3:30"


def test_bathrooms_is_a_range_too():
    params = _rentcast_query_params(rentcast_config(rentcast_min_baths=2.0))
    assert params["bathrooms"] == "2:30"


def test_several_home_types_are_pipe_separated():
    """Previously more than one type meant no propertyType filter at all."""
    params = _rentcast_query_params(
        rentcast_config(rentcast_home_types=["Single Family", "Condo"])
    )
    assert params["propertyType"] == "Single Family|Condo"


def test_days_on_market_becomes_days_old():
    assert _rentcast_query_params(rentcast_config(rentcast_max_dom=30))["daysOld"] == 30


def test_the_no_limit_sentinel_is_not_sent_as_a_filter():
    """max_dom=9999 means 'no limit', not 'listed within 9999 days'."""
    assert "daysOld" not in _rentcast_query_params(rentcast_config(rentcast_max_dom=9999))


def test_status_and_page_size_are_always_set():
    params = _rentcast_query_params(rentcast_config())
    assert params["status"] == "Active"
    assert params["limit"] == 500


@pytest.mark.asyncio
async def test_lapsed_subscription_says_so(httpx_mock: HTTPXMock, monkeypatch):
    """
    A 403 here means the key is fine but the plan lapsed. The old generic
    "returned HTTP 403" sent you hunting for a bad city name.
    """
    monkeypatch.setattr(fetch_module, "_RENTCAST_KEY", "rc_test_key")
    monkeypatch.setattr(fetch_module, "_CACHE_TTL_SECONDS", 0)
    httpx_mock.add_response(
        status_code=403,
        json={"status": 403, "error": "billing/subscription-inactive"},
    )

    with pytest.raises(RuntimeError) as exc:
        await fetch_module._fetch_from_rentcast(rentcast_config())

    assert "subscription" in str(exc.value)
    assert "rentcast.io" in str(exc.value)
