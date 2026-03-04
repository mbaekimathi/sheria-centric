# Deploy from GitHub using cPanel Terminal

Use **only** cPanel’s **Terminal**. The app updates in **sheriacentric.com/SHERIA-CENTRIC** (your cPanel Application root).

---

## First-time setup (clone into sheriacentric.com/SHERIA-CENTRIC)

1. In cPanel, open **Terminal** (under “Advanced” or “Tools”).

2. Go to the folder that will contain the app (parent of SHERIA-CENTRIC). Create it if needed, then clone the repo into **SHERIA-CENTRIC**:

```bash
cd ~
mkdir -p sheriacentric.com
cd sheriacentric.com
git clone https://github.com/mbaekimathi/sheria-centric.git SHERIA-CENTRIC
cd SHERIA-CENTRIC
```

3. Make the deploy script executable (optional, for one-command updates later):

```bash
chmod +x deploy.sh
```

4. In cPanel **Setup Python App**, set **Application root** to **sheriacentric.com/SHERIA-CENTRIC**. Startup file: **app.py**, Entry point: **app**. Save.

5. Click **Run Pip Install** in cPanel to install dependencies. Then click **RESTART**.

All app files and folders will be in **sheriacentric.com/SHERIA-CENTRIC**.

---

## Every time you want to update (pull latest code)

1. Open **Terminal** in cPanel.
2. Go to the app folder and pull:

```bash
cd ~/sheriacentric.com/SHERIA-CENTRIC
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

```bash
cd ~/sheriacentric.com/SHERIA-CENTRIC
./deploy.sh
```

This runs `git pull origin main` and `touch tmp/restart.txt`. Then use cPanel **RESTART** if needed.

---

## Quick reference (cPanel Terminal only)

| Task | Commands |
|------|----------|
| **First clone** | `cd ~` → `mkdir -p sheriacentric.com` → `cd sheriacentric.com` → `git clone https://github.com/mbaekimathi/sheria-centric.git SHERIA-CENTRIC` → `cd SHERIA-CENTRIC` |
| **Update code** | `cd ~/sheriacentric.com/SHERIA-CENTRIC` then `git pull origin main` |
| **Restart app** | `touch tmp/restart.txt` (from inside `~/sheriacentric.com/SHERIA-CENTRIC`) or cPanel **RESTART** |
| **All-in-one** | `cd ~/sheriacentric.com/SHERIA-CENTRIC` then `./deploy.sh` |

- **Application root**: sheriacentric.com/SHERIA-CENTRIC (folder where `app.py` lives)
- **Repo**: https://github.com/mbaekimathi/sheria-centric  
- **Branch**: `main`

---

## Troubleshooting

| Issue | Fix in Terminal |
|-------|------------------|
| **`git: command not found`** | Ask your host to enable Git for your account. |
| **Permission denied** | Run `chmod +x deploy.sh` inside `~/sheriacentric.com/SHERIA-CENTRIC`. |
| **Pull asks for password** | Use a GitHub Personal Access Token as the password. |
| **App not updating** | Run `touch tmp/restart.txt` in `~/sheriacentric.com/SHERIA-CENTRIC` or click **RESTART** in cPanel. |
| **Wrong directory** | Use `pwd` and `ls`; app must run from the folder that contains `app.py` (sheriacentric.com/SHERIA-CENTRIC). |
