from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND = "http://localhost:5173"
DEFAULT_BACKEND = "http://localhost:8001"


def read_env_urls() -> tuple[str, str]:
    values = {
        "STORYDRIVER_FRONTEND_URL": DEFAULT_FRONTEND,
        "STORYDRIVER_BACKEND_URL": DEFAULT_BACKEND,
    }
    for path in (ROOT / ".env", ROOT / "backend" / ".env", ROOT / "scripts" / "storydriver.local.env"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in values:
                values[key.strip()] = value.strip().strip('"').rstrip("/")
    for key in values:
        values[key] = os.environ.get(key, values[key]).rstrip("/")
    return values["STORYDRIVER_FRONTEND_URL"], values["STORYDRIVER_BACKEND_URL"]


def port_from_url(url: str, fallback: int) -> int:
    try:
        parsed = urlparse(url)
        return parsed.port or fallback
    except ValueError:
        return fallback


def lan_ipv4_candidates() -> list[str]:
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError:
            return
        if address.version == 4 and address.is_private and not address.is_loopback and not address.is_link_local:
            text = str(address)
            if text not in candidates:
                candidates.append(text)

    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            add(item[4][0])
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            add(sock.getsockname()[0])
    except Exception:
        pass
    try:
        result = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=8, errors="replace")
        for line in result.stdout.splitlines():
            if "IPv4" in line and ":" in line:
                add(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return candidates


def http_probe(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    try:
        request = Request(url, headers={"User-Agent": "StoryDriver LAN diagnostics"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read(300).decode("utf-8", errors="replace")
            return 200 <= response.status < 400, f"HTTP {response.status} {body[:160].replace(chr(10), ' ')}"
    except Exception as error:
        return False, str(error)


def listeners_for_ports(ports: list[int]) -> list[str]:
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=8, errors="replace")
    except Exception as error:
        return [f"netstat failed: {error}"]
    lines: list[str] = []
    suffixes = tuple(f":{port}" for port in ports)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(suffixes):
                lines.append(line.strip())
    return lines or ["No TCP listeners found for requested ports."]


def main() -> int:
    frontend_url, backend_url = read_env_urls()
    frontend_port = port_from_url(frontend_url, 5173)
    backend_port = port_from_url(backend_url, 8000)
    lan_ips = lan_ipv4_candidates()

    print("StoryDriver LAN access diagnostics")
    print("=" * 36)
    print(f"Configured desktop frontend: {frontend_url}")
    print(f"Configured desktop backend:  {backend_url}")
    print(f"LAN IP candidates:           {', '.join(lan_ips) if lan_ips else 'none detected'}")
    print("")

    urls = [
        ("localhost frontend", f"http://localhost:{frontend_port}"),
        ("127 frontend", f"http://127.0.0.1:{frontend_port}"),
        ("localhost backend health", f"http://localhost:{backend_port}/health"),
        ("127 backend health", f"http://127.0.0.1:{backend_port}/health"),
    ]
    for ip in lan_ips:
        urls.extend(
            [
                (f"{ip} frontend", f"http://{ip}:{frontend_port}"),
                (f"{ip} backend health", f"http://{ip}:{backend_port}/health"),
                (f"{ip} backend root", f"http://{ip}:{backend_port}/"),
            ]
        )

    for label, url in urls:
        ok, detail = http_probe(url)
        print(f"[{'OK' if ok else 'FAIL'}] {label:<28} {url}")
        print(f"       {detail}")

    print("")
    print("Listeners:")
    for line in listeners_for_ports([frontend_port, backend_port]):
        print(f"  {line}")

    print("")
    print("Phone test:")
    if lan_ips:
        print(f"  App:    http://{lan_ips[0]}:{frontend_port}")
        print(f"  Health: http://{lan_ips[0]}:{backend_port}/health")
    else:
        print("  No private IPv4 was detected on this PC.")
    print("")
    print("If LAN checks fail from the phone:")
    print("  - Keep the PC and phone on the same non-guest Wi-Fi.")
    print("  - Disable guest Wi-Fi/client isolation for this test.")
    print("  - Allow Node.js/Vite and Python/FastAPI on Windows private networks.")
    print("  - Do not port-forward StoryDriver; this diagnostic is local/LAN only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
