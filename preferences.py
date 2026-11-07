# SPDX-License-Identifier: AGPL-3.0-only
import json


GROUPS = {"favorites": "Favoriten", "workflows": "Gespeicherte Abläufe",
          "page-labels": "Kopf- und Fußzeilen", "scan": "Scanprofil"}
DEFAULT_FAVORITES = ["scan", "quick-scan", "folder", "duplex"]
COMMANDS = {"scan", "quick-scan", "inbox", "folder", "duplex", "booklet", "open", "save", "archive", "compare"}
STEPS = {"ocr", "rotate", "compress"}


def short_text(value, maximum):
    return isinstance(value, str) and len(value) <= maximum and not any(ord(char) < 32 for char in value)


def validate_groups(groups):
    if not isinstance(groups, dict) or not groups or not groups.keys() <= GROUPS.keys():
        raise ValueError("Die Datei enthält unbekannte oder fehlende Einstellungsgruppen.")
    for group, value in groups.items():
        if group == "favorites":
            valid = isinstance(value, list) and len(value) <= 100 and all(
                short_text(key, 130) and (key in COMMANDS or key.startswith("profile:") and len(key) > 8)
                for key in value)
        elif group == "workflows":
            valid = isinstance(value, dict) and len(value) <= 100 and all(
                short_text(name, 120) and name.strip() and isinstance(steps, list) and 1 <= len(steps) <= 3
                and all(isinstance(step, str) and step in STEPS for step in steps)
                for name, steps in value.items())
        elif group == "page-labels":
            valid = isinstance(value, dict) and len(value) <= 100 and all(
                short_text(name, 120) and name.strip() and isinstance(fields, list) and len(fields) == 4
                and all(short_text(field, 500) for field in fields[:2]) and fields[2] in ("links", "mittig", "rechts")
                and type(fields[3]) is int and 6 <= fields[3] <= 24 for name, fields in value.items())
        else:
            valid = (isinstance(value, dict) and value.keys() == {"a4", "ocr"}
                     and all(type(flag) is bool for flag in value.values()))
        if not valid:
            raise ValueError(f"Ungültige Daten in der Gruppe „{GROUPS[group]}“.")
    return groups


def read_package(path):
    with path.open("rb") as stream:
        raw = stream.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError("Das Einstellungspaket ist zu groß.")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as exc:
        raise ValueError("Das Einstellungspaket konnte nicht gelesen werden.") from exc
    if not isinstance(data, dict) or data.get("format") != "PDF-Werkbank-Einstellungen" or data.get("version") != 1:
        raise ValueError("Das ist kein unterstütztes Einstellungspaket für PDF Werkbank.")
    return validate_groups(data.get("groups"))


def export_package(settings):
    groups = validate_groups({
        "favorites": settings.value("toolbar/favorites", DEFAULT_FAVORITES),
        "workflows": json.loads(settings.value("batch/profiles", "{}", str)),
        "page-labels": json.loads(settings.value("document/page-labels", "{}", str)),
        "scan": {"a4": settings.value("scan/a4", False, bool), "ocr": settings.value("scan/ocr", True, bool)},
    })
    return json.dumps({"format": "PDF-Werkbank-Einstellungen", "version": 1, "groups": groups},
                      ensure_ascii=False, indent=2).encode("utf-8")


def import_groups(settings, groups):
    for group, value in validate_groups(groups).items():
        if group == "favorites":
            settings.setValue("toolbar/favorites", list(dict.fromkeys(value)))
        elif group == "workflows":
            settings.setValue("batch/profiles", json.dumps(value, ensure_ascii=False))
        elif group == "page-labels":
            settings.setValue("document/page-labels", json.dumps(value, ensure_ascii=False))
        else:
            for key in ("a4", "ocr"):
                settings.setValue("scan/" + key, value[key])
    settings.sync()
