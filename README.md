# PDF Werkbank 1.40.0 für Windows

PDFs lokal bearbeiten, drucken, vergleichen und durchsuchen. PDF Werkbank
speichert und druckt ohne Demo-Wasserzeichen.

## Starten

1. Windows-ZIP vollständig entpacken und `PDF-Werkbank-1.40.0.exe` starten.
2. Beim ersten Start den Download der Laufzeitpakete bestätigen. Der
   Starter lädt rund 140 MB aus den in `DEPENDENCIES.tsv` dokumentierten
   Originalquellen und prüft jede Datei mit SHA-256. Die Einrichtung
   zeigt den Fortschritt pro Paket. Nach einem Abbruch verwendet sie
   vollständig heruntergeladene und geprüfte Pakete erneut.
3. PDFs in der App öffnen oder als Dateipfad an die EXE übergeben.

Voraussetzungen sind ein 64-Bit-Windows-PC (x86-64), .NET Framework 4.8
und eine Internetverbindung bei der ersten Einrichtung. Die Dateien liegen
standardmäßig unter `%LOCALAPPDATA%\PDF Werkbank\GitHub\1.40.0`.
Eine eigene Python- oder Qt-Installation ist nicht nötig.

Für **Verbessern → KI-Textvorschläge** lädt die App nach eigener Bestätigung
ein Modell von rund 1,1 GB. Danach laufen die Textvorschläge lokal.
Das Modell liegt unter `%LOCALAPPDATA%\PDF Werkbank\KI`. Eine bereits
vorhandene passende Modelldatei wird verwendet.

## Funktionen

Für einen Rechner ohne Internet gibt es ein separat zusammengestelltes
Offline-Paket. Den gesamten entpackten Ordner mit `Pakete` auf den
USB-Stick kopieren und **PDF-Werkbank-Offline-Setup.exe** starten. Der
Starter prüft die Originalpakete anhand der in der EXE hinterlegten
SHA-256-Werte. Nach der Einrichtung steht **PDF Werkbank Offline** im
Windows-Startmenü. Die automatische Updatesuche ist bei diesem Start
aus. Optionale Downloads wie das KI-Modell bleiben separat.

**Start → Offene Arbeiten** bündelt registrierte Formularläufe mit
offenen Zeilen, Entwürfe im Scan-Posteingang und fällige Wiedervorlagen
im überwachten Rechnungsordner. Ein Doppelklick öffnet den Eintrag.
Die Übersicht erscheint beim normalen Programmstart. Das lässt sich
direkt in der Übersicht abschalten. Rechnungen erscheinen, sobald
der Ordner eingelesen ist.

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

## Arbeitsstand sichern

**Datei → Arbeitsstand sichern** speichert Einstellungen, lokale Messlisten
und den Verlauf der Rechnungsfreigaben und Notizen. Beim Import können
die Gruppen ausgewählt und Änderungen vorab geprüft werden. Einen neuen
Archivordner kann die Werkbank per Prüfsumme zuordnen. Die PDFs und Rechnungsdateien selbst sind
separat zu übertragen. Freigaben verweisen weiterhin auf ihren Ablagepfad.

Beim manuellen Speichern lässt sich das Backup mit einem Passwort schützen.
Die Werkbank fragt es beim Öffnen ab. Ein vergessenes Passwort lässt sich
nicht zurücksetzen. Ältere Sicherungen und die lokalen Sicherungen vor
Updates lassen sich weiterhin ohne Passwort öffnen.
**Rücksicherung testen** prüft die Lesbarkeit und listet die enthaltenen
Gruppen, Messlisten und Belegnotizen auf. Die Werkbank prüft außerdem
gespeicherte lokale Archivpfade auf fehlende Dateien. Der Test übernimmt
keine Einstellungen und schreibt keine Messlisten.

Die Gruppe **Ordner und Formularläufe** sichert die Pfade für
Rechnungsordner, Rechnungsarchiv, Scan-Posteingang und Scan-Archiv sowie
die Laufdateien offener Formularläufe. In der Vorschau weist **Ordner
und Formularläufe neu zuordnen** einem alten Ordner seinen neuen Ort zu.
Excel- und PDF-Eingaben, fertige PDFs und Scan-Entwürfe müssen separat
mitgenommen werden. Eine vorhandene passende Laufdatei behält ihren
Fortschritt.

