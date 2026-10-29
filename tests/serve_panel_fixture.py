"""Manual UI server with an isolated store; never launches games.

Run: python tests/serve_panel_fixture.py
"""
import json
import os
import sys
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recon"))
import autologin
import hub
import settings


def main():
    with tempfile.TemporaryDirectory(prefix="m2lor-ui-") as directory:
        autologin.STORE_PATH = os.path.join(directory, "accounts.json")
        settings.PATH = os.path.join(directory, "settings.json")
        exe = os.path.join(directory, "GameCoreTop.exe")
        Path(exe).touch()
        Path(settings.PATH).write_text(json.dumps({"game_path": exe}), encoding="utf-8")
        hub.is_admin = lambda: True
        panel = hub.Hub()
        panel.list = lambda: []
        # No Manager.start(): launch requests can only enqueue work here.
        server = ThreadingHTTPServer(("127.0.0.1", 18769), hub.make_handler(panel, 18769))
        print("Isolated UI fixture: http://127.0.0.1:18769/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            panel._pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
