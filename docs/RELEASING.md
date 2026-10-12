# Shipping an update

The apps look at this repository's releases to find out whether a newer CAVY exists, so shipping an update
is: change the version, push a tag, and wait. Nothing else needs to be told.

## How people find out

- Each app asks GitHub for the latest release when it starts and then every few hours (at most about twice a
  day it really asks; the answer is kept in `update_check.json` in the CAVY data folder).
- If a newer version exists, a small notice appears: **CAVY X.Y.Z is available**, with **Download** (opens the
  installer for that computer's operating system) and **Later** (hides it for three days).
- The notice never appears during a lab, and nothing is downloaded or installed by the app itself.
- Offline, or GitHub unreachable: nothing is shown and nothing breaks.
- Hosting a server (the **Host a server** window, and the admin panel's Overview) says so when a newer
  version is available.

## One version for the whole class

Every call an app makes to a classroom server carries its version. The server refuses a call from a different
version, and an app will not connect to a server on a different one. The message says which side to update.
So before a class: update the computer that hosts the server first, then every other computer.

Upgrading in place keeps all data. Accounts, labs and work live in the data folder (not in the install
folder), and the database is upgraded automatically the first time the new version starts.

## Steps

1. Choose the number (`MAJOR.MINOR.PATCH`): a fix is `0.2.1`, new features `0.3.0`.
2. Change it in **both** `src/eaal_platform/buildinfo.py` (`VERSION`) and `pyproject.toml` (`version`). A test
   fails if they differ. The installer scripts read the number from `buildinfo.py`.
3. Write the notes in `docs/releases/vX.Y.Z.md` (a short list of what changed and why someone should update).
4. Run the checks (`ruff check . && mypy src && pytest`), commit and push to `main`.
5. Tag and push the tag:

   ```bash
   git tag v0.3.0
   git push origin v0.3.0
   ```

6. GitHub Actions builds the macOS and Windows installers, checks that the tag matches the version in the
   code, and publishes the release with the installers, `SHA256SUMS.txt` and your notes. This takes about
   15 minutes. Running **Build installers** by hand (without a tag) only builds downloadable files.

Apps that are already installed will show the notice the next time they check.

## Limits (and what would remove them)

- The person still downloads and runs the installer. A fully automatic update needs signed installers
  (Windows code-signing certificate, Apple Developer ID and notarization for the Mac).
- The notice only appears for releases on GitHub; a computer with no internet will not see it.
