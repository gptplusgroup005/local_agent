# Talos Pipeline

## Final Goal

Talos is a local tool server for Codex. Its job is to let Codex work with IDEs and apps outside VSCode. The first supported target is Arduino IDE: detect open sketches, resolve the real sketch folder, read and edit files, detect board settings, verify code in a sandbox, and support a repeatable debug loop.

Short version:

```text
Talos = local bridge between Codex and external IDEs/apps, starting with Arduino IDE.
```

## Current Position

```text
Current active stage: Stage 2 - Verify output cleanup
Next major stage: Stage 3 - Arduino file workflow
```

Stage 1 is usable and will keep receiving hardening fixes. Stage 2 is partly complete: ANSI cleanup, memory parsing, library parsing, and platform parsing are implemented. The remaining Stage 2 work is to surface compile issues by file and line in the UI.

## Progress Rules

- Every pipeline-related task must update this file before the task is considered done.
- When a checklist item is completed, change `[ ]` to `[x]`.
- When a new requirement appears, add it under the closest matching stage.
- Use `scripts/pipeline_status.ps1` to check stage progress from this file.

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pipeline_status.ps1
```

## Stage 0 - Pipeline Management

- [x] Keep this pipeline note aligned with current project status.
- [x] Provide a command-line pipeline progress checker.
- [x] Require pipeline updates when a pipeline-related task is completed.

## Stage 1 - Arduino Detection Stability

- [x] Detect Arduino IDE process.
- [x] Detect multiple open `.ino` sketches.
- [x] Resolve the real sketch folder when the sketch is saved.
- [x] Ignore stale process paths after an old sketch is closed.
- [x] Do not treat `.arduinoIDE-unsaved...` folders as valid workspaces.
- [x] Detect board/FQBN from Arduino language server processes.
- [x] Verify sketches through `arduino-cli`.
- [x] Use bundled Arduino IDE `arduino-cli` when it is not in PATH.
- [x] Build and load native C DLL at `native/bin/talos_native.dll`.
- [x] Reduce UI refresh latency while the Arduino tab is active.
- [ ] Make unsaved sketch status clearer in the Arduino UI.
- [ ] Improve exact sketch-to-board mapping when many Arduino IDE windows are open.

## Stage 2 - Verify Output Cleanup

- [x] Strip ANSI escape codes from Arduino CLI output.
- [x] Parse program memory usage.
- [x] Parse dynamic memory usage.
- [x] Parse used libraries.
- [x] Parse used platform.
- [x] Parse basic compile errors and warnings into `issues`.
- [x] Show memory/library/platform summary in the Verify output UI.
- [ ] Show compile issues by file and line in the Verify output UI.
- [ ] Add a copy button for issue-only debug context.

## Stage 3 - Arduino File Workflow

- [x] Expose scoped backend APIs to read, write, and delete workspace files.
- [x] Show workspace file list in the Arduino UI.
- [ ] Add a file viewer/editor for `.ino`, `.cpp`, `.h`, and related source files.
- [ ] Save edited files back into the selected sketch folder.
- [ ] Keep all file operations scoped inside the sketch workspace.
- [ ] Add UI feedback for dirty/unsaved edited files.

## Stage 4 - Codex Debug Loop

- [x] Verify selected workspace in a sandbox copy.
- [x] Copy verify output for pasting into Codex.
- [ ] Feed parsed compile issues into a compact debug context.
- [ ] Let Codex patch relevant files through Talos APIs.
- [ ] Re-run verify after a Codex patch.
- [ ] Keep a short history of verify attempts and patches.

## Stage 5 - Native C Expansion

- [x] Native C extracts `.ino` names from Arduino IDE titles.
- [x] Native C lists top-level window titles.
- [ ] Move more Windows process/window detection into native C.
- [ ] Reduce dependence on PowerShell/CIM for hot-path detection.
- [ ] Add native build/check command to normal verification flow.

## Stage 6 - MATLAB Later

- [ ] Detect MATLAB process.
- [ ] Detect MATLAB current folder/script.
- [ ] Read and edit MATLAB files through scoped APIs.
- [ ] Run MATLAB scripts or commands in a controlled runtime.

## Development Principles

- Arduino first, MATLAB later.
- Unsaved/temp sketches are signals only, not valid workspaces.
- Verify must run against a sandbox copy, not the original sketch folder.
- UI must make output/context easy to copy into Codex.
- Native C should own speed-sensitive Windows/system helpers.
- Python should remain the bridge/API layer.
