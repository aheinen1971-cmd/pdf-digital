# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from filing import file_signature, pdf_files
from intake import DuplicateSource, inspect_duplicate, invoice_xml
from invoice_validation import InvoiceValidation, validate_invoice_xml
from update_worker import digest_file


class IntakeSettler:
    def __init__(self, folder, settle_seconds=2.5):
        self.folder = Path(folder)
        self.settle_seconds = settle_seconds
        self.observed = {}

    def poll(self, now):
        previous = self.observed
        self.observed = {}
        for path in pdf_files(self.folder, False, (".pdf", ".xml")):
            try:
                signature = file_signature(path)
            except OSError:
                continue
            old, since = previous.get(path, (None, now))
            self.observed[path] = signature, since if old == signature else now
        return {path: signature for path, (signature, since) in self.observed.items()
                if signature[1] and now - since >= self.settle_seconds}


def open_idle_source(path):
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    # Permit concurrent readers only, so a copier with an open write handle delays ingestion.
    handle = create(os.path.abspath(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        close(handle)
        raise
    return os.fdopen(fd, "rb")


@dataclass(frozen=True)
class WatchedInvoice:
    source: DuplicateSource
    signature: tuple
    validation: InvoiceValidation | None = None
    check_problem: str = ""
    embedded: bool = False


def read_watched_invoice(path, signature, tessdata, tools=None):
    with TemporaryDirectory(prefix="werkbank-eingang-") as directory:
        snapshot = Path(directory) / path.name
        with open_idle_source(path) as source, snapshot.open("wb") as target:
            if file_signature(path) != signature:
                return None
            shutil.copyfileobj(source, target)
            if file_signature(path) != signature:
                return None
        result = inspect_duplicate(snapshot, tessdata, {})
        record = DuplicateSource(path, result.digest, result.fields, result.origin, result.problem)
        validation, issue, embedded = None, "", False
        try:
            data, embedded = invoice_xml(snapshot)
            if data is not None:
                if tools:
                    validation = validate_invoice_xml(data, *tools)
                else:
                    issue = "E-Rechnung noch nicht geprüft"
        except Exception as exc:
            issue = str(exc)
        return WatchedInvoice(record, signature, validation, issue, embedded)


def archive_watched_invoice(record, incoming, destination):
    path = record.source.path
    destination = destination.resolve(strict=True)
    if not destination.is_dir() or destination.is_relative_to(incoming.resolve()):
        raise ValueError("Bitte einen Archivordner außerhalb des Rechnungsordners auswählen.")
    target = destination / path.name
    with TemporaryDirectory(dir=destination) as directory:
        temporary = Path(directory) / path.name
        with open_idle_source(path) as source, temporary.open("wb") as output:
            if file_signature(path) != record.signature:
                raise ValueError("Die Rechnung wurde geändert. Bitte erneut prüfen.")
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
            if file_signature(path) != record.signature or digest_file(temporary) != record.source.digest:
                raise ValueError("Die Rechnung wurde geändert. Bitte erneut prüfen.")
        # Windows rename fails if the target already exists, including concurrent creation.
        if os.name == "nt":
            temporary.rename(target)
        else:
            os.link(temporary, target)
    return target


def archive_setting_key(path):
    return "invoice-watch/archived/" + hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
