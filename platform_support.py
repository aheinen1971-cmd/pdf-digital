# SPDX-License-Identifier: AGPL-3.0-only
"""Platform boundaries for the Linux preview; never run Windows installers there."""
import os
import sys
from pathlib import Path
from functools import wraps

IS_LINUX = sys.platform.startswith("linux")
LINUX_NOTICE = "Linux-Testversion: Diese Windows-Zusatzfunktion ist noch nicht verfügbar."


def data_root():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF Werkbank"
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if not base.is_absolute():
        base = Path.home() / ".local/share"
    return base / "pdf-digital"


def windows_feature(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if IS_LINUX:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "PDF Digital · Linux", LINUX_NOTICE)
            return None
        return method(self, *args, **kwargs)
    guarded.windows_only = True
    return guarded
