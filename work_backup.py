# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from plan_measure import MAX_PLAN_BYTES
from preferences import GROUPS, export_package, import_groups, validate_groups
from recovery import atomic_write
from update_worker import digest_file, version_tuple


BACKUP_GROUPS = {"settings": "Favoriten und gespeicherte Einstellungen",
                 "plans": "Messlisten und Maßstäbe",
                 "archive": "Archivfreigaben und Belegnotizen"}
MAX_TOTAL = 48 * 1024 * 1024
HEX = r"[a-f0-9]{64}"


def write_backup(target, settings, root):
    keys = [key for key in settings.allKeys() if re.fullmatch(r"invoice-watch/(archived|archived-digest|note|reviewed)/" + HEX, key)]
    history = {key: settings.value(key, "", str) for key in keys}
    entries = {"settings.json": export_package(settings),
               "archive.json": json.dumps(history, ensure_ascii=False).encode("utf-8")}
    total = sum(map(len, entries.values()))
    plan_times = {}
    for path in root.glob("*/*.json"):
        relative = path.relative_to(root).as_posix()
        if not re.fullmatch(r"(?:" + HEX + r"|Importiert)/" + HEX + r"\.json", relative) or path.is_symlink():
            continue
        with path.open("rb") as stream:
            data = stream.read(MAX_PLAN_BYTES + 1)
        total += len(data)
        if len(data) > MAX_PLAN_BYTES or total > MAX_TOTAL:
            raise ValueError("Die Messlisten sind für ein gemeinsames Backup zu groß.")
        entries["plans/" + relative] = data
        plan_times[relative] = path.stat().st_mtime_ns
    entries["meta.json"] = json.dumps({"plan_times": plan_times,
                                      "archive_root": settings.value("invoice-watch/archive", "", str)},
                                     ensure_ascii=False).encode("utf-8")
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        archive.writestr("format", b"PDF-Werkbank-Backup-1")
        for name, data in entries.items():
            archive.writestr(name, data)


def read_backup(path):
    with ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if (len(set(names)) != len(names) or sum(entry.file_size for entry in entries) > MAX_TOTAL
                or not {"format", "settings.json", "archive.json"} <= set(names)
                or archive.getinfo("format").file_size > 30 or archive.read("format") != b"PDF-Werkbank-Backup-1"):
            raise ValueError("Das Werkbank-Backup ist ungültig oder zu groß.")
        groups = validate_groups(json.loads(archive.read("settings.json"))["groups"])
        history = json.loads(archive.read("archive.json"))
        if not isinstance(history, dict) or any(
                not re.fullmatch(r"invoice-watch/(archived|archived-digest|note|reviewed)/" + HEX, key)
                or not isinstance(value, str) or len(value) > 4096
                for key, value in history.items()):
            raise ValueError("Ungültige Archivfreigaben oder Belegnotizen.")
        plans = {}
        for entry in entries:
            if entry.filename in ("format", "settings.json", "archive.json", "meta.json"):
                continue
            match = re.fullmatch(r"plans/((?:" + HEX + r"|Importiert)/" + HEX + r"\.json)", entry.filename)
            if not match or entry.file_size > MAX_PLAN_BYTES:
                raise ValueError("Ungültiger Dateieintrag im Backup.")
            raw = archive.read(entry)
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("version") != 1 or not isinstance(body.get("pages"), list):
                raise ValueError("Ungültige Messliste im Backup.")
            plans[match[1]] = raw
        metadata = json.loads(archive.read("meta.json")) if "meta.json" in names else {}
        if not isinstance(metadata, dict) or not isinstance(metadata.get("plan_times", {}), dict):
            raise ValueError("Ungültige Metadaten im Backup.")
        times = {name: value for name, value in metadata.get("plan_times", {}).items()
                 if name in plans and type(value) is int and value > 0}
        archive_root = metadata.get("archive_root", "")
        if not isinstance(archive_root, str) or len(archive_root) > 4096:
            raise ValueError("Ungültiger Archivpfad im Backup.")
    return {"settings": groups, "plans": plans, "archive": history, "plan_times": times, "archive_root": archive_root}


