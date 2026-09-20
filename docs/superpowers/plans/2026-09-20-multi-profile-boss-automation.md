# Multi-Profile Boss Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execute tasks sequentially in this document. Do not use subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace credential-based account automation with persistent, independently runnable game profiles that clone the selected source, bind each worker to its own PID/HWND, confirm game state, and run boss automation safely at all supported resolutions.

**Architecture:** Keep Tkinter, OpenCV, Pillow, pywin32, psutil, and Frida. Split persistent profile/settings data from runtime context, add profile cloning/storage and window layout services, and evolve the existing worker into a per-profile state machine. The launcher PID is resolved through its descendant process tree to the actual game PID; the HWND must belong to that actual PID. Boss packet transport uses `boss/enter_boss.py` and the workspace `boss/enter_boss.js` only if the Task 4 existence gate passes, with public APIs `enter_boss(pid, boss_type)` / `exit_boss(pid)`.

**Tech Stack:** Python 3, Tkinter, pywin32, Pillow, OpenCV, NumPy, psutil, Frida, Tesseract OCR via `pytesseract` and the Windows Tesseract executable.

**Spec:** `plan.txt`

## Global Constraints

- Do not store or request username/password credentials.
- Validate `<source_path>\\ThienMenhLacHong_Launcher.exe` before profile creation.
- Clone the complete source directory to `<BOT_ROOT>\\Game\\profiles\\<profile_name>\\` without excluding cache/runtime files.
- If a clone is missing or damaged, mark `ERROR / Missing Game Files`; never silently reclone. Provide `Repair/Recreate`.
- Runtime PID, HWND, state, OCR state, boss-dead streak, and stop event are profile-local and are not persisted.
- Use exact PID-to-HWND association; never infer the target from `GetForegroundWindow()` or a global process scan.
- Use base coordinates `860x484`, scale X/Y and ROI dimensions independently, and cache scaled templates per asset and resolution.
- Signal 1 never clicks; Signals 2 and 3 click their scaled centers.
- Confirm `IN_GAME` only after all three signals are absent continuously for at least 3 seconds.
- Wait interruptibly for 10 seconds after launch, 3 seconds after entering a boss, 2 seconds between OCR checks, and 16 seconds after exiting a boss.
- Boss is dead only after two consecutive OCR results without a digit.
- Physical mouse input is serialized with one global input lock.
- A profile failure must not stop other profiles.
- Configure the Windows Tesseract executable path once; reuse OCR configuration and preprocessing, and restrict OCR to the scaled boss HP ROI.
- All physical mouse input goes through `InputManager` and `GlobalInputLock`; detection code returns actions and never sends input directly.
- Each task may modify only the files listed in that task. If another file is necessary, stop, explain why, update this plan, and continue only after scope is updated.
- Do not make automatic commits. After each task run focused tests, `git diff --stat`, and `git diff`; commit only when explicitly requested.
- Do not modify `asset_cropper.py` unless a concrete runtime dependency proves it is required.
- Do not modify or create `boss/enter_boss.js` unless it is confirmed to exist in the current project; if absent, stop Task 4 and report `BLOCKED`.
- Treat `vision.py` as stable after Task 5. Task 8 may modify it only after a concrete integration defect is demonstrated and documented before the change; otherwise Task 8 must leave it unchanged.

## Review Focus

- A profile with missing clone files must remain recoverable without destructive automatic recloning; test `ERROR / Missing Game Files` and explicit repair.
- Two profiles must never share a process, HWND, stop event, or boss-dead streak; test three-profile startup and isolated stop.
- Frida must attach to the supplied PID even when multiple matching game processes exist; test `enter_boss(pid, ...)` and `exit_boss(pid)` with mocked Frida.
- Small resolutions must scale both ROI and template before matching; test all five resolutions against reference dimensions.
- Physical clicks must not interleave; test two workers contending for `GlobalInputLock`.

### Task 1: Add dependency and test scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_dependencies.py`

**Interfaces:**
- Produces the dependency contract used by all later tasks.

- [ ] **Step 1: Write the failing dependency/import test**

```python
def test_runtime_dependencies_import():
    import cv2
    import numpy
    import PIL
    import psutil
    import pytesseract
```

- [ ] **Step 2: Run it to verify the Python OCR package contract**

Run: `python -m pytest tests/test_dependencies.py -q`

Expected: the test passes when the `pytesseract` Python package imports. This test does not require `tesseract.exe` and must not fail solely because the Windows executable is absent.

- [ ] **Step 3: Add pinned-or-minimum dependencies**

Add `pytesseract` to `requirements.txt` and document that the Windows Tesseract executable must be installed/configured separately; retain existing runtime dependencies instead of replacing them.

- [ ] **Step 4: Install and verify the Python package only**

