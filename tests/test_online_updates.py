# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import update_worker as updates

version = "1.10.0"
name = f"PDF-Werkbank-{version}.exe"
payload = BytesIO()
with ZipFile(payload, "w") as archive:
    archive.writestr(name, b"verified executable")
    archive.writestr("../outside.txt", b"must not be written")
data = payload.getvalue()
checksum = hashlib.sha256(data).hexdigest()
asset = {"name": f"PDF-Werkbank-{version}-Windows.zip", "state": "uploaded", "size": len(data),
         "browser_download_url": updates.package_url(version), "digest": "sha256:" + checksum}
release = {"tag_name": "v" + version, "assets": [asset]}
assert updates.available_release(release, (1, 4, 1)) == (version, checksum, len(data))
assert updates.available_release(release, (1, 10, 0)) is None
assert updates.available_release(dict(release, prerelease=True), (1, 4, 1)) is None
assert updates.available_release(dict(release, draft=True), (1, 4, 1)) is None
assert updates.available_release(dict(release, assets=[]), (1, 4, 1)) is None
assert updates.available_release(dict(release, assets=[dict(asset, browser_download_url="https://example.com")]), (1, 4, 1)) is None
assert updates.available_release(dict(release, assets=[dict(asset, digest=None)]), (1, 4, 1)) is None


class FakeOpener:
    def open(self, request, timeout):
        assert request.full_url == updates.package_url(version)
        return BytesIO(data)


with TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder}):
    runtime = Path(folder) / "PDF Werkbank" / "GitHub" / version
    invoked = []

    def prepare(args, **kwargs):
        assert args[1:] == ["--prepare-update"]
        assert Path(args[0]).read_bytes() == b"verified executable"
        invoked.append(args)
        (runtime / "python").mkdir(parents=True)
        (runtime / "python" / "pythonw.exe").touch()
        (runtime / f"ready-{version}").touch()

    with patch.object(updates, "build_opener", return_value=FakeOpener()), patch.object(updates.subprocess, "run", side_effect=prepare):
        try:
            updates.stage_update(version, "0" * 64, len(data))
        except ValueError:
            pass
        else:
            raise AssertionError("Ungültige Prüfsumme wurde akzeptiert.")
        assert not invoked
        assert updates.prepared_update((1, 4, 1)) is None
        assert updates.stage_update(version, checksum, len(data)) == 0
        assert len(invoked) == 1
        result = updates.prepared_update((1, 4, 1))
        assert result and result.name == name
        assert updates.prepared_update((1, 10, 0)) is None
        assert not list(Path(folder).rglob("outside.txt"))
        result.write_bytes(b"changed")
        assert updates.prepared_update((1, 4, 1)) is None
print("Automatisches Update: stabile Releases, Paketprüfung, stille Einrichtung und Startmarker getestet.")
