class HarnessError(Exception):
    """A call that could not finish."""

    def __init__(
        self, message: str, *, stderr: str | None = None, session_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.session_id = session_id


class HarnessTimeoutError(HarnessError):
    """A call stopped because its timeout ran out."""
