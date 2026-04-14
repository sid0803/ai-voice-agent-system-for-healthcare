"""Exotel credential validation utility."""

import logging

logger = logging.getLogger(__name__)


def validate_exotel_credentials(creds: dict[str, str | None]) -> None:
    """Validate that required Exotel credentials are present.

    Args:
        creds: Dict of credential name -> value.

    Raises:
        ValueError: If any required credential is missing.
    """
    missing = [k for k, v in creds.items() if not v]
    if missing:
        msg = f"Missing required Exotel credentials: {', '.join(missing)}"
        logger.error(msg)
        raise ValueError(msg)
    logger.info("Exotel credentials validated: %s", ", ".join(creds.keys()))
