# Talos

Talos is a local Windows tool server for Codex.

It does not try to replace Codex or run a separate AI model. Codex remains the reasoning layer in VS Code or another Codex surface. Talos provides local Arduino workspace access, sandbox verification, and a small HTTP API that Codex can call while you work.

## Current Scope

- Native Windows desktop shell via pywebview.
- Local HTTP API on `127.0.0.1`.
- Arduino sketch folder registration.
- Arduino source file discovery for `.ino`, `.h`, `.hpp`, `.c`, `.cpp`, `.S`, `.txt`, and `.md`.
- Safe read/write/delete endpoints constrained to the configured sketch folder.
- Sandbox compile using `arduino-cli compile --fqbn ...`.
- Desktop UI for configuring the Arduino sketch folder and running sandbox verify.

## Tool API

Default source run URL:

```text
http://127.0.0.1:8787
```

Important endpoints:

```text
GET  /api/health
GET  /api/state
GET  /api/arduino_context
GET  /api/arduino_projects
GET  /api/arduino_file?path=Blink.ino
POST /api/arduino_workspace
POST /api/arduino_file
POST /api/arduino_delete
POST /api/arduino_verify
```

Example write request:

```json
{
  "path": "Blink.ino",
  "content": "void setup() {}\nvoid loop() {}\n"
}
```

Example verify request:

```json
{
  "path": "C:\\Users\\You\\Documents\\Arduino\\Blink",
  "fqbn": "arduino:avr:uno"
}
```

## Project Files

```text
desktop_app.py          Desktop pywebview shell
web_app.py              Local HTTP tool server
talos_client.py         CLI bridge for Codex and terminal use
talos_core.py           Shared config, paths, utility code, and legacy local actions
talos_arduino.py        Arduino workspace and sandbox runner
web_frontend/           Desktop UI assets
config.json             Runtime configuration
build_app.ps1           Build one-file Windows executable
install_app.ps1         Install built app to LocalAppData and create desktop shortcut
launch_desktop.ps1      Open source app or installed app
requirements.txt        Build/runtime Python dependencies
```

Runtime files such as `.talos_sandbox/`, `tasks.json`, and `memory.json` are ignored by Git.

## Codex Bridge CLI

Codex can call Talos from a VS Code terminal through `talos_client.py`.

```powershell
python -B talos_client.py state
python -B talos_client.py projects
python -B talos_client.py workspace "C:\Users\You\Documents\Arduino\Blink" --fqbn arduino:avr:uno
python -B talos_client.py context
python -B talos_client.py read Blink.ino
python -B talos_client.py write Blink.ino --from-file edited\Blink.ino
python -B talos_client.py verify
```

## Run From Source

```powershell
python -B desktop_app.py
```

Or:

```powershell
.\launch_desktop.ps1
```

To run only the HTTP server:

```powershell
python -B web_app.py --port 8787
```

## Build And Install

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Build:

```powershell
.\build_app.ps1
```

Install locally:

```powershell
.\install_app.ps1
```

## Arduino Requirements

For sandbox verify, install `arduino-cli` and make sure it is available in `PATH`.

Talos does not compile in your real sketch folder. It copies the sketch into `.talos_sandbox/arduino/...` and compiles the copy.
