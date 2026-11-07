# SPDX-License-Identifier: AGPL-3.0-only
import io
import json
import os
import re
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from plan_measure import MAX_PLAN_BYTES
from preferences import (FORM_TEMPLATES, GROUPS, export_package, form_templates, import_groups,
                         validate_form_templates, validate_groups)
from recovery import atomic_write
from update_worker import digest_file, version_tuple


BACKUP_GROUPS = {"settings": "Favoriten und gespeicherte Einstellungen",
                 "plans": "Messlisten und Maßstäbe",
                 "archive": "Archivfreigaben und Belegnotizen"}
MAX_TOTAL = 48 * 1024 * 1024
MAX_ENCRYPTED = MAX_TOTAL + 4 * 1024 * 1024
ENCRYPTED_PREFIX = b"PDF-WERKBANK-BACKUP\x00"
ENCRYPTED_VERSION = b"\x01"
MIN_PASSWORD = 8
MAX_PASSWORD = 1024
HEX = r"[a-f0-9]{64}"
ARCHIVE_KEY = r"invoice-watch/(archived|archived-digest|note|reviewed)/" + HEX
REMINDER_KEY = r"invoice-watch/reminder/" + HEX


class BackupPasswordRequired(ValueError):
    pass


def backup_cipher(password, salt):
    if not 1 <= len(password) <= MAX_PASSWORD:
        raise ValueError("Die Passwortlänge ist ungültig.")
    key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))
    return AESGCM(key)


