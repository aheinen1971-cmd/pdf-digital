# SPDX-License-Identifier: AGPL-3.0-only
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile

REPOSITORY = "aheinen1971-cmd/pdf-werkbank"
REPOSITORY_ID = 1449472766
LATEST = f"https://api.github.com/repositories/{REPOSITORY_ID}/releases/latest"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
RENAMED_RELEASES = "https://github.com/aheinen1971-cmd/pdf-digital/releases"
MAX_PACKAGE = 20 * 1024 * 1024
VERSION_PATTERN = r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})"
SHA_PATTERN = r"[a-f0-9]{64}"


PUBLIC_MODULUS = (
    "wIhGX47WvM5T5fBx9vcHFPLHbyx7r5XxgKIV7bgf/ONGxieObcHesqs8LtroLSSTKUmzW1DFFAUaUJeMYPgME+XCjKtey+a0bQHiSKEK0JD5v1YXjhkwAW/nYBZX9v2LjIU9nNO0dMpmY+hbv99EyTncZsBKUXentZlIctgctpSA+EJBsTL5Q010bDfLpww/pD0jzwqu8sv2omFP4ZVSte0e5oG58Yxih5CODfV+H9B88P1PpW+2gNwrJoNlOCsxUneydMu/Buh9FyP89WvNLFYfILQGAUgNFZCEcTVn8qjViST1qV3tYJTWoc0Sx21gLzaSFyy2wjj2STLFr0RMXgK1QX2P/Tq/TYjzho0pKnObJHuDv/mnrg6wrMoJnGf5HV06/WoormJkB5O51dZ4jpA7htgpXisKUbohfj/6hTSrrZot2bqbQ75OWn8WaKQOxPFgI8dOo/a5bcUMQMEFXlgTDYKxK4gVNmIXYRNxJOgyRKxg3H8ELFRYX4EHVObp"
)
SIGNER = "PDF Werkbank · Projektschlüssel 2026-01"
SIGNATURE_LIMIT = 8192


class SignatureError(ValueError):
    pass


def signature_url(version):
    return f"{RELEASES}/download/v{version}/PDF-Werkbank-{version}-Signatur.json"


