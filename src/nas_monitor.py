from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

NAS_CHECK_RETRIES = 4
NAS_CHECK_BACKOFF = [1, 2, 4, 8]  # seconds


def check_nas_available(nas_path: Path) -> bool:
    """Check NAS mount is writable. Returns True if available."""
    return _write_check(nas_path)


def check_nas_with_retry(nas_path: Path) -> bool:
    """Check NAS with retry backoff. Returns True if available."""
    for attempt in range(NAS_CHECK_RETRIES):
        if _write_check(nas_path):
            return True
        if attempt < NAS_CHECK_RETRIES - 1:
            import time

            delay = NAS_CHECK_BACKOFF[attempt]
            logger.warning(
                "NAS unavailable (attempt %d/%d), retrying in %ds",
                attempt + 1,
                NAS_CHECK_RETRIES,
                delay,
            )
            time.sleep(delay)
    logger.error("NAS unavailable after %d attempts", NAS_CHECK_RETRIES)
    return False


def _write_check(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / f".inkwell_nas_check_{id(path)}"
        test_file.write_text("check")
        test_file.unlink()
        return True
    except OSError as e:
        logger.debug("NAS write check failed for %s: %s", path, e)
        return False
