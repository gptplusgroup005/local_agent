# Talos Pipeline

## Final Goal

Talos is a local AI control layer for Codex. Its job is to let Codex work safely with IDEs and apps outside VSCode without replacing those tools. The first supported target is Arduino IDE: detect open sketches, resolve the real sketch folder, read and edit files, detect board settings, stage Codex changes, verify code in a sandbox, and support a repeatable debug loop.

The primary UI is an IDE workbench, not a monitoring dashboard. Change Workspace is the central surface for inspecting files, reviewing Codex changes, and deliberately saving them; project discovery remains in Explorer and tool output in a lower panel.
The global Talos navigation should remain secondary: compact by default, expandable on hover, and pinnable without taking permanent editor space.

Short version:

```text
Talos = local AI control layer between Codex and external IDEs/apps, starting with Arduino IDE.
```

## Current Position

```text
Current active stage: Stage 9 - Final optimization and release readiness
Next major stage: Stage 10 - Commercial app packaging
```

Stages 0 through 8 are complete. Talos can detect Arduino sketches and boards, present structured verify results, safely read or edit source files, host a real Codex app-server conversation beside the editor, stage Codex changes outside the original sketch, use native C for speed-sensitive Windows detection, protect real sketches with conflicts, checkpoints, rollback, and staged sandbox verification, and document the repeatable Arduino MVP workflow.

Stage 6 established Change Workspace as a review-and-control surface rather than a competing Arduino editor. Stage 7 completed the safe change lifecycle, and Stage 8 documented the repeatable Arduino MVP evidence. Active work is Stage 9: optimize and validate the completed Arduino foundation for the 0.1.0 Beta release. Commercial packaging begins in Stage 10 only after every Stage 9 release gate is complete. MATLAB and other app integrations remain paused.

## Delivery Order

1. Stages 0-5 are completed foundations and remain as the technical history of the Arduino MVP.
2. Stage 6 establishes user ownership and review controls in Change Workspace.
3. Stage 7 makes the resulting change flow safe against external edits and recoverable on failure.
4. Stage 8 proves the current Arduino workflow through context, profiles, and repeatable MVP evidence.
5. Stage 9 optimizes and validates the completed Stages 0-8 foundation for the 0.1.0 Beta release.
6. Stage 10 may begin only after every Stage 9 item is checked. It packages the workflow proven by Stages 8 and 9 and does not introduce new Arduino behavior.

## Arduino MVP Exit Criteria

Talos can be considered Arduino-ready when these are true:

- [x] Multiple open Arduino IDE windows are detected as separate sketch candidates.
- [x] `.ino`, `.h`, `.hpp`, `.c`, and `.cpp` files in the selected sketch folder are visible and editable.
- [x] Board/FQBN is detected and stays synchronized with the selected sketch when practical.
- [x] Verify runs against a sandbox copy and returns structured output.
- [x] Codex can receive workspace context, edit files through Talos, and trigger verify again.
- [x] Updated native DLL is built and loaded with current process/window exports.
- [x] Native DLL build/check is part of normal verification so native regressions are caught early.
- [x] Hot-path Arduino process/window detection avoids PowerShell/CIM when the native DLL is available.
- [x] One manual end-to-end Arduino smoke test is documented: detect sketch, edit file, verify, ask Codex, apply patch, verify again.

## Progress Rules

