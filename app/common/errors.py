class JiujiangError(Exception):
    """Base application error."""


class ConfigurationError(JiujiangError):
    """Invalid or missing runtime configuration."""


class StructuredOutputError(JiujiangError):
    """LLM output could not be validated."""


class EvidenceValidationError(JiujiangError):
    """Evidence failed a deterministic validation rule."""


class DataValidationError(JiujiangError):
    """Input data does not meet the draft contract."""

