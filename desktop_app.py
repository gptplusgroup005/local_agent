from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from tkinter import messagebox
from typing import Any

from talos.core import WEBVIEW_MIN_HEIGHT, WEBVIEW_MIN_WIDTH

def run_desktop_shell() -> None:
    try:
        import webview
    except ImportError:
        messagebox.showerror(
            "Talos",
            "Desktop WebView runtime is missing.\n\nRun:\npython -m pip install pywebview",
        )
        return

    from talos.server import CODEX_BRIDGE, LocalAgentWebHandler, STOP_EVENT, find_port, worker_loop

    host = "127.0.0.1"
    port = find_port(host, 8787)
    threading.Thread(target=worker_loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), LocalAgentWebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window_ref: dict[str, Any] = {"window": None, "maximized": False}

    class WindowApi:
        def minimize(self) -> None:
            window = window_ref["window"]
            if window is not None:
                window.minimize()

        def toggle_maximize(self) -> bool:
            window = window_ref["window"]
            if window is None:
                return False
            state = str(getattr(window, "state", ""))
            if window_ref["maximized"] or "maximized" in state.lower():
                window.restore()
                window_ref["maximized"] = False
            else:
                window.maximize()
                window_ref["maximized"] = True
            return window_ref["maximized"]

        def get_window_state(self) -> dict[str, Any]:
            window = window_ref["window"]
            state = str(getattr(window, "state", "")) if window is not None else ""
            maximized = window_ref["maximized"] or "maximized" in state.lower()
            window_ref["maximized"] = maximized
            return {"maximized": maximized, "state": state}

        def snap_to(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
            window = window_ref["window"]
            if window is None:
                return {"maximized": False}
            window.restore()
            window_ref["maximized"] = False
            window.move(int(x), int(y))
            window.resize(max(WEBVIEW_MIN_WIDTH, int(width)), max(WEBVIEW_MIN_HEIGHT, int(height)))
            return {"maximized": False}

        def close(self) -> None:
            window = window_ref["window"]
            if window is not None:
                window.destroy()

    def on_closed() -> None:
        STOP_EVENT.set()
        CODEX_BRIDGE.shutdown()
        server.shutdown()
        server.server_close()

    window = webview.create_window(
        "Talos",
        f"http://{host}:{port}",
        width=1280,
        height=840,
        min_size=(WEBVIEW_MIN_WIDTH, WEBVIEW_MIN_HEIGHT),
        background_color="#ffffff",
        frameless=True,
        easy_drag=False,
        js_api=WindowApi(),
    )
    window_ref["window"] = window
    window.events.closed += on_closed
    webview.start(debug="--debug-webview" in sys.argv)

if __name__ == "__main__":
    try:
        run_desktop_shell()
    except Exception as exc:
        messagebox.showerror("Talos", str(exc))
