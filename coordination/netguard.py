"""
netguard.py — on a hosted office, no program can be pointed at the server's
own network.

A person on a hosted office can give the agency any address: their website,
a rival's, a picture to fetch. Pointed at 127.0.0.1, 10.x, 169.254.169.254 (a
cloud's metadata service, which hands out the server's own credentials) or the
office's own port, the program would be fetching from inside the server for
them (SSRF). The crawler checks addresses already; this closes it for every
library at once, at the one place they all meet: turning a name into an address.

Only public internet addresses come back. A name that resolves to nothing else
is refused, and because the check is made at connect time, on the address that
is actually used, a name that changes its answer between check and fetch (DNS
rebinding) gains nothing. Installed by coordination/__init__.py when the office
runs hosted (GLASSWORK_HOSTED=1); a copy on someone's own computer may still
reach its own network, a website being built on localhost included.
"""
import ipaddress
import socket

_real = socket.getaddrinfo


def public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def _guarded(host, *args, **kwargs):
    found = [r for r in _real(host, *args, **kwargs) if public(r[4][0])]
    if not found:
        raise socket.gaierror(f"{host} is not a public internet address — refused on a hosted office")
    return found


def install() -> None:
    socket.getaddrinfo = _guarded