def write_backup(target, settings, root, password=None):
    if password is not None and not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise ValueError(f"Das Passwort muss {MIN_PASSWORD} bis {MAX_PASSWORD} Zeichen lang sein.")
    keys = settings.allKeys()
    history = {key: settings.value(key, "", str) for key in keys if re.fullmatch(ARCHIVE_KEY, key)}
    reminders = {key: settings.value(key, "", str) for key in keys if re.fullmatch(REMINDER_KEY, key)}
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
    entries["meta.json"] = json.dumps({"plan_times": plan_times, "reminders": reminders,
                                      "form_templates": form_templates(settings),
                                      "archive_root": settings.value("invoice-watch/archive", "", str)},
                                     ensure_ascii=False).encode("utf-8")
    buffer = io.BytesIO() if password is not None else None
    with ZipFile(target if buffer is None else buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("format", b"PDF-Werkbank-Backup-1")
        for name, data in entries.items():
            archive.writestr(name, data)
    if buffer is not None:
        salt, nonce = os.urandom(16), os.urandom(12)
        header = ENCRYPTED_PREFIX + ENCRYPTED_VERSION + salt + nonce
        payload = buffer.getvalue()
        if len(header) + len(payload) + 16 > MAX_ENCRYPTED:
            raise ValueError("Das Werkbank-Backup ist zu groß.")
        encrypted = backup_cipher(password, salt).encrypt(nonce, payload, header)
        with Path(target).open("wb") as stream:
            stream.write(header)
            stream.write(encrypted)


def read_backup(path, password=None):
    with Path(path).open("rb") as stream:
        if stream.read(len(ENCRYPTED_PREFIX)) != ENCRYPTED_PREFIX:
            if password is not None:
                raise ValueError("Das verschlüsselte Backup wurde beim Lesen verändert.")
            stream.seek(0)
            return read_backup_zip(stream)
        version = stream.read(1)
        if version != ENCRYPTED_VERSION:
            raise ValueError("Dieses verschlüsselte Backup-Format wird nicht unterstützt.")
        salt, nonce = stream.read(16), stream.read(12)
        header = ENCRYPTED_PREFIX + version + salt + nonce
        size = os.fstat(stream.fileno()).st_size
        if len(nonce) != 12 or not len(header) + 16 <= size <= MAX_ENCRYPTED:
            raise ValueError("Das verschlüsselte Backup ist beschädigt oder zu groß.")
        if password is None:
            raise BackupPasswordRequired("Dieses Backup ist mit einem Passwort geschützt.")
        ciphertext = stream.read(MAX_ENCRYPTED - len(header) + 1)
        if len(ciphertext) != size - len(header):
            raise ValueError("Das verschlüsselte Backup wurde beim Lesen verändert.")
    try:
        payload = backup_cipher(password, salt).decrypt(nonce, ciphertext, header)
    except InvalidTag as exc:
        raise ValueError("Das Passwort ist falsch oder die Sicherungsdatei wurde beschädigt.") from exc
    return read_backup_zip(io.BytesIO(payload))


def read_backup_zip(source):
    with ZipFile(source) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if (len(set(names)) != len(names) or sum(entry.file_size for entry in entries) > MAX_TOTAL
                or not {"format", "settings.json", "archive.json"} <= set(names)
                or archive.getinfo("format").file_size > 30 or archive.read("format") != b"PDF-Werkbank-Backup-1"):
            raise ValueError("Das Werkbank-Backup ist ungültig oder zu groß.")
        groups = validate_groups(json.loads(archive.read("settings.json"))["groups"])
        history = json.loads(archive.read("archive.json"))
        if not isinstance(history, dict) or any(
                not re.fullmatch(ARCHIVE_KEY, key)
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
        reminders = metadata.get("reminders", {})
        if not isinstance(reminders, dict) or any(not re.fullmatch(REMINDER_KEY, key) or not isinstance(value, str)
                                                  for key, value in reminders.items()):
            raise ValueError("Ungültige Wiedervorlagen im Backup.")
        for value in reminders.values():
            reminder_date(value)
        history.update(reminders)
        times = {name: value for name, value in metadata.get("plan_times", {}).items()
                 if name in plans and type(value) is int and value > 0}
        archive_root = metadata.get("archive_root", "")
        if not isinstance(archive_root, str) or len(archive_root) > 4096:
            raise ValueError("Ungültiger Archivpfad im Backup.")
        templates = validate_form_templates(metadata.get("form_templates", {}))
    return {"settings": groups, "plans": plans, "archive": history, "plan_times": times,
            "archive_root": archive_root, "form_templates": templates}


def restore_backup(data, selection, settings, root, mapped=None):
    if "settings" in selection:
        templates = data.get("form_templates")
        if templates:
            merged = validate_form_templates({**form_templates(settings), **templates})
            settings.setValue(FORM_TEMPLATES, json.dumps(merged, ensure_ascii=False))
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
    if data.get("form_templates"):
        local_templates = form_templates(settings)
        for name, columns in data["form_templates"].items():
            state = ("Bereits gleich" if local_templates.get(name) == columns else
                     "Übernehmen – ersetzt die gleichnamige Vorlage" if name in local_templates else "Übernehmen")
            rows.append(("Einstellungen", f"Excel-Formularvorlage · {name}", state))
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
                 "note": "Belegnotiz", "reviewed": "Prüfstatus", "reminder": "Wiedervorlage"}[kind]
        entry = value.partition("|")[2] if kind.startswith("archived") else key.rsplit("/", 1)[-1][:16]
        status = ("Bereits gleich" if settings.value(key, "", str) == value
                  else "Lokaler Eintrag bleibt erhalten") if settings.contains(key) else "Übernehmen"
        rows.append((label, entry, status))
    return rows


def reminder_date(value):
    day, separator, note = value.partition("|")
    parsed = date.fromisoformat(day)
    if not separator or parsed.isoformat() != day or len(note) > 1000:
        raise ValueError("Ungültige Wiedervorlage.")
    return parsed, note


def backup_check_rows(data):
    rows = [("Einstellungen", GROUPS[key], "Lesbar") for key in data["settings"]]
    rows.extend(("Excel-Formularvorlage", name, "Lesbar") for name in data.get("form_templates", {}))
    rows.extend(("Messliste", name, "Lesbar") for name in data["plans"])
    labels = {"note": "Belegnotiz", "reviewed": "Prüfstatus", "reminder": "Wiedervorlage"}
    links = set()
    for key, value in data["archive"].items():
        kind = key.split("/")[1]
        if kind in labels:
            rows.append((labels[kind], key.rsplit("/", 1)[-1][:16], "Lesbar"))
        else:
            links.add(value.partition("|")[2])
    for name in sorted(links):
        target = Path(name)
        if target.drive.startswith(("\\\\", "//")):
            state = "Netzwerkpfad nicht geprüft"
        elif not target.is_absolute():
            state = "Kein gültiger absoluter Pfad"
        elif target.is_file():
            state = "Datei vorhanden"
        else:
            state = "Datei fehlt oder ist nicht erreichbar"
        rows.append(("Archivverweis", name or "Ohne Dateipfad", state))
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
