"""Operator-owned, exact-origin exceptions for private LAN MCP servers."""

from ipaddress import IPv4Address, IPv4Network
from urllib.parse import urlsplit

_LAN_NETWORKS = tuple(
    IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def private_mcp_origin(value: str) -> tuple[str, str, int]:
    """Accept literal RFC1918 origins only; never allow metadata or DNS exceptions."""
    try:
        parsed = urlsplit(value)
        address = IPv4Address(parsed.hostname or "")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.path in {"", "/"}
            and not parsed.username
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and 1 <= port <= 65535
            and any(address in network for network in _LAN_NETWORKS)
        )
    except ValueError as error:
        raise ValueError("MCP private origins require a literal RFC1918 IPv4 origin") from error
    if not valid:
        raise ValueError(
            "MCP private origins must be HTTP(S) origins with literal RFC1918 IPv4 addresses"
        )
    return parsed.scheme, str(address), port