Unter **Datei → Umzugsassistent** kannst du einen Formularlauf und
ausgewählte Scan-Entwürfe samt PDF- und Excel-Dateien in ein Projektpaket
mit bis zu 64 MiB Inhalt packen. Optional schützt ein Passwort das Paket.
Beim Import entsteht ein neuer Projektordner. Scan-Kopien erscheinen im
Scan-Posteingang und der importierte Lauf unter **Offene Arbeiten**.

## Pläne messen

Unter **Messen** gilt der Maßstab für die aktuelle Seite. **Maßstab
eingeben** setzt ein Verhältnis wie 1 : 100. Mit **Kalibrieren** klickst
du die Enden einer bekannten Strecke an und gibst ihre tatsächliche
Länge in Metern ein. Das ist auch bei eingescannten Plänen möglich.

**Strecke messen** verwendet zwei Klicks. Für **Fläche messen** klickst
du die Eckpunkte am Rand entlang an und bestätigst mit Eingabe oder
**Fläche abschließen**. Esc verwirft einen begonnenen Umriss. **Letzte
Messung entfernen** entfernt die letzte Messmarke der Seite.
Die Messwerte erscheinen in m und m². In **Messliste** lassen sich die
Messungen benennen und zusammen mit ihren Maßstäben lokal speichern.
Die Werkbank lädt die Liste beim Öffnen derselben PDF-Fassung wieder.
**PDF mit Maßen** zeigt vor dem Speichern eine Vorschau einer separaten
PDF-Kopie mit sichtbaren Maßlinien, Flächen und Beschriftungen.

**Messoptionen** bietet Fangpunkte für Vektorlinien und eine Lupe für
Bildscans. In **Messliste → Export und Projekte** lassen sich Messwerte
als Excel ausgeben. Namen, Räume und Gewerke können direkt in der Tabelle
bearbeitet oder mehreren Einträgen gemeinsam zugewiesen werden.
Das Excel-Blatt Gruppensummen trennt Flächen, Strecken, Stückzahlen,
Außenumfänge und Innenkanten je Raum und Gewerk.
Eine .messprojekt-Datei enthält Namen, Messungen und Maßstäbe und lässt
sich auf einem anderen PC zur bytegleichen PDF-Fassung öffnen.
Mit **Pläne überlagern** richtest du zwei Seiten über je zwei
entsprechende Punkte aus und blendest zwischen ihnen um. Die farbige
Änderungsansicht zeigt entfernte Bereiche rot und neue grün.
**Aufmaß** zieht Aussparungen von der zuletzt gemessenen Fläche ab
und zählt Symbole je Kategorie. Außenumfang und Innenkanten stehen in
der Messliste. Excel enthält auch die Stückzahlen.

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
  Mit **Pausieren** kann der Lauf unterbrochen werden. **Archivlauf
  fortsetzen** nutzt den lokal gespeicherten Stand und überspringt
  fertige Kopien. Geänderte Entwürfe müssen erneut freigegeben werden.

## Archiv, Rechnungen und Explorer

- **Archiv & Zugang → Archiv-Check für Ordner** prüft gespeicherte PDFs
  mit optionalen Unterordnern. Bei deklarierter PDF/A-Version prüft
  veraPDF das zugehörige Profil. Die Tabelle zeigt auch Seiten ohne
  extrahierbaren Text, einschließlich Leerseiten. Die sichtbaren
  Ergebnisse lassen sich als Excel-Bericht speichern. Für ausgewählte
  Dateien kann die Werkbank Suchtext ergänzen und geprüfte PDF/A-2b-Kopien
  in einem Zielordner speichern.
