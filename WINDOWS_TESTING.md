# Testing CAVY on Windows

Goal: confirm CAVY runs correctly on a Windows machine. Development stays on
the Mac; the Windows laptop is **only for testing**. When something fails, you
copy the error back to the Mac session and the fix is made there, then pulled
onto Windows (Part 6).

Windows support has so far been verified only by reading the code. Expect
some problems on the first run. That is the point of this exercise.

---

## Part 1 · Get the code

Repository: <https://github.com/ViralKariya-VK/eaal-platform>

The latest work is on the branch **`app-completion`**, not `main`.

> **Before you start (do this on the Mac):** the branch must be pushed to
> GitHub first, otherwise Windows can't see it:
> `git push -u origin app-completion`

On Windows, in PowerShell:

```powershell
cd $HOME\Documents          # or wherever you keep projects
git clone https://github.com/ViralKariya-VK/eaal-platform.git
cd eaal-platform
git checkout app-completion
```

If the repository is private, Git will ask you to sign in to GitHub (a browser
window opens). If you don't have Git, install it from <https://git-scm.com>
and reopen PowerShell.

Check you have the right code:

```powershell
git log --oneline -3
```

The top line should read `Complete core app features: AI setup, lab
management, accounts, progress`.

---

## Part 2 · Install prerequisites

