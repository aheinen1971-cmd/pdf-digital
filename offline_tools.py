# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from invoice_validation import KOSIT_FOLDER
from local_ai import MODEL_NAME, MODEL_SHA256, MODEL_SIZE


TOOLS = {"veraPDF": "PDF/A und PDF/UA prüfen", "KoSIT": "E-Rechnungen prüfen", "KI": "Lokale KI"}
DIRECTORIES = {"Java": "Java", "veraPDF": "veraPDF", "KoSIT": KOSIT_FOLDER}
PACKAGE_NAME = "PDF-Werkbank-Zusatzpakete"


def catalog():
    entries = json.loads(Path(__file__).with_name("offline_tools_catalog.json").read_text(encoding="utf-8"))
    entries["KI"] = {MODEL_NAME: [MODEL_SIZE, MODEL_SHA256]}
    return entries


def tool_sources(base, model):
    return {key: base / name for key, name in DIRECTORIES.items()} | {"KI": model.parent}


def required_tools(selected):
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(key, str) or key not in TOOLS for key in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("Die Auswahl im Offline-Paket ist ungültig.")
    return (["Java"] if any(key != "KI" for key in selected) else []) + selected


def check_file(path, expected, hash_file=False):
    try:
        size, digest = expected
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            return False
        if hash_file:
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest() == digest
        return True
    except OSError:
        return False


def tool_status(base, model):
    entries = catalog()
    sources = tool_sources(base, model)
    installed = {key: all(check_file(sources[key] / name, expected) for name, expected in files.items())
                 for key, files in entries.items()}
    return {key: installed[key] and (key == "KI" or installed["Java"]) for key in TOOLS}


def package_size(selected):
    entries = catalog()
    return sum(size for key in required_tools(selected) for size, _ in entries[key].values())


def copy_verified(source, target, expected):
    if not check_file(source, expected):
        raise ValueError(f"Datei fehlt oder hat eine falsche Größe: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if not check_file(target, expected, hash_file=True):
        raise ValueError(f"Die Prüfsumme stimmt nicht: {source.name}")


def export_tools(target, selected, base, model, progress=lambda *_: None):
    entries = catalog()
    sources = tool_sources(base, model)
    modules = required_tools(selected)
    if target.exists() or target.is_symlink():
        raise FileExistsError("Ein Zusatzpaket mit diesem Namen ist schon vorhanden.")
    if shutil.disk_usage(target.parent).free < package_size(selected):
        raise OSError("Auf dem Ziellaufwerk ist zu wenig Speicherplatz.")
    with TemporaryDirectory(dir=target.parent) as temp:
        stage = Path(temp) / PACKAGE_NAME
        for key in modules:
            for name, expected in entries[key].items():
                progress(key, name)
                copy_verified(sources[key] / name, stage / key / name, expected)
        (stage / "Paket.json").write_text(json.dumps({"version": 1, "selected": selected}), encoding="utf-8")
        stage.rename(target)


def read_selection(source):
    with (source / "Paket.json").open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Die Paketliste ist zu groß.")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Das Offline-Zusatzpaket wird nicht unterstützt.")
    selected = data.get("selected")
    required_tools(selected)
    return selected


def install_tools(source, base, model, progress=lambda *_: None):
    selected = read_selection(source)
    entries = catalog()
    targets = tool_sources(base, model)
    modules = required_tools(selected)
    staged, installed = {}, []
    local = base.parent
    local.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=local) as temp:
        stage = Path(temp)
        for key in modules:
            progress(key, "")
            target = model if key == "KI" else targets[key]
            if target.exists() or target.is_symlink():
                if not all(check_file(targets[key] / name, expected, hash_file=True)
                           for name, expected in entries[key].items()):
                    raise ValueError(f"{key} ist bereits in einer anderen oder unvollständigen Fassung vorhanden.")
                for name, expected in entries[key].items():
                    progress(key, name)
                    if not check_file(source / key / name, expected, hash_file=True):
                        raise ValueError(f"Die Prüfsumme stimmt nicht: {name}")
                continue
            for name, expected in entries[key].items():
                progress(key, name)
                copy_verified(source / key / name, stage / key / name, expected)
            staged[key] = target
        try:
            for key, target in staged.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                candidate = stage / key / MODEL_NAME if key == "KI" else stage / key
                if key == "KI" and os.name != "nt":
                    os.link(candidate, target)
                else:
                    candidate.rename(target)
                installed.append(target)
        except OSError as exc:
            done = ", ".join(path.name for path in installed) or "keine"
            raise OSError(f"Das Kopieren wurde unterbrochen. Bereits übernommen: {done}. {exc}") from exc
    return selected
