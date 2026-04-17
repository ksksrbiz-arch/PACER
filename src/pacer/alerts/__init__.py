"""Out-of-band alerting channels (Slack, etc.)."""
from pacer.alerts.slack import (
    alert_pipeline_complete,
    alert_pipeline_error,
    send_slack,
)

__all__ = ["send_slack", "alert_pipeline_complete", "alert_pipeline_error"]
