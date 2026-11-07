# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from filing import file_signature
from preferences import validate_form_templates
from recovery import atomic_write
from update_worker import digest_file


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class FormRun:
    def __init__(self, path, data):
        self.path = path
        self.data = data
        self.records = data["records"]

    @classmethod
    def begin(cls, target, source, source_hash, workbook, sheet, columns):
        path = target / f"Formularlauf-{uuid.uuid4().hex[:12]}.werkbanklauf"
        run = cls(path, {"version": 1, "source": str(source), "source_hash": source_hash,
                         "workbook": str(workbook), "sheet": sheet, "columns": columns, "records": {}})
        run.write()
        return run

    @classmethod
    def load(cls, path):
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Der gespeicherte Formularlauf ist zu groß.")
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get("version") != 1 or not valid_digest(data.get("source_hash"))
                or not isinstance(data.get("sheet"), str) or not isinstance(data.get("records"), dict)
                or len(data["records"]) > 500):
            raise ValueError("Der gespeicherte Formularlauf ist ungültig.")
        for key, suffix in (("source", ".pdf"), ("workbook", ".xlsx")):
            value = data.get(key)
            if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).suffix.lower() != suffix:
                raise ValueError("Ungültiger Eingabepfad im Formularlauf.")
        validate_form_templates({"Serienlauf": data.get("columns")})
        for key, record in data["records"].items():
            if (not re.fullmatch(r"[1-9][0-9]{0,7}", key) or not isinstance(record, dict)
                    or not valid_digest(record.get("row")) or not valid_digest(record.get("pdf"))
                    or not isinstance(record.get("name"), str) or not re.fullmatch(r'[^<>:"/\\|?*\x00-\x1f]{1,180}', record["name"])
                    or not record["name"].lower().endswith(".pdf") or os.path.isreserved(record["name"])
                    or type(record.get("done")) is not bool):
                raise ValueError("Ein gespeicherter Zeilenstatus ist ungültig.")
        return cls(path, data)

    def write(self):
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def row_hash(name, values):
        data = json.dumps([name, values], ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def reconcile(self, plan):
        by_line = {str(line): (name, path, values, issues) for line, name, path, values, issues in plan}
        done, changed = {}, False
        for key, record in list(self.records.items()):
            row = by_line.get(key)
            if row is None or record["row"] != self.row_hash(row[0], row[2]):
                target = self.path.parent / record["name"]
                if record["done"] or target.exists() or target.is_symlink():
                    raise ValueError(f"Excel-Zeile {key} eines gespeicherten Ergebnisses wurde verschoben oder geändert. "
                                     "Bitte behalte fertige Zeilen bei und korrigiere nur offene Zeilen.")
                del self.records[key]
                changed = True
                continue
            path = row[1]
            verified = False
            if path is not None and path.is_file() and not path.is_symlink():
                stamp = file_signature(path)
                verified = digest_file(path) == record["pdf"] and file_signature(path) == stamp
            if verified:
                if not record["done"]:
                    record["done"] = changed = True
                done[int(key)] = "Gespeichert"
            elif record["done"]:
                done[int(key)] = "Gespeicherte PDF fehlt oder wurde verändert"
        if changed:
            self.write()
        return done

    def publish(self, line, path, values, candidate):
        key = str(line)
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        record = {"row": self.row_hash(path.name, values), "pdf": digest_file(candidate), "name": path.name, "done": False}
        self.records[key] = record
        self.write()
        try:
            if os.name == "nt":
                candidate.rename(path)
            else:
                os.link(candidate, path)
        except FileExistsError:
            del self.records[key]
            self.write()
            raise
        record["done"] = True
        self.write()