def restore_backup(data, selection, settings, root, mapped=None):
    if "settings" in selection:
        import_groups(settings, data["settings"])
    if "archive" in selection:
        for key, value in data["archive"].items():
            if not settings.contains(key):
                settings.setValue(key, value)
        for key, value in (mapped or {}).items():
            if not settings.contains(key) or settings.value(key, "", str) == data["archive"].get(key):
                settings.setValue(key, value)
        settings.sync()
    conflicts = 0
    if "plans" in selection:
        portable = {}
        for name, raw in data["plans"].items():
            path = root / name
            if path.is_file():
                if path.read_bytes() != raw:
                    conflicts += 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(path, raw)
            portable.setdefault(path.name, set()).add(raw)
        for name, alternatives in portable.items():
            path = root / "Importiert" / name
            if len(alternatives) == 1 and not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(path, next(iter(alternatives)))
    return conflicts


def app_backup_root():
    return Path(os.environ["LOCALAPPDATA"]) / "PDF Werkbank" / "Sicherungen"


def automatic_backup(settings, measurements, source, target, root=None, refresh=False):
    from PySide6.QtCore import QLockFile, QSettings

    if version_tuple(source) is None or version_tuple(target) is None or version_tuple(source) >= version_tuple(target):
        raise ValueError("Ungültiger Versionswechsel für die Sicherung.")
    root = root or app_backup_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"Vor-{source}-auf-{target}.werkbankbackup"
    lock = QLockFile(str(root / ".update-backup.lock"))
    if not lock.tryLock(5000):
        raise OSError("Ein anderes Fenster sichert gerade den Arbeitsstand.")
    try:
        if path.is_file() and not refresh:
            try:
                read_backup(path)
                return path
            except (OSError, ValueError, BadZipFile, KeyError):
                pass
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise OSError("Die lokalen Einstellungen konnten nicht gelesen werden.")
        with TemporaryDirectory(dir=root) as folder:
            candidate = Path(folder) / path.name
            write_backup(candidate, settings, measurements)
            read_backup(candidate)
            os.replace(candidate, path)
        return path
    finally:
        lock.unlock()


def backup_preview(data, settings, root):
    local_groups = json.loads(export_package(settings))["groups"]
    rows = [("Einstellungen", GROUPS[key], "Bereits gleich" if value == local_groups.get(key)
             else "Übernehmen – ersetzt die lokalen Einstellungen dieser Gruppe")
            for key, value in data["settings"].items()]
    for name, raw in data["plans"].items():
        target = root / name
        status = "Übernehmen"
        if target.is_file():
            if target.read_bytes() == raw:
                status = "Bereits gleich"
            else:
                saved_time = data.get("plan_times", {}).get(name, 0)
                status = ("Lokale Fassung ist neuer" if saved_time and target.stat().st_mtime_ns > saved_time
                          else "Lokale Fassung weicht ab")
                status += " – bleibt erhalten"
        rows.append(("Messlisten", name.split("/")[-1][:16], status))
    for key, value in data["archive"].items():
        kind = key.split("/")[1]
        label = {"archived": "Archivfreigabe", "archived-digest": "Archivkopie",
                 "note": "Belegnotiz", "reviewed": "Prüfstatus"}[kind]
        entry = value.partition("|")[2] if kind.startswith("archived") else key.rsplit("/", 1)[-1][:16]
        status = ("Bereits gleich" if settings.value(key, "", str) == value
                  else "Lokaler Eintrag bleibt erhalten") if settings.contains(key) else "Übernehmen"
        rows.append((label, entry, status))
    return rows


def archive_remap(history, old, new):
    old, new = Path(old), Path(new).resolve(strict=True)
    if not old.is_absolute() or not new.is_dir():
        raise ValueError("Bitte einen absoluten bisherigen Pfad und einen vorhandenen neuen Ordner wählen.")
    mapped, rows = {}, []
    for key, value in history.items():
        if key.split("/")[1] not in ("archived", "archived-digest"):
            continue
        digest, separator, original = value.partition("|")
        if not separator or not re.fullmatch(HEX, digest):
            continue
        try:
            relative = Path(original).relative_to(old)
            candidate = (new / relative).resolve()
            if not candidate.is_relative_to(new):
                continue
        except (ValueError, OSError):
            continue
        try:
            matches = candidate.is_file() and digest_file(candidate) == digest
        except OSError:
            matches = False
        rows.append(("Archivzuordnung", str(candidate), "Datei geprüft" if matches else "Fehlt oder weicht ab"))
        if matches:
            mapped[key] = f"{digest}|{candidate}"
            mapped["invoice-watch/archived-digest/" + digest] = f"{digest}|{candidate}"
    return mapped, rows