- **Bilder & Scan → Rechnungseingang** nimmt Scan-PDFs, ZUGFeRD-PDFs mit
  CII-Anlage und XRechnung-Dateien in UBL oder CII auf. Die gemeinsame
  Tabelle zeigt Nummer, Datum, Zahlbetrag und Quelle. Bei Scans werden
  die ersten drei und die letzte Seite lokal ausgewertet. Bei XML
  werden die Daten ausgelesen. Mit **E-Rechnung der Auswahl prüfen**
  läuft eine separate lokale KoSIT-Prüfung für XRechnung 3.0 (UBL/CII)
  und die im Regelpaket enthaltenen EN-16931-Szenarien. Der erste
  Prüflauf lädt den KoSIT-Prüfer und die Regeln mit SHA-256-Prüfung.
  Java 11 oder neuer wird benötigt. Bei PDF-Anlagen wird die XML geprüft.
  Der PDF-Aufbau und der Abgleich mit der Sichtfassung sind separat.
- **Datei → Explorer-Befehle einrichten** legt für den aktuellen Windows-
  Benutzer zwei Befehle unter **Rechtsklick → Senden an** an. Unter Windows
  11 ist das Menü über **Weitere Optionen anzeigen** erreichbar.
  Die Stapelvorschau zeigt Seitenzahlen, Ziele und Konflikte. Beim
  Zusammenführen lässt sich die Reihenfolge per Ziehen oder mit den
  Schaltflächen ändern. Gespeicherte Abläufe zeigen die geplanten Schritte.

- **Bilder & Scan → Rechnungsordner** überwacht den gewählten Ordner,
  solange PDF Werkbank läuft. Ein Zähler meldet neue Belege auch bei
  geschlossenem Rechnungsfenster. Die Liste zeigt Prüfergebnisse und
  mögliche Dubletten im Eingang. Die Archivfreigabe schreibt eine
  unveränderte Kopie in einen anderen Ordner; vorhandene Ziele bleiben erhalten.
  Mehrere Belege lassen sich mit Strg oder Umschalt auswählen und prüfen.
  In der Sammelfreigabe sind Fehler und mögliche Dubletten zunächst
  abgewählt. Bereits archivierte Belege und belegte Ziele sind gesperrt.
  **Wiedervorlage** hinterlegt einen Termin und eine Notiz zu einem Beleg.
  Ein Filter zeigt heute fällige und überfällige Einträge. Die Hinweise
  erscheinen im Rechnungsordner und im Zähler der geöffneten Werkbank.
- **Archiv & Zugang → Dubletten prüfen** vergleicht PDF- und XML-Dateien
  im gewählten Ordner. Dateiidentische Kopien und mögliche Doppelbelege
  mit übereinstimmenden Rechnungsdaten stehen getrennt in einer Vorschau.
  Auch die bereits eingelesenen Dateien im Rechnungseingang lassen sich
  vergleichen. Die Prüfung verwendet die ausgelesenen Originalwerte.
- **Formular → Pflichtfelder prüfen** zeigt leere, als Pflichtfeld
  gekennzeichnete Felder an und springt zum ausgewählten Feld. Die
  feste Formularkopie setzt voraus, dass diese Felder ausgefüllt sind.
- **Formular → Formulare nach Excel** liest die gespeicherten Feldwerte
  aus mehreren PDFs. Die Vorschau unterscheidet leere und fehlende Felder.
  Excel erhält eine Zeile pro PDF und eine Spalte pro Feldname.
  Passwort- und Signaturfelder werden ausgelassen.
- **Formular → Excel-Formularvorlagen** speichert die Reihenfolge der Spalten,
  alternative PDF-Feldnamen und Pflichtspalten. Stimmen mehrere ausgefüllte
  Felder für dieselbe Spalte überein, wird der Wert einmal exportiert.
  Bei widersprüchlichen Werten oder fehlenden Pflichtangaben bleibt die Zelle
  leer. Vorschau und Excel-Spalte **Hinweis** nennen den Grund. Die Vorlagen
  werden auch über **Datei → Arbeitsstand sichern** gesichert.
  Doppelklick auf eine zugeordnete Zelle springt zur PDF-Seite und markiert
  die Felder. **Nächste Feldseite** führt zu weiteren Fundstellen.
  **Zur Formularvorschau** bringt dich zurück. Nach dem Speichern einer
  Korrektur aktualisiert **PDFs neu einlesen** die Vorschau.