def verify_release_signature(raw, version, package_hash=None, size=None):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        if len(raw) > SIGNATURE_LIMIT:
            raise ValueError()
        manifest = json.loads(raw)
        payload = base64.b64decode(manifest["payload"], validate=True)
        signature = base64.b64decode(manifest["signature"], validate=True)
        public = rsa.RSAPublicNumbers(65537, int.from_bytes(base64.b64decode(PUBLIC_MODULUS), "big")).public_key()
        public.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
        marker, label, archive_hash, archive_size, executable, end = payload.decode("ascii").split("\n")
        if (marker != "PDF-Werkbank-Update-v1" or label != version or version_tuple(label) is None or end
                or not re.fullmatch(SHA_PATTERN, archive_hash) or not re.fullmatch(SHA_PATTERN, executable)
                or not re.fullmatch(r"[1-9][0-9]{0,7}", archive_size) or not 0 < int(archive_size) <= MAX_PACKAGE
                or manifest.get("sha256") != executable or (package_hash is not None and archive_hash != package_hash)
                or (size is not None and int(archive_size) != size)):
            raise ValueError()
        return executable
    except (InvalidSignature, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise SignatureError("Die Projektsignatur des Updates fehlt oder ist ungültig.") from exc


def verified_launcher(exe, version, marker=None):
    marker = marker or update_root() / f"{version}.json"
    if exe.is_symlink() or marker.is_symlink():
        raise ValueError("Eine verknüpfte Update-Datei ist unzulässig.")
    with marker.open("rb") as stream:
        expected = verify_release_signature(stream.read(SIGNATURE_LIMIT + 1), version)
    if digest_file(exe) != expected:
        raise SignatureError("Die EXE stimmt nicht mit der signierten Freigabe überein.")
    return expected


def version_tuple(text):
    match = re.fullmatch(VERSION_PATTERN, text if isinstance(text, str) else "")
    return tuple(map(int, match.groups())) if match else None


def package_url(version):
    return f"{RELEASES}/download/v{version}/PDF-Werkbank-{version}-Windows.zip"


def available_release(data, current):
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        return None
    label = tag[1:]
    version = version_tuple(label)
    if version is None or version <= current or not isinstance(data.get("assets"), list):
        return None
    expected_urls = (package_url(label), f"{RENAMED_RELEASES}/download/v{label}/PDF-Werkbank-{label}-Windows.zip")
    for asset in data["assets"]:
        if (isinstance(asset, dict) and asset.get("name") == f"PDF-Werkbank-{label}-Windows.zip"
                and asset.get("state") == "uploaded" and asset.get("browser_download_url") in expected_urls):
            digest, size = asset.get("digest"), asset.get("size")
            if (isinstance(digest, str) and re.fullmatch("sha256:" + SHA_PATTERN, digest)
                    and type(size) is int and 0 < size <= MAX_PACKAGE):
                return label, digest[7:], size
    return None


def update_root():
    return Path(os.environ["LOCALAPPDATA"]) / "PDF Werkbank" / "Online-Updates"


def runtime_ready(version):
    runtime = Path(os.environ["LOCALAPPDATA"]) / "PDF Werkbank" / "GitHub" / version
    return (runtime / f"ready-{version}").is_file() and (runtime / "python" / "pythonw.exe").is_file()


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def failed_update(version, digest):
    try:
        return (update_root() / f"{version}.failed").read_text(encoding="utf-8").strip() == digest
    except (OSError, UnicodeError, KeyError):
        return False


def prepared_update(current):
    try:
        root = update_root()
        candidates = []
        for marker in root.glob("*.json"):
            version = version_tuple(marker.stem)
            if version is not None and version > current:
                candidates.append((version, marker))
        for version, marker in sorted(candidates, reverse=True):
            try:
                exe = root / f"PDF-Werkbank-{marker.stem}.exe"
                if not runtime_ready(marker.stem) or not exe.is_file():
                    continue
                digest = verified_launcher(exe, marker.stem, marker)
                if not failed_update(marker.stem, digest):
                    return exe
            except (OSError, ValueError):
                continue
    except (OSError, KeyError):
        pass
    return None


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != "https" or url.netloc not in
                {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}):
            raise ValueError("Unerwartetes Ziel beim Update-Download.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def usb_update_info(package):
    match = re.fullmatch(rf"PDF-Werkbank-({VERSION_PATTERN})-Windows\.zip", package.name)
    if match is None:
        raise ValueError("Bitte die Windows-ZIP-Datei eines signierten Werkbank-Updates wählen.")
    version = match[1]
    size = package.stat().st_size
    if not 0 < size <= MAX_PACKAGE:
        raise ValueError("Ungültige Größe des USB-Updates.")
    signature = package.with_name(f"PDF-Werkbank-{version}-Signatur.json")
    try:
        with signature.open("rb") as stream:
            signed = stream.read(SIGNATURE_LIMIT + 1)
    except FileNotFoundError as exc:
        raise SignatureError("Die Signaturdatei muss neben der Windows-ZIP-Datei liegen.") from exc
    expected = verify_release_signature(signed, version, digest_file(package), size)
    return version, signed, expected


def export_usb_update(folder):
    headers = {"User-Agent": "PDF-Werkbank", "Accept": "application/vnd.github+json"}
    opener = build_opener(GitHubRedirect())
    with opener.open(Request(LATEST, headers=headers), timeout=15) as response:
        result = available_release(json.loads(response.read(1024 * 1024)), (0, 0, 0))
    if not result:
        raise ValueError("Es wurde keine passende Windows-Freigabe gefunden.")
    version, digest, size = result
    target = folder / f"PDF-Werkbank-USB-Update-{version}"
    if target.exists():
        raise FileExistsError("Dieses USB-Update liegt bereits im gewählten Ordner.")
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        stage = Path(temporary) / "USB-Update"
        stage.mkdir()
        package = stage / f"PDF-Werkbank-{version}-Windows.zip"
        with opener.open(Request(signature_url(version), headers=headers), timeout=30) as response:
            signed = response.read(SIGNATURE_LIMIT + 1)
        verify_release_signature(signed, version, digest, size)
        with opener.open(Request(package_url(version), headers={"User-Agent": "PDF-Werkbank"}), timeout=30) as response:
            data = response.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Die Prüfsumme oder Größe des Update-Pakets stimmt nicht.")
        package.write_bytes(data)
        package.with_name(f"PDF-Werkbank-{version}-Signatur.json").write_bytes(signed)
        stage.rename(target)
    return target


def prepare_candidate(package, candidate, version, expected_exe, offline_packages=None):
    with ZipFile(package) as archive:
        entry = archive.getinfo(candidate.name)
        if not 0 < entry.file_size <= MAX_PACKAGE:
            raise ValueError("Die Größe der Programmdatei stimmt nicht.")
        with archive.open(entry) as source, candidate.open("wb") as output:
            output.write(source.read(MAX_PACKAGE + 1))
    if digest_file(candidate) != expected_exe:
        raise SignatureError("Die EXE stimmt nicht mit der signierten Freigabe überein.")
    env = os.environ.copy()
    env.pop("PDF_WERKBANK_RUNTIME_ROOT", None)
    env.pop("PDF_WERKBANK_UPDATE_PACKAGES", None)
    if offline_packages is not None:
        env["PDF_WERKBANK_UPDATE_PACKAGES"] = str(offline_packages)
    subprocess.run([str(candidate), "--prepare-update"], env=env, check=True, timeout=360,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    if not runtime_ready(version):
        raise ValueError("Die neue Laufzeit wurde nicht vollständig eingerichtet.")


def stage_usb_update(package, current):
    import msvcrt

    root = update_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "update.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise OSError("Ein anderes Fenster bereitet bereits ein Update vor.") from exc
        version, signed, expected = usb_update_info(package)
        if version_tuple(version) <= current:
            raise ValueError("Das USB-Update ist nicht neuer als die laufende Version.")
        if failed_update(version, expected):
            raise ValueError("Diese Freigabe ist beim Start bereits fehlgeschlagen.")
        with tempfile.TemporaryDirectory(dir=root) as folder:
            temp = Path(folder)
            name = f"PDF-Werkbank-{version}.exe"
            candidate = temp / name
            try:
                prepare_candidate(package, candidate, version, expected, package.parent / "Pakete")
            except subprocess.SubprocessError as exc:
                raise ValueError("Das USB-Update konnte nicht eingerichtet werden. "
                                 "Falls neue Laufzeitpakete benötigt werden, lege sie im Ordner „Pakete“ neben das Update.") from exc
            marker = temp / "verified.json"
            marker.write_bytes(signed)
            os.replace(candidate, root / name)
            os.replace(marker, root / f"{version}.json")
    return version


def stage_update(version, digest, size):
    import msvcrt

    parsed = version_tuple(version)
    if parsed is None or not re.fullmatch(SHA_PATTERN, digest) or not 0 < size <= MAX_PACKAGE:
        raise ValueError("Ungültige Update-Daten.")
    root = update_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "update.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 2
        if prepared_update((0, 0, 0)) == root / f"PDF-Werkbank-{version}.exe":
            return 0
        with tempfile.TemporaryDirectory(dir=root) as folder:
            temp = Path(folder)
            signature = Request(signature_url(version), headers={"User-Agent": "PDF-Werkbank"})
            try:
                with build_opener(GitHubRedirect()).open(signature, timeout=30) as response:
                    signed = response.read(SIGNATURE_LIMIT + 1)
            except HTTPError as exc:
                if exc.code == 404:
                    raise SignatureError("Die signierte Freigabe fehlt.") from exc
                raise
            expected_exe = verify_release_signature(signed, version, digest, size)
            if failed_update(version, expected_exe):
                return 4
            package = temp / "update.zip"
            request = Request(package_url(version), headers={"User-Agent": "PDF-Werkbank"})
            with build_opener(GitHubRedirect()).open(request, timeout=30) as response, package.open("wb") as output:
                remaining = size
                while remaining:
                    chunk = response.read(min(131072, remaining))
                    if not chunk:
                        break
                    output.write(chunk)
                    remaining -= len(chunk)
                if remaining or response.read(1):
                    raise ValueError("Die Größe des Update-Pakets stimmt nicht.")
            if digest_file(package) != digest:
                raise ValueError("Die Prüfsumme des Update-Pakets stimmt nicht.")
            name = f"PDF-Werkbank-{version}.exe"
            candidate = temp / name
            prepare_candidate(package, candidate, version, expected_exe)
            marker = temp / "verified.json"
            marker.write_bytes(signed)
            os.replace(candidate, root / name)
            os.replace(marker, root / f"{version}.json")
    return 0


if __name__ == "__main__":
    try:
        version, digest, size = sys.argv[1:]
        sys.exit(stage_update(version, digest, int(size)))
    except SignatureError:
        sys.exit(3)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit(1)
