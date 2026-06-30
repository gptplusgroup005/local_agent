# Talos 0.2.0 Stage 1 Validation

Checked: 2026-06-30

## Summary

Stage 1 release-candidate validation is partially complete. The build, installer, signing-status, installer smoke, installed-app automated launch smoke, and source-debug launch checks passed. The release checklist can be generated, but the `-RequireReady` gate remains blocked until the installed-app Arduino/Codex manual confirmation is performed.

## Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Active branch | Passed | `develop/0.2.0` |
| Clean release build without `-AllowDirty` | Passed | `releases/Talos-0.1.0-beta/release_manifest.json` |
| Windows installer build | Passed | `releases/Talos-0.1.0-beta/Talos-0.1.0-beta-setup.exe` |
| Signing status | Passed as explicit unsigned Beta | `releases/Talos-0.1.0-beta/signing_status.json` |
| Installer install/uninstall smoke | Passed | `releases/Talos-0.1.0-beta/installer_smoke.json` |
| Installed-app automated smoke | Passed automated launch/health/packaged-mode checks | `releases/Talos-0.1.0-beta/installed_app_smoke.json` |
| Source-debug launch | Passed | `desktop_app.py` responded through `/api/health` on port 8787 |
| Distribution checklist generation | Generated but not release-ready; signing gate is ready, manual installed-app smoke remains open | `releases/Talos-0.1.0-beta/DISTRIBUTION_CHECKLIST.md` |

## Blocker

`scripts/distribution_checklist.ps1 -RequireReady` fails because `installed_app_smoke.json` is still `manual-confirmation-required`.

Required manual steps are listed in `docs/INSTALLED_APP_SMOKE_TEST.md` and mirrored inside `installed_app_smoke.json`:

- launch installed Talos;
- detect an open Arduino sketch;
- select sketch and board;
- open a source file;
- edit in Talos and save file;
- verify sandbox;
- ask Codex for a safe change;
- review, apply, and save Codex change;
- verify sandbox again;
- confirm Arduino IDE reflects the saved change.

After those manual checks pass, rerun:

```powershell
.\scripts\smoke_installed_app.ps1 -SkipBuild -ManualArduinoConfirmed
.\scripts\distribution_checklist.ps1 -RequireReady
```

## Issue Found And Fixed

- The first installed-app smoke run left the packaged `Talos.exe` process alive long enough for cleanup to fail when deleting the temp install folder. `scripts/smoke_installed_app.ps1` was hardened to stop the launched app, stop matching installed Talos processes, wait briefly, and retry temp-folder cleanup.
- The distribution checklist initially treated `status: unsigned-beta` as missing signing evidence. `scripts/distribution_checklist.ps1` now recognizes signed releases and explicit unsigned-Beta status.
