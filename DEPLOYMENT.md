# Deploy from GitHub using cPanel Terminal

Use **only** cPanel’s **Terminal**. The app lives in the **SHERIA-CENTRIC** folder. When your prompt shows:

```text
(SHERIA-CENTRIC:3.13) [baunilaw@rs3 SHERIA-CENTRIC]$
```

you’re in the right directory and can run the update commands below.

---

## Every time you want to update (pull latest code)

1. Open **Terminal** in cPanel.
2. Go to the app folder (if you’re not already there):

```bash
cd ~/SHERIA-CENTRIC
```

3. Pull the latest code from GitHub:

```bash
git pull origin main
```

4. Restart the app so changes load:

```bash
mkdir -p tmp
touch tmp/restart.txt
```

Or click **RESTART** in cPanel for the Python app.

**If you’re already in SHERIA-CENTRIC** (prompt shows `[baunilaw@rs3 SHERIA-CENTRIC]$`), you only need:

```bash
git pull origin main
mkdir -p tmp
touch tmp/restart.txt
```

---

## One-command deploy (when you’re in SHERIA-CENTRIC)

From the **SHERIA-CENTRIC** folder:

```bash
./deploy.sh
```

This runs `git pull origin main` and `touch tmp/restart.txt`. Then use cPanel **RESTART** if needed.

If you get “permission denied”, run once: `chmod +x deploy.sh`

---

## First-time setup (if you still need to clone)

1. Open **Terminal** in cPanel.
2. Go to your home directory and clone into **SHERIA-CENTRIC**:

```bash
cd ~
git clone https://github.com/mbaekimathi/sheria-centric.git SHERIA-CENTRIC
cd SHERIA-CENTRIC
chmod +x deploy.sh
```

3. In cPanel **Setup Python App**, set **Application root** to **SHERIA-CENTRIC**. Startup file: **app.py**, Entry point: **app**. Save.
4. Click **Run Pip Install**, then **RESTART**.

---

## Quick reference (cPanel Terminal only)

| Task | Commands |
|------|----------|
| **Go to app folder** | `cd ~/SHERIA-CENTRIC` |
| **Update code** | `git pull origin main` (run from inside SHERIA-CENTRIC) |
| **Restart app** | `touch tmp/restart.txt` or cPanel **RESTART** |
| **All-in-one** | `cd ~/SHERIA-CENTRIC` then `./deploy.sh` |

- **App folder**: SHERIA-CENTRIC (prompt: `[baunilaw@rs3 SHERIA-CENTRIC]$`, Python env: SHERIA-CENTRIC:3.13)
- **Repo**: https://github.com/mbaekimathi/sheria-centric  
- **Branch**: `main`

---

## Troubleshooting

| Issue | Fix in Terminal |
|-------|------------------|
| **Not in app folder** | Run `cd ~/SHERIA-CENTRIC` then `pwd` — you should see a path ending in SHERIA-CENTRIC. |
| **Permission denied (deploy.sh)** | Run `chmod +x deploy.sh` inside SHERIA-CENTRIC. |
| **Pull asks for password** | Use a GitHub Personal Access Token as the password. |
| **App not updating** | Run `touch tmp/restart.txt` inside SHERIA-CENTRIC or click **RESTART** in cPanel. |
