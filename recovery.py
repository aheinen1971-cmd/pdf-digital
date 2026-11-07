# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
import tempfile
import uuid
from pathlib import Path

import pymupdf as pdf
from PySide6.QtCore import QLockFile


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Recovery:
    def __init__(self, root):
        self.root = root
        self.folder = root / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.lock = self.acquire(self.folder)
        self.last_manifest = None
        if self.lock is None:
            raise OSError("Die lokale Wiederherstellung konnte nicht eingerichtet werden.")

    @staticmethod
    def acquire(folder):
        lock = QLockFile(str(folder / "laufend.lock"))
        lock.setStaleLockTime(0)
        return lock if lock.tryLock(0) else None

    @staticmethod
    def records(folder):
        entries = json.loads((folder / "tabs.json").read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("Ungültige Wiederherstellungsdatei.")
        return entries

    def pending(self):
        for folder in self.root.iterdir():
            if folder != self.folder and folder.is_dir() and (folder / "tabs.json").is_file():
                lock = self.acquire(folder)
                if lock is not None:
                    yield folder, lock

    def save(self, sessions, current):
        records = []
        keep = set()
        for session in sessions:
            filename = session.recovery_id + ".pdf"
            path = self.folder / filename
            if session.changed:
                if session.recovery_dirty or not path.exists():
                    atomic_write(path, session.doc.tobytes(encryption=pdf.PDF_ENCRYPT_KEEP))
                    session.recovery_dirty = False
                keep.add(filename)
            records.append({"path": str(session.recovery_path), "page": session.page,
                            "snapshot": filename if session.changed else None, "active": session is current})
        if records != self.last_manifest:
            atomic_write(self.folder / "tabs.json", json.dumps(records, ensure_ascii=False).encode("utf-8"))
            self.last_manifest = records
        for path in self.folder.glob("*.pdf"):
            if path.name not in keep:
                path.unlink(missing_ok=True)

    @staticmethod
    def clear(folder, lock):
        for path in folder.iterdir():
            if path.name != "laufend.lock" and path.is_file():
                path.unlink(missing_ok=True)
        lock.unlock()
        folder.rmdir()
