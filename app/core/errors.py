class CortexBaseError(Exception):
    """Root exception for all CORTEX-AEV errors."""


class DatabaseError(CortexBaseError):
    """Raised on any failure inside the db/ infrastructure layer."""


class ValidationError(CortexBaseError):
    """Raised when input or intermediate state fails validation."""


class TimeoutError(CortexBaseError):
    """Raised when a routing/execution stage exceeds its deadline."""


class FallbackError(CortexBaseError):
    """Raised when even Graceful Fallback cannot produce a safe response."""


class LockTimeoutError(CortexBaseError):
    """Lock acquisition timed out — triggers Teardown Sequence."""

    def __init__(
        self,
        trace_id: str,
        field_name: str,
        lock_type: str,
        timeout: float,
    ) -> None:
        self.trace_id = trace_id
        self.field_name = field_name
        self.lock_type = lock_type
        self.timeout = timeout
        super().__init__(
            f"Lock timeout: trace={trace_id} field={field_name} "
            f"type={lock_type} timeout={timeout}s"
        )


class TeardownTriggered(CortexBaseError):
    """Teardown Sequence triggered — lock timeout or LC force-push."""

    def __init__(self, trigger_source: str, trace_id: str) -> None:
        self.trigger_source = trigger_source  # "lock_timeout" | "lc_force_push"
        self.trace_id = trace_id
        super().__init__(
            f"Teardown triggered: source={trigger_source} trace={trace_id}"
        )
