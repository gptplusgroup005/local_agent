# Arduino Smoke Test

This smoke test closes the Arduino MVP loop. Run it before treating Talos as ready for daily Arduino work or before starting another app integration.

## Prerequisites

- Arduino IDE is installed.
- At least one saved Arduino sketch folder exists. The folder must contain a main `.ino` file with the same name as the folder.
- `arduino-cli` is available either from PATH or from the bundled Arduino IDE location.
- Talos project verification passes:

```powershell
.\scripts\check.ps1
```

## Test Sketch

Use a small saved sketch first:

```cpp
void setup() {
  Serial.begin(115200);
}

void loop() {
  delay(1000);
}
```

Optional second-pass test: add a `.h` or `.cpp` tab in the same sketch folder and include it from the `.ino` file.

## Steps

1. Open Arduino IDE.
2. Open the saved test sketch in Arduino IDE.
3. Select a real board in Arduino IDE.
4. Start Talos:

```powershell
.\scripts\launch_desktop.ps1
```

5. In Talos, open the Arduino workspace view.
6. Confirm the open sketch appears in Explorer.
7. Select the sketch.
8. Confirm the Sketch Folder points to the saved sketch folder.
9. Confirm Board shows the selected board name or FQBN.
10. Select the main `.ino` file in the Files list.
11. Make a tiny safe edit, such as changing `delay(1000);` to `delay(500);`.
12. Save the file from Talos.
13. Click Verify Sandbox.
14. Confirm Verify Output shows one current result only, not old passed cards mixed into the current result.
15. Confirm the sandbox path is under `.talos_sandbox\arduino`.
16. Confirm the real sketch folder was not used as the compile target.
17. Open the Codex panel.
18. Ask Codex to review or make a small safe patch.
19. If edits are allowed, confirm Talos records the changed file.
20. Confirm Talos automatically verifies the sandbox again after the Codex patch.
21. Confirm the final verify result is visible and copyable.

## Pass Criteria

- Arduino IDE is detected without manually typing the sketch path.
- The selected sketch does not jump back to another sketch during refresh.
- Files in the sketch folder, including `.ino`, `.h`, and `.cpp`, appear in the Files list.
- File edits stay scoped inside the selected sketch folder.
- Verify Sandbox compiles a copied sandbox folder, not the original folder.
- Verify Output resets on each run and shows only the current verify result.
- Codex receives the selected workspace, active file, and latest verify result.
- Codex edits, when allowed, are applied through Talos-controlled file writes.
- A Codex patch triggers another sandbox verify.

## Fail Conditions

- Talos shows a closed or stale sketch after Arduino IDE has closed it.
- Talos picks the wrong board for the selected sketch.
- Verify Sandbox compiles the original sketch folder.
- Verify Output mixes old history cards into the current result.
- Codex can edit outside the selected sketch folder.
- Talos UI becomes unresponsive during detection, verify, or Codex polling.

## Result Log

Record the latest manual run here:

```text
Date:
Arduino IDE version:
Board:
Sketch:
Talos native available:
Verify result:
Codex patch result:
Notes:
```
