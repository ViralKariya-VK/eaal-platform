# Running CAVY in a classroom: installers, server, professor, students, admin

Four computers on one network (it also works with fewer):

```
   PC 1  (Mac)  — hosts the server        PC 2 (Win/Mac)   PC 3 (Win/Mac)   PC 4 (Win/Mac)
   ┌──────────────────────────┐          ┌────────────┐   ┌────────────┐   ┌────────────┐
   │ CAVY app → "Host a       │◄────────►│ CAVY app   │   │ CAVY app   │   │ CAVY app   │
   │ server"                  │◄─────────┼────────────┼──►│ student 1  │   │ student 2  │
   │ browser → admin panel    │          │ professor  │   └────────────┘   └────────────┘
   │ all data lives here      │          └────────────┘
   └──────────────────────────┘
```

- **One installer per operating system** (`.dmg` for Mac, `Setup.exe` for
  Windows). It contains everything: no Python, no pip, **no internet needed to
  install**. Internet is only needed if you use Groq for the AI assistant.
- **Everything is stored on PC 1** (accounts, labs, sessions, events, code, AI
  chats, scores). Student code runs on the student's own PC.
- **Screens update live.** When the professor publishes a lab it appears on
  every student's screen within about a second, with nobody refreshing. When a
  student submits, the professor's Home and Reports update by themselves.
- **The admin panel** is a web page served by PC 1.

---

## 1. Get the installers

You build them with GitHub, no Windows machine needed:

1. Push the code to GitHub (the `app-completion` branch, or `main`).
2. On GitHub: **Actions → Build installers → Run workflow**.
3. Wait about 15–25 minutes. Open the finished run and download
   **CAVY-macOS** (the `.dmg`) and **CAVY-Windows** (`CAVY-0.1.0-Windows-Setup.exe`)
   from *Artifacts*.
4. Share those two files. (Or build locally: `python scripts/build_release.py`
   builds the installer for the OS you run it on; a Mac builds the Mac one,
   Windows builds the Windows one.)

> **Apple Silicon only for Mac.** The Mac installer built on GitHub runs on M1/M2/M3
> Macs. Older Intel Macs need their own build.

### Installing (and the security warnings)
The installers are **not signed by a paid Apple/Microsoft developer account**,
so each system blocks them the first time. This is normal for unsigned apps
and is a one-time step per computer. The app itself is fine.

**Mac** (Apple Silicon: M1 or newer)
1. Open the `.dmg` and drag **CAVY** into **Applications**.
2. Open CAVY from Applications. macOS shows **"CAVY" Not Opened** and only
   offers *Done* and *Move to Bin*. Click **Done** (never *Move to Bin*).
3. Open **System Settings → Privacy & Security**, scroll to the **Security**
   section, find *"CAVY" was blocked…* and click **Open Anyway**. Enter the
   Mac's password, then click **Open**. From now on it opens normally.
   - On macOS 13 and 14 you can instead *right-click CAVY → Open → Open*.
     On macOS 15 and newer that shortcut was removed; use System Settings.
   - Faster alternative: in **Terminal**, run
     `xattr -dr com.apple.quarantine /Applications/CAVY.app` once, then open CAVY.
4. If the app instead says it **can't be opened because it's damaged**, run
   the same Terminal command as above.
5. "This app is not supported on this Mac" means it is an Intel Mac. The
   installer is for Apple Silicon only (Apple menu → About This Mac shows the chip).

> Sending the installer by a **USB drive or a file server** usually avoids the
> block entirely, because macOS only adds the warning to files that arrive by
> download, AirDrop or messaging apps.

**Windows**
- Run `CAVY-…-Setup.exe`. SmartScreen says "Windows protected your PC": click
  **More info → Run anyway**. Windows 10/11 already contain the WebView2
  component CAVY needs.

**To remove the warnings for good** the installers must be *signed and
notarized* with a paid Apple Developer account (about US$99/year) and, for
Windows, a code-signing certificate. That is the right step before giving
CAVY to people who can't follow the steps above.

