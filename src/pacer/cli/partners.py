"""``pacer partners`` CLI — monthly payout run + 1099-NEC CSVs.

Monthly flow (Cathedral Principle: Revenue stage, after Foundation):
    1. Read revenue attribution from :class:`DomainCandidate` rows
       (``revenue_to_date_cents`` + ``partner_id`` + ``partner_rev_share_pct``).
    2. Compute :class:`PayoutLine` per (partner, domain) via ``compute_payout``.
    3. Persist to ``payout_entries`` via :class:`PayoutLedger.record_batch`.
    4. Emit two CSVs to ``reports/payouts/<period>/``:
         - ``ledger_<period>.csv``   -- full audit trail, all partners
         - ``1099nec_<year>.csv``    -- year-to-date totals for partners ≥ $600

Dry-run mode computes + writes CSVs but DOES NOT persist ledger rows. Useful
for previewing totals before you commit the billing period.

Monthly revenue attribution: we use a simple "revenue delta since last
period" model — each candidate's ``revenue_to_date_cents`` is a running
total, so the period contribution is (current_total - prior_period_total).
For the first run of a candidate we treat the full ``revenue_to_date_cents``
as period revenue (candidate was newly monetized).

Usage
-----
    pacer partners payout run --period 2026-04
    pacer partners payout run --period 2026-04 --dry-run
    pacer partners payout list --period 2026-04
    pacer partners payout list --period 2026-04 --status pending
    pacer partners payout mark-paid --id 42 --ref ACH-93821 --paid-on 2026-05-05
"""

from __future__ import annotations

import asyncio
import calendar
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import click
from loguru import logger
from sqlalchemy import and_, func, select

from pacer.db import session_scope
from pacer.models.domain_candidate import DomainCandidate
from pacer.partners.ledger import PayoutEntry, PayoutLedger, PayoutStatus
from pacer.partners.models.partner import Partner
from pacer.partners.payout import PayoutLine, compute_payout

# 1099-NEC filing threshold (IRS): aggregate payments ≥ $600/year to a
# non-corporate contractor trigger a 1099-NEC. We cut the roster at this
# line so you don't chase paperwork for one-off micro-payments.
IRS_1099_NEC_THRESHOLD_CENTS = 60_000

REPORT_ROOT = Path("reports/payouts")


# ─────────────────────────── period helpers ─────────────────────────
def _parse_period(period: str) -> tuple[date, date]:
    """Parse ``YYYY-MM`` into (start, end) inclusive date bounds."""
    try:
        year_str, month_str = period.split("-")
        year, month = int(year_str), int(month_str)
    except (ValueError, AttributeError) as e:
        raise click.BadParameter(f"period must be YYYY-MM (got {period!r})") from e
    if not 1 <= month <= 12:
        raise click.BadParameter(f"month out of range: {month}")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


# ─────────────────────────── revenue delta ──────────────────────────
@dataclass(frozen=True)
class RevenueDelta:
    partner_id: int
    domain: str
    domain_candidate_id: int
    period_revenue_cents: int
    rev_share_pct: float


async def _compute_period_deltas(period_start: date, period_end: date) -> list[RevenueDelta]:
    """Compute (partner, domain) revenue for the period.

    For each candidate with a non-null ``partner_id`` and positive
    ``revenue_to_date_cents``: the period contribution = running_total minus
    sum(prior periods already ledgered for that partner+domain).

    First run: full running total is attributed to the period.
    """
    async with session_scope() as sess:
        # Candidates with partner attribution + revenue
        cand_stmt = select(DomainCandidate).where(
            and_(
                DomainCandidate.partner_id.is_not(None),
                DomainCandidate.revenue_to_date_cents > 0,
            )
        )
        candidates: list[DomainCandidate] = list((await sess.execute(cand_stmt)).scalars().all())

        # Sum of already-ledgered revenue per (partner_id, domain) across all
        # prior periods ending before this period_start. "Prior" means the
        # entry's period_end < period_start so same-month reruns are idempotent
        # on the ledger layer — you can re-run a month and net delta will be 0
        # for candidates already captured.
        prior_stmt = (
            select(
                PayoutEntry.partner_id,
                PayoutEntry.domain,
                func.coalesce(func.sum(PayoutEntry.gross_revenue_cents), 0).label("prior_gross"),
            )
            .where(
                and_(
                    PayoutEntry.period_end < period_start,
                    PayoutEntry.status != PayoutStatus.VOIDED,
                )
            )
            .group_by(PayoutEntry.partner_id, PayoutEntry.domain)
        )
        prior_rows = (await sess.execute(prior_stmt)).all()
        prior_by_key: dict[tuple[int, str], int] = {
            (r.partner_id, r.domain): int(r.prior_gross) for r in prior_rows
        }

    deltas: list[RevenueDelta] = []
    for c in candidates:
        key = (c.partner_id, c.domain)  # type: ignore[arg-type]
        delta_cents = int(c.revenue_to_date_cents) - prior_by_key.get(key, 0)
        if delta_cents <= 0:
            continue
        deltas.append(
            RevenueDelta(
                partner_id=c.partner_id,  # type: ignore[arg-type]
                domain=c.domain,
                domain_candidate_id=c.id,
                period_revenue_cents=delta_cents,
                rev_share_pct=float(c.partner_rev_share_pct or 0.0) or None,  # type: ignore[assignment]
            )
        )
    return deltas


