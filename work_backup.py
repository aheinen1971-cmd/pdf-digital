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
from form_run import FormRun
from scan_form import SCAN_FORM_TEMPLATES, scan_form_templates, validate_scan_templates
from filing import file_signature
from preferences import (FORM_TEMPLATES, GROUPS, export_package, form_templates, import_groups,
                         validate_form_templates, validate_groups)
from recovery import atomic_write
from update_worker import digest_file, version_tuple


BACKUP_GROUPS = {"settings": "Favoriten und gespeicherte Einstellungen",
                 "plans": "Messlisten und Maßstäbe",
                 "archive": "Archivfreigaben und Belegnotizen",
                 "locations": "Ordner und Formularläufe"}
FOLDER_SETTINGS = {"invoice-watch/folder": "Rechnungsordner", "invoice-watch/archive": "Rechnungsarchiv",
                   "scan/inbox-folder": "Scan-Posteingang", "scan/archive-folder": "Scan-Archiv",
                   "form-export/run-folder": "Letzter Ordner für Formularläufe"}
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


def default_scan_inbox():
    return app_backup_root().with_name("Scan-Posteingang")


def absolute_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096 and "\x00" not in value
            and Path(value).is_absolute() and ".." not in Path(value).parts)


def local_path(path):
    return not path.drive.startswith(("\\\\", "//"))


def capture_locations(settings):
    folders = {}
    for key in FOLDER_SETTINGS:
        name = settings.value(key, "", str)
        if absolute_path(name):
            folders[key] = name
    if "scan/inbox-folder" not in folders and default_scan_inbox().is_dir():
        folders["scan/inbox-folder"] = str(default_scan_inbox())
    paths = [Path(value) for value in settings.value("form-export/runs", [], list) if absolute_path(value)]
    if "form-export/run-folder" in folders:
        paths.extend(Path(folders["form-export/run-folder"]).glob("*.werkbanklauf"))
    runs = {}
    for path in list(dict.fromkeys(paths))[:200]:
        if path.suffix.lower() != ".werkbanklauf":
            continue
        try:
            run = FormRun.load(path)
            overview = run.data.get("overview")
            if overview and sum(record["done"] for record in run.records.values()) >= overview[0]:
                try:
                    if tuple(overview[1:]) == file_signature(Path(run.data["workbook"])):
                        continue
                except OSError:
                    pass
            runs[str(path)] = run.data
        except (OSError, ValueError):
            continue
    return {"folders": folders, "runs": runs}


def validate_locations(data):
    if not isinstance(data, dict):
        raise ValueError("Ungültige Ordnerdaten im Backup.")
    folders, runs = data.get("folders", {}), data.get("runs", {})
    if (not isinstance(folders, dict) or not folders.keys() <= FOLDER_SETTINGS.keys()
            or not all(absolute_path(path) for path in folders.values())
            or not isinstance(runs, dict) or len(runs) > 200):
        raise ValueError("Ungültige Ordner oder Formularläufe im Backup.")
    for name, state in runs.items():
        if not absolute_path(name) or Path(name).suffix.lower() != ".werkbanklauf":
            raise ValueError("Ungültiger Pfad für einen Formularlauf.")
        FormRun.from_data(Path(name), state)
        if not all(absolute_path(state[key]) for key in ("source", "workbook")):
            raise ValueError("Ungültiger PDF- oder Excel-Pfad im Formularlauf.")
    return {"folders": folders, "runs": runs}


def location_roots(locations):
    roots = set(locations["folders"].values())
    for journal, state in locations["runs"].items():
        roots.update(str(Path(value).parent) for value in (journal, state["source"], state["workbook"]))
    return sorted(roots, key=str.casefold)


def relocated_path(value, replacements):
    path = Path(value)
    for old in sorted(replacements, key=lambda name: len(Path(name).parts), reverse=True):
        if path.is_relative_to(old):
            root = Path(replacements[old]).resolve(strict=True)
            candidate = (root / path.relative_to(old)).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("Ein Pfad liegt außerhalb des neu zugeordneten Ordners.")
            return candidate
    return path if local_path(path) else None