---

## 2. Network requirements (most demo problems are here)

All computers must be on the **same Wi-Fi or network**, and it must let
computers talk to each other.

- Many **college / guest Wi-Fi networks block this** ("client isolation"). If
  the PCs can't reach each other, use a **phone hotspot** or a small router.
- Keep PC 1 awake and on the same network for the whole demo (turn off sleep).

---

## 3. PC 1: start the server (Mac)

1. Open **CAVY**. On the login screen, at the bottom, click
   **Host a server on this computer → Start server**.
2. macOS / Windows asks whether to allow CAVY to accept network connections.
   Choose **Allow** (on Windows tick **Private networks**).
3. A box shows the address other computers use, for example
   **`192.168.1.41:8000`**. Write it down.
4. Click **Open admin panel**. The first time, it asks you to **create the
   administrator account**: do this yourself right away. This account controls
   every user and all data.
5. In the admin panel, open **AI assistant** and paste a **Groq key** (works
   from any computer; or use Ollama if it's installed on PC 1). Students can't
   change the AI; this one assistant serves the whole class.

**Leave CAVY open on PC 1.** Closing it stops the server for everyone. (You can
still use PC 1's login screen to sign in as someone, if you like.)

> Prefer a terminal? `python -m eaal_platform.server` does the same thing, and
> the installed app can run headless with `CAVY --cavy-serve`.

---

## 4. PC 2, 3, 4: professor and students

On each: install, open **CAVY**, and on the login screen click
**Connect to a server**, type the address from PC 1 (`192.168.1.41:8000`),
press **Connect**. The login screen now says *Connected to …*. It remembers
this.

- **PC 2:** *Create an account* → **Teacher**. (e.g. `prof@demo.edu`)
- **PC 3:** *Create an account* → **Student** (`student1@demo.edu`).
- **PC 4:** *Create an account* → **Student** (`student2@demo.edu`).

Passwords need 8+ characters. Accounts can also be created by the admin
(admin panel → Users → Add account).

If the server is unreachable you'll see "Can't reach the CAVY server…". The
app deliberately does **not** fall back to local data while a server is
configured, so student work can't get stranded on one PC.

---

## 5. What to show (about 10 minutes)

1. **Admin panel → Overview** on PC 1: accounts, **Signed in now** (who, from
   which computer, when), **Working on a task right now**, and the latest
   activity: all live, updating every few seconds.
2. **PC 2 (professor):** *My Labs → Create New Session* and publish a lab.
3. **PC 3 and PC 4 (students):** the new lab **appears on both Home screens by
   itself**. No refresh.
4. **Students:** start the lab, write code, **Run** (runs on that PC), ask the
   AI, **Submit**, answer the concept question.
5. **Classes and resources:** on PC 2 open **My Class** and add student 1 (a
   student belongs to one teacher's class). Open **Resources → Add resource**:
   upload a PDF, add a link, or write instructions; attach it to a lab; share
   with the whole class or chosen students. Student 1's **Resources** screen
   (and the lab's page) shows it **immediately**; student 2, who isn't in the
   class, sees nothing. Students can open or save the files on their own computer.
6. **PC 2 (professor):** *Home* and *Reports* **update by themselves** with the
   submissions and scores. *Export Report* saves a CSV.
7. **Admin panel:** *Overview* shows both students mid-task; click **Open** on a
   session to see that student's event timeline, AI conversation, code runs,
   final code and signal scores. *Labs* shows how many students are working on
   / have finished each test.
8. **Admin panel → Users:** assign students to teachers; edit a student's name, email or password; **Sign out**
   a student (on *Overview*); **Disable** an account; **Reset** the
   professor's password. The affected person is returned to the login screen with
   the reason shown.
9. **Admin panel → Resources** lists everything shared (and can delete it);
   **Audit log:** every sign-in, sign-out, lab created, submission
   and admin action, with time and computer. **Database** lets you browse every
   table and export it. **Backup** downloads the whole database.

---

## 6. What the admin can do

| Need | Where |
|---|---|
| How many accounts, how many online | Overview tiles; Users (online badge) |
| Who is signed in, from where, since when | Overview → Signed in now |
| **Sign someone out** / sign everyone out | Overview → Sign out / Sign everyone out |
| Edit name, email, enrolment, **password** | Users → Edit |
| **Reset** a password (temporary, shown once) | Users → Reset password (students *and* teachers) |
| **Disable / enable** an account (keeps history) | Users → Disable |
| Delete an account (not if a student has work) | Users → Delete |
| Create accounts | Users → Add account |
| Which tests (labs) are running, who's working | Overview; Labs (working / submitted counts) |
| One student's whole session | Open on any session |
| Every action on the system | Audit log |
| Browse / export all data | Database |
| **Assign a student to a teacher** / move them | Users → Teacher |
| See / delete shared resources | Resources |
| Archive or restore a lab | Labs |
| Set the AI assistant | AI assistant |
| Copy of everything | Backup |

Changing someone's email or password, disabling or deleting them, or
resetting their password signs them out immediately.

---

## 7. Everyday operations

| Task | How |
|---|---|
| Where is the server's data? | Shown in the "hosting" box. Mac: `~/Library/Application Support/CAVY/server.db`; Windows: `%APPDATA%\CAVY\server.db` |
| Back up | Admin panel → Backup (works while running) |
| Start completely fresh | Close CAVY, delete `server.db` (and its `-wal`/`-shm` files), start again, create the admin again |
| Server restarted | Everyone is returned to the login screen with a "session expired" message and signs in again. No data is lost |
| Change the port | The "Host a server" box has a Port field |

---

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "Can't reach the CAVY server at …" | Wrong address, server not running, firewall, or the network blocks device-to-device traffic (try a phone hotspot). From the other PC, open `http://<address>/api/health` in a browser: you should see `{"ok":true,…}`. |
| Mac says "CAVY Not Opened" | System Settings → Privacy & Security → **Open Anyway** (see Part 1). |
| Windows: SmartScreen warning | More info → Run anyway. |
| Mac asks to allow CAVY to find devices on the local network | Allow. Without it the Mac can't reach the server. |
| "Couldn't start the server on port 8000" | Another program uses it. Pick another port in the box. |
| A student sees "Assistant not available — ask your teacher" | The AI isn't set up. Admin panel → AI assistant. |
| Admin "Too many attempts" | 5 wrong admin passwords lock that email for 5 minutes. |
| Students' Run fails with import errors | They can import the Python standard library, numpy, pandas and matplotlib. Other packages aren't included. |

---

## 9. Honest limitations

- **Unsigned installers** (warnings above). Real distribution to the public
  would need an Apple Developer account and a Windows code-signing certificate.
- **Not yet tested by me on Windows or with several real computers.** The
  Mac installer was built and its core functions were checked; the Windows one
  has to be built by GitHub and tried on your laptop. Expect small issues.
- **No encryption (HTTP).** Fine on a private classroom network; don't expose
  the server to the internet.
- **Open sign-up.** Anyone who can reach the server can create a student or
  teacher account. The admin can disable or delete them.
- **One server computer = one point of failure.** If PC 1 sleeps, closes CAVY, or
  loses the network, nobody can work.
- **The AI is shared.** A free Groq key has rate limits that every student shares.
- **Labs are not class-scoped yet.** Every student sees every lab; only resources
  are restricted to a teacher's class.
- **Shared files are limited to 15 MB** and open in the computer's own program
  (no preview inside CAVY).
- **Windows sandbox:** student code has a time limit but no memory/CPU cap yet.
- **No offline mode.** If the server drops mid-session, Run/Submit fail with an error.
- **Live updates** need the server to be reachable; the admin panel refreshes every few
  seconds rather than instantly.
