# Quick Start: Push to GitHub and Connect to cPanel

## Step 1: Prepare Your Code (DONE ✅)

- ✅ Sensitive passwords removed from DEPLOYMENT.md
- ✅ .gitignore updated to exclude zip files and sensitive data
- ✅ GitHub remote configured to: https://github.com/mbaekimathi/sheria-centric.git
- ✅ .gitattributes file created for consistent line endings

## Step 2: Push to GitHub

Run these commands in your terminal:

```powershell
# Navigate to project directory
cd "c:\DEV OPS\ADVOCATES"

# Check status
git status

# Add all files (zip files will be ignored due to .gitignore)
git add .

# Commit your changes
git commit -m "Complete SHERIA CENTRIC application - ready for production"

# Push to GitHub
git push -u origin main
```

**Note**: If you get authentication errors:
- GitHub no longer accepts passwords. Use a Personal Access Token instead
- Go to GitHub → Settings → Developer settings → Personal access tokens → Generate new token
- Use the token as your password when pushing

## Step 3: Set Up cPanel Git Integration

### Option A: Manual Pull (Simplest)

1. Log in to cPanel
2. Go to **Git Version Control**
3. Click **Create**
4. Enter:
   - **Repository URL**: `https://github.com/mbaekimathi/sheria-centric.git`
   - **Repository Path**: `sheria-centric` (or your preferred path)
   - **Repository Name**: `sheria-centric`
5. Click **Create**
6. After cloning, click **Pull** whenever you push updates to GitHub

### Option B: Auto-Pull with Cron Job

1. After cloning in cPanel, go to **Cron Jobs**
2. Create a new cron job:
   ```
   Command: cd /home/yourusername/sheria-centric && /usr/local/bin/git pull origin main
   Minute: */10
   Hour: *
   Day: *
   Month: *
   Weekday: *
   ```
   This will pull every 10 minutes automatically.

### Option C: SSH Setup (For Private Repos)

If your repository becomes private:
1. In cPanel, go to **SSH Access**
2. Generate SSH keys
3. Add the public key to GitHub (Settings → SSH and GPG keys)
4. Use SSH URL in cPanel: `git@github.com:mbaekimathi/sheria-centric.git`

## Step 4: Configure Environment in cPanel

After cloning in cPanel:

1. Create `.env` file in the repository directory:
   ```env
   FLASK_ENV=production
   SECRET_KEY=your-strong-secret-key-here
   DB_HOST=localhost
   DB_USER=your_cpanel_db_user
   DB_PASSWORD=your_cpanel_db_password
   DB_NAME=your_cpanel_db_name
   GOOGLE_CLIENT_ID=your-google-client-id
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   OAUTHLIB_INSECURE_TRANSPORT=0
   ```

2. Set file permissions (600 recommended):
   ```bash
   chmod 600 .env
   ```

## Step 5: Daily Workflow

**To update your cPanel site:**

1. Make changes locally
2. Commit and push:
   ```bash
   git add .
   git commit -m "Your change description"
   git push origin main
   ```
3. Pull in cPanel (if not using cron):
   - Go to Git Version Control → Click **Pull**
4. Restart application if needed

## Troubleshooting

### Can't push to GitHub?
- Use Personal Access Token instead of password
- Or set up SSH keys

### cPanel Git not working?
- Check if Git is enabled in cPanel
- Verify repository path
- Check file permissions

### Changes not showing?
- Ensure you pulled in cPanel
- Restart Python application
- Clear Python cache

## Files Created for You

- `GITHUB_CPANEL_SETUP.md` - Detailed setup guide
- `QUICK_START_GITHUB.md` - This file (quick reference)
- `.gitattributes` - Line ending configuration
- Updated `.gitignore` - Excludes sensitive files and archives
- Updated `DEPLOYMENT.md` - Removed sensitive credentials

## Next Steps

1. ✅ Push your code to GitHub
2. ✅ Clone repository in cPanel
3. ✅ Set up auto-pull (optional but recommended)
4. ✅ Create `.env` file in cPanel
5. ✅ Configure your Python application
6. ✅ Test your deployment

For detailed instructions, see `GITHUB_CPANEL_SETUP.md`