- **Formular → Formulare aus Excel füllen** verwendet eine gespeicherte
  Feldzuordnung in umgekehrter Richtung. Die erste belegte Zeile im
  XLSX-Tabellenblatt enthält die Spaltennamen der Vorlage. Eine
  optionale Spalte **Dateiname** benennt die Kopien. Die Vorschau meldet
  fehlende Pflichtwerte, Formeln, ungültige Feldwerte und Namenskonflikte.
  Jede ausgewählte, geprüfte Zeile wird als eigene PDF-Kopie gespeichert.
  Im Zielordner liegt eine Laufdatei mit der Endung `.werkbanklauf`.
  Über **Formular → Formularlauf fortsetzen** öffnest du sie nach einem
  Abbruch oder Neustart. Speichere Korrekturen in derselben Excel-Datei
  und lasse bereits verarbeitete Zeilen an ihrer Position. Der
  **Excel-Bericht** enthält Zeilennummer, Dateiname und Ergebnis.
- **Formular → Excel-Eingabevorlage erstellen** legt die Spalten einer
  gespeicherten Zuordnung in der richtigen Reihenfolge an. Orange
  Überschriften markieren Pflichtangaben. Die Vorlage enthält
  **Dateiname**, Textformat für führende Nullen und Auswahllisten für
  gemeinsame feste PDF-Auswahlwerte und Kontrollkästchen.

## Favoriten

Über **Favoriten** oberhalb der Registerkarten lassen sich häufige
Funktionen anheften oder ausblenden. Scannen, Samsung → Posteingang,
Ordnersuche und Samsung-Duplex sind voreingestellt. Eigene Auswahlen
bleiben beim Neustart erhalten.
- Unter **Favoriten → Gespeicherte Abläufe** lassen sich auch eigene
  Stapelvorlagen anheften. Die Funktion fragt nach den Eingabedateien
  und dem Zielordner und führt die gespeicherten Schritte aus.

- **Strg+K** öffnet die Befehlssuche. Mit Pfeiltasten und Eingabe lässt
  sich eine Funktion aus den Registerkarten starten.
- **Samsung → Posteingang** liest beim Start eine Seite über Windows WIA
  ein. Danach können weitere Seiten aufgenommen und mit den gespeicherten
  A4- und OCR-Einstellungen im Posteingang abgelegt werden.
- **Strg+G** springt zu einer Seitennummer. Bei großen PDFs zeichnet
  die Werkbank zuerst die aktuelle Seite. Sichtbare Miniaturen werden
  danach geladen. Zuletzt gezeigte Seiten bleiben kurz im Arbeitsspeicher.

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
- **Datei → Broschürendruck** ordnet ausgewählte Seiten auf A4 im
  Querformat an. Die Faltvorschau zeigt Vorder- und Rückseite, den Falz
  und erforderliche Leerseiten. Drucken erfordert Duplex an der kurzen
  Kante. Die angeordneten Seiten lassen sich auch als Druck-PDF speichern.

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
  Bei aktivierter OCR-Fehlertoleranz erscheinen typische Verwechslungen
  in Nummern, etwa 4718 und 47I8, als mögliche Treffer hinter exakten
  Treffern. Der Textausschnitt markiert das gefundene Wort. Beim Öffnen
  springt die Werkbank zur Fundstelle und hebt sie auf der Seite hervor.
  Die Funktion arbeitet mit und ohne Suchindex.
- **Formular** erstellt und füllt Textfelder, Kontrollkästchen und Dropdowns.
  „Felder vorschlagen“ sucht nach Kästchen und Eingabelinien in PDF-Seiten
  und in klaren Scans. Vorschläge lassen sich einzeln prüfen und auswählen.
  **Feste Formularkopie** zeigt die ausgefüllten Felder in einer Vorschau
  und speichert ihre sichtbaren Werte als festen Seiteninhalt.

**Datei → Einstellungen übertragen** exportiert Favoriten, gespeicherte
Abläufe, Kopf- und Fußzeilen sowie A4- und OCR-Optionen als JSON-Paket.
Beim Import werden nur die ausgewählten Gruppen ersetzt. PDFs, der
Suchindex, Arbeitsmappen und gespeicherte Ordnerpfade sind davon getrennt.
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
- **Start → Änderungsverlauf** zeigt die letzten Bearbeitungen.
  **Strg+Z** nimmt den letzten Schritt zurück, **Strg+Y** stellt ihn
  wieder her. Eine Seitenverschiebung betrifft beide geöffneten PDFs.
