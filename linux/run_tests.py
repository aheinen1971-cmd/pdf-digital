"""Run every applicable existing suite, with a fresh HOME per process."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
reports = Path(sys.argv[1]).resolve()
reports.mkdir(parents=True, exist_ok=True)
skipped = {"test_builtin_ai.py": "Requires the separate 1.1 GB model and Windows llama runtime; unavailable in Linux preview."}
results = []
for test in sorted((root / "tests").glob("test_*.py")):
    if test.name in skipped:
        results.append({"test": test.name, "status": "not-tested", "reason": skipped[test.name]})
        continue
    with tempfile.TemporaryDirectory(prefix="pdf-digital-test-") as folder:
        env = dict(os.environ, HOME=folder, XDG_CONFIG_HOME=folder + "/config",
                   XDG_DATA_HOME=folder + "/data", XDG_CACHE_HOME=folder + "/cache",
                   LOCALAPPDATA=folder + "/windows-compat-tests", PDF_WERKBANK_OFFLINE="1",
                   PYTHONPATH=str(root), TESSDATA_PREFIX=str(root / "tessdata"))
        start = time.monotonic()
        with (reports / (test.stem + ".log")).open("w") as log:
            child = subprocess.Popen([sys.executable, "-X", "utf8", str(test)], cwd=root,
                                     env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = child.wait(timeout=100)
                status = "passed" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                status, code = "timeout", None
        results.append({"test": test.name, "status": status, "exitCode": code,
                        "seconds": round(time.monotonic() - start, 1)})
        print(json.dumps(results[-1]), flush=True)
        (reports / "results.json").write_text(json.dumps(results, indent=2))
        for image in (root / "tests").glob("*.png"):
            import shutil
            shutil.copy2(image, reports / image.name)
(reports / "results.json").write_text(json.dumps(results, indent=2))
sys.exit(any(row["status"] in ("failed", "timeout") for row in results))
