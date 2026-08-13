# FoxQuiz mit Codex unter WSL einrichten

## Empfehlung

FoxQuiz sollte vollständig innerhalb von WSL2 bearbeitet werden. Das
Repository, die Shell, Git, Python, `uv`, `agents-cli` und Codex sollten dabei
in derselben Linux-Umgebung laufen.

Eine gemischte Umgebung ist zu vermeiden: Wenn das Repository unter Linux
liegt, einzelne Werkzeuge aber als Windows-Prozesse über einen UNC-Pfad wie
`\\wsl.localhost\Ubuntu\...` zugreifen, können Dateioperationen, Patches und
Shell-Kommandos an der Windows/WSL-Grenze scheitern.

Die offizielle OpenAI-Dokumentation empfiehlt WSL2, wenn ein Projekt und seine
Entwicklungswerkzeuge bereits unter Linux liegen. Repositorys sollten für
bessere Leistung sowie weniger Symlink- und Berechtigungsprobleme unter
`/home/...` und nicht unter `/mnt/c/...` gespeichert werden:

- [Offizielle Codex-WSL-Dokumentation](https://learn.chatgpt.com/docs/windows/wsl)

## Speicherort des FoxQuiz-Repositorys

Der bestehende Speicherort ist geeignet:

```text
/home/lmuff/projects/Kaggle_Google_AIAgents/capstone_project/foxquiz
```

Das Repository sollte nicht nach `/mnt/c/...` verschoben werden. Windows kann
bei Bedarf über folgenden Explorer-Pfad lesend darauf zugreifen:

```text
\\wsl$\Ubuntu\home\lmuff\projects\Kaggle_Google_AIAgents\capstone_project\foxquiz
```

Dieser UNC-Pfad sollte jedoch nicht die aktive Arbeitsumgebung für
Windows-native Entwicklungswerkzeuge sein.

## Codex direkt in WSL verwenden

Ubuntu beziehungsweise WSL öffnen und Codex innerhalb der Linux-Umgebung
installieren und starten:

```bash
cd /home/lmuff/projects/Kaggle_Google_AIAgents/capstone_project/foxquiz

curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex
```

Damit verwenden Codex, Git, Patch-Werkzeuge und Projektkommandos dieselben
Linux-Pfade und Linux-Berechtigungen.

## Alternative: VS Code Remote für WSL

Voraussetzungen:

- WSL2 mit Ubuntu
- Visual Studio Code
- VS-Code-Erweiterung **WSL**

Das Projekt aus einer WSL-Shell öffnen:

```bash
cd /home/lmuff/projects/Kaggle_Google_AIAgents/capstone_project/foxquiz
code .
```

Anschließend prüfen:

- Unten links in VS Code steht `WSL: Ubuntu`.
- Integrierte Terminals zeigen Linux-Pfade wie `/home/...`.
- `echo $WSL_DISTRO_NAME` gibt den Namen der Distribution aus.
- Codex und alle benötigten Erweiterungen laufen im WSL-Remote-Fenster.

Falls das Projekt versehentlich als Windows-Ordner geöffnet wurde, in der
Befehlspalette **WSL: Reopen Folder in WSL** auswählen.

## Projektwerkzeuge in WSL installieren

Alle für FoxQuiz benötigten Werkzeuge müssen innerhalb von WSL verfügbar sein.
Eine reine Windows-Installation ist für Linux-Kommandos nicht ausreichend.

Beispiele:

```bash
uv --version
python --version
git --version
agents-cli info
```

Projektabhängigkeiten werden im Repository installiert:

```bash
uv sync --locked
```

Falls die Google Agents CLI fehlt:

```bash
uv tool install google-agents-cli
```

## Empfohlener Arbeitsablauf

1. Ubuntu beziehungsweise eine WSL-Remote-Umgebung starten.
2. Mit einem Linux-Pfad in das Repository wechseln.
3. Codex, Tests, Linter und Anwendung aus derselben WSL-Sitzung starten.
4. Für alle Projektkommandos Bash und Linux-Pfade verwenden.
5. Windows-Anwendungen nur für ergänzende Aufgaben wie Browser-Tests nutzen.

Beispiele:

```bash
cd /home/lmuff/projects/Kaggle_Google_AIAgents/capstone_project/foxquiz

git status
uv run pytest tests/unit tests/integration
agents-cli lint
uv run uvicorn app.fast_api_app:app --reload --host 127.0.0.1 --port 8000
```

## Zu vermeidende Mischkonfigurationen

- Das Linux-Repository als Windows-Workspace über
  `\\wsl.localhost\Ubuntu\...` bearbeiten.
- Windows-PowerShell und Linux-Bash abwechselnd für dieselben Dateioperationen
  verwenden.
- Windows-native Patch- oder Build-Werkzeuge aus WSL heraus starten.
- Linux-Werkzeuge ausschließlich unter Windows installieren.
- Das Repository unter `/mnt/c/...` ablegen, obwohl die Entwicklung
  überwiegend unter Linux erfolgt.
- Windows- und Linux-Pfade innerhalb desselben Kommandos mischen.

## Diagnose

Prüfen, ob die aktuelle Shell wirklich unter WSL läuft:

```bash
echo "$WSL_DISTRO_NAME"
pwd
uname -a
```

Erwartet werden eine WSL-Distribution, ein Pfad unter `/home/...` und ein
Linux-Kernel.

Prüfen, ob das Projekt korrekt erreichbar ist:

```bash
cd /home/lmuff/projects/Kaggle_Google_AIAgents/capstone_project/foxquiz
git status
```

Wenn stattdessen Pfade wie `C:\...` oder `\\wsl.localhost\...` als aktueller
Arbeitsordner erscheinen, läuft das betreffende Werkzeug wahrscheinlich noch
auf der Windows-Seite.

## WSL aktualisieren und neu starten

Bei allgemeinen WSL-Zugriffsproblemen in einer administrativen PowerShell
ausführen:

```powershell
wsl --update
wsl --shutdown
```

Danach Ubuntu, die WSL-Remote-Umgebung und Codex neu starten.

## Kurzfassung

Für FoxQuiz gilt: Das Repository bleibt unter `/home/...`; Codex und alle
Entwicklungswerkzeuge werden innerhalb von WSL2 gestartet. Dadurch entfällt
die fehleranfällige Übersetzung zwischen Windows-UNC-Pfaden und dem
Linux-Dateisystem weitgehend.
