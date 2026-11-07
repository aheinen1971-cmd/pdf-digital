# GitHub-Veröffentlichung

1. Ein öffentliches Repository erstellen. Das Quellcode-ZIP entpacken und
   die enthaltenen Dateien einschließlich LICENSE, THIRD_PARTY.md und
   DEPENDENCIES.tsv ins Hauptverzeichnis übernehmen.
2. Diesen Quellcode als Version v1.30.0 markieren.
3. Eine GitHub Release zu v1.30.0 anlegen. Das Windows-ZIP als Release-Datei
   hinzufügen. Es enthält die dazugehörige EXE und denselben Quellcode.
4. Für direkte Weitergabe dasselbe Windows-ZIP verwenden. Die Lizenz und
   der zu genau dieser EXE gehörende Quellcode bleiben so beieinander.

Im Release-Text auf den einmaligen Download der Original-Laufzeitpakete
hinweisen. Ein Offline-Paket mit vorinstallierten Fremdbibliotheken ist
eine andere Vertriebsform und wurde hier nicht geprüft. Den alten
1.2.0-Ein-Datei-Build mit eingebetteten Bibliotheken für diese
Veröffentlichung nicht verwenden.


## Signierte Online-Freigabe

Nach dem Paketbau erzeugt Signieren.ps1 eine separate Datei
PDF-Werkbank-1.30.0-Signatur.json. Der benötigte private Projektschlüssel
liegt DPAPI-geschützt außerhalb des Repositories. Nur die Signaturdatei
wird zusammen mit dem Windows-ZIP veröffentlicht. EXE und ZIP danach
unverändert lassen. Ein eigener Herausgeber muss seinen eigenen Schlüssel
verwenden und den öffentlichen Schlüssel im Windows-Starter und im
Python-Prüfer hinterlegen. Die Projektsignatur dient dem Werkbank-Updater.