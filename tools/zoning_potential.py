"""
Zoning development potential — rule-based engine for Seattle/WA properties.

Combines:
  - Seattle Land Use Code (NR1/NR2/NR3 Neighborhood Residential, LR1-3 Lowrise)
  - King County unincorporated zones (R-4, R-6, R-8, etc.)
  - Seattle ADU/DADU eligibility (detached ADU requires lot ≥ 3,200 sqft)
  - WA HB 1110 (2023) — duplexes required in all WA residential zones

Designed for Seattle metro. For non-KC properties without zoning data, only
HB 1110 baseline applies. Always verify with local planning department before
making investment decisions.

Development score guide:
  1 — minimal  : no practical additional unit potential
  2 — low      : ADU/DADU only (small rental unit, no subdivide)
  3 — moderate : DADU feasible + WA duplex rights
  4 — good     : 3-4 units zoned, or DADU + large lot
  5 — strong   : 4+ units zoned (NR3/LR) or clear subdivision potential
"""
import re

from tools.models import ZoningPotential

# ── Seattle NR zones ───────────────────────────────────────────────────────────
# Post-2019 MHA rezoning replaced SF1/SF5/SF7000.
# max_units: principal dwelling units the zone allows.
# sqft_per_unit: approx minimum lot sqft per unit (for capacity estimate).
_NR_ZONES: dict[str, dict] = {
    "NR1": {"max_units": 2,  "sqft_per_unit": 3_200},   # SF + 1 ADU/DADU
    "NR2": {"max_units": 4,  "sqft_per_unit": 2_500},
    "NR3": {"max_units": 6,  "sqft_per_unit": 2_000},
}

# ── Seattle LR zones ───────────────────────────────────────────────────────────
# units_per_acre is approximate — actual capacity depends on FAR/setbacks.
_LR_ZONES: dict[str, dict] = {
    "LR1": {"units_per_acre": 14},   # ~3,100 sqft/unit
    "LR2": {"units_per_acre": 29},   # ~1,500 sqft/unit
    "LR3": {"units_per_acre": 43},   # ~1,000 sqft/unit
}

# ── King County unincorporated zones ──────────────────────────────────────────
_KC_ZONES: dict[str, dict] = {
    "R-4":  {"units_per_acre": 4},    # ~10,890 sqft/unit
    "R-6":  {"units_per_acre": 6},    # ~7,260 sqft/unit
    "R-8":  {"units_per_acre": 8},    # ~5,445 sqft/unit
    "R-12": {"units_per_acre": 12},
    "R-18": {"units_per_acre": 18},
    "R-24": {"units_per_acre": 24},
    "RA-2.5": {"units_per_acre": 0.4},  # rural area, very low density
    "RA-5":   {"units_per_acre": 0.2},
    "RA-10":  {"units_per_acre": 0.1},
}

_SQFT_PER_ACRE = 43_560
_DADU_MIN_LOT_SQFT = 3_200   # Seattle requirement for a detached ADU


# ── Public API ─────────────────────────────────────────────────────────────────

