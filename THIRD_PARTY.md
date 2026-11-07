# Abhängigkeiten und Lizenzen

PDF Werkbank und der Windows-Starter stehen unter AGPL-3.0-only.
Der Starter enthält unseren Python-Quellcode, die Lizenz, diese Hinweise
und die Liste der Downloads. Er enthält keine Drittanbieter-Binärdateien.

Die Einrichtung lädt die unveränderten Originalpakete direkt bei den
jeweiligen Anbietern. Die genauen Versionen, URLs und SHA-256-Prüfsummen
stehen in DEPENDENCIES.tsv. Die Programme der Anbieter bleiben unter
ihren jeweiligen Lizenzen.

| Baustein | Lizenz und Quelle |
|---|---|
| CPython 3.14.10 | PSF-Lizenz und Drittanbieterhinweise im offiziellen Paket. [Release](https://www.python.org/downloads/release/python-31410/) · [Quellcode](https://github.com/python/cpython/tree/v3.14.10) |
| PySide6 Essentials und Shiboken 6.12.0 | Open-Source-Option LGPL v3. [Lizenzen](https://doc.qt.io/qtforpython-6/licenses.html) · [Qt-for-Python-Quellcode](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.12.0) · [Qt-Quellpakete](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/) |
| PyMuPDF 1.26.7 / MuPDF | AGPL v3 für diese Nutzung. [PyMuPDF-Quellpaket](https://pypi.org/project/PyMuPDF/1.26.7/#files) · [MuPDF-Quellcode](https://mupdf.com/releases/) · [Lizenz](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| Tesseract-Sprachdaten | Apache-2.0. [Quellrepository und Lizenz am festgelegten Stand](https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd) |
| llama.cpp b12189 und dessen Laufzeit | MIT und Drittanbieterhinweise. [Release](https://github.com/ggml-org/llama.cpp/releases/tag/b12189) · [Quellcode und Hinweise](https://github.com/ggml-org/llama.cpp/tree/b12189) |
| Qwen2.5-1.5B-Instruct-GGUF | Apache-2.0. Die App lädt das Modell erst nach Bestätigung separat. [Modell und Lizenz](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/tree/91cad51170dc346986eccefdc2dd33a9da36ead9) |

Die Originalpakete können weitere Komponenten wie Microsoft Visual C++,
OpenSSL oder LLVM OpenMP enthalten. Deren Weitergabe als Bestandteil
eines eigenen Offline-Bundles wurde für dieses Release nicht freigegeben.

