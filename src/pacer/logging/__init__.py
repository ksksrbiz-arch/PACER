"""Structured logging and audit trail system."""

from .audit import AuditEvent, AuditLogger

__all__ = ["AuditLogger", "AuditEvent"]
