# PDF Werkbank 1.6.0 für Windows

PDFs lokal bearbeiten, drucken, vergleichen und durchsuchen. PDF Werkbank
speichert und druckt ohne Demo-Wasserzeichen.

## Starten

1. Windows-ZIP vollständig entpacken und `PDF-Werkbank-1.6.0.exe` starten.
2. Beim ersten Start den Download der Laufzeitpakete bestätigen. Der
   Starter lädt rund 140 MB aus den in `DEPENDENCIES.tsv` dokumentierten
   Originalquellen und prüft jede Datei mit SHA-256.
3. PDFs in der App öffnen oder als Dateipfad an die EXE übergeben.

Voraussetzungen sind ein 64-Bit-Windows-PC (x86-64), .NET Framework 4.8
und eine Internetverbindung bei der ersten Einrichtung. Die Dateien liegen
standardmäßig unter `%LOCALAPPDATA%\PDF Werkbank\GitHub\1.6.0`.
Eine eigene Python- oder Qt-Installation ist nicht nötig.

Für **Verbessern → KI-Textvorschläge** lädt die App nach eigener Bestätigung
ein Modell von rund 1,1 GB. Danach laufen die Textvorschläge lokal.
Das Modell liegt unter `%LOCALAPPDATA%\PDF Werkbank\KI`. Eine bereits
vorhandene passende Modelldatei wird verwendet.

## Funktionen

- PDFs in Tabs öffnen, durchsuchen, markieren und kommentieren.
- Seiten drehen, verschieben, anhängen oder als neue PDF extrahieren.
- Textfelder und Kontrollkästchen ausfüllen, Textabschnitte korrigieren
  und Seitenbereiche dauerhaft schwärzen.
- Scan-PDFs komprimieren, Scanbilder aufbereiten und Dokumente auf Deutsch
  und Englisch per OCR durchsuchbar machen.
- PDF-Fassungen nebeneinander vergleichen.
- Druckvorschau und Duplexdruck nach den Fähigkeiten des Druckertreibers.
- Bearbeitete PDFs als Kopie speichern. Vorhandene Wasserzeichen gehören
  zum jeweiligen Dokument.

## Bilder und Scanner

- **Bilder & Scan → Seite zuschneiden**. Einen Ausschnitt aufziehen.
  Der Zuschnitt ändert den sichtbaren Bereich. Inhalte außerhalb des
  Ausschnitts bleiben in der PDF enthalten.
- **Bild verschieben**. Ein direkt eingebettetes PDF-Bild mit gedrückter
  Maustaste verschieben. **Bild ersetzen** tauscht das ausgewählte Bild
  im vorhandenen Rahmen. Das Bild kann dabei gestreckt werden. Komplex
  verschachtelte Bilder werden abgelehnt. Andere Verwendungen desselben
  Bildes auf der Seite oder im Dokument bleiben erhalten.
- **Vom Scanner einlesen**. Liest eine Seite direkt über Windows WIA ein.
  Sind mehrere Scanner verfügbar, fragt die App nach dem Gerät.
  Pro Aufruf kann ein Scan als PDF gespeichert werden. Windows muss den
  Scanner mit einem passenden WIA-Treiber erkennen.

## Versand, Schwärzungsprüfung und Druckprofile

- **Werkzeuge → Versandkopie** vereint Seitenauswahl, OCR und Bildkomprimierung.
  Die Vorschau zeigt die tatsächliche Dateigröße und die Originalseiten,
  für die OCR keine Textebene erzeugt hat. Das Original bleibt erhalten.
- **Werkzeuge → Schwärzung prüfen** sucht in einer gespeicherten Kopie
  nach eingegebenen Begriffen und nennt die Fundorte. Erfasst werden
  auslesbarer Text, Kommentare, Formularfelder, Metadaten, Lesezeichen
  und Anlagennamen. Bildinhalte ohne Textebene und der Inhalt eingebetteter
  Dateien sind außerhalb dieser Prüfung.