Run: `python -m pip install -r requirements.txt`; then run `python -m pytest tests/test_dependencies.py -q`.

Expected: Python imports pass. Windows executable discovery and validation are owned by `OcrService`, which must produce a clear configuration error when `tesseract.exe` is unavailable.

- [ ] **Step 5: Run `git diff --stat` and `git diff`; do not commit.**

### Task 2: Create shared profile models, constants, and storage

**Files:**
- Create: `profile_models.py`
- Create: `profile_storage.py`
- Create: `app_settings.py`
- Create: `automation_constants.py`
- Create: `tests/test_profile_storage.py`

**Interfaces:**
- `Profile(profile_id: str, profile_name: str, game_path: str, login_ready: bool, selected_boss: str, window_width: int, window_height: int, created_at: str)`
- `ProfileRuntimeContext(profile_id, profile_name, game_path, process_id=None, window_handle=None, selected_boss, window_width, window_height, state, stop_event, boss_dead_streak=0)`
- `ProfileStorage.load() -> list[Profile]`
- `ProfileStorage.save(profiles: list[Profile]) -> None`
- `ProfileStorage.upsert(profile: Profile) -> None`
- `ProfileStorage.remove(profile_id: str) -> None`
- `AppSettings(game_source_path: str)`
- `AppSettingsStorage.load() -> AppSettings`
- `AppSettingsStorage.save(settings: AppSettings) -> None`
- `parse_resolution(value: str) -> tuple[int, int]`
- Constants for base size, five resolutions, four base ROIs, and timing values.

- [ ] **Step 1: Write tests for JSON round-trip, corrupt JSON, runtime exclusion, duplicate IDs, and resolution validation.**
- [ ] **Step 2: Run `python -m pytest tests/test_profile_storage.py -q` and verify failures.**
- [ ] **Step 3: Implement dataclasses and atomic JSON persistence without PID/HWND/runtime fields.**
- [ ] **Step 4: Implement constants and resolution parsing.**
- [ ] **Step 5: Run the focused tests and verify they pass.**
- [ ] **Step 6: Run focused tests, then `git diff --stat` and `git diff`; do not commit.**

### Task 3: Implement source validation, full cloning, and repair

**Files:**
- Create: `profile_manager.py`
- Create: `tests/test_profile_manager.py`

**Interfaces:**
- `validate_source_path(source_path: Path) -> Path`
- `profile_clone_path(bot_root: Path, profile_name: str) -> Path`
- `validate_profile_name(name: str, existing: list[Profile]) -> str`
- `ProfileManager.create_profile(name, source_path) -> Profile`
- `ProfileManager.check_clone(profile) -> None | MissingGameFilesError`
- `ProfileManager.repair_profile(profile, source_path) -> None`

- [ ] **Step 1: Test launcher validation, illegal/duplicate names, exact destination, complete recursive copy, and no automatic reclone.**
- [ ] **Step 2: Run `python -m pytest tests/test_profile_manager.py -q` and verify failures.**
- [ ] **Step 3: Implement safe folder naming and fixed destination `<BOT_ROOT>\\Game\\profiles\\<profile_name>\\`.**
- [ ] **Step 4: Implement background-compatible copy operations with progress callbacks and a distinct repair operation.**
- [ ] **Step 5: Ensure missing clone validation raises a typed error that maps to `ERROR / Missing Game Files`.**
- [ ] **Step 6: Run focused tests, then `git diff --stat` and `git diff`; do not commit. Leave `asset_cropper.py` unchanged unless the tests prove a runtime dependency requiring it.**

### Task 4: Make boss transport PID-specific

**Files:**
- Modify: `boss/enter_boss.py`
- Modify: `boss/enter_boss.js`
- Create: `tests/test_boss_api.py`

**Interfaces:**
- `enter_boss(pid: int, boss_type: str) -> int`
- `exit_boss(pid: int) -> int`
- `resolve_boss(boss_type: str) -> tuple[str, int]`

- [ ] **Step 1: Before any code change, verify `boss/enter_boss.js` exists in the current project. If it is missing, stop this task and report `BLOCKED / boss/enter_boss.js is missing`; do not create it.**
- [ ] **Step 1a: Test that the supplied PID is passed to `frida.attach(pid)` and no global process enumeration occurs.**
- [ ] **Step 2: Test all three boss names/IDs and invalid boss values.**
- [ ] **Step 3: Refactor `send_packet(pid, packet, wait, stop_event=None)` to attach directly to the supplied PID.**
- [ ] **Step 4: Preserve packet validation and update CLI commands to obtain a PID explicitly.**
- [ ] **Step 5: Make Frida readiness polling interruptible.**
- [ ] **Step 6: Run focused tests, then `git diff --stat` and `git diff`; do not commit.**

