"""
Tests for tools/config_file.py — locating and reading the settings file.

config.yaml is untracked (it holds your own budget and loan terms), so a fresh
clone starts without one. The failure that produces has to name the fix rather
than surfacing as a bare FileNotFoundError from deep inside a request handler.
"""
import pytest
import yaml

from tools import config_file
from tools.config_file import ConfigNotFound, load_config, read_config_data


@pytest.fixture
def example_config():
    """The tracked example, parsed — the thing a new user copies."""
    return yaml.safe_load(config_file.EXAMPLE_CONFIG_PATH.read_text())


def _write(path, data):
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# ── the shipped example ───────────────────────────────────────────────────────

def test_example_config_exists():
    """Without it the documented `cp config.yaml.example config.yaml` is a lie."""
    assert config_file.EXAMPLE_CONFIG_PATH.exists()


def test_example_config_is_valid(example_config, tmp_path):
    """
    The example must validate as-is. A new user copies it and runs; if it
    doesn't satisfy InvestmentConfig, their first command fails on our file.
    """
    path = _write(tmp_path / "config.yaml", example_config)
    config = load_config(path)
    assert config.output.market


# ── missing file ──────────────────────────────────────────────────────────────

def test_missing_config_names_the_fix(tmp_path):
    with pytest.raises(ConfigNotFound) as excinfo:
        read_config_data(tmp_path / "config.yaml")

    message = str(excinfo.value)
    assert "config.yaml.example" in message
    assert "cp " in message


def test_missing_config_is_a_filenotfounderror(tmp_path):
    """Callers that already catch FileNotFoundError keep working."""
    with pytest.raises(FileNotFoundError):
        read_config_data(tmp_path / "config.yaml")


def test_missing_example_points_at_the_working_directory(tmp_path, monkeypatch):
    """
    Both files sit at the repo root, so neither existing means the user is
    somewhere else — a more useful thing to say than "copy the example".
    """
    monkeypatch.setattr(
        config_file, "EXAMPLE_CONFIG_PATH", tmp_path / "config.yaml.example"
    )
    with pytest.raises(ConfigNotFound) as excinfo:
        read_config_data(tmp_path / "config.yaml")

    assert "project root" in str(excinfo.value)


# ── malformed file ────────────────────────────────────────────────────────────

def test_empty_config_is_rejected_clearly(tmp_path):
    """An empty file parses to None, which would otherwise TypeError later."""
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="not a YAML mapping"):
        read_config_data(path)


def test_non_mapping_config_is_rejected_clearly(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a YAML mapping"):
        read_config_data(path)


# ── round trip ────────────────────────────────────────────────────────────────

def test_read_config_data_returns_raw_dict(example_config, tmp_path):
    """
    Overrides are layered on the dict before validation, so this has to stay a
    plain mapping rather than a validated model.
    """
    path = _write(tmp_path / "config.yaml", example_config)
    data = read_config_data(path)
    assert isinstance(data, dict)
    assert data["output"]["market"] == example_config["output"]["market"]