def assess(
    zoning: str | None,
    lot_sqft: int | None,
    address: str | None = None,
) -> ZoningPotential:
    """
    Assess development potential for a property.

    Returns ZoningPotential with scored opportunities.
    Degrades gracefully when zoning or lot_sqft are unknown.
    """
    zone = _normalize_zone(zoning)
    opportunities: list[str] = []
    zoned_units: int | None = None
    adu_eligible = False
    dadu_eligible = False
    subdivision_eligible = False

    # WA HB 1110 baseline applies to all WA residential properties
    hb1110_duplex = _is_washington(address)
    if hb1110_duplex:
        opportunities.append(
            "WA HB 1110 (2023): duplex rights apply statewide — 2 units allowed by "
            "law regardless of local zoning"
        )

    if zone in _NR_ZONES:
        _assess_nr(zone, lot_sqft, opportunities,
                   _NR_ZONES[zone], locals_out := {})
        zoned_units = locals_out.get("zoned_units")
        adu_eligible = locals_out.get("adu_eligible", False)
        dadu_eligible = locals_out.get("dadu_eligible", False)
        subdivision_eligible = locals_out.get("subdivision_eligible", False)

    elif zone in _LR_ZONES:
        _assess_lr(zone, lot_sqft, opportunities,
                   _LR_ZONES[zone], locals_out := {})
        zoned_units = locals_out.get("zoned_units")
        adu_eligible = True
        dadu_eligible = lot_sqft is None or lot_sqft >= _DADU_MIN_LOT_SQFT

    elif zone in _KC_ZONES:
        _assess_kc(zone, lot_sqft, opportunities,
                   _KC_ZONES[zone], locals_out := {})
        zoned_units = locals_out.get("zoned_units")
        adu_eligible = locals_out.get("adu_eligible", False)
        dadu_eligible = locals_out.get("dadu_eligible", False)

    else:
        # Unknown zoning — rely on lot size heuristic + HB 1110
        if lot_sqft and lot_sqft >= _DADU_MIN_LOT_SQFT:
            adu_eligible = True
            dadu_eligible = True
            opportunities.append(
                f"Lot ({lot_sqft:,} sqft) is large enough for an ADU/DADU — "
                "verify eligibility with local planning department"
            )

    score = _score(zone, zoned_units, adu_eligible, dadu_eligible, subdivision_eligible)
    summary = _summarize(zone, zoned_units, score, lot_sqft, opportunities)

    return ZoningPotential(
        zoned_units=zoned_units,
        adu_eligible=adu_eligible,
        dadu_eligible=dadu_eligible,
        subdivision_eligible=subdivision_eligible,
        hb1110_duplex=hb1110_duplex,
        development_score=score,
        opportunities=opportunities,
        summary=summary,
    )


# ── Zone-specific helpers ──────────────────────────────────────────────────────

