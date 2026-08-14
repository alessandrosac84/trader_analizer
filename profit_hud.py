"""
profit_hud.py
-------------
HUD desktop always-on-top (WebView) no visual STARK dos monitores.

  python profit_hud.py

- Janela nativa sobre o Profit Chart (on_top)
- Front HTML/CSS/JS fluido (lerp 60fps, sem recriar DOM / sem piscar)
- Relógio local (não “volta no tempo”); dados só avançam
- Consome Profit Bridge em :5000

Requer: pip install pywebview
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

_DEFAULT = "http://127.0.0.1:5000/profit/hud"
_HEALTH = "http://127.0.0.1:5000/api/profit/status"


def _wait(url: str, tries: int = 30, delay: float = 0.4) -> bool:
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=1.2) as r:
                if getattr(r, "status", 200) < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(delay)
    return False


def _run_webview(url: str) -> int:
    try:
        import webview
    except ImportError:
        print("Instalando pywebview…")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pywebview", "-q"])
        import webview

    def _keep_top(window):
        # reforça topo após criar (Windows)
        try:
            window.on_top = True
        except Exception:
            pass

    window = webview.create_window(
        title="Trade AI · HUD",
        url=url,
        width=980,
        height=820,
        on_top=True,
        background_color="#05070d",
    )
    webview.start(func=_keep_top, args=(window,), gui="edgechromium")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=_DEFAULT)
    ap.add_argument("--browser", action="store_true", help="Abre no Chrome/Edge em vez da WebView")
    args = ap.parse_args(argv)

    print("Aguardando Profit Bridge (:5000)…")
    if not _wait(_HEALTH):
        print(
            "AVISO: bridge não respondeu. Suba iniciar_B3.bat e rode de novo.\n"
            f"URL: {args.url}",
            file=sys.stderr,
        )
    else:
        print("Bridge OK.")

    if args.browser:
        webbrowser.open(args.url)
        return 0

    try:
        return _run_webview(args.url)
    except Exception as exc:
        print(f"WebView falhou ({exc}) — abrindo browser…", file=sys.stderr)
        webbrowser.open(args.url)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
