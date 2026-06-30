# Talos Pipeline - Version 0.2.0 Beta

## Final Goal For 0.2.0

Talos 0.2.0 Beta should turn the completed 0.1.0 technical MVP into a more reliable Arduino-first Beta that can be installed, tested outside the source tree, and used repeatedly with real Arduino sketches.

This version is not a MATLAB release and not the final 1.0.0 Alpha. It focuses on the next logical product step:

```text
0.2.0 Beta = packaged Arduino reliability and daily-use readiness.
```

## Baseline

```text
Roadmap: docs/TALOS_ROADMAP.md
Previous completed pipeline: docs/TALOS_PIPELINE_010.md
Current active pipeline: docs/TALOS_PIPELINE_020.md
Target version: 0.2.0 Beta
```

Talos 0.1.0 Beta already completed the Arduino MVP, Codex bridge, staged change review, sandbox verify, native Windows detection, release packaging scripts, installer smoke tooling, and distribution checklist tooling. Version 0.2.0 should not redesign those foundations unless a reliability issue requires it.

## 0.2.0 Release Criteria

Talos 0.2.0 Beta is ready only when these are true:

- It can be built, installed, launched outside the source tree, smoke-tested, and uninstalled with release evidence.
- It can repeatedly detect live saved Arduino sketches without keeping stale closed sketches in the active list.
- It can keep board/profile information synchronized for the selected sketch when Arduino IDE exposes usable metadata.
- It can verify sandbox builds without stale output accumulation and with clear cache/cancel behavior.
- It remains usable in normal, maximized, and narrow desktop windows without fixed-layout failures.
- It preserves the 0.1.0 safety contract: Codex changes never write directly to the real Arduino sketch, and external Arduino edits are not silently overwritten.

## Non-Goals For 0.2.0

- No MATLAB or second-app integration.
- No commercial-grade upload or serial-monitor guarantee.
- No plugin SDK or marketplace.
- No auto-update infrastructure.
- No rewrite of the Codex bridge, staging workspace, or native detection layer unless required to fix a concrete reliability bug.

## Progress Rules

