from __future__ import annotations

import re

X_COM_PATTERN = re.compile(
    r"^https?://(x\.com|twitter\.com)/([a-zA-Z0-9_]{1,15})/?$"
)


def validate_url(url: str) -> tuple[str, str]:
    """Validate an X.com/Twitter URL and return (handle, normalized_url).

    Raises ValueError if the URL is invalid.
    """
    url = url.strip()
    match = X_COM_PATTERN.match(url)
    if not match:
        raise ValueError(
            "Invalid URL. Must be https://x.com/{handle} or https://twitter.com/{handle}"
        )
    handle = match.group(2)
    normalized_url = f"https://x.com/{handle}"
    return handle, normalized_url
