# Thiên Mệnh Lạc Hồng profile bot

Run `python launcher_gui.py` on Windows. Install Python packages with `python -m pip install -r requirements.txt`. The OCR runtime is the separate Windows Tesseract executable; the bot checks for `tesseract.exe` on `PATH` or at `C:\Program Files\Tesseract-OCR\tesseract.exe` and reports an error if it is unavailable.

Choose the original game folder containing `ThienMenhLacHong_Launcher.exe`. This path is saved in `settings.json`. Enter a profile name and choose **Create Profile**. The full game folder is copied to `Game/profiles/<profile_name>/` in the bot directory. The profile record is saved immediately with `login_ready=false`; the UI remains responsive during the copy. After the cloned game launches and the ten second startup wait finishes, log in manually and press **Đã đăng nhập**. Only then is the profile saved as ready in `profiles.json`.

If the bot closes during creation, the incomplete profile remains in `profiles.json`. Select it and use **Continue Login** once the clone is valid, or **Repair/Recreate** if game files are missing. Repair is explicit and keeps extra files already in the clone while copying the source files again. Login confirmation is required after repair.

Use the checkbox column to choose several ready profiles. Select a row to set its boss and resolution, then use **Save options**. **Start Selected** launches or attaches each checked profile, finds its game process and HWND, resizes the client area, arranges visible windows from left to right, and starts an independent automation worker. **Stop Selected** signals only the checked workers. Arrangement uses the monitor's Windows working area. If all windows cannot fit, the bot warns and retains the chosen resolutions.

Each worker waits ten seconds after a new launch, detects three scaled game signals, and confirms all are absent for three continuous seconds before entering the chosen boss. Signal 1 is never clicked; Signals 2 and 3 return click actions, which pass through a global physical input lock. Boss HP OCR uses a scaled client ROI derived from the canonical `860x484` coordinates, upscales small crops before OCR, checks every two seconds, and requires two consecutive digit-free results before exiting. After a 16 second respawn wait the worker validates the game state again. Long waits use the profile's stop event.

Automated checks: `python -m pytest -p no:cacheprovider tests -q` and `python -m compileall .`.

Real-game checks still needed on the target machine: create three profiles from the actual source folder, manually log in, confirm each profile, choose mixed resolutions and different bosses, start all three, verify unique game PIDs/HWNDs and correct clicks/OCR, stop one without stopping the others, and exercise explicit repair on a disposable clone. Frida packet injection and background screenshot behavior depend on the running game and cannot be validated by unit tests alone.