### Task 5: Add scaled ROI/template and HWND capture utilities

**Files:**
- Create: `vision.py`
- Create: `tests/test_vision.py`

**Interfaces:**
- `scale_roi(base_roi, width, height) -> tuple[int, int, int, int]`
- `ScaledAssetCache.get(asset_name, width, height) -> numpy.ndarray`
- `capture_client(hwnd) -> numpy.ndarray`
- `match_asset_in_roi(hwnd, asset_name, base_roi, width, height, cache, threshold) -> MatchResult`
- `roi_center(roi) -> tuple[int, int]`

- [ ] **Step 1: Test exact reference ROI values for all five resolutions.**
- [ ] **Step 2: Test cache reuse and template resizing, including the `320x180` 29x38-to-approximately-11x14 case.**
- [ ] **Step 3: Test HWND/client capture and no desktop-wide fallback when a valid HWND exists.**
- [ ] **Step 4: Implement independent X/Y scaling with rounding and OpenCV downscaling.**
- [ ] **Step 5: Run focused tests, then `git diff --stat` and `git diff`; do not integrate `vision.py` into `game_automation.py` yet.**

### Task 6: Add exact process/window lifecycle and layout

**Files:**
- Create: `window_manager.py`
- Create: `window_layout.py`
- Create: `tests/test_window_manager.py`
- Create: `tests/test_window_layout.py`

**Interfaces:**
- `launch_profile(profile) -> subprocess.Popen`
- `find_window_for_pid(pid: int) -> int`
- `resolve_actual_game_pid(launcher_pid: int) -> int`
- `resize_client(hwnd, width, height) -> None`
- `arrange_windows(items, working_area) -> list[WindowPlacement]`

- [ ] **Step 1: Test PID-specific HWND lookup, missing HWND, resize-once behavior, and heterogeneous layout.**
- [ ] **Step 2: Implement launcher invocation from `profile.game_path`, retain the launcher PID, walk descendants, identify the actual game process, and set `RuntimeContext.process_id` to that actual PID.**
- [ ] **Step 2a: Resolve the HWND only from windows whose owning PID is the actual game PID; fail clearly if no valid HWND is found.**
- [ ] **Step 3: Implement client-size-aware window resize and working-area left-to-right wrapping layout.**
- [ ] **Step 4: Add best-effort warning when windows cannot fit without changing selected resolutions.**
- [ ] **Step 5: Run focused tests, then `git diff --stat` and `git diff`; do not commit.**

### Task 7: Implement state detection and OCR boss detection

**Files:**
- Create: `game_state.py`
- Create: `boss_detector.py`
- Create: `ocr_service.py`
- Create: `tests/test_game_state.py`
- Create: `tests/test_boss_detector.py`

**Interfaces:**
- `GameStateDetector.check_signals(context) -> SignalCheck`
- `GameStateDetector.confirm_in_game(context, stop_event) -> bool`
- `BossDetector.read_hp(context) -> str`
- `BossDetector.is_alive(text: str) -> bool`
- `BossDetector.update_dead_streak(text: str) -> bool`
- `OcrService(tesseract_cmd: Path | str)` with reusable configuration/preprocessing, not a persistent `pytesseract` engine object.

- [ ] **Step 1: Test that `GameStateDetector` never clicks or calls `SendInput`/mouse APIs: Signal 1 returns no action; Signal 2 returns `CLICK_CENTER` with the correct scaled coordinates; Signal 3 returns `CLICK_CENTER` with the correct scaled coordinates.**
- [ ] **Step 2: Test OCR digit normalization, two consecutive failures, and streak reset after a digit.**
- [ ] **Step 3: Implement one-time executable-path configuration and reusable OCR configuration/preprocessing with `pytesseract`; do not describe or implement a persistent OCR engine instance.**
- [ ] **Step 4: Keep OCR at a two-second interval and avoid per-frame disk screenshots.**
- [ ] **Step 5: Run focused tests, then `git diff --stat` and `git diff`; do not commit.**

### Task 8: Replace login worker with profile automation state machine

**Files:**
- Modify: `game_automation.py`
- Create: `input_manager.py`
- Create: `tests/test_automation_worker.py`

**Interfaces:**
- `interruptible_wait(seconds, stop_event) -> bool`
- `GlobalInputLock`
- `AutomationWorker(context, on_status, on_error).start() / stop()`
- States: `STOPPED`, `LAUNCHING_GAME`, `WAITING_STARTUP`, `WAITING_GAME`, `CONFIRMING_IN_GAME`, `IN_GAME`, `ENTERING_BOSS`, `WAITING_BOSS_LOAD`, `CHECKING_BOSS`, `BOSS_DEAD`, `EXITING_BOSS`, `WAITING_RESPAWN`, `ERROR`.

