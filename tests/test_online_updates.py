import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from PySide6.QtCore import QEventLoop, QObject, QTimer
from PySide6.QtWidgets import QApplication

import online_updates
from online_updates import RELEASES, ReleaseCheck, available_release

release = {
    "tag_name": "v1.10.0",
    "assets": [{"name": "PDF-Werkbank-1.10.0-Windows.zip", "state": "uploaded"}],
}
assert available_release(release, (1, 3, 1)) == ("v1.10.0", RELEASES + "/tag/v1.10.0")
assert available_release(release, (1, 10, 0)) is None
assert available_release(dict(release, prerelease=True), (1, 3, 1)) is None
assert available_release(dict(release, draft=True), (1, 3, 1)) is None
assert available_release(dict(release, assets=[]), (1, 3, 1)) is None
assert available_release(dict(release, tag_name="v1.10.0-beta"), (1, 3, 1)) is None
assert available_release(dict(release, html_url="https://example.com"), (1, 3, 1))[1].startswith(RELEASES)


class Host(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        payload = json.dumps(release).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


app = QApplication([])
server = ThreadingHTTPServer(("127.0.0.1", 0), Host)
Thread(target=server.serve_forever, daemon=True).start()
try:
    online_updates.LATEST = f"http://127.0.0.1:{server.server_port}/latest"
    owner = QObject()
    checker = ReleaseCheck((1, 3, 1), owner)
    loop = QEventLoop()
    observed = []
    checker.finished.connect(lambda *args: (observed.append(args), loop.quit()))
    timer = QTimer(owner, singleShot=True)
    timer.timeout.connect(loop.quit)
    timer.start(4000)
    checker.check()
    loop.exec()
    assert observed == [(("v1.10.0", RELEASES + "/tag/v1.10.0"), True, False)], observed
finally:
    server.shutdown()
    server.server_close()
print("Versionsvergleich, Release-Filter und automatische HTTP-Prüfung getestet.")