| Need | Why | Check it |
|---|---|---|
| **Python 3.11 or newer** (python.org, tick "Add python.exe to PATH") | runs the app | `python --version` |
| **WebView2 runtime** | draws the window. Built into Windows 10/11; Windows prompts to install it if missing | none needed |
| **Ollama for Windows** (<https://ollama.com>), *optional* | local AI assistant | `ollama --version` |
| **Node.js / npm**, *optional* | only to fetch the Monaco code editor | `npm --version` |

You do not need Ollama or Node to start testing. Without Ollama you can use a
Groq key instead (Part 3, step 5), and without Monaco the editor falls back to
a plain text box (known and expected).

---

## Part 3 · Set up and start the app

In PowerShell, inside the `eaal-platform` folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[sandbox-libs]"
```

- If activation fails with *"running scripts is disabled"*, run
  `Set-ExecutionPolicy -Scope Process Bypass` and activate again. This only
  affects the current window.
- `.[sandbox-libs]` installs numpy, pandas and matplotlib, which some labs make
  students import. They are installed into the same environment as the app
  because student code runs in that same Python.
- You don't need the `dev` extra just to test. Add it (Part 5) only if you
  want to run the automated tests.

Start the app:

```powershell
python -m eaal_platform.app
```

(`run.bat` does the same thing, but you must activate the environment first.)

**Optional, the code editor:** `python scripts\fetch_monaco.py` (needs npm).
Skip it if you don't have npm.

**Where data is stored on Windows:** `%APPDATA%\CAVY` (type that into File
Explorer's address bar). The database lives there. To start completely fresh,
close the app and delete that folder.

---

## Part 4 · The test checklist

Work through these in order and note **pass / fail** for each. If anything
fails, go to Part 6 straight away; later items often depend on earlier ones.

### A. Startup
1. The window opens and shows the **login screen** (not a blank window or a
   crash). Window title is "CAVY".
2. The terminal you launched from shows no red error text.

### B. Accounts
3. Create a **Teacher** account (any name/email, password of 8+ characters).
   You should land on the professor's screen.
4. Sign out (Profile → Log Out), then create a **Student** account.
5. Sign in again with each. A wrong password should show an error, not crash.
6. Profile → **Change password** works; sign in with the new password.

### C. Professor flow (sign in as teacher)
7. **My Labs → Create New Session**: fill in a lab, save it. It appears in the list.
8. **Edit** the lab; change the title; save.
9. **Archive** it, tick *Show archived labs*, **Restore** it.

### D. Student flow (sign in as student)
10. The lab appears on Home. Open it, start the **Learning** stage.
11. The workspace opens. Type code, e.g. `print("hello")`, press **Run**; the
    output `hello` appears.
12. Does the editor look like VS Code (Monaco) or a plain text box? Either is
    acceptable; just note which.
13. **Submit** the session. A short written question about the code appears;
    answer it. The submission and **CIQ score** screens open.
14. **My Progress** (sidebar) loads. With one session it will say "Complete at
    least two sessions to see your trend", which is normal. Do a second
    practice session and check the chart appears.

### E. Code execution and the sandbox (the critical part)
Use **Practice** (sidebar). Paste each snippet, press Run, and compare to the
expected result.

| Snippet | Expected |
|---|---|
| `print(2 + 2)` | prints `4` |
| `import numpy as np; print(np.arange(3))` | prints `[0 1 2]` |
| `import pandas as pd; print(pd.DataFrame({"a":[1,2]}))` | prints a small table |
| `x = 1/0` | shows a `ZeroDivisionError` traceback, no crash |
| `while True: pass` | stops after the time limit with a **timed out** message; the app stays responsive |
| `print(open("C:/Windows/win.ini").read()[:20])` | note what happens (the sandbox is not meant to block this yet; just record it) |

> **Do not** run a memory bomb such as `x = [0]*10**10`. On Windows there is
> currently no memory limit (only the timeout applies), so it could freeze the
> laptop. That limit is the next piece of work. After a timeout, open Task
> Manager and check no stray `python.exe` is left running from the test.

### F. Report export
15. As teacher, open a lab's **Report** and press **Export Report**. A native
    Windows **Save** dialog should appear. Save it, open the file in Excel or
    Notepad, and check it contains the student rows.

### G. AI assistant
16. After login a dialog may say the AI assistant isn't available. That is
    expected if Ollama isn't set up.
17. **Groq route:** choose Groq, paste a key (from your `groq.txt`; type or
    paste it into the app only, never into a chat or a file you commit). It
    should say it's connected. Open a lab workspace and ask the chat a
    question; you should get an answer.
18. **Ollama route (optional):** install Ollama, run `ollama pull qwen3:8b`
    in a terminal, then press **Check again** in the app.

### H. Closing
19. Close the window; the terminal returns to a prompt (the process exits
    cleanly, no hang).
20. Reopen the app: your accounts and labs are still there.

---

## Part 5 · Optional: run the automated tests on Windows

These show whether the engine's own logic behaves the same on Windows.

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

Expected: about 216 passed and 1 skipped. Some tests may fail on Windows (for
example anything involving POSIX resource limits or file paths). Copy the
**names of failing tests and the error text** into the Mac session.

---

## Part 6 · When something goes wrong

### What to send back (to the Mac session)
Copy as much of this as you can into the chat:
1. **Which checklist item** failed (e.g. "E, `while True` snippet").
2. **The exact error text** from the PowerShell window (the whole traceback,
   copied as text; a screenshot is fine if it can't be copied).
3. What you **saw on screen** versus what you expected.
4. `python --version` and `ver` (your Windows version).

Then on the Mac I fix it, you commit/push there, and on Windows:

```powershell
git pull
```

Restart the app and re-test that item. (If packages changed, also re-run
`python -m pip install -e ".[sandbox-libs]"`.)

### Common problems and quick fixes

| Symptom | Likely cause / fix |
|---|---|
| `python` is not recognised | Python isn't on PATH. Reinstall and tick "Add python.exe to PATH", or use `py -3.11` instead of `python`. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope Process Bypass`, then activate again. |
| `ModuleNotFoundError: eaal_platform` | Run commands from inside the `eaal-platform` folder, with the venv activated, after `pip install -e`. |
| Window never appears / blank white window | WebView2 runtime missing. Install "Evergreen WebView2 Runtime" from Microsoft, then relaunch. |
| `pip install` fails building pandas/numpy | Use Python 3.11 or 3.12 (very new Python versions lack prebuilt wheels). |
| Garbled characters in the app (`Â·`, `â€”`) | A text-encoding problem. Report it with a screenshot. |
| `UnicodeEncodeError` in the terminal | Run `$env:PYTHONUTF8 = "1"` first, retry, and report it anyway. |
| Run button does nothing, or "No such file" | Sandbox problem on Windows. **Report it with the full terminal output.** |
| Export Report does nothing | Report it; the Save dialog may not be wired up on Windows. |
| App starts but behaves oddly after a crash | Close the app, delete `%APPDATA%\CAVY`, start fresh (this erases local accounts). |
| Antivirus/Defender blocks a Python process | Allow it and retry; mention it in your report. |

### Known limitations (not bugs to chase)
- On Windows only a **timeout** limits student code. There are no CPU or
  memory limits yet. Planned next (roadmap F2).
- Without `scripts\fetch_monaco.py` the editor is a plain text box.
- Accounts and labs are local to this one computer (no sync yet).
- The Groq key is kept in memory only and must be re-entered each launch.

---

## Part 7 · Done means

The app is considered "running on Windows" when every item in Part 4
(A through H) passes, apart from items you deliberately skipped (Ollama,
Monaco). Record the results (pass/fail per item) and send them to the Mac
session so the roadmap items **F1** (full run on Windows) and **F2** (Windows
sandbox limits) can be updated.