- [ ] **Step 1: Test stop responsiveness during every required wait and verify no post-stop click/OCR/boss command.**
- [ ] **Step 2: Test restart behavior: launch, scan signals, wait for manual login if needed, and confirm `IN_GAME` after 3 seconds of signal absence without treating persisted `login_ready` as truth.**
- [ ] **Step 3: Test boss flow using mocked `enter_boss(context.process_id, context.selected_boss)` and `exit_boss(context.process_id)`.**
- [ ] **Step 3a: Test that GameStateDetector returns detection/action information and never calls SendInput or controls the mouse.**
- [ ] **Step 4: Test one worker error does not terminate another worker.**
- [ ] **Step 5: Implement the state machine and profile-local runtime fields, consuming detector actions only through `InputManager` under `GlobalInputLock`.**
- [ ] **Step 5a: If and only if a concrete integration defect in stable `vision.py` is demonstrated, document the defect and update this plan/task scope before modifying `vision.py`; otherwise leave `vision.py` unchanged.**
- [ ] **Step 6: Guard physical input with `GlobalInputLock`; keep capture/OCR parallel.**
- [ ] **Step 7: Run focused tests, then `git diff --stat` and `git diff`; do not commit.**

### Task 9: Replace account UI with profile UI

**Files:**
- Modify: `launcher_gui.py`
- Create: `tests/test_launcher_model.py`

**Interfaces:**
- UI actions for Browse, Create Profile, Repair/Recreate, login confirmation, Start Selected, Stop Selected.
- Table columns: selected, profile, status, boss, resolution, runtime status.

- [ ] **Step 1: Test UI-level selection mapping, create validation, repair action, and independent start/stop dispatch with mocked workers.**
- [ ] **Step 2: Remove username/password fields and all credential persistence.**
- [ ] **Step 3: Add `AppSettings` persistence for `game_source_path`; keep runtime PID/HWND/state out of settings.**
- [ ] **Step 3a: Implement creation lifecycle exactly as: validate source → clone game → launch cloned game → wait 10 seconds → user manually logs in → user presses `Đã đăng nhập` → mark profile READY and persist it.**
- [ ] **Step 3b: Persist safe temporary creation metadata/progress so an app close does not falsely mark an incomplete profile READY; resume as an incomplete creation requiring explicit user continuation or repair.**
- [ ] **Step 4: Add thread-safe status updates using `after()` and profile-specific callbacks.**
- [ ] **Step 5: Add explicit `ERROR / Missing Game Files` and `Repair/Recreate` behavior.**
- [ ] **Step 6: Run focused tests, then `git diff --stat` and `git diff`; do not commit.**

### Task 10: Integrate, verify, and document

**Files:**
- Modify: `README.md` if present, otherwise create `README.md`
- Create: `tests/test_integration_contracts.py`

- [ ] **Step 1: Add contract tests for three profiles: distinct clone paths, PIDs, HWNDs, stop events, and boss streaks.**
- [ ] **Step 2: Run syntax/import checks:** `python -m compileall .`
- [ ] **Step 3: Run all tests:** `python -m pytest -q`.
- [ ] **Step 4: Run a Windows smoke test with a configured source path and document results, including OCR executable configuration.**
- [ ] **Step 5: Verify within the NEW/CHANGED runtime automation path only:**
  - no credential storage or credential-based login remains in launcher/profile automation;
  - no hard-coded game source path is used by profile creation/runtime;
  - boss automation performs no global process scan;
  - profile targeting does not use `GetForegroundWindow()`;
  - required automation waits are interruptible.
  Do not modify unrelated utilities or legacy tools merely because they contain hard-coded paths, `GetForegroundWindow()`, sleeps, or other legacy behavior. Report unrelated occurrences only.
- [ ] **Step 6: Document architecture, storage, clone repair, coordinate/template scaling, OCR cadence, state flow, multi-profile isolation, dependencies, and known limitations.**
- [ ] **Step 7: Run focused tests, `python -m compileall .`, `python -m pytest -q`, `git diff --stat`, and `git diff`; do not commit.**

## Self-Review

- The plan covers source selection, complete cloning, profile persistence, repair-only recovery, exact PID/HWND binding, window sizing/layout, scaled vision, OCR, boss transport, state machine, input serialization, UI replacement, error isolation, and required tests.
- The specified `enter_boss(pid, boss_type)` and `exit_boss(pid)` interfaces are defined once and consumed by the worker.
- `login_ready` remains a persisted compatibility field if needed by the schema, but restart behavior is determined by live signal detection as requested.
- No task relies on automatic recloning or global process discovery.
- The missing OCR executable is surfaced as configuration failure rather than hidden dependency behavior.
