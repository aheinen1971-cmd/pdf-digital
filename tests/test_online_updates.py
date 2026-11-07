# SPDX-License-Identifier: AGPL-3.0-only
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

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


private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
modulus = base64.b64encode(private.public_key().public_numbers().n.to_bytes(256, "big")).decode()
exe_hash = hashlib.sha256(b"verified executable").hexdigest()
message = f"PDF-Werkbank-Update-v1\n{version}\n{checksum}\n{len(data)}\n{exe_hash}\n".encode("ascii")
signature = private.sign(message, padding.PKCS1v15(), hashes.SHA256())
manifest = {"payload": base64.b64encode(message).decode(), "signature": base64.b64encode(signature).decode(),
            "sha256": exe_hash}
signed = json.dumps(manifest).encode()


class FakeOpener:
    def open(self, request, timeout):
        if request.full_url == updates.signature_url(version):
            return BytesIO(signed)
        assert request.full_url == updates.package_url(version)
        return BytesIO(data)


with TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder}), patch.object(updates, "PUBLIC_MODULUS", modulus):
    assert updates.verify_release_signature(signed, version, checksum, len(data)) == exe_hash
    for changed in (dict(manifest, sha256="0" * 64), dict(manifest, signature="AAAA"), {}):
        try:
            updates.verify_release_signature(json.dumps(changed).encode(), version)
        except ValueError:
            pass
        else:
            raise AssertionError("Manipulierte Freigabe akzeptiert.")
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
        marker = updates.update_root() / f"{version}.json"
        marker.write_text(json.dumps({"sha256": exe_hash}), encoding="utf-8")
        assert updates.prepared_update((1, 4, 1)) is None
        marker.write_bytes(signed)
        result.write_bytes(b"changed")
        assert updates.prepared_update((1, 4, 1)) is None
print("Automatisches Update: stabile Releases, Paketprüfung, stille Einrichtung und Startmarker getestet.")
