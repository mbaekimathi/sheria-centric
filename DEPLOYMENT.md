# Deploy from GitHub using cPanel Terminal

Use **only** cPanel’s **Terminal**. The app and all its folders will live in **SHERIACENTRIC** (your cPanel Application root).

---

## First-time setup (create SHERIACENTRIC and clone into it)

1. In cPanel, open **Terminal** (under “Advanced” or “Tools”).

2. Go to your home directory. If you already have a folder named **SHERIACENTRIC** that is empty or from an old setup, remove it so we can clone into a fresh one:

```bash
cd ~
# If SHERIACENTRIC already exists and you want to start clean:
# rmdir SHERIACENTRIC 2>/dev/null || rm -rf SHERIACENTRIC
```

3. Clone the repo **into** a folder named **SHERIACENTRIC** (so it matches your cPanel Application root):

```bash
git clone https://github.com/mbaekimathi/sheria-centric.git SHERIACENTRIC
cd SHERIACENTRIC
```

4. Make the deploy script executable (optional, for one-command updates later):

```bash
chmod +x deploy.sh
```

5. In cPanel **Setup Python App**, set **Application root** to **SHERIACENTRIC** (as in your screenshot). Startup file: **app.py**, Entry point: **app**. Save.

6. Click **Run Pip Install** in cPanel to install dependencies. Then **RESTART** the app.

All app files and folders (e.g. `static`, `templates`, `tmp`) will now be created inside **SHERIACENTRIC**.

---

## Every time you want to update (pull latest code)

1. Open **Terminal** in cPanel.
2. Go to **SHERIACENTRIC** and pull:

```bash
cd ~/SHERIACENTRIC
git pull origin main
```

3. Restart the app so changes load:

```bash
mkdir -p tmp
touch tmp/restart.txt
```

Or in cPanel, click **RESTART** for the Python app.

---

## One-command deploy (after first-time setup)

From the app folder:

```bash
cd ~/SHERIACENTRIC
./deploy.sh
```

This runs `git pull origin main` and `touch tmp/restart.txt`. Then use cPanel **RESTART** if needed.

---

## Quick reference (cPanel Terminal only)

| Task | Commands |
|------|----------|
| **First clone into SHERIACENTRIC** | `cd ~` then `git clone https://github.com/mbaekimathi/sheria-centric.git SHERIACENTRIC` then `cd SHERIACENTRIC` |
| **Update code** | `cd ~/SHERIACENTRIC` then `git pull origin main` |
| **Restart app** | `touch tmp/restart.txt` (from inside `~/SHERIACENTRIC`) or use cPanel **RESTART** |
| **All-in-one** | `cd ~/SHERIACENTRIC` then `./deploy.sh` |

- **Application root**: SHERIACENTRIC (folder where `app.py` lives)
- **Repo**: https://github.com/mbaekimathi/sheria-centric  
- **Branch**: `main`

---

## Troubleshooting

| Issue | Fix in Terminal |
|-------|------------------|
| **`git: command not found`** | Ask your host to enable Git for your account. |
| **Permission denied** | Run `chmod +x deploy.sh` inside `~/SHERIACENTRIC`. |
| **Pull asks for password** | Use a GitHub Personal Access Token as the password. |
| **App not updating** | Run `touch tmp/restart.txt` in `~/SHERIACENTRIC` or click **RESTART** in cPanel. |
| **Wrong directory** | Use `pwd` and `ls`; your app must run from the folder that contains `app.py` (SHERIACENTRIC). |
