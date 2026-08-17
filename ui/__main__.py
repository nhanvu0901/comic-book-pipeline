"""
Launch the Flet UI:
    python -m ui                 # desktop window on this machine (unchanged default)
    python -m ui --lan           # also serve it to the LAN, for reviewing from a phone/laptop
    python -m ui --lan --port N  # pin the port (default 8550)

--lan exists because panel review is the one step that genuinely wants a bigger screen than
the machine doing the rendering, and Master reviews from another device on the same network.

READ THIS BEFORE USING --lan: the UI has NO AUTHENTICATION. Anyone who can reach the port can
edit narration text, re-lock panels, hide panels, approve the render gate and read every
project on disk. That is fine on a home network and not fine on a shared or public one. The
port is bound to 0.0.0.0, so it is reachable on every interface this machine has — including
a VPN like Tailscale, which is the safer way in from outside the house.
"""
import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from . import _flet_compat  # noqa: F401 — patches flet 0.85 before any screen imports it
import flet as ft
from .app import main


DEFAULT_PORT = 8550


def _lan_addresses() -> list[str]:
    """Every non-loopback IPv4 this machine answers on, best-guess primary first.

    Read live rather than remembered: this box's LAN address was 192.168.1.7 when the notes
    were written and is not any more — DHCP moved it — so a hard-coded address sends Master
    to a dead URL.
    """
    out: list[str] = []
    try:
        # The address the OS would SOURCE traffic from — the primary LAN NIC, even with a
        # VPN adapter also present. No packet is actually sent; UDP connect just picks a route.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


def _run() -> None:
    ap = argparse.ArgumentParser(prog="python -m ui", description=__doc__.splitlines()[1])
    ap.add_argument("--lan", action="store_true",
                    help="serve to the local network instead of opening a desktop window")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"port for --lan (default {DEFAULT_PORT})")
    args = ap.parse_args()

    if not args.lan:
        ft.run(main)
        return

    print(f"\n  Review UI on the network — port {args.port}\n")
    for ip in _lan_addresses():
        label = "  (VPN)" if ip.startswith("100.") else ""
        print(f"    http://{ip}:{args.port}{label}")
    print("\n  No password — anyone who can reach this port can edit and approve,")
    print(f"  and every file under {Path(__file__).parent.parent / 'projects'} is served over HTTP.")
    print("  Windows may ask to allow Python through the firewall: say yes for PRIVATE "
          "networks only.\n")
    # Serve projects/ as the asset root and tell the bridge to emit asset-relative image
    # srcs. Without this the review screen renders NO panel thumbnails over the LAN: it
    # passes absolute local paths to ft.Image, which a browser on another device cannot read.
    # Base64 is not the answer — embedding every tile up front (100+ per beat × 25 beats) was
    # measured to kill the client, so the browser has to fetch them lazily over HTTP just as
    # the desktop client reads them lazily off disk.
    from .bridge import PROJECTS_ROOT, set_web_mode
    set_web_mode(True)
    # 0.0.0.0 so it answers on every interface — the LAN NIC and any VPN adapter alike.
    ft.run(main, view=ft.AppView.WEB_BROWSER, host="0.0.0.0", port=args.port,
           assets_dir=str(PROJECTS_ROOT))


if __name__ == "__main__":
    _run()