def location_plan(locations, replacements):
    folders, runs, rows = {}, {}, []
    for key, old in locations["folders"].items():
        target = relocated_path(old, replacements)
        exists = target is not None and target.is_dir()
        if exists:
            folders[key] = str(target)
        rows.append(("Ordner und Läufe", FOLDER_SETTINGS[key] + " · " + str(target or old),
                     "Übernehmen" if exists else "Ordner fehlt oder wurde nicht geprüft"))
    for name, state in locations["runs"].items():
        journal = relocated_path(name, replacements)
        source = relocated_path(state["source"], replacements)
        workbook = relocated_path(state["workbook"], replacements)
        exists = (journal is not None and journal.parent.is_dir() and source is not None and source.is_file()
                  and workbook is not None and workbook.is_file())
        if exists:
            updated = {**state, "source": str(source), "workbook": str(workbook)}
            if journal in runs:
                raise ValueError("Zwei Formularläufe würden dieselbe Laufdatei verwenden.")
            runs[journal] = state, updated
        rows.append(("Ordner und Läufe", str(journal or name),
                     "Lauf übernehmen" if exists else "Zielordner, PDF-Vorlage oder Excel-Datei fehlt"))
    return folders, runs, rows


def restore_locations(locations, replacements, settings):
    from PySide6.QtCore import QLockFile

    folders, runs, _ = location_plan(locations, replacements)
    restored, warnings = [], []
    for path, (saved, updated) in runs.items():
        lock = QLockFile(str(path) + ".lock")
        lock.setStaleLockTime(0)
        try:
            if not lock.tryLock(0):
                raise OSError("Der Lauf ist bereits geöffnet.")
            if path.is_symlink():
                raise ValueError("Die Laufdatei ist eine Verknüpfung.")
            if path.exists():
                current = FormRun.load(path).data
                identity = ("source_hash", "sheet", "columns")
                if (any(current[key] != saved[key] for key in identity)
                        or any(current[key] not in (saved[key], updated[key]) for key in ("source", "workbook"))):
                    raise ValueError("Eine andere Laufdatei ist bereits vorhanden.")
                updated = {**current, "source": updated["source"], "workbook": updated["workbook"]}
            FormRun.from_data(path, updated).write()
            restored.append(str(path))
        except (OSError, ValueError) as exc:
            warnings.append(f"{path.name}: {exc}")
        finally:
            if lock.isLocked():
                lock.unlock()
    for key, value in folders.items():
        settings.setValue(key, value)
    if restored:
        known = settings.value("form-export/runs", [], list)
        settings.setValue("form-export/runs", list(dict.fromkeys([*restored, *known]))[:200])
    settings.sync()
    return warnings


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
                                      "scan_form_templates": scan_form_templates(settings),
                                      "dark_mode": settings.value("view/dark-mode", False, bool),
                                      "locations": capture_locations(settings),
                                      "archive_root": settings.value("invoice-watch/archive", "", str)},
                                     ensure_ascii=False).encode("utf-8")
    if total + len(entries["meta.json"]) > MAX_TOTAL:
        raise ValueError("Der Arbeitsstand ist für ein gemeinsames Backup zu groß.")
    buffer = io.BytesIO() if password is not None else None
    with ZipFile(target if buffer is None else buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("format", b"PDF-Werkbank-Backup-1")
        for name, data in entries.items():
            archive.writestr(name, data)
    if buffer is not None:
        encrypt_package(target, buffer.getvalue(), password, ENCRYPTED_PREFIX, MAX_ENCRYPTED)


def encrypt_package(target, payload, password, prefix, maximum):
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise ValueError(f"Das Passwort muss {MIN_PASSWORD} bis {MAX_PASSWORD} Zeichen lang sein.")
    salt, nonce = os.urandom(16), os.urandom(12)
    header = prefix + ENCRYPTED_VERSION + salt + nonce
    if len(header) + len(payload) + 16 > maximum:
        raise ValueError("Das Paket ist zu groß.")
    encrypted = backup_cipher(password, salt).encrypt(nonce, payload, header)
    with Path(target).open("wb") as stream:
        stream.write(header)
        stream.write(encrypted)


def open_package(path, password, prefix, maximum, reader):
    with Path(path).open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > maximum:
            raise ValueError("Das Paket ist zu groß.")
        if stream.read(len(prefix)) != prefix:
            if password is not None:
                raise ValueError("Das verschlüsselte Backup wurde beim Lesen verändert.")
            stream.seek(0)
            return reader(stream)
        version = stream.read(1)
        if version != ENCRYPTED_VERSION:
            raise ValueError("Dieses verschlüsselte Backup-Format wird nicht unterstützt.")
        salt, nonce = stream.read(16), stream.read(12)
        header = prefix + version + salt + nonce
        if len(nonce) != 12 or not len(header) + 16 <= size <= maximum:
            raise ValueError("Das verschlüsselte Backup ist beschädigt oder zu groß.")
        if password is None:
            raise BackupPasswordRequired("Dieses Backup ist mit einem Passwort geschützt.")
        ciphertext = stream.read(maximum - len(header) + 1)
        if len(ciphertext) != size - len(header):
            raise ValueError("Das verschlüsselte Backup wurde beim Lesen verändert.")
    try:
        payload = backup_cipher(password, salt).decrypt(nonce, ciphertext, header)
    except InvalidTag as exc:
        raise ValueError("Das Passwort ist falsch oder die Sicherungsdatei wurde beschädigt.") from exc
    return reader(io.BytesIO(payload))


def read_backup(path, password=None):
    return open_package(path, password, ENCRYPTED_PREFIX, MAX_ENCRYPTED, read_backup_zip)


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
        scan_templates = validate_scan_templates(metadata.get("scan_form_templates", {}))
        dark_mode = metadata.get("dark_mode")
        if dark_mode is not None and type(dark_mode) is not bool:
            raise ValueError("Ungültige Darstellungseinstellung im Backup.")
        locations = validate_locations(metadata.get("locations", {}))
    return {"settings": groups, "plans": plans, "archive": history, "plan_times": times,
            "archive_root": archive_root, "form_templates": templates, "scan_form_templates": scan_templates,
            "locations": locations, "dark_mode": dark_mode}


def restore_backup(data, selection, settings, root, mapped=None):
    if "settings" in selection:
        templates = data.get("form_templates")
        scan_templates = data.get("scan_form_templates")
        merged_scan = validate_scan_templates({**scan_form_templates(settings), **scan_templates}) if scan_templates else None
        if templates:
            merged = validate_form_templates({**form_templates(settings), **templates})
            settings.setValue(FORM_TEMPLATES, json.dumps(merged, ensure_ascii=False))
        if merged_scan is not None:
            settings.setValue(SCAN_FORM_TEMPLATES, json.dumps(merged_scan, ensure_ascii=False))
        if data.get("dark_mode") is not None:
            settings.setValue("view/dark-mode", data["dark_mode"])
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
    if data.get("scan_form_templates"):
        local_scans = scan_form_templates(settings)
        for name, fields in data["scan_form_templates"].items():
            state = ("Bereits gleich" if local_scans.get(name) == fields else
                     "Übernehmen – ersetzt die gleichnamige Vorlage" if name in local_scans else "Übernehmen")
            rows.append(("Einstellungen", f"Scan-Feldvorlage · {name}", state))
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
    rows.extend(("Scan-Feldvorlage", name, "Lesbar") for name in data.get("scan_form_templates", {}))
    _, _, paths = location_plan(data.get("locations", {"folders": {}, "runs": {}}), {})
    rows.extend((group, name, status.replace("Übernehmen", "Erreichbar").replace("Lauf übernehmen", "Laufdaten lesbar"))
                for group, name, status in paths)
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
        if not local_path(target):
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
