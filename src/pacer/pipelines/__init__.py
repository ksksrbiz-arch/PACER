"""Six discovery pipelines, all returning DomainCandidate records."""

from pacer.pipelines.edgar import run_edgar
from pacer.pipelines.pacer_recap import run_pacer_recap
from pacer.pipelines.probate import run_probate
from pacer.pipelines.sos_dissolutions import run_sos_dissolutions
from pacer.pipelines.ucc_liens import run_ucc_liens
from pacer.pipelines.uspto import run_uspto

ALL_PIPELINES = (
    run_pacer_recap,
    run_sos_dissolutions,
    run_edgar,
    run_uspto,
    run_ucc_liens,
    run_probate,
)

__all__ = [
    "ALL_PIPELINES",
    "run_edgar",
    "run_pacer_recap",
    "run_probate",
    "run_sos_dissolutions",
    "run_ucc_liens",
    "run_uspto",
]
