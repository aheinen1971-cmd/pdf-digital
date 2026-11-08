"""Build a Linux test bundle from this checkout, without publishing a release."""
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
out = root / "dist/PDF-Digital-Linux-Test"
out.mkdir(parents=True, exist_ok=False)
app = out / "app"
app.mkdir()
for path in root.glob("*.py"):
    shutil.copy2(path, app / path.name)
for name in ["ui_languages.json", "offline_tools_catalog.json", "pdf-werkbank.ico",
             "LICENSE", "THIRD_PARTY.md", "DEPENDENCIES.tsv"]:
    shutil.copy2(root / name, app / name)
shutil.copytree(root / "LICENSES", app / "LICENSES")
shutil.copytree(root / "tests", app / "tests", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(root / "linux", app / "linux", ignore=shutil.ignore_patterns("__pycache__"))
for name in ["install.sh", "start.sh", "README.md"]:
    shutil.copy2(root / "linux" / name, out / name)
    if name.endswith(".sh"):
        (out / name).chmod(0o755)
wheels = out / "wheels"
subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                "--dest", str(wheels), "-r", str(root / "linux/requirements.txt")], check=True)
# Freeze every resolved dependency in the self-contained offline package.
requirements = []
for wheel in sorted(wheels.glob("*.whl")):
    name, version, *_ = wheel.name.split("-")
    requirements.append(f"{name}=={version}")
(out / "requirements.txt").write_text("\n".join(requirements) + "\n")
tess = app / "tessdata"
tess.mkdir()
for line in (root / "DEPENDENCIES.tsv").read_text().splitlines():
    fields = line.split("\t")
    if len(fields) == 5 and fields[2].endswith(".traineddata"):
        with urlopen(fields[4], timeout=60) as response:
            data = response.read(32 * 1024 * 1024)
        assert hashlib.sha256(data).hexdigest() == fields[3]
        (tess / fields[2]).write_bytes(data)
manifest = []
for path in sorted(out.rglob("*")):
    if path.is_file():
        manifest.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(out).as_posix())
(out / "SHA256SUMS").write_text("\n".join(manifest) + "\n")
archive = out.with_suffix(".tar.gz")
with tarfile.open(archive, "w:gz") as tar:
    tar.add(out, arcname=out.name)
print(json.dumps({"archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))