def _assess_nr(zone: str, lot_sqft: int | None, opps: list, cfg: dict, out: dict) -> None:
    """Populate opportunities and out-dict for Seattle NR zones."""
    max_units = cfg["max_units"]
    sqft_per_unit = cfg["sqft_per_unit"]
    out["zoned_units"] = max_units
    out["adu_eligible"] = True

    opps.append(
        f"Zoned {zone} (Neighborhood Residential): up to {max_units} units allowed — "
        f"this 'single family' lot is already zoned for multi-unit use"
    )

    if lot_sqft is None:
        return

    # DADU eligibility
    if lot_sqft >= _DADU_MIN_LOT_SQFT:
        out["dadu_eligible"] = True
        opps.append(
            f"DADU eligible (lot {lot_sqft:,} sqft ≥ {_DADU_MIN_LOT_SQFT:,} sqft minimum) — "
            f"backyard cottage can add $1,200–1,800/mo rental income without rezoning"
        )
    else:
        opps.append(
            f"Lot ({lot_sqft:,} sqft) is below DADU minimum ({_DADU_MIN_LOT_SQFT:,} sqft) — "
            f"attached ADU may still be feasible"
        )

    # How many units the lot actually supports
    if zone != "NR1":
        supportable = min(lot_sqft // sqft_per_unit, max_units)
        supportable = max(supportable, 1)
        if supportable >= 2:
            opps.append(
                f"Lot supports ~{supportable} units at {sqft_per_unit:,} sqft/unit "
                f"(zone allows {max_units})"
            )

    # Subdivision potential
    if lot_sqft >= sqft_per_unit * 2 and zone != "NR1":
        out["subdivision_eligible"] = True
        n_lots = lot_sqft // sqft_per_unit
        opps.append(
            f"Lot may support {n_lots}-way subdivision "
            f"(~{lot_sqft // n_lots:,} sqft each at {sqft_per_unit:,} sqft/unit minimum)"
        )


def _assess_lr(zone: str, lot_sqft: int | None, opps: list, cfg: dict, out: dict) -> None:
    """Populate opportunities and out-dict for Seattle LR (Lowrise) zones."""
    if lot_sqft:
        units = max(1, int((lot_sqft / _SQFT_PER_ACRE) * cfg["units_per_acre"]))
        out["zoned_units"] = units
        opps.append(
            f"Zoned {zone} (Lowrise Multifamily): lot supports ~{units} units — "
            f"significant redevelopment or small apartment potential"
        )
        if units >= 4:
            opps.append(
                f"Could qualify for townhouse or small apartment development ({units} units) — "
                f"highest-value development path"
            )
    else:
        opps.append(
            f"Zoned {zone} (Lowrise Multifamily): high-density redevelopment potential — "
            f"verify unit count with lot size"
        )


def _assess_kc(zone: str, lot_sqft: int | None, opps: list, cfg: dict, out: dict) -> None:
    """Populate opportunities and out-dict for KC unincorporated zones."""
    units_per_acre = cfg["units_per_acre"]
    if lot_sqft and units_per_acre > 0:
        units = max(1, int((lot_sqft / _SQFT_PER_ACRE) * units_per_acre))
        out["zoned_units"] = units
        sqft_per_unit = int(_SQFT_PER_ACRE / units_per_acre)
        opps.append(
            f"Zoned {zone}: {units_per_acre} units/acre — "
            f"lot supports ~{units} unit{'s' if units > 1 else ''} "
            f"(~{sqft_per_unit:,} sqft/unit)"
        )
        if lot_sqft >= _DADU_MIN_LOT_SQFT:
            out["adu_eligible"] = True
            out["dadu_eligible"] = True
            opps.append(
                "ADU/DADU likely permitted — verify with King County permitting"
            )
    else:
        opps.append(f"Zoned {zone} — verify development potential with KC planning")


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score(
    zone: str | None,
    zoned_units: int | None,
    adu: bool,
    dadu: bool,
    subdiv: bool,
) -> int:
    if zone in _LR_ZONES:
        return 5
    if zoned_units and zoned_units >= 4:
        return 5 if subdiv else 4
    if zoned_units and zoned_units >= 3:
        return 4
    if dadu:
        return 3
    if adu:
        return 2
    return 1


def _summarize(
    zone: str | None,
    zoned_units: int | None,
    score: int,
    lot_sqft: int | None,
    opportunities: list[str],
) -> str:
    labels = {1: "minimal", 2: "low", 3: "moderate", 4: "good", 5: "strong"}
    label = labels.get(score, "unknown")

    if zone in _LR_ZONES:
        return (
            f"{zone} (Lowrise Multifamily) zoning — {label} development potential "
            f"with ~{zoned_units or '?'} units possible. Prime redevelopment candidate."
        )
    if zone in _NR_ZONES and zoned_units:
        dadu_note = (
            " DADU addition feasible, could generate rental income without rezoning."
            if (lot_sqft or 0) >= _DADU_MIN_LOT_SQFT
            else " Lot is below DADU minimum but attached ADU may be feasible."
        )
        return (
            f"Zoned {zone} — already permits up to {zoned_units} units despite appearing "
            f"as a single-family home.{dadu_note}"
        )
    if zone in _KC_ZONES and zoned_units:
        return (
            f"Zoned {zone} — lot supports ~{zoned_units} unit{'s' if zoned_units > 1 else ''}. "
            f"{label.capitalize()} development potential."
        )
    if opportunities:
        return opportunities[0]
    return "Verify development potential with local planning department."


# ── Utilities ─────────────────────────────────────────────────────────────────

def _normalize_zone(zoning: str | None) -> str | None:
    """Strip MHA affordability suffixes like ' (M)', ' (M1)' and uppercase."""
    if not zoning:
        return None
    return re.sub(r"\s*\(M\d?\)\s*$", "", zoning.strip()).upper()


def _is_washington(address: str | None) -> bool:
    if not address:
        return True  # assume WA when unknown
    return bool(re.search(r",\s*WA\b", address))
