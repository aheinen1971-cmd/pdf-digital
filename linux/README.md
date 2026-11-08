# PDF Digital für Linux – Testvorbereitung

Eigenständiges Programm, kein Bestandteil von Rosavio. Erstes Testziel:
Ubuntu 24.04 x86-64 mit Python 3.12; andere Distributionen und ARM sind noch
nicht qualifiziert. Kein öffentliches stabiles Release und kein Linux-Autoupdate.

Das CI-Testpaket enthält Quellcode, Lizenzen, Python-Wheels und OCR-Daten
(Deutsch/Englisch). Nach dem Entpacken `./install.sh`, dann `./start.sh` starten.
Die Einrichtung verwendet ausschließlich enthaltene Wheels in einer eigenen
virtuellen Umgebung. Keine Administratorrechte, keine Windows-EXE und keine
Änderung der PDF-Standardanwendung. Systemvoraussetzungen: python3.12-venv,
libegl1, libopengl0, libgl1, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4,
libxcb-keysyms1, libxcb-shape0 und libxcb-xinerama0.

Linux speichert Anwendungsdaten unter `$XDG_DATA_HOME/pdf-digital`, ansonsten
`~/.local/share/pdf-digital`. Qt-Einstellungen liegen im Benutzer-Konfigurationsordner.

## Umfang und Grenzen

Die bestehenden Tests prüfen PDF-Bearbeitung, Formulare, Speichern, Export,
Schwärzung, Messungen, Wiederherstellung und die Oberfläche in 20 Sprachen.
Die tatsächlichen Ergebnisse stehen im mitgelieferten Prüfbericht; ein gestarteter
Test ist kein bestandener Test. Gedruckte PDFs ersetzen keinen Hardware-Drucktest.

Windows-Explorer-Integration, Windows-Updater/USB-Updates, WIA/TWAIN-Scanner,
Windows-Zusatzpakete und die mitgelieferte Windows-KI-Laufzeit sind in dieser
Linux-Vorbereitung gesperrt. Eingescannte PDF-Dateien können importiert werden.
PDF/A-/E-Rechnungsvalidierung benötigt zusätzlich separat eingerichtetes Java und
die jeweiligen Prüfwerkzeuge. Keine pauschale Funktionsgleichheit behaupten.

SHA256SUMS prüft die Paketintegrität, ist aber keine Herausgebersignatur.
Für eine öffentliche Veröffentlichung bleiben Paketverteilung, Updateverfahren
und Tests auf weiteren Linux-Systemen getrennte Freigabeschritte.