# ─────────────────────────── CSV writers ────────────────────────────
def _write_ledger_csv(entries: list[PayoutEntry], period_start: date, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"ledger_{period_start:%Y-%m}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "entry_id",
                "partner_id",
                "domain",
                "period_start",
                "period_end",
                "gross_revenue_cents",
                "partner_cents",
                "llc_cents",
                "rev_share_pct",
                "status",
            ]
        )
        for e in entries:
            w.writerow(
                [
                    e.id or "",
                    e.partner_id,
                    e.domain,
                    e.period_start.isoformat(),
                    e.period_end.isoformat(),
                    e.gross_revenue_cents,
                    e.partner_cents,
                    e.llc_cents,
                    f"{e.rev_share_pct:.2f}",
                    e.status.value if hasattr(e.status, "value") else e.status,
                ]
            )
    return path


async def _write_1099nec_csv(period_start: date, report_dir: Path) -> tuple[Path, int]:
    """Year-to-date 1099-NEC roster for partners ≥ $600.

    We aggregate all PAID entries for the calendar year of ``period_start``
    and emit one row per partner whose cumulative partner_cents ≥ $600.

    Returns (path, row_count). row_count == 0 if nobody cleared the threshold.
    """
    year = period_start.year
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    async with session_scope() as sess:
        stmt = (
            select(
                Partner.id.label("partner_id"),
                Partner.legal_name,
                Partner.email,
                Partner.tax_id_last4,
                Partner.state,
                func.sum(PayoutEntry.partner_cents).label("ytd_cents"),
            )
            .join(PayoutEntry, PayoutEntry.partner_id == Partner.id)
            .where(
                and_(
                    PayoutEntry.period_start >= year_start,
                    PayoutEntry.period_end <= year_end,
                    PayoutEntry.status == PayoutStatus.PAID,
                )
            )
            .group_by(
                Partner.id,
                Partner.legal_name,
                Partner.email,
                Partner.tax_id_last4,
                Partner.state,
            )
            .having(func.sum(PayoutEntry.partner_cents) >= IRS_1099_NEC_THRESHOLD_CENTS)
        )
        rows = (await sess.execute(stmt)).all()

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"1099nec_{year}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "partner_id",
                "legal_name",
                "email",
                "tax_id_last4",
                "state",
                "ytd_nonemployee_compensation_cents",
                "ytd_nonemployee_compensation_usd",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.partner_id,
                    r.legal_name,
                    r.email,
                    r.tax_id_last4 or "",
                    r.state or "",
                    int(r.ytd_cents),
                    f"{int(r.ytd_cents) / 100:.2f}",
                ]
            )
    return path, len(rows)


# ─────────────────────────── run command ────────────────────────────
async def _run_payout(period: str, dry_run: bool) -> dict:
    period_start, period_end = _parse_period(period)
    report_dir = REPORT_ROOT / period
    logger.info(
        "payout_run_start period={} dry_run={} report_dir={}",
        period,
        dry_run,
        report_dir,
    )

    deltas = await _compute_period_deltas(period_start, period_end)
    if not deltas:
        logger.info("payout_run_empty period={} no_revenue_deltas", period)

    lines: list[PayoutLine] = []
    candidate_map: dict[str, int] = {}
    for d in deltas:
        line = compute_payout(
            partner_id=d.partner_id,
            domain=d.domain,
            gross_revenue_cents=d.period_revenue_cents,
            rev_share_pct=d.rev_share_pct,
        )
        lines.append(line)
        candidate_map[d.domain] = d.domain_candidate_id

    entries: list[PayoutEntry] = []
    if dry_run:
        # Build entries in-memory without persisting.
        async with session_scope() as sess:
            ledger = PayoutLedger(sess)
            entries = [
                ledger.build_entry(line, period_start=period_start, period_end=period_end)
                for line in lines
            ]
            # Explicitly do NOT add to session / commit.
    else:
        async with session_scope() as sess:
            ledger = PayoutLedger(sess)
            entries = await ledger.record_batch(
                lines,
                period_start=period_start,
                period_end=period_end,
                candidate_id_by_domain=candidate_map,
            )
            await sess.commit()

    ledger_csv = _write_ledger_csv(entries, period_start, report_dir)
    nec_path, nec_rows = await _write_1099nec_csv(period_start, report_dir)

    total_partner_cents = sum(e.partner_cents for e in entries)
    total_llc_cents = sum(e.llc_cents for e in entries)

    summary = {
        "period": period,
        "dry_run": dry_run,
        "entry_count": len(entries),
        "partner_count": len({e.partner_id for e in entries}),
        "total_partner_cents": total_partner_cents,
        "total_llc_cents": total_llc_cents,
        "ledger_csv": str(ledger_csv),
        "nec_csv": str(nec_path),
        "nec_partners_over_threshold": nec_rows,
    }
    logger.info("payout_run_complete {}", summary)
    return summary


