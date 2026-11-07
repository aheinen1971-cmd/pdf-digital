# PDF Werkbank 1.12.0 für Windows

PDFs lokal bearbeiten, drucken, vergleichen und durchsuchen. PDF Werkbank
speichert und druckt ohne Demo-Wasserzeichen.

## Starten

1. Windows-ZIP vollständig entpacken und `PDF-Werkbank-1.12.0.exe` starten.
2. Beim ersten Start den Download der Laufzeitpakete bestätigen. Der
   Starter lädt rund 140 MB aus den in `DEPENDENCIES.tsv` dokumentierten
   Originalquellen und prüft jede Datei mit SHA-256.
3. PDFs in der App öffnen oder als Dateipfad an die EXE übergeben.

Voraussetzungen sind ein 64-Bit-Windows-PC (x86-64), .NET Framework 4.8
und eine Internetverbindung bei der ersten Einrichtung. Die Dateien liegen
standardmäßig unter `%LOCALAPPDATA%\PDF Werkbank\GitHub\1.12.0`.
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
- Im Scanassistenten liest **Blatteinzug** bis zur gewählten Obergrenze
  oder bis zum leeren Einzug ein. Das Gerät muss einen über WIA
  steuerbaren Einzug haben. **TWAIN** verwendet alternativ die separat
  erhältliche [NAPS2-Konsole](https://www.naps2.com/doc/command-line)
  und einen TWAIN-Treiber. Dort lassen sich Einzug oder Glas auswählen.
  Liefert TWAIN keine Seite, bietet der Assistent einen erneuten Versuch
  mit derselben Papierquelle über Windows WIA an.
- **Scan-Posteingang** bewahrt Scan-Entwürfe lokal auf. Erkannter Text
  liefert einen Vorschlag für Dateiname und Unterordner. Das Datum stammt
  aus dem Text oder vom Eingang des Scans. Nach erfolgreicher PDF/A-Prüfung
  und bestätigter Vorschau wird das Archiv gespeichert und der Entwurf
  aus dem Posteingang entfernt.
- **Stapel aufteilen** schlägt Dokumentanfänge anhand von Seitenzahlen,
  Überschriften und Leerseiten vor. In der Vorschau lassen sich die
  Trennstellen korrigieren. Die Werkbank erstellt einzelne Entwürfe zur
  Archivierung und behält alle Seiten in ihrer ursprünglichen Reihenfolge.
- **Mehrere archivieren** zeigt Entwürfe mit Miniatur, Seitenzahl und
  bearbeitbarem Ziel in einer Tabelle. Ein Klick auf **Auswahl archivieren**
  startet OCR und PDF/A-Prüfung für die markierten Entwürfe. Ein Entwurf
  wird erst nach dem Speichern seiner geprüften Kopie entfernt.

## Favoriten

Über **Favoriten** oberhalb der Registerkarten lassen sich häufige
Funktionen anheften oder ausblenden. Scannen, Ordnersuche und
Samsung-Duplex sind voreingestellt. Die Auswahl bleibt beim Neustart
erhalten.

## Versand, Schwärzungsprüfung und Druckprofile

- **Werkzeuge → Versandkopie** vereint Seitenauswahl, OCR und Bildkomprimierung.
  Die Vorschau zeigt die tatsächliche Dateigröße und die Originalseiten,
  für die OCR keine Textebene erzeugt hat. Das Original bleibt erhalten.
- **Werkzeuge → Schwärzung prüfen** sucht in einer gespeicherten Kopie
  nach eingegebenen Begriffen und nennt die Fundorte. Erfasst werden
  auslesbarer Text, Kommentare, Formularfelder, Metadaten, Lesezeichen
  und Anlagennamen. Bildinhalte ohne Textebene und der Inhalt eingebetteter
  Dateien sind außerhalb dieser Prüfung.
- **Werkzeuge → Versandcheck** zeigt Texttreffer vor dem Schwärzen in der
  Seitenvorschau. Auf Wunsch entfernt die Funktion Dokumenteigenschaften,
  XML-Metadaten und Dateianhänge aus der Kopie. Die Abschlussprüfung zeigt
  verbleibende Texttreffer und Bildseiten zur Sichtprüfung.
- **Werkzeuge → PDF-Versionen vergleichen** markiert Textänderungen und
  sichtbare Unterschiede. „Nächste Änderung“ springt zur nächsten
  betroffenen Seite. Der Bericht lässt sich als Textdatei speichern.
- **Datei → Samsung-Profil** wählt einen unterstützten Samsung-Drucker
  mit A4 und Duplex an der langen Kante. Die Duplex-Vorschau zeigt
  Vorder- und Rückseite pro Blatt. Bei ungerader Seitenanzahl wird die
  letzte Rückseite als leer angezeigt.

## Dokumentwerkzeuge und Export

- **Bilder & Scan → Mehrseitig scannen** sammelt Seiten in einer Liste.
  Miniaturen lassen sich verschieben und entfernen. Die PDF kann beim
  Speichern auf Deutsch und Englisch durchsuchbar gemacht werden. Der
  Assistent merkt sich A4 und Texterkennung, meldet verdächtige Leer- oder
  Doppelseiten und bietet die Korrektur erkannter Schräglage pro Seite an.
- **Bearbeiten** bietet Textformatierung, Absatzkorrektur im bestehenden
  Textrahmen und frei platzierten mehrzeiligen Text mit Standardschriften.
  Für neuen Text und Absatzkorrekturen lassen sich Position und Rahmengröße
  in Millimetern angeben. **Ansicht → Hilfsraster** unterstützt das
  Ausrichten neuer Texte und verschobener Bilder. **Bild positionieren**
  stellt die Abstände zur sichtbaren Seitenkante direkt ein.
- **Seiten → Seiten zwischen PDFs** zeigt zwei PDFs nebeneinander.
  Seiten lassen sich ziehen, kopieren und verschieben. Links zu Seiten,
  die beim Transfer nicht mitkommen, können entfallen. Zum gemeinsamen
  Rückgängigmachen einer Verschiebung beide PDFs geöffnet lassen.
- **Start → Ordner durchsuchen** sucht in vorhandenen Textebenen, optional
  auch in Unterordnern. Die Trefferliste öffnet die Fundseite. Gesperrte
  oder beschädigte Dateien werden übersprungen. Der optionale lokale
  Suchindex speichert extrahierten Text und liest beim nächsten Durchlauf
  nur neue oder geänderte PDFs ein. Filter greifen auf Dateiname,
  Unterordner und Änderungsdatum. Der Index kann für den gewählten Ordner
  gelöscht werden.
- **Formular** erstellt und füllt Textfelder, Kontrollkästchen und Dropdowns.
  „Felder vorschlagen“ sucht nach Kästchen und Eingabelinien in PDF-Seiten
  und in klaren Scans. Vorschläge lassen sich einzeln prüfen und auswählen.
- **Dokument** verwaltet Lesezeichen und fügt Seitenlinks, HTTPS-Links,
  Seitenzahlen und wiederverwendbare Textstempel ein.
- **Kopf- und Fußzeilen** bietet lokale Vorlagen, Seitenauswahl,
  Platzhalter für Datum, Dateiname und Seitenzahlen sowie eine
  Seitenvorschau und einen Hinweis auf mögliche Überdeckungen.
- **Kommentar → Kommentarübersicht** filtert nach Text, Typ, Autor oder
  Seite, springt zur ausgewählten Stelle und exportiert die Treffer als
  Textdatei. **Dokument → Anlagen** listet Dokument- und Seitenanlagen.
  Dateien lassen sich als Dokumentanlage hinzufügen, speichern und
  entfernen.
- **Export & Schutz** exportiert auslesbaren Text nach Word und erkannte
  Tabellen nach Excel. Der Export rekonstruiert das Seitenlayout nicht.
  **Word mit Layout** ist eine separate Option. Sie übernimmt Schriftgrößen,
  Formatierungen, Bilder, erkannte Tabellen und Seitenwechsel in ein
  bearbeitbares DOCX. Mehrspaltige oder überlappende Inhalte können sich
  beim Umbruch verschieben.
  Mehrere PDFs lassen sich in einem Durchgang drehen, per OCR erkennen
  oder komprimieren. Verschlüsselte PDFs werden aus Export- und
  Stapelfunktionen ausgeschlossen. Normale Kopien behalten ihren Schutz.
  Eine neue geschützte PDF-Kopie verwendet AES-256.
- **Signaturen** signiert eine gespeicherte PDF-Kopie mit einem eigenen
  PKCS#12-Zertifikat und prüft bestehende digitale Dokumentensignaturen.
  Die Prüfung unterscheidet intakte Signatur und bestätigte Vertrauenskette.
  Sie fragt keine Online-Sperrlisten ab.

## Wiederherstellung und gespeicherte Abläufe

- Nach einem unerwarteten Ende bietet PDF Werkbank an, offene Tabs und
  ungespeicherte Änderungen wiederherzustellen. Änderungen werden lokal
  gespeichert. Verschlüsselte Kopien behalten ihren Passwortschutz. Beim
  Wiederöffnen wird das Passwort erneut abgefragt. Die Originaldateien
  werden dafür nicht überschrieben.
- **Export & Schutz → Gespeicherte Abläufe** speichert kombinierte
  Vorlagen. Die Schritte laufen in der Reihenfolge Texterkennung, Drehen,
  Verkleinern. Zielordner wird pro Lauf gewählt. Vorhandene Ausgabedateien
  werden nicht überschrieben.

## Archiv und Barrierefreiheit

- **PDF/A-Archivkopie** erstellt eine PDF/A-2b-Kopie des Seitenbilds
  mit durchsuchbarer Textebene. Seiten ohne erkennbaren Text lassen sich
  zuvor per OCR aufbereiten. veraPDF prüft die Kopie vor dem Speichern.
  Die Vorschau nennt Seiten ohne Suchtext. Den Ablauf gibt es auch direkt
  im Scanassistenten. Ausfüllbare Felder, Anhänge und Signaturen verbleiben
  in der ursprünglichen Datei.
- **Lesereihenfolge & Bildbeschreibungen** erstellt eine Kopie mit
  Dokumenttitel, Sprache, geordneten Absätzen, Überschriften und
  Alternativtexten. Die visuelle Ebene wird als Bild übernommen.
  Links und Formulare bleiben deshalb in der Originaldatei interaktiv.
  Bei Scans gegebenenfalls zuerst Texterkennung ausführen.
- **PDF/A oder PDF/UA prüfen** verwendet lokal den separat erhältlichen
  [veraPDF-CLI-Prüfer](https://docs.verapdf.org/cli/validation/).
  Bei der ersten Verwendung können die Dateien `java.exe` und
  `cli-*.jar` aus der Java- bzw. veraPDF-Installation ausgewählt werden.
  Lesereihenfolge, Beschreibungen und Eignung für assistive Technik müssen
  zusätzlich von Hand geprüft werden.

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
