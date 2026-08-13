"""
Where the settings file lives, and what to say when it isn't there.

`config.yaml` holds your own settings — budget, loan terms, target market — so
it is deliberately NOT tracked in git. What ships instead is
`config.yaml.example`, which you copy once:

    cp config.yaml.example config.yaml

That split keeps your local edits from showing up as a pending change on every
`git status`, and keeps a pull from overwriting them.

Both entry points (`scout.py` and `app.py`) read the file through this module
rather than opening it themselves. Two copies of a rule is exactly how the
market-refresh skip check ended up unreachable from the CLI while its tests
passed against the other copy — see TODOS.md.
"""
from pathlib import Path

import yaml

from tools.models import InvestmentConfig

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.yaml.example")


class ConfigNotFound(FileNotFoundError):
    """Raised when the settings file is missing, with how to fix it."""


def _missing_message(path: Path) -> str:
    if EXAMPLE_CONFIG_PATH.exists():
        return (
            f"Settings file not found: {path}\n\n"
            f"Copy the example to create one:\n"
            f"    cp {EXAMPLE_CONFIG_PATH} {path}\n\n"
            f"Then edit it — budget, loan terms, and target market are yours to set."
        )
    # No example either: almost certainly the wrong working directory, since
    # both files sit at the repo root.
    return (
        f"Settings file not found: {path}\n\n"
        f"{EXAMPLE_CONFIG_PATH} is missing too, so this is probably not the "
        f"project root — run from the directory containing scout.py."
    )


def read_config_data(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """
    Parse the settings file into a plain dict, before validation.

    Callers that need to layer CLI overrides on top do it here, between parsing
    and validation, so an override is checked by the same rules as a file value.

    Raises ConfigNotFound (a FileNotFoundError) naming the fix when absent.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigNotFound(_missing_message(path))

    with path.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{path} is not a YAML mapping — it parsed as "
            f"{type(data).__name__}. An empty or malformed file will do this."
        )
    return data


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> InvestmentConfig:
    """Read and validate the settings file."""
    return InvestmentConfig.model_validate(read_config_data(path))
