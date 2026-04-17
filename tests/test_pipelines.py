"""Smoke tests for pipelines + dropcatch orchestrator + monetization hooks.

These tests avoid hitting live APIs. Each pipeline is verified at the import
level (callable, registered in ALL_PIPELINES); side-effectful paths are
exercised with monkeypatched stubs.
"""
from __future__ import annotations

import pytest

from pacer.models.domain_candidate import DomainCandidate, PipelineSource, Status
from pacer.pipelines import (
    ALL_PIPELINES,
    run_edgar,
    run_pacer_recap,
    run_probate,
    run_sos_dissolutions,
    run_ucc_liens,
    run_uspto,
)


# ─────────────────────── pipeline registry ─────────────────────
def test_all_pipelines_registered():
    assert run_pacer_recap in ALL_PIPELINES
    assert run_sos_dissolutions in ALL_PIPELINES
    assert run_edgar in ALL_PIPELINES
    assert run_uspto in ALL_PIPELINES
    assert run_ucc_liens in ALL_PIPELINES
    assert run_probate in ALL_PIPELINES
    assert len(ALL_PIPELINES) == 6


def test_pipelines_are_callables():
    for p in ALL_PIPELINES:
        assert callable(p), f"{p.__name__} is not callable"


# ─────────────────────── dropcatch fan-out ─────────────────────
@pytest.mark.asyncio
async def test_submit_backorders_sets_queued_when_any_registrar_accepts(monkeypatch):
    from pacer.dropcatch import dropcatch_com, dynadot, godaddy, namejet
    from pacer.dropcatch.orchestrator import submit_backorders

    async def ok(domain: str) -> dict:
        return {"ok": True, "raw": {"domain": domain}}

    async def fail(domain: str) -> dict:
        raise RuntimeError("registrar down")

    monkeypatch.setattr(dynadot, "place_backorder", ok)
    monkeypatch.setattr(dropcatch_com, "place_backorder", fail)
    monkeypatch.setattr(namejet, "place_backorder", fail)
    monkeypatch.setattr(godaddy, "place_backorder", ok)

    # Rebind the tuple inside orchestrator so patched callables are used
    from pacer.dropcatch import orchestrator

    monkeypatch.setattr(
        orchestrator,
        "_REGISTRARS",
        (
            ("dynadot", dynadot.place_backorder),
            ("dropcatch", dropcatch_com.place_backorder),
            ("namejet", namejet.place_backorder),
            ("godaddy", godaddy.place_backorder),
        ),
    )

    # Audit write goes to DB — stub it out
    async def noop_record_event(**kwargs):
        return None

    monkeypatch.setattr(orchestrator, "record_event", noop_record_event)

    c = DomainCandidate(
        domain="acme.com",
        source=PipelineSource.PACER_RECAP,
        status=Status.SCORED,
        score=75.0,
    )
    result = await submit_backorders(c)
    assert result.status == Status.QUEUED_DROPCATCH


@pytest.mark.asyncio
async def test_submit_backorders_marks_failed_when_all_registrars_error(monkeypatch):
    from pacer.dropcatch import dropcatch_com, dynadot, godaddy, namejet
    from pacer.dropcatch import orchestrator

    async def fail(domain: str) -> dict:
        raise RuntimeError("all registrars down")

    for mod in (dynadot, dropcatch_com, namejet, godaddy):
        monkeypatch.setattr(mod, "place_backorder", fail)

    monkeypatch.setattr(
        orchestrator,
        "_REGISTRARS",
        (
            ("dynadot", dynadot.place_backorder),
            ("dropcatch", dropcatch_com.place_backorder),
            ("namejet", namejet.place_backorder),
            ("godaddy", godaddy.place_backorder),
        ),
    )

    async def noop_record_event(**kwargs):
        return None

    monkeypatch.setattr(orchestrator, "record_event", noop_record_event)

    c = DomainCandidate(
        domain="doomed.com",
        source=PipelineSource.EDGAR,
        status=Status.SCORED,
        score=80.0,
    )
    result = await orchestrator.submit_backorders(c)
    assert result.status == Status.FAILED


# ─────────────────────── RWA gate ──────────────────────────────
@pytest.mark.asyncio
async def test_securitize_fractional_gate_blocks_when_flag_disabled(monkeypatch):
    """Default posture = DFR exemption safe: no fractional offerings allowed.

    Directly exercises SecuritizeRouter.create_offering so the test doesn't
    need to hit Doma or Securitize over the wire.
    """
    from pacer.config import get_settings
    from pacer.rwa.doma_client import DomaToken
    from pacer.rwa.securitize_router import SecuritizeRouter

    s = get_settings()
    monkeypatch.setattr(s, "rwa_fractional_sales_enabled", False, raising=False)

    token = DomaToken(
        token_id="0xdead",
        domain="safe.com",
        token_type="DST",
        chain_id=1,
        tx_hash="0xabc",
    )

    router = SecuritizeRouter()
    # No __aenter__ so _client stays None — the flag guard must short-circuit first
    with pytest.raises(RuntimeError, match="Fractional RWA sales disabled"):
        await router.create_offering(token, total_supply=1_000_000)
