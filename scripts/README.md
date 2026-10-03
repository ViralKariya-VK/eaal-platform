# scripts/

Everything you run by hand lives here. Run them from anywhere; they find the project themselves.

## Run CAVY from source (developers)

| Script | What it does |
|---|---|
| `run-app.sh` / `run-app.bat` | Start the desktop app (Mac / Windows) |
| `run-server.sh` / `run-server.bat` | Start the classroom server and admin panel without opening the app |

## Build the installers

| Script | What it does |
|---|---|
| `build_release.py` | Build the installer for the computer you are on (`.dmg` on Mac, `Setup.exe` on Windows). `--package-only` redoes just the installer from an existing build |
| `build_macos_app.py` | Small dev-only helper used by `run-app.sh` so the Dock shows "CAVY" and the right icon. Also supplies the Mac icon to `build_release.py` |
| `fetch_monaco.py` | Download the code editor (Monaco) into the app; `build_release.py` calls it for you |
| `generate_icon.py` | Regenerate `src/eaal_platform/assets/icon.png` |
