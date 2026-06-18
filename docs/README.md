# Talos

Talos is a local Windows tool server for Codex.

It does not try to replace Codex or run a separate AI model. Codex remains the reasoning layer in VS Code or another Codex surface. Talos provides local Arduino workspace access, sandbox verification, and a small HTTP API that Codex can call while you work.

## Current Scope

- Native Windows desktop shell via pywebview.
- Local HTTP API on `127.0.0.1`.
- Native C window/title discovery for open Arduino sketches.
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
talos/server.py         Local HTTP tool server
talos/client.py         CLI bridge for Codex and terminal use
talos/core.py           Thin Python bridge config and path utilities
talos/arduino.py        Arduino workspace and sandbox runner
talos/native_bridge.py  ctypes bridge to the native library
native/talos_native.c   Native Windows app-discovery logic
ui/web_frontend/        Desktop UI assets
config/config.json      Runtime configuration
config/requirements.txt Build/runtime Python dependencies
scripts/build_app.ps1   Build one-file Windows executable
scripts/install_app.ps1 Install built app to LocalAppData and create desktop shortcut
scripts/launch_desktop.ps1 Open source app or installed app
tests/                  Regression tests
docs/                   README and license
```

Runtime files such as `.talos_sandbox/` and `config/run_history.json` are ignored by Git.

## Codex Bridge CLI

Codex can call Talos from a VS Code terminal through `talos.client`.

```powershell
python -B -m talos.client state
python -B -m talos.client projects
python -B -m talos.client workspace "C:\Users\You\Documents\Arduino\Blink" --fqbn arduino:avr:uno
python -B -m talos.client context
python -B -m talos.client read Blink.ino
python -B -m talos.client write Blink.ino --from-file edited\Blink.ino
python -B -m talos.client verify
```

## Run From Source

```powershell
python -B desktop_app.py
```

Or:

```powershell
.\scripts\launch_desktop.ps1
```

To run only the HTTP server:

```powershell
python -B -m talos.server --port 8787
```

## Build And Install

Install dependencies:

```powershell
python -m pip install -r config\requirements.txt
```

Build the native C helper when a C compiler is available:

```powershell
.\scripts\build_native.ps1
```

Run the normal project verification flow:

```powershell
.\scripts\check.ps1
```

This rebuilds the native DLL, checks that the current Python bridge can load the expected native exports, runs the regression tests, and prints the pipeline status.

Run the manual Arduino MVP smoke test before treating Arduino support as ready:

```text
docs\ARDUINO_SMOKE_TEST.md
```

Build:

```powershell
.\scripts\build_app.ps1
```

Install locally:

```powershell
.\scripts\install_app.ps1
```

## Arduino Requirements

For sandbox verify, install `arduino-cli` and make sure it is available in `PATH`.

Talos does not compile in your real sketch folder. It copies the sketch into `.talos_sandbox/arduino/...` and compiles the copy.
