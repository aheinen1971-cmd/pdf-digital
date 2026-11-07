# SPDX-License-Identifier: AGPL-3.0-only
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from plan_measure import MAX_PLAN_BYTES
from preferences import export_package, import_groups, validate_groups
from recovery import atomic_write


BACKUP_GROUPS = {"settings": "Favoriten und gespeicherte Einstellungen",
                 "plans": "Messlisten und Maßstäbe",
                 "archive": "Archivfreigaben und Belegnotizen"}
MAX_TOTAL = 48 * 1024 * 1024
HEX = r"[a-f0-9]{64}"


def write_backup(target, settings, root):
    keys = [key for key in settings.allKeys() if re.fullmatch(r"invoice-watch/(archived|note|reviewed)/" + HEX, key)]
    history = {key: settings.value(key, "", str) for key in keys}
    entries = {"settings.json": export_package(settings),
               "archive.json": json.dumps(history, ensure_ascii=False).encode("utf-8")}
    total = sum(map(len, entries.values()))
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
                not re.fullmatch(r"invoice-watch/(archived|note|reviewed)/" + HEX, key)
                or not isinstance(value, str) or len(value) > 4096
                for key, value in history.items()):
            raise ValueError("Ungültige Archivfreigaben oder Belegnotizen.")
        plans = {}
        for entry in entries:
            if entry.filename in ("format", "settings.json", "archive.json"):
                continue
            match = re.fullmatch(r"plans/((?:" + HEX + r"|Importiert)/" + HEX + r"\.json)", entry.filename)
            if not match or entry.file_size > MAX_PLAN_BYTES:
                raise ValueError("Ungültiger Dateieintrag im Backup.")
            raw = archive.read(entry)
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("version") != 1 or not isinstance(body.get("pages"), list):
                raise ValueError("Ungültige Messliste im Backup.")
            plans[match[1]] = raw
    return {"settings": groups, "plans": plans, "archive": history}


def restore_backup(data, selection, settings, root):
    if "settings" in selection:
        import_groups(settings, data["settings"])
    if "archive" in selection:
        for key, value in data["archive"].items():
            if not settings.contains(key):
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
