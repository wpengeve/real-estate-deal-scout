"""
Fetch listings from a data source.

Current implementation: loads from fixtures/listings.json (mock data).
Swap this file when a real data source (RapidAPI, MLS) is integrated —
the rest of the pipeline is not affected.
"""
import json
from pathlib import Path

from tools.models import RawListing

_DEFAULT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "listings.json"


def fetch_listings(
    market: str,
    fixtures_path: Path = _DEFAULT_FIXTURES,
) -> list[RawListing]:
    """
    Return raw listings for the given market.

    Raises:
        FileNotFoundError: fixtures file is missing
        ValueError: fixtures file contains invalid JSON or schema mismatch
    """
    if not fixtures_path.exists():
        raise FileNotFoundError(
            f"Fixtures file not found: {fixtures_path}\n"
            "Run with real data source, or add fixtures/listings.json."
        )

    try:
        data = json.loads(fixtures_path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in fixtures file: {e}") from e

    if not isinstance(data, list):
        raise ValueError("Fixtures file must contain a JSON array of listings.")

    listings = []
    for i, item in enumerate(data):
        try:
            listings.append(RawListing.model_validate(item))
        except Exception as e:
            raise ValueError(f"Invalid listing at index {i}: {e}") from e

    return listings