- **Datei → Arbeitsmappe speichern** speichert bewusst einen benannten
  Vorgang mit PDF-Pfaden und zuletzt angezeigten Seiten. Geänderte PDFs
  werden vorher als Kopie gespeichert. **Arbeitsmappe öffnen** ergänzt
  die benötigten Tabs und meldet verschobene oder fehlende Dateien.
- **Export & Schutz → Gespeicherte Abläufe** speichert kombinierte
  Vorlagen. Die Schritte laufen in der Reihenfolge Texterkennung, Drehen,
  Verkleinern. Zielordner wird pro Lauf gewählt. Vorhandene Ausgabedateien
  werden nicht überschrieben.

## Archiv und Barrierefreiheit

- **OCR-Korrektur** im Bearbeiten-Menü und im Scan-Posteingang stellt
  Scanbild und erkannte Zeilen nebeneinander. Sie unterstützt Scan-Seiten
  mit unsichtbarer Textebene und lässt das Seitenbild unverändert.
  Für die Korrektur stehen deutsche und westeuropäische Zeichen zur Verfügung.

- **PDF/A-Archivkopie** erstellt eine PDF/A-2b-Kopie des Seitenbilds
  mit durchsuchbarer Textebene. Seiten ohne erkennbaren Text lassen sich
  zuvor per OCR aufbereiten. veraPDF prüft die Kopie vor dem Speichern.
  Die Vorschau nennt Seiten ohne Suchtext. Den Ablauf gibt es auch direkt
  im Scanassistenten. Ausfüllbare Felder, Anhänge und Signaturen verbleiben
  in der ursprünglichen Datei.
- **PDF/A mit Originaltext** erhält vorhandene Textobjekte und Vektorgrafik.
  Das Werkzeug ergänzt Archivmetadaten und bei Bedarf das Windows-sRGB-
  Profil. Es eignet sich für PDFs, die damit die PDF/A-2b-Prüfung bestehen,
  beispielsweise mit bereits eingebetteten Schriften. Bei einer Ablehnung
  bleibt die Zieldatei unverändert. Die Seitenbild-Archivkopie bleibt
  separat verfügbar.
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

## Info

Das Register **Info** direkt hinter **Formular** zeigt die laufende Version,
Neuerungen, Lizenz und den Zugang zur Update-Prüfung.

## Updates

Ab 1.26.0 prüft der neue Starter die Projektsignatur der Freigabe.
Unter **Datei → Update-Info** steht der Status für die gestartete EXE.
Auch Updates aus einem lokalen Ordner brauchen die passende Signaturdatei.
Vor dem Wechsel auf eine neuere Version sichert die Werkbank den
gespeicherten Arbeitsstand unter `%LOCALAPPDATA%\PDF Werkbank\Sicherungen`.
Scheitert die Sicherung, wird der Versionswechsel zurückgestellt.

Die Titelleiste zeigt die tatsächlich laufende Version an.

PDF Werkbank sucht beim Start auf GitHub nach stabilen Releases. Ein
passendes Windows-Paket wird automatisch heruntergeladen und anhand der
bei GitHub hinterlegten SHA-256-Prüfsumme geprüft. Die App richtet die
neue Version im Hintergrund ein. Beim nächsten Start öffnet sich die
vorbereitete Version. Offene Dokumente bleiben währenddessen geöffnet.
Über **Datei → Online-Update suchen** kann die Prüfung sofort starten.
Kann eine aktualisierte Version ihr Fenster beim Start nicht öffnen,
versucht der Starter eine frühere, lokal vorbereitete und geprüfte
Version zu öffnen. Die fehlerhafte Version wird anhand ihrer Prüfsumme
markiert und beim nächsten Start übersprungen, solange ein Rückweg
verfügbar ist. Das gilt für Fehler vor dem Startsignal des Fensters.

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
