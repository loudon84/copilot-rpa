"""与手动创建的 rpa_engine Schema 对应的 ORM 模型。"""

from nodeskclaw_rpa_engine.db.models.browser import (
    RpaBrowserProfile,
    RpaCdpEndpoint,
)
from nodeskclaw_rpa_engine.db.models.execution import (
    RpaCallbackOutbox,
    RpaExecutionAttempt,
    RpaWorkerInstance,
)
from nodeskclaw_rpa_engine.db.models.flow import (
    RpaFlow,
    RpaFlowReleaseAudit,
    RpaFlowValidationRun,
    RpaFlowVersion,
)

__all__ = [
    "RpaBrowserProfile",
    "RpaCallbackOutbox",
    "RpaCdpEndpoint",
    "RpaExecutionAttempt",
    "RpaFlow",
    "RpaFlowReleaseAudit",
    "RpaFlowValidationRun",
    "RpaFlowVersion",
    "RpaWorkerInstance",
]
