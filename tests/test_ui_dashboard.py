"""Smoke tests for the Rich dev dashboard.

These don't hit the DB or any live API — they verify:
  1. The module imports cleanly (catches syntax / import regressions).
  2. _status_colour covers every Status enum value.
  3. _strategy_colour handles all router-produced strategy strings.
  4. show_config_summary() runs end-to-end with the default Settings
     (no secret values exposed, no exceptions).
  5. show_vps_link() and show_deploy_flow() render without raising.
"""

from __future__ import annotations

import pytest
from pacer.models.domain_candidate import Status


def test_dashboard_imports_cleanly():
    from pacer.ui import dashboard  # noqa: F401

    assert hasattr(dashboard, "run_pipeline_live")
    assert hasattr(dashboard, "show_status_table")
    assert hasattr(dashboard, "score_domain_live")
    assert hasattr(dashboard, "show_config_summary")
    assert hasattr(dashboard, "show_vps_link")
    assert hasattr(dashboard, "show_deploy_flow")
    # New operator helpers (health / monetize dry-run / partners summary).
    assert hasattr(dashboard, "show_health_check")
    assert hasattr(dashboard, "monetize_dry_run")
    assert hasattr(dashboard, "show_partners_summary")


def test_dev_cli_wires_new_commands():
    """`pacer dev --help` must list the three new subcommands."""
    from click.testing import CliRunner
    from pacer.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "--help"])
    assert result.exit_code == 0, result.output
    for sub in ("health", "monetize", "partners"):
        assert sub in result.output, f"`pacer dev {sub}` is not registered"


def test_dev_monetize_rejects_invalid_tier():
    """Click should reject any tier outside the 5 known router profiles."""
    from click.testing import CliRunner
    from pacer.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "monetize", "x.com", "--tier", "garbage"])
    assert result.exit_code != 0
    assert "garbage" in result.output.lower() or "invalid" in result.output.lower()


def test_status_colour_covers_every_enum_value():
    """If a new Status is added we want the colour map to catch it."""
    from pacer.ui.dashboard import _status_colour

    for status in Status:
        colour = _status_colour(status.value)
        # "white" is the fallback — every real status should have its own colour.
        assert colour != "white", f"Status.{status.name} is missing from _status_colour"


@pytest.mark.parametrize(
    "strategy",
    ["auction_bin", "lease_to_own", "dropcatch", "parking", "aftermarket", "discarded"],
)
def test_strategy_colour_covers_router_outputs(strategy: str):
    from pacer.ui.dashboard import _strategy_colour

    assert _strategy_colour(strategy) != "white"


def test_strategy_colour_handles_none_and_empty():
    from pacer.ui.dashboard import _strategy_colour

    assert _strategy_colour(None) == "dim"
    assert _strategy_colour("") == "dim"


def test_show_config_summary_runs(capsys):
    from pacer.ui.dashboard import show_config_summary

    show_config_summary()
    out = capsys.readouterr().out
    # Banner rule + at least one section title must appear.
    assert "Active Configuration" in out
    assert "Core" in out
    assert "LLM" in out
    assert "Monetization" in out
    assert "Compliance" in out
    # Must surface the aftermarket gate (one of the key refinements).
    assert "aftermarket_listings_enabled" in out
    # Must surface the CTA/BOI partner cap.
    assert "partner_max_rev_share_pct" in out
    # Never expose raw secret values — only ✓/— indicators.
    assert "secret_value" not in out.lower()


def test_show_vps_link_runs(capsys):
    from pacer.ui.dashboard import show_vps_link

    show_vps_link()
    out = capsys.readouterr().out
    assert "hostinger" in out.lower() or "vps" in out.lower()


def test_show_deploy_flow_runs(capsys):
    from pacer.ui.dashboard import show_deploy_flow

    show_deploy_flow()
    out = capsys.readouterr().out
    assert "Deployment" in out or "deploy" in out.lower()