- Keep `docs/TALOS_PIPELINE_010.md` as the frozen 0.1.0 Beta comparison record.
- Track all 0.2.0 work in this file.
- Every completed 0.2.0 task must update this file.
- Do not add MATLAB or unrelated app targets to this pipeline.
- Use the pipeline status checker with this file:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pipeline_status.ps1 -Path docs\TALOS_PIPELINE_020.md
```

## Stage 0 - 0.2.0 Pipeline Setup

Purpose: establish this file as the active version-specific pipeline.

- [x] Preserve the completed 0.1.0 Beta pipeline as `docs/TALOS_PIPELINE_010.md`.
- [x] Create a dedicated 0.2.0 Beta pipeline as `docs/TALOS_PIPELINE_020.md`.
- [x] Update documentation and pipeline status defaults to use the active 0.2.0 pipeline.
- [x] Remove obsolete generic pipeline filenames after references are migrated.

Exit condition: the repository has a version-specific 0.1.0 history pipeline and a version-specific active 0.2.0 pipeline, with no active workflow depending on generic `TALOS_PIPELINE.md` or `TALOS_PIPELINE_NEXT.md`.

## Stage 1 - Release Candidate Validation

Purpose: prove that the current app can be built, installed, launched, and uninstalled outside the development folder before deeper 0.2.0 reliability work begins.

- [x] Build a clean release folder without `-AllowDirty`.
- [x] Build the Windows installer from the clean release output.
- [x] Record signing status as signed or explicit unsigned Beta.
- [x] Run installer install/uninstall smoke and record `installer_smoke.json`.
- [x] Run installed-app smoke outside the source tree and record `installed_app_smoke.json`.
- [ ] Generate `DISTRIBUTION_CHECKLIST.md` with `-RequireReady`.
- [x] Confirm `desktop_app.py` still launches the source app for debug.
- [x] Record packaging/runtime issues found during release-candidate validation.

Stage evidence: `docs/TALOS_020_STAGE1_VALIDATION.md`.

Exit condition: the app can be installed and launched outside the source tree, uninstalled cleanly, and represented by complete release evidence.

## Stage 2 - Arduino Detection Reliability

Purpose: make live Arduino IDE detection more dependable across real-world sketch/window workflows.

- [ ] Test multiple Arduino IDE windows with saved sketches on different folders.
- [ ] Test closing a sketch/window and confirm Talos removes stale candidates quickly.
- [ ] Test switching focus between `.ino`, `.h`, and `.cpp` tabs without losing the real sketch folder.
- [ ] Improve board-to-sketch mapping for common AVR and ESP32 boards when multiple IDE windows are open.
- [ ] Add diagnostics for missing or ambiguous board/FQBN detection.
- [ ] Add regression coverage for stale sketch and stale board cases found during manual testing.

Exit condition: Talos reliably lists only live saved sketches and keeps the selected sketch's board/profile synchronized when Arduino IDE exposes enough metadata.

## Stage 3 - Verify And Runtime Responsiveness

Purpose: make sandbox verification fast, cancellable, and predictable enough for repeated daily use.

- [ ] Validate verify cache invalidation for source edits, profile edits, board changes, CLI path changes, and build-property changes.
- [ ] Tune verify cancellation and clear-cache feedback so users understand what happened.
- [ ] Keep verify output from accumulating stale passed/failed cards after a new verify starts.
- [ ] Review sandbox copy exclusions for common Arduino build/cache folders.
- [ ] Add timing thresholds or telemetry checks for prepare, copy, compile, and total verify time.

Exit condition: Verify Sandbox provides one current result, clear cancellation/cache behavior, and actionable timing data without blocking the workbench.

## Stage 4 - Arduino Workbench UX Polish

Purpose: reduce friction in the Arduino workbench while preserving Arduino IDE as the owner of the real sketch.

- [ ] Polish responsive layout at normal, maximized, and narrow desktop sizes.
- [ ] Make Explorer, Change Workspace, Verify Output, and Codex panes resizable where useful without fixed-pixel layout failures.
- [ ] Keep active-file highlighting in the Files list as the primary file location signal.
- [ ] Simplify labels around Review, Edit in Talos, Save File, Save And Verify, Rollback, and Verify Sandbox.
- [ ] Add first-run or empty-state hints explaining Arduino IDE ownership and Talos save boundaries.

Exit condition: a user can understand and operate the Arduino workflow without reading developer notes, and the UI remains usable outside maximized windows.

## Stage 5 - Codex Context And Change Loop Stability

Purpose: make the Codex side of the Arduino loop clear, recoverable, and useful without expanding scope beyond Arduino.

- [ ] Ensure pre-send context clearly shows workspace map, active file, profile, verify result, and edit permission.
- [ ] Improve Codex panel states for tasks, active conversation, pending turn, cancellation, reconnect, and recovery.
- [ ] Keep staged changes grouped by file, hunk, and Codex turn.
- [ ] Recommend verify-before-save for Codex-generated changes.
- [ ] Record Codex turn outcomes by sketch in run history for debugging and support.

Exit condition: a user can ask Codex for an Arduino change, understand what context was sent, review the result, verify it, and decide whether to save it.

## Stage 6 - Safety And Recovery Hardening

Purpose: protect real Arduino sketches from accidental overwrite during normal Talos/Codex usage.

- [ ] Expand conflict tests for external Arduino IDE edits while Talos has staged or editor-applied changes.
- [ ] Test restart recovery during pending review, staged verify, save, and rollback flows.
- [ ] Make restore/discard decisions for unfinished reviews clear after restart.
- [ ] Confirm rollback refuses to overwrite files changed after the saved checkpoint.
- [ ] Document the safest manual recovery path when a user edits both Arduino IDE and Talos at the same time.

Exit condition: no normal Talos/Codex path can silently overwrite external Arduino changes, and recovery is understandable after restart.

## Stage 7 - 0.2.0 Release Gate

Purpose: package and validate the completed 0.2.0 Beta.

- [ ] Run full automated checks.
- [ ] Run manual Arduino smoke tests for simple `.ino`, multi-file `.h/.cpp`, AVR board, and ESP32 board cases.
- [ ] Build final 0.2.0 Beta release artifacts.
- [ ] Sign artifacts or explicitly mark unsigned Beta status.
- [ ] Run installer smoke and installed-app smoke.
- [ ] Generate final 0.2.0 distribution checklist with `-RequireReady`.
- [ ] Bump app identity, release manifest naming, and release notes from 0.1.0 to 0.2.0 only at the final release gate.
- [ ] Update release notes with 0.2.0 fixes, known limitations, and upgrade notes.

Exit condition: Talos 0.2.0 Beta can be installed, run outside the development environment, operate reliably with real Arduino sketches, and ship with release evidence.

## Deferred Until After 0.2.0

- MATLAB target integration.
- Hardware upload and serial-monitor guarantees.
- Auto-update infrastructure.
- Public plugin/target SDK.
- 1.0.0 Alpha release gate.
