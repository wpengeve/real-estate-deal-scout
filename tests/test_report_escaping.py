"""
Escaping of untrusted strings in the HTML report.

Listing fields come from the Redfin feed and narratives come from the model.
Neither is trusted markup, and both land in a file the user opens in a browser
and may forward to someone else.

"Town & Country" is a real WA city in the shipped market slice, so the benign
half of this is not hypothetical — the report emitted invalid markup for it.
The hostile half guards the same code path against the worse case.
"""
import pytest

import tools.report as report_mod
from tools.models import DealNarrative, Shortlist

BREAKOUT = "</script><script>alert(1)</script>"


def _deal(**overrides) -> DealNarrative:
    base = dict(
        rank=1,
        address="100 A & B St, Seattle, WA 98118",
        price=900_000.0,
        sqft=1_500,
        risk_level="LOW",
        narrative="Solid rental & good bones.\nBuy — strong fundamentals.",
        latitude=47.5,
        longitude=-122.2,
    )
    base.update(overrides)
    return DealNarrative(**base)


def _card(deal: DealNarrative) -> str:
    return report_mod._render_deal(deal, 0, purpose="rental")


@pytest.mark.parametrize(
    "field,value",
    [
        ("address", f"100 {BREAKOUT} St, Seattle, WA 98118"),
        ("home_type", f"Single Family {BREAKOUT}"),
        ("zoning", f"R1 {BREAKOUT}"),
        ("flood_zone", f"X {BREAKOUT}"),
        ("narrative", f"A thesis {BREAKOUT}.\nPass — no."),
    ],
)
def test_untrusted_card_fields_cannot_inject_markup(field, value):
    html = _card(_deal(**{field: value}))
    assert BREAKOUT not in html
    assert "<script>" not in html
    assert "&lt;/script&gt;" in html


@pytest.mark.parametrize("field", ["listing_url", "photo_url"])
def test_untrusted_urls_cannot_break_out_of_attributes(field):
    """A quote in a URL would otherwise end the attribute and start a new one."""
    html = _card(_deal(**{field: 'https://x.test/?a=1&b="><img onerror=alert(1)>'}))
    assert '"><img' not in html
    assert "&quot;" in html or "&gt;" not in html.split('src="')[0]


def test_ampersands_are_escaped_not_dropped():
    """The benign case: real data contains bare ampersands today."""
    html = _card(_deal(address="100 A & B St, Town & Country, WA 98118"))
    assert "A &amp; B St" in html
    assert "A & B St" not in html


def test_narrative_markup_is_shown_not_rendered():
    """Model output is text. Bold tags in a thesis are a bug, not formatting."""
    html = _card(_deal(narrative="This home is <b>underpriced</b>.\nBuy — yes."))
    assert "<b>underpriced</b>" not in html
    assert "&lt;b&gt;underpriced&lt;/b&gt;" in html


def test_map_payload_cannot_close_the_script_block():
    """
    The map data is JSON inside <script>. json.dumps escapes quotes but not
    "</script>", which ends the block early and spills the rest into the
    document as markup.
    """
    deals = [
        _deal(address=f"1 {BREAKOUT} Ave, Seattle, WA 98118"),
        _deal(rank=2, narrative=f"Thesis {BREAKOUT}.\nPass — no."),
    ]
    html = report_mod._render_map(Shortlist(market="Seattle, WA", deals=deals))

    body = html.split("<script>")[1].split("</script>")[0]
    assert BREAKOUT not in body
    assert "\\u003c" in body, "'<' must be unicode-escaped inside the payload"


def test_comparison_table_escapes_address_and_verdict():
    deals = [
        _deal(address=f"1 {BREAKOUT} Ave, Seattle, WA 98118"),
        _deal(rank=2, narrative=f"Thesis.\nPass {BREAKOUT}"),
    ]
    html = report_mod._render_comparison_table(
        Shortlist(market="Seattle, WA", deals=deals)
    )
    assert BREAKOUT not in html
    assert "<script>" not in html


def test_school_names_are_escaped():
    """School names come from the NCES/OSPI feeds, not from us."""
    from tools.models import SchoolInfo

    deal = _deal(nearby_schools=[
        SchoolInfo(nces_id="530001", name=f"Adams & Sons {BREAKOUT}",
                   level="Elementary", distance_miles=0.4,
                   proficiency_score=72.0),
    ])
    html = report_mod._render_schools(deal)
    assert BREAKOUT not in html
    assert "Adams &amp; Sons" in html


def test_escaping_does_not_mangle_ordinary_content():
    """The common case must be untouched — no stray entities in clean data."""
    html = _card(_deal(address="4521 Rainier Ave S, Seattle, WA 98118",
                       home_type="Single Family", zoning="LR2"))
    assert "4521 Rainier Ave S" in html
    assert "Single Family" in html
    assert "LR2" in html
    assert "&amp;" not in html.split('class="card-address"')[1][:200]
