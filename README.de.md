# Audio Stems

[English](README.md) · [Русский](README.ru.md) · **Deutsch**

Ein Übungswerkzeug für Musiker: zerlegt eine Aufnahme in Gesang, Schlagzeug, Bass, Gitarre, Klavier und den
Rest, damit Sie zu den synchron laufenden Spuren spielen können — lokal auf Ihrem eigenen Computer. Öffnen Sie
Ihre eigene Audiodatei oder ziehen Sie sie ins Fenster; einen Song können Sie auch auf YouTube finden.

## Was es kann

- **Öffnet Ihre eigene Audiodatei** — MP3, WAV, FLAC, M4A, OGG und andere — per Knopf oder durch Ziehen ins
  Fenster. Einen Song können Sie auch auf YouTube über den Namen finden oder einen YouTube-Link einfügen.
- **Trennt ihn in Spuren.** Das schnelle Modell liefert vier (Gesang, Schlagzeug, Bass und alles Übrige) in etwa
  einer Minute pro Minute Musik; „Qualität verbessern“ trennt einen Song mit einem deutlich besseren, aber
  langsameren Modell noch einmal in sechs Spuren: Gesang, Schlagzeug, Bass, Gitarre, Klavier und alles Übrige.
- **Spielt alle Spuren synchron**, jede mit Lautstärkeregler, Solo und Mute, und mit einer Wellenform, in die
  Sie zum Springen klicken.
- **Zum Üben**:
  - ein Metronom auf den Schlägen des Songs selbst;
  - ein Einzähler über vier Schläge vor dem Start;
  - Tempo von 25 % bis 150 % ohne Änderung der Tonhöhe;
  - die Stille am Anfang wird übersprungen.
- **Bewahrt Ihre Projekte auf**: Songs und Spuren bleiben auf der Festplatte; die Einstellungen zeigen, wie viel
  Platz sie belegen, und haben Knöpfe, um ihn freizugeben.

Alles läuft auf Ihrem Computer; Ihre Musik verlässt ihn nicht. Die Oberfläche ist auf Russisch.

## Voraussetzungen

- **Windows** 10 oder 11 (64 Bit) oder **macOS** auf Apple Silicon (M1 oder neuer) mit Chrome oder Safari 16.4+.
  Macs mit Intel-Prozessor werden noch nicht unterstützt. Smartphones und Tablets werden nicht unterstützt.
- Etwa 3 GB freier Speicher für das Programm und seine Modelle, dazu etwa 20 MB pro Minute Musik.
- Internet für den ersten Start und zum Herunterladen von Songs.

## Herunterladen und starten

1. Klicken Sie auf dieser Seite auf **Code → Download ZIP** und entpacken Sie das Archiv an einen beliebigen Ort,
   zum Beispiel in Ihren Benutzerordner.
2. Starten:
   - **Windows**: Doppelklick auf `Start.bat`.
   - **macOS**: Doppelklick auf `Start.command`. Beim ersten Mal meldet macOS *„Apple konnte nicht überprüfen,
     ob "Start.command" frei von Schadsoftware ist“*. Das heißt nicht, dass Schadsoftware gefunden wurde: Die App
     ist nicht mit einem kostenpflichtigen Apple-Entwicklerzertifikat signiert, und macOS meldet das bei jeder
     unsignierten Datei aus dem Internet. Einmal erlauben: **Fertig** klicken, **Systemeinstellungen →
     Datenschutz & Sicherheit** öffnen, unten bei *„"Start.command" wurde blockiert“* auf **Trotzdem öffnen**
     klicken und mit dem Passwort bestätigen. (Vor macOS 15: Rechtsklick auf `Start.command` → **Öffnen** →
     **Öffnen**.) Danach genügt ein Doppelklick.
3. Die App öffnet sich im Browser unter `http://127.0.0.1:8000`. Ein Konsolenfenster zeigt, was sie tut;
   schließen Sie dieses Fenster, um die App zu beenden.

**Der erste Start dauert 5–15 Minuten**: Bei Bedarf wird Python installiert (ohne Administratorpasswort) und die
Komponenten werden heruntergeladen (etwa 1,5 GB). Jedes Trennungsmodell lädt seine Gewichte bei der ersten
Verwendung (das schnelle etwa 160 MB, das beste etwa 650 MB, die Schlagerkennung etwa 80 MB). Spätere Starts
dauern wenige Sekunden.

## Aktualisieren

**Einstellungen → Обновления → Проверить** zeigt, was spätere Versionen Neues bringen. **Обновить до …** lädt die
neueste herunter (wenn gerade nichts getrennt wird); schließen Sie das Audio-Stems-Fenster und starten Sie die App
erneut – das Update wird vor dem Öffnen installiert. Ihre Songs, Spuren und Einstellungen bleiben, wie sie sind.

## Wo Ihre Dateien liegen

Songs, Spuren, die Projektdatenbank und Protokolle liegen im Ordner `Audio Stems` in Ihrem Benutzerordner,
getrennt vom Programm. Die Spuren sind gewöhnliche FLAC-Dateien: `Audio Stems/Music/Stems/<Song>/vocals.flac`
usw., Sie können sie also in jedem anderen Programm verwenden.

## Womit sich die App verbindet

Alles läuft auf Ihrem Computer; die App sendet keine Daten über Sie oder Ihre Musik und hat keine Analyse.
Verbindungen gibt es nur zu:

- **YouTube** (youtube.com und seine Medienserver): wenn Sie einen Song suchen oder einen Link einfügen;
- **PyPI** (pypi.org) und **python.org** oder **GitHub** (uv, unter macOS): beim ersten Start, um Python und die
  Bibliotheken zu installieren;
- **Hugging Face** (huggingface.co), **GitHub** (die Releases, die audio-separator nutzt) und
  **cloud.cp.jku.at**: einmal, um ein Trennmodell oder das Beat-Modell bei der ersten Nutzung herunterzuladen;
- **GitHub** (api.github.com, codeload.github.com): um nach einer neuen Version von Audio Stems zu suchen und sie
  herunterzuladen, wenn Sie sie installieren möchten.

## Bitte beachten

Verwenden Sie die App nur für Musik, die Sie herunterladen und bearbeiten dürfen. Sie umgeht keine
Zugriffsbeschränkungen.

Audio Stems ist ein kostenloses Hobbyprojekt und wird ohne jede Gewährleistung bereitgestellt, so wie es ist
(siehe [LICENSE](LICENSE)). Für das, was Sie herunterladen, trennen und weitergeben, sind Sie selbst verantwortlich.

Wie die beiden Modelle ausgewählt wurden (Hörtests, Methodik, Gesamtbericht, auf Russisch): [separate-music-instruments-research](https://github.com/sergeypolevich/separate-music-instruments-research).

Der Code ist frei unter der [MIT-Lizenz](LICENSE). Die Trennmodelle, Python-Pakete und ffmpeg, die die App
herunterlädt, haben eigene Lizenzen, aufgeführt in
[THIRD_PARTY.md](THIRD_PARTY.md).

---

Für Entwickler: manuelle Einrichtung, Tests, API und Worker, Sicherungen und Wartung stehen in
[app/README.md](app/README.md) (auf Englisch).