- **Datei → Samsung-Profil** wählt einen unterstützten Samsung-Drucker
  mit A4 und Duplex an der langen Kante. Die Duplex-Vorschau zeigt
  Vorder- und Rückseite pro Blatt. Bei ungerader Seitenanzahl wird die
  letzte Rückseite als leer angezeigt.

## Dokumentwerkzeuge und Export

- **Bilder & Scan → Mehrseitig scannen** sammelt Seiten in einer Liste.
  Miniaturen lassen sich verschieben und entfernen. Die PDF kann beim
  Speichern auf Deutsch und Englisch durchsuchbar gemacht werden.
- **Bearbeiten** bietet Textformatierung, Absatzkorrektur im bestehenden
  Textrahmen und frei platzierten mehrzeiligen Text mit Standardschriften.
- **Formular** erstellt und füllt Textfelder, Kontrollkästchen und Dropdowns.
- **Dokument** verwaltet Lesezeichen und fügt Seitenlinks, HTTPS-Links,
  Seitenzahlen und wiederverwendbare Textstempel ein.
- **Export & Schutz** exportiert auslesbaren Text nach Word und erkannte
  Tabellen nach Excel. Der Export rekonstruiert das Seitenlayout nicht.
  Mehrere PDFs lassen sich in einem Durchgang drehen, per OCR erkennen
  oder komprimieren. Verschlüsselte PDFs werden aus Export- und
  Stapelfunktionen ausgeschlossen. Normale Kopien behalten ihren Schutz.
  Eine neue geschützte PDF-Kopie verwendet AES-256.
- **Signaturen** signiert eine gespeicherte PDF-Kopie mit einem eigenen
  PKCS#12-Zertifikat und prüft bestehende digitale Dokumentensignaturen.
  Die Prüfung unterscheidet intakte Signatur und bestätigte Vertrauenskette.
  Sie fragt keine Online-Sperrlisten ab.

## Rotes PDF-Symbol

Unter **Datei → PDF-Standard** öffnet PDF Werkbank die Windows-Einstellungen.
Wähle dort PDF Werkbank für **.pdf**. Windows verwendet dann das rote
Dateisymbol, auch für PDFs auf dem Desktop.

## Updates

Die Titelleiste zeigt die tatsächlich laufende Version an.

PDF Werkbank sucht beim Start auf GitHub nach stabilen Releases. Ein
passendes Windows-Paket wird automatisch heruntergeladen und anhand der
bei GitHub hinterlegten SHA-256-Prüfsumme geprüft. Die App richtet die
neue Version im Hintergrund ein. Beim nächsten Start öffnet sich die
vorbereitete Version. Offene Dokumente bleiben währenddessen geöffnet.
Über **Datei → Online-Update suchen** kann die Prüfung sofort starten.

## Aus dem Quellcode bauen

Öffne PowerShell in diesem Ordner und führe `./Bauen.ps1` aus.
Der Windows-.NET-Framework-Compiler baut die EXE in `dist`.
Der Build benötigt keine Drittanbieter-Binärdateien. Die EXE enthält den
C#-Starter als Programmcode und die Python-Dateien als Quelltext.

## Qt austauschen

Beende PDF Werkbank. Qt und seine Python-Bindungen liegen in
`python\Lib\site-packages\PySide6` und `python\Lib\site-packages\shiboken6`
unter dem oben genannten Laufzeitordner. Sichere die vorhandenen Ordner.
Ersetze sie durch kompatible x64-Builds und starte PDF Werkbank erneut.
Ein kompatibles Qt-DLL- oder Plugin-Update kann auch innerhalb von
`PySide6` vorgenommen werden. Der Starter prüft die Originalpakete beim
Download und verwendet beim nächsten Start die Dateien aus diesen Ordnern.

Für eine separate Laufzeit kannst du vor dem Start die Umgebungsvariable
`PDF_WERKBANK_RUNTIME_ROOT` auf einen absoluten lokalen Pfad setzen.

## Lizenz

Der Quellcode der Anwendung und des Starters steht unter AGPL-3.0-only.
Die Lizenzen und Quellcode-Verweise der Abhängigkeiten stehen in
`THIRD_PARTY.md`. Die Downloads bleiben bei ihren Originalanbietern.
