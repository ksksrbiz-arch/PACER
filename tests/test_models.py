"""
Tests for src/models/domain.py (SQLAlchemy model instantiation).
"""

from src.models.domain import ComplianceLog, DomainCandidate


def test_domain_candidate_defaults():
    candidate = DomainCandidate(company_name="Acme SaaS Inc")
    assert candidate.company_name == "Acme SaaS Inc"
    assert candidate.domain is None
    assert candidate.seo_score is None
    assert candidate.source == "pacer_pcl"


def test_domain_candidate_full():
    candidate = DomainCandidate(
        company_name="TechCo Platform LLC",
        domain="techco.io",
        case_id="24-12346",
        filing_date="2026-04-15",
        source="recap",
        seo_score=75.5,
        topical_score=0.82,
    )
    assert candidate.domain == "techco.io"
    assert candidate.seo_score == 75.5
    assert candidate.topical_score == 0.82
    assert candidate.source == "recap"


def test_compliance_log_defaults():
    log = ComplianceLog(event="pacer_daily_run", details={"candidates": 5})
    assert log.event == "pacer_daily_run"
    assert log.llc_entity == "1COMMERCE LLC"
    assert log.source == "PACER"


def test_domain_candidate_repr():
    candidate = DomainCandidate(company_name="TestCo", domain="testco.com", seo_score=80.0)
    r = repr(candidate)
    assert "TestCo" in r
    assert "testco.com" in r
