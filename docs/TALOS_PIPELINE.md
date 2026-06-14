# Talos Pipeline

## Final Goal

Talos is a local tool server for Codex. Its job is to let Codex work with IDEs and apps outside VSCode. The first supported target is Arduino IDE: detect open sketches, resolve the real sketch folder, read and edit files, detect board settings, verify code in a sandbox, and support a repeatable debug loop.

The primary UI is an IDE workbench, not a monitoring dashboard. Source editing is the central surface, with project discovery in Explorer, tool output in a lower panel, and future Codex/Arduino/MATLAB integrations arranged around that workspace.
The global Talos navigation should remain secondary: compact by default, expandable on hover, and pinnable without taking permanent editor space.

Short version:

```text
Talos = local bridge between Codex and external IDEs/apps, starting with Arduino IDE.
```

## Current Position

```text
Current active stage: Stage 4 - Codex debug loop
Next major stage: Stage 5 - Native C expansion
```

Stages 1 through 3 are complete. Talos can detect Arduino sketches and boards, present structured verify results, safely read or edit source files, and host a real Codex app-server conversation beside the editor. The active work is Stage 4: turning compile issues, Codex patches, and repeated sandbox verification into one compact debug loop.

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
- [x] Make unsaved sketch status clearer in the Arduino UI.
- [x] Improve exact sketch-to-board mapping when many Arduino IDE windows are open.
- [x] Ignore persisted Arduino workspace state after its sketch or IDE window is closed.
- [x] Keep the selected workspace board synchronized with live Arduino IDE changes.
- [x] Map each Arduino window to its own board through the IDE process tree.
- [x] Prevent stale auto-refresh responses from reverting a newly selected sketch.

## Stage 2 - Verify Output Cleanup

- [x] Strip ANSI escape codes from Arduino CLI output.
- [x] Parse program memory usage.
- [x] Parse dynamic memory usage.
- [x] Parse used libraries.
- [x] Parse used platform.
- [x] Parse basic compile errors and warnings into `issues`.
- [x] Show memory/library/platform summary in the Verify output UI.
- [x] Show compile issues by file and line in the Verify output UI.
- [x] Preserve Verify Output scrolling when compile issue details are visible.
- [x] Let the Arduino results area fill available window height with one primary output scrollbar.
- [x] Add a copy button for issue-only debug context.

## Stage 3 - Arduino File Workflow

- [x] Expose scoped backend APIs to read, write, and delete workspace files.
- [x] Show workspace file list in the Arduino UI.
- [x] Add a file viewer/editor for `.ino`, `.cpp`, `.h`, and related source files.
- [x] Save edited files back into the selected sketch folder.
- [x] Keep all file operations scoped inside the sketch workspace.
- [x] Add UI feedback for dirty/unsaved edited files.
- [x] Detect all `.ino`, `.h`, `.hpp`, `.c`, and `.cpp` tabs in the selected Arduino sketch folder.
- [x] Keep project detection stable when Arduino IDE focuses a secondary `.h` or `.cpp` tab.
- [x] Promote the editor into a VSCode-style Explorer/Editor/Output workbench.
- [x] Add a compact hover-expand navigation rail with a persisted pin state.
- [x] Collapse the unpinned navigation rail immediately when the pointer leaves.
- [x] Polish the IDE workbench with editor line numbers and a focused Codex welcome/composer layout.

## Stage 4 - Codex Debug Loop

- [x] Verify selected workspace in a sandbox copy.
- [x] Copy verify output for pasting into Codex.
- [x] Add a collapsible Codex chat panel to the right side of the IDE workbench.
- [x] Connect the panel to the locally authenticated Codex app-server runtime.
- [x] Feed the selected workspace, active file, and parsed verify issues into compact Codex context.
- [x] Keep Codex startup and UI polling non-blocking to avoid workbench lag.
- [x] Let Codex patch relevant files through the Talos-controlled workspace bridge.
- [x] Recover cleanly from rejected or timed-out Codex turns and allow manual cancellation.
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
