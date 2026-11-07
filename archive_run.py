# SPDX-License-Identifier: AGPL-3.0-only
import json
import re
from pathlib import Path

from filing import file_signature
from recovery import atomic_write


class ArchiveRun:
    def __init__(self, inbox):
        self.inbox = inbox.resolve()
        self.path = self.inbox / ".archivlauf.json"
        self.jobs = []

    def begin(self, jobs):
        if self.path.exists():
            raise ValueError("Es gibt bereits einen gespeicherten Archivlauf.")
        self.jobs = [{"draft": str(draft.resolve()), "target": str(target.resolve()),
                      "source": list(file_signature(draft)), "sha256": None, "done": False}
                     for draft, target in jobs]
        self.write()

    def load(self):
        with self.path.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Der gespeicherte Archivlauf ist zu groß.")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("jobs"), list):
            raise ValueError("Der gespeicherte Archivlauf konnte nicht gelesen werden.")
        jobs = data["jobs"]
        for job in jobs:
            if (not isinstance(job, dict) or not all(isinstance(job.get(key), str) for key in ("draft", "target"))
                    or not isinstance(job.get("source"), list) or len(job["source"]) != 3
                    or type(job.get("done")) is not bool
                    or (job.get("sha256") is not None and (not isinstance(job["sha256"], str)
                        or not re.fullmatch(r"[a-f0-9]{64}", job["sha256"])))):
                raise ValueError("Ein Eintrag des Archivlaufs ist ungültig.")
            draft, target = self.paths(job)
            if (draft.parent.resolve() != self.inbox or draft.suffix.lower() != ".pdf"
                    or not target.is_absolute() or target.suffix.lower() != ".pdf"
                    or target.resolve().is_relative_to(self.inbox)):
                raise ValueError("Ein Dateipfad im Archivlauf ist ungültig.")
        self.jobs = jobs

    @staticmethod
    def paths(job):
        return Path(job["draft"]), Path(job["target"])

    def write(self):
        atomic_write(self.path, json.dumps({"version": 1, "jobs": self.jobs}, ensure_ascii=False).encode("utf-8"))

    def matches_source(self, job):
        draft, _ = self.paths(job)
        return list(file_signature(draft)) == job["source"]
