from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse


REMOTE_OVERRIDE_ENV = "STORYDRIVER_ALLOW_REMOTE_ENDPOINTS"


def remote_endpoints_allowed() -> bool:
    return os.environ.get(REMOTE_OVERRIDE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def is_local_service_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.strip().lower().rstrip(".")
    if hostname in {"localhost", "0.0.0.0", "::", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname.endswith(".local") or "." not in hostname
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def validate_local_service_url(value: str, label: str = "Service") -> str:
    cleaned = (value or "").strip().rstrip("/")
    if remote_endpoints_allowed() or is_local_service_url(cleaned):
        return cleaned
    raise ValueError(
        f"{label} must use localhost or a private LAN address. "
        f"Set {REMOTE_OVERRIDE_ENV}=true only for an intentional advanced override."
    )


def privacy_status(endpoints: dict[str, str]) -> dict:
    checked = {
        name: {
            "url": value,
            "local": is_local_service_url(value),
        }
        for name, value in endpoints.items()
        if value
    }
    return {
        "mode": "advanced_override" if remote_endpoints_allowed() else "local_only",
        "external_endpoints_blocked": not remote_endpoints_allowed(),
        "telemetry": False,
        "cloud_llm": False,
        "cloud_tts": False,
        "remote_assets": False,
        "all_configured_endpoints_local": all(item["local"] for item in checked.values()),
        "endpoints": checked,
    }