# ─────────────────────────── list command ───────────────────────────
async def _list_payouts(period: str, status: str | None) -> list[dict]:
    period_start, period_end = _parse_period(period)
    async with session_scope() as sess:
        stmt = select(PayoutEntry).where(
            and_(
                PayoutEntry.period_start >= period_start,
                PayoutEntry.period_end <= period_end,
            )
        )
        if status:
            try:
                stmt = stmt.where(PayoutEntry.status == PayoutStatus(status))
            except ValueError as e:
                raise click.BadParameter(
                    f"status must be one of {[s.value for s in PayoutStatus]}"
                ) from e
        rows = list((await sess.execute(stmt)).scalars().all())

    return [
        {
            "id": r.id,
            "partner_id": r.partner_id,
            "domain": r.domain,
            "partner_cents": r.partner_cents,
            "llc_cents": r.llc_cents,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        }
        for r in rows
    ]


# ─────────────────────────── mark-paid command ──────────────────────
async def _mark_paid(entry_id: int, ref: str, paid_on: date) -> dict:
    async with session_scope() as sess:
        entry = (
            await sess.execute(select(PayoutEntry).where(PayoutEntry.id == entry_id))
        ).scalar_one_or_none()
        if entry is None:
            raise click.ClickException(f"no payout entry id={entry_id}")

        ledger = PayoutLedger(sess)
        await ledger.mark_paid(entry, paid_on=paid_on, payment_ref=ref)
        await sess.commit()
        return {
            "id": entry.id,
            "partner_id": entry.partner_id,
            "domain": entry.domain,
            "status": entry.status.value,
            "payment_ref": entry.payment_ref,
            "paid_at": entry.paid_at.isoformat() if entry.paid_at else None,
        }


# ─────────────────────────── Click groups ───────────────────────────
@click.group("partners")
def cmd_partners() -> None:
    """Partner management: payout runs, 1099-NEC exports, ledger lookups."""


@cmd_partners.group("payout")
def cmd_payout() -> None:
    """Monthly payout operations."""


@cmd_payout.command("run")
@click.option("--period", required=True, help="Billing period, YYYY-MM (e.g. 2026-04).")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Compute + write CSVs, DON'T persist ledger rows.",
)
def cmd_payout_run(period: str, dry_run: bool) -> None:
    """Compute payouts, persist ledger, write CSV + 1099-NEC reports."""
    summary = asyncio.run(_run_payout(period, dry_run))
    click.echo(_format_summary(summary))


@cmd_payout.command("list")
@click.option("--period", required=True, help="Billing period, YYYY-MM.")
@click.option(
    "--status",
    type=click.Choice([s.value for s in PayoutStatus]),
    default=None,
    help="Filter by ledger status.",
)
def cmd_payout_list(period: str, status: str | None) -> None:
    """List ledger entries for a billing period."""
    rows = asyncio.run(_list_payouts(period, status))
    if not rows:
        click.echo(f"(no entries for {period}{' status=' + status if status else ''})")
        return
    # Compact table: id | partner | domain | partner$ | llc$ | status
    click.echo(
        f"{'id':>4}  {'partner':>7}  {'domain':<32}  {'partner$':>10}  "
        f"{'llc$':>10}  {'status':<8}  {'paid_at':<12}"
    )
    for r in rows:
        click.echo(
            f"{r['id']:>4}  {r['partner_id']:>7}  {r['domain']:<32.32}  "
            f"{r['partner_cents'] / 100:>10.2f}  {r['llc_cents'] / 100:>10.2f}  "
            f"{r['status']:<8}  {r['paid_at'] or '':<12}"
        )


@cmd_payout.command("mark-paid")
@click.option("--id", "entry_id", type=int, required=True, help="PayoutEntry.id")
@click.option("--ref", required=True, help="ACH txn id / check number.")
@click.option(
    "--paid-on",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Payment date YYYY-MM-DD.",
)
def cmd_payout_mark_paid(entry_id: int, ref: str, paid_on) -> None:  # noqa: ANN001
    """Transition a pending entry to paid."""
    result = asyncio.run(_mark_paid(entry_id, ref, paid_on.date()))
    click.echo(f"OK: entry {result['id']} → paid (ref={result['payment_ref']})")


# ─────────────────────────── formatting ─────────────────────────────
def _format_summary(s: dict) -> str:
    return (
        f"Payout run complete — period {s['period']} "
        f"({'DRY-RUN' if s['dry_run'] else 'PERSISTED'})\n"
        f"  Entries:           {s['entry_count']}\n"
        f"  Unique partners:   {s['partner_count']}\n"
        f"  Total partner $:   ${s['total_partner_cents'] / 100:,.2f}\n"
        f"  Total LLC $:       ${s['total_llc_cents'] / 100:,.2f}\n"
        f"  Ledger CSV:        {s['ledger_csv']}\n"
        f"  1099-NEC CSV:      {s['nec_csv']} "
        f"({s['nec_partners_over_threshold']} partners ≥ $600 YTD)"
    )