- Every pipeline-related task must update this file before the task is considered done.
- When a checklist item is completed, change `[ ]` to `[x]`.
- When a new requirement appears, add it under the closest matching stage.
- Do not begin a later stage until the prior stage exit condition is met, except for short research or design work that does not alter the product.
- Each safety-critical item must include focused automated coverage or a documented manual smoke-test step.
- Use `scripts/pipeline_status.ps1` to check stage progress from this file.

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pipeline_status.ps1
```

## Stage 0 - Pipeline Management

- [x] Keep this pipeline note aligned with current project status.
- [x] Provide a command-line pipeline progress checker.
- [x] Require pipeline updates when a pipeline-related task is completed.
- [x] Keep disposable sandbox, staging, and test-cache artifacts out of version control and provide a safe cleanup command.

Exit condition: the pipeline has a runnable status command, every completed delivery is recorded here, and disposable runtime artifacts can be safely removed without touching source or user configuration.

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

Exit condition: Talos reliably lists only live, saved Arduino sketches, maps each selected sketch to its current board when available, and retains a responsive polling fallback when native detection is unavailable.

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
- [x] Show verify timing breakdown and skip common build/cache folders during sandbox copy.
- [x] Keep Verify Output and run history mutually exclusive so old passed cards do not leak into the current verify result.

Exit condition: a sandbox compile produces clean, copyable structured diagnostics, memory/library/platform information, timing data, and only the current result in Verify Output.

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

Exit condition: every Talos file operation is constrained to the selected sketch, supported source files are discoverable and usable, and the workbench remains navigable without replacing Arduino IDE as the source owner.

## Stage 4 - Codex Debug Loop

- [x] Verify selected workspace in a sandbox copy.
- [x] Copy verify output for pasting into Codex.
- [x] Add a collapsible Codex chat panel to the right side of the IDE workbench.
- [x] Connect the panel to the locally authenticated Codex app-server runtime.
- [x] Feed the selected workspace, active file, and parsed verify issues into compact Codex context.
- [x] Keep Codex startup and UI polling non-blocking to avoid workbench lag.
- [x] Let Codex patch relevant files through the Talos-controlled workspace bridge.
- [x] Recover cleanly from rejected or timed-out Codex turns and allow manual cancellation.
- [x] Load and resume real Codex app-server threads shared with the VSCode Codex history.
- [x] Present real Codex thread history as a VSCode-style Tasks view with recent and full lists.
- [x] Keep the Codex Tasks view visible for empty threads and pin the composer to the panel bottom.
- [x] Add responsive, draggable Explorer and Codex panes with persisted IDE proportions.
- [x] Make Codex Tasks the default panel view and replace the History toggle with conversation back navigation.
- [x] Stretch the IDE container chain from the live body width without stale `vw` sizing after maximize or restore.
- [x] Bound Explorer, Editor, Output, and Codex as explicit workbench boxes with clipped overflow.
- [x] Rebuild the Codex panel as a flexible workbench column without pixel-based pane persistence.
- [x] Remove the duplicate native build script, no-op worker loop, legacy local chat cache, and obsolete workspace CSS architecture.
- [x] Keep verify separate from Codex staging so a patch never changes the Arduino sketch before Save File.
- [x] Keep a short local history of verify attempts and Codex patches.
- [x] Stream Codex patch previews into the active Talos editor before final disk sync and sandbox verify.
- [x] Keep the active Talos editor open across Codex patch refreshes and transient workspace snapshots.
- [x] Stage Codex file changes outside the Arduino sketch, show a color diff, then apply into the Talos editor or reject explicitly; only Save File updates Arduino IDE.
- [x] Replace the persistent virtual-patch UI with an internal staging workspace and focused Codex Change Review.

Exit condition: a locally authenticated Codex thread can receive bounded Arduino context, stage focused changes through Talos, and recover from cancellation or timeout without blocking the workbench or writing directly to the real sketch.

## Stage 5 - Native C Expansion

- [x] Native C extracts `.ino` names from Arduino IDE titles.
- [x] Native C lists top-level window titles.
- [x] Move window title/PID row detection into native C with Python fallback.
- [x] Move Arduino process presence and parent PID snapshot into native C with Python fallback.
- [x] Build and load the updated DLL with the new native process/window exports.
- [x] Add native build/check command to normal verification flow.
- [x] Reduce dependence on PowerShell/CIM for hot-path detection when native exports are available.
- [x] Document the Arduino end-to-end smoke test.

Exit condition: the native DLL builds and exposes the current process/window APIs, detection uses those APIs on the hot path, and Python retains a tested fallback when native loading is unavailable.

## Stage 6 - Change Workspace Ownership

Purpose: make Talos the deliberate review/control layer while Arduino IDE remains the primary coding and hardware environment.

- [x] Keep Codex edits inside a Talos staging workspace rather than the Arduino sketch folder.
- [x] Present each staged Codex file change in a colored Change Review against the Talos editor.
- [x] Make Apply To Editor update only the Talos editor and require Save File before Arduino IDE receives changes.
- [x] Remove persistent per-file virtual-patch slots and the Patch On/Off switch; staging remains an internal safety boundary.
- [x] Track the simple change lifecycle: `staged`, `reviewing`, `applied-to-editor`, `saved`, `rejected`, and reserved `conflict`.
- [x] Define the Talos source surface as a Change Workspace for inspection, diff review, and explicit save control rather than a replacement Arduino editor.
- [x] Make the Change Workspace read/review-first by default, with an explicit local-edit action and clear Arduino IDE ownership messaging.
- [x] Rebalance the IDE workbench so Explorer, Change Workspace, Output, and Codex remain spacious and responsive across desktop widths.
- [x] Keep Board details compact and make the full FQBN/options popover visible within Explorer.
- [x] Support hunk-level review: apply or reject selected diff hunks without accepting an entire file.
- [x] Add Apply Turn and Reject Turn for a Codex turn while preserving per-file, per-hunk status, and editor drafts.

Exit condition: a user can understand which tool owns the real sketch, review or edit a proposed Codex change intentionally, and send it to Arduino IDE only through Save File.

## Stage 7 - Change Safety And Staged Verification

Purpose: ensure Talos never loses external work or silently overwrites an Arduino sketch while Codex changes are pending.

- [x] Detect when Arduino IDE or another editor changes a source file while Talos has a staged Codex change.
- [x] Present a three-way conflict view: original base, current Arduino file, and staged Codex change.
- [x] Add an explicit conflict-resolution action that preserves the current Arduino file and rejects the conflicting Codex change without writing to disk.
- [x] Create a lightweight checkpoint before Save File and provide guarded rollback to the last saved Talos checkpoint.
- [x] Show a patch timeline with source, files/hunks, editor apply/save time, verify result, conflict decision, and rollback action.
- [x] Verify a staged Codex change in a sandbox before Save File by compiling a temporary merged workspace.
- [x] Offer Save And Verify as a deliberate compound action, while retaining separate Save File and Verify Sandbox commands.

Exit condition: every staged change is either safely saved, rejected, rolled back, or resolved through an explicit conflict decision; it can be verified before touching the real sketch.

## Stage 8 - Arduino Operating Workflow And MVP Evidence

Purpose: prove the completed safe change loop as a repeatable Arduino workflow and collect the baseline evidence required before improvement work begins.

- [x] Build a compact workspace map for Codex: main sketch, related source tabs, board profile, libraries, and latest diagnostics.
- [x] Add per-sketch environment profiles for FQBN, serial port, baud rate, build flags, and library metadata.
- [x] Add a manual MVP smoke-test matrix covering multi-window detection, staging, hunk apply, conflict, rollback, staged verify, and Save And Verify in `docs/ARDUINO_SMOKE_TEST.md`.

Exit condition: Talos can repeatedly detect an Arduino sketch, guide a Codex change through review and verification, save it safely, and demonstrate the complete loop in a documented MVP smoke test.

## Stage 9 - Final Optimization And Release Readiness

Purpose: optimize and validate the completed Stage 0-8 Arduino foundation before commercial packaging. These are release-readiness gates for the current 0.1.0 Beta, not a claim that prior stages were unfinished.

**Commercialization gate:** every checkbox in this stage is mandatory. Stage 10 must not start while any Stage 9 release gate remains open.

- [x] [Stage 0] Add versioned configuration validation and migration/backup for user settings, workspace profiles, checkpoints, and run history so an update cannot silently discard local state.
- [x] [Stage 1] Optimize Arduino window/process refresh with event assistance while retaining polling as the safe fallback for missed events.
- [x] [Stage 2] Optimize compile responsiveness with cancellation, workspace-hash caching, clear cache invalidation, and timing telemetry. `Verify Sandbox` exposes Cancel and Clear Cache controls; cached results are labelled, and source/profile/CLI changes generate a new cache key.
- [ ] [Stage 3] Optimize Save File coordination with atomic writes and file-change debounce so Arduino IDE and Talos do not race during normal editing.
- [ ] [Stage 4] Optimize Codex resilience with reconnect/retry status for transient app-server failures without replaying a user turn automatically.
- [ ] [Stage 4] Expose a compact pre-send context preview that identifies the workspace map, active file, profile, and verify data included in each Codex turn.
- [ ] [Stage 5] Validate native detection performance with a benchmark and build/fallback verification across supported Windows environments.
- [x] [Stage 6] Optimize desktop usability with native Windows chrome, standard resize/drag/minimize/maximize/restore behavior, and Explorer-based active-file signaling.
- [ ] [Stage 6] Optimize Change Review with faster diff/hunk scanning and persisted unfinished reviews, offering an explicit restore/discard decision after Talos restarts.
- [ ] [Stage 7] Add an optional three-way merge draft that starts from the current Arduino file, requires editor review and Save File before disk writes, and retains Keep Arduino Version as the non-destructive default.
- [ ] [Stage 7] Optimize checkpoint operations with retention/cleanup policy and a short rollback history without weakening the external-change guard.
- [ ] [Stage 8] Validate the selected sketch profile before verify, including FQBN and build flags/properties, surface profile readiness clearly, then record a versioned MVP smoke-test result with its workspace map, profile, verify summary, and any blocked cases as release evidence.
- [ ] Validate release recovery: restart Talos during a pending review, reconnect Codex, and confirm no external Arduino changes are overwritten.

Exit condition: every 0.1.0 Beta optimization gate is complete, automated where practical, and validated by the recovery smoke test without weakening the completed Stage 0-8 contracts.

## Stage 10 - Commercial App Packaging

Purpose: convert the proven Arduino workflow into an installable, supportable Beta product.

**Entry gate:** all Stage 8 evidence and Stage 9 optimization gates must be complete, including recorded passing MVP and recovery smoke-test runs, before any Stage 10 implementation work starts.

- [x] Define the commercial app identity: final name, publisher, version format, support URL, and release channel.
- [ ] Create a dedicated Talos icon set, including `.ico` and source PNG sizes for Windows packaging.
- [ ] Apply the app icon to the desktop window, taskbar entry, packaged executable, and installer shortcuts.
- [ ] Package the app into a standalone Windows executable that includes Python bridge code, web frontend assets, native DLL, config defaults, and scripts needed at runtime.
- [ ] Add a repeatable release build command that starts from a clean tree and writes artifacts into a versioned release folder.
- [ ] Build a Windows installer with Start Menu shortcut, optional Desktop shortcut, install location, and clean uninstall behavior.
- [ ] Move user-writable runtime data to the correct per-user app data location instead of relying on the source tree.
- [ ] Add version display inside the UI and expose build metadata in the server state endpoint.
- [ ] Add release notes, license/EULA, privacy notes, and third-party dependency notices.
- [ ] Define the code-signing path for commercial distribution, even if signing is initially documented rather than automated.
- [ ] Smoke-test the installed app outside the repository: launch, detect Arduino IDE, edit sketch, verify sandbox, ask Codex, apply patch, verify again.
- [ ] Produce a distribution checklist with artifact names, hashes, installer test result, rollback/uninstall test, and known limitations.

Exit condition: a signed or explicitly unsigned Beta installer can be built from a clean tree, installed outside the repository, launch Talos with its branding and native assets, pass the Arduino smoke test, and uninstall cleanly with documented release evidence.

## Future Backlog - Other Apps

These are intentionally paused until Arduino is stable and smoke-tested:

- MATLAB process detection.
- MATLAB current folder/script detection.
- MATLAB scoped read/edit APIs.
- MATLAB controlled runtime execution.

## Development Principles

- Arduino first, MATLAB later.
- Talos is an AI control layer, not a replacement for Arduino IDE, VSCode, or PlatformIO.
- Arduino IDE remains the primary environment for editing, upload, serial monitoring, and board interaction.
- Talos Change Workspace is the review and change-control surface: inspect real files, review Codex changes, apply them locally, and save deliberately to Arduino IDE.
- Codex changes must enter a Talos staging workspace and Change Review before they can enter the Talos editor or the real sketch folder.
- Apply To Editor updates the Talos editor; Save File is the only normal path that updates the Arduino sketch folder.
- Every staged change transition must be visible and reversible.
- Prefer per-file and per-hunk review over whole-workspace replacement.
- A staged Codex change must detect and resolve external file changes instead of overwriting them silently.
- Checkpoint rollback must refuse to overwrite a file changed after Talos saved it.
- Sandbox verify should be able to compile a staged change before it is saved.
- Operational hardening must preserve Arduino IDE as the owner of the real workspace and keep polling fallbacks when event-driven helpers fail.
- Unsaved/temp sketches are signals only, not valid workspaces.
- Verify must run against a sandbox copy, not the original sketch folder.
- UI must make output/context easy to copy into Codex.
- Native C should own speed-sensitive Windows/system helpers.
- Python should remain the bridge/API layer.
