# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import io
import json
import os
import re
import tempfile
import uuid
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from form_run import FormRun, valid_digest
from work_backup import encrypt_package, open_package


PROJECT_PREFIX = b"PDF-WERKBANK-PROJEKT\x00"
MAX_PROJECT = 64 * 1024 * 1024
MAX_CONTAINER = MAX_PROJECT + 4 * 1024 * 1024


def file_name(name, suffix):
    return (isinstance(name, str) and bool(re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]{1,180}', name))
            and name.lower().endswith(suffix) and not os.path.isreserved(name))


def file_hash(data):
    return hashlib.sha256(data).hexdigest()


def project_sources(journal, scans):
    files, state = {}, None
    if journal is not None:
        run = FormRun.load(journal)
        state = dict(run.data)
        for key, group in (("source", "Vorlage"), ("workbook", "Excel")):
            path = Path(state[key])
            state[key] = group + "/" + path.name
            files[state[key]] = path, state["source_hash"] if key == "source" else None
        for record in run.records.values():
            path = journal.parent / record["name"]
            if record["done"] or path.exists():
                files["Ausgabe/" + path.name] = path, record["pdf"]
    for path in scans:
        files["Scans/" + path.name] = path, None
    return state, files


def write_project(path, state, files, password=None):
    entries = {}
    total = 0
    for name, (source, expected) in files.items():
        with source.open("rb") as stream:
            data = stream.read(MAX_PROJECT - total + 1)
        total += len(data)
        if total > MAX_PROJECT:
            raise ValueError("Das Projektpaket darf bis zu 64 MiB an Dateien enthalten.")
        digest = file_hash(data)
        if expected is not None and digest != expected:
            raise ValueError(f"Die Datei wurde seit dem Formularlauf verändert: {source.name}")
        entries[name] = data, digest
    manifest = {"version": 1, "run": state, "files": {name: digest for name, (_, digest) in entries.items()}}
    buffer = io.BytesIO() if password is not None else None
    with ZipFile(path if buffer is None else buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("projekt.json", json.dumps(manifest, ensure_ascii=False))
        for name, (data, _) in entries.items():
            archive.writestr(name, data)
    if buffer is not None:
        encrypt_package(path, buffer.getvalue(), password, PROJECT_PREFIX, MAX_CONTAINER)


def read_project(path, password=None):
    if path.stat().st_size > MAX_CONTAINER:
        raise ValueError("Das Projektpaket ist zu groß.")
    return open_package(path, password, PROJECT_PREFIX, MAX_CONTAINER, read_project_zip)


def read_project_zip(source):
    with ZipFile(source) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (len(names) > 1003 or len(names) != len(set(names)) or "projekt.json" not in names
                or archive.getinfo("projekt.json").file_size > 2 * 1024 * 1024
                or sum(info.file_size for info in infos if info.filename != "projekt.json") > MAX_PROJECT):
            raise ValueError("Das Projektpaket ist ungültig oder zu groß.")
        manifest = json.loads(archive.read("projekt.json"))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("Nicht unterstütztes Projektpaket.")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or set(files) != set(names) - {"projekt.json"}:
            raise ValueError("Die Dateiliste im Projektpaket ist ungültig.")
        groups = {"Vorlage": ".pdf", "Excel": ".xlsx", "Ausgabe": ".pdf", "Scans": ".pdf"}
        folded = set()
        for key, digest in files.items():
            group, _, name = key.partition("/")
            if (group not in groups or not file_name(name, groups[group]) or not valid_digest(digest)
                    or key.casefold() in folded):
                raise ValueError("Ungültiger Dateiname oder Prüfsumme im Projektpaket.")
            folded.add(key.casefold())
        run = manifest.get("run")
        expected = set()
        if run is not None:
            if (not isinstance(run, dict) or not isinstance(run.get("source"), str)
                    or not isinstance(run.get("workbook"), str)
                    or not run["source"].startswith("Vorlage/") or not run["workbook"].startswith("Excel/")
                    or run["source"] not in files or run["workbook"] not in files):
                raise ValueError("Die Eingabedateien für den Formularlauf fehlen.")
            checked = relocated_run(run, Path.cwd() / "Projekt")
            expected.update((run["source"], run["workbook"]))
            if checked.data["source_hash"] != files[run["source"]]:
                raise ValueError("Die PDF-Vorlage passt nicht zum Formularlauf.")
            for record in checked.records.values():
                key = "Ausgabe/" + record["name"]
                if record["done"] or key in files:
                    if files.get(key) != record["pdf"]:
                        raise ValueError("Eine fertige PDF fehlt oder passt nicht zum Formularlauf.")
                    expected.add(key)
        if expected != {key for key in files if not key.startswith("Scans/")}:
            raise ValueError("Das Projektpaket enthält unerwartete Dateien.")
        data = {name: archive.read(name) for name in files}
        if any(file_hash(raw) != files[name] for name, raw in data.items()):
            raise ValueError("Mindestens eine Datei im Projektpaket ist beschädigt.")
        return run, data


def relocated_run(state, target):
    journal = target / "Ausgabe" / "Formularlauf.werkbanklauf"
    data = {**state, "source": str(target / state["source"]), "workbook": str(target / state["workbook"])}
    data.pop("overview", None)
    return FormRun.from_data(journal, data)


def scan_destinations(files, inbox):
    paths = {}
    for key in files:
        if key.startswith("Scans/"):
            name = Path(key).name
            target = inbox / name
            try:
                day = date.fromisoformat(name[:10])
            except ValueError:
                day = None
            if day is None or target.exists() or target.is_symlink():
                target = inbox / f"{day or date.today()}-{uuid.uuid4().hex}.pdf"
            paths[key] = target
    return paths


def publish_new(source, target):
    if os.name == "nt":
        source.rename(target)
    else:
        os.link(source, target)


def import_project(target, run, files, scans):
    if target.exists() or target.is_symlink():
        raise FileExistsError("Der Projektordner ist bereits vorhanden.")
    copied = {}
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        staging = Path(folder) / "Projekt"
        staging.mkdir()
        journal = None
        if run is not None:
            imported = relocated_run(run, target)
            journal = imported.path
            candidate = staging / journal.relative_to(target)
            candidate.parent.mkdir()
            FormRun.from_data(candidate, imported.data).write()
        for name, raw in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        try:
            for name, destination in scans.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
                    candidate = Path(directory) / "Scan.pdf"
                    candidate.write_bytes(files[name])
                    publish_new(candidate, destination)
                copied[destination] = file_hash(files[name])
            # On Windows rename fails if another process just created the target.
            staging.rename(target)
        except Exception:
            for path, digest in copied.items():
                try:
                    if path.is_file() and file_hash(path.read_bytes()) == digest:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
    return journal
