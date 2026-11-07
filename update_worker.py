# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile

REPOSITORY = "aheinen1971-cmd/pdf-werkbank"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
MAX_PACKAGE = 20 * 1024 * 1024
VERSION_PATTERN = r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})"
SHA_PATTERN = r"[a-f0-9]{64}"


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
    for asset in data["assets"]:
        if (isinstance(asset, dict) and asset.get("name") == f"PDF-Werkbank-{label}-Windows.zip"
                and asset.get("state") == "uploaded" and asset.get("browser_download_url") == package_url(label)):
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
                data = json.loads(marker.read_text(encoding="utf-8"))
                exe = root / f"PDF-Werkbank-{marker.stem}.exe"
                digest = data.get("sha256") if isinstance(data, dict) else None
                if (isinstance(digest, str) and re.fullmatch(SHA_PATTERN, digest)
                        and runtime_ready(marker.stem) and not exe.is_symlink()
                        and exe.is_file() and digest_file(exe) == digest):
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
            with ZipFile(package) as archive:
                entry = archive.getinfo(name)
                if not 0 < entry.file_size <= MAX_PACKAGE:
                    raise ValueError("Die Größe der Programmdatei stimmt nicht.")
                with archive.open(entry) as source, candidate.open("wb") as output:
                    output.write(source.read(MAX_PACKAGE + 1))
            exe_hash = digest_file(candidate)
            env = os.environ.copy()
            env.pop("PDF_WERKBANK_RUNTIME_ROOT", None)
            subprocess.run([str(candidate), "--prepare-update"], env=env, check=True, timeout=360,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            if not runtime_ready(version):
                raise ValueError("Die neue Laufzeit wurde nicht vollständig eingerichtet.")
            marker = temp / "verified.json"
            marker.write_text(json.dumps({"sha256": exe_hash}), encoding="utf-8")
            os.replace(candidate, root / name)
            os.replace(marker, root / f"{version}.json")
    return 0


if __name__ == "__main__":
    try:
        version, digest, size = sys.argv[1:]
        sys.exit(stage_update(version, digest, int(size)))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit(1)
