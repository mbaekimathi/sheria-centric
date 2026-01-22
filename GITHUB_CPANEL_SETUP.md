# GitHub and cPanel Setup Guide

This guide will help you set up your repository on GitHub and connect it to cPanel for automated deployments.

## Prerequisites

- GitHub account
- cPanel hosting account with Git Version Control access
- Git installed on your local machine

## Step 1: Prepare Your Code for GitHub

### 1.1 Remove Sensitive Information

Make sure all sensitive information (passwords, API keys) are:
- Stored in `.env` file (which is in `.gitignore`)
- Not hardcoded in the source code
- Not included in documentation files

### 1.2 Verify .gitignore

Your `.gitignore` file should exclude:
- `.env` files
- `static/uploads/` directory
- Python cache files
- Log files
- Database files
- Archive files (`.zip`, etc.)

## Step 2: Set Up GitHub Repository

### 2.1 Initialize/Verify Git Repository

```bash
cd "c:\DEV OPS\ADVOCATES"
git status
```

### 2.2 Add GitHub Remote (if not already added)

```bash
# Add the remote repository
git remote add origin https://github.com/mbaekimathi/sheria-centric.git

# Or if remote already exists, update it:
git remote set-url origin https://github.com/mbaekimathi/sheria-centric.git

# Verify remote
git remote -v
```

### 2.3 Stage and Commit Your Changes

```bash
# Add all files (except those in .gitignore)
git add .

# Commit your changes
git commit -m "Initial commit - SHERIA CENTRIC application"

# Push to GitHub
git push -u origin main
```

If you get authentication errors, you may need to:
- Use a Personal Access Token instead of password
- Set up SSH keys
- Use GitHub Desktop or another Git client

## Step 3: Set Up cPanel Git Version Control

### 3.1 Access cPanel Git Version Control

1. Log in to your cPanel account
2. Navigate to **Git Version Control** (under Software section)
3. Click **Create** or **Clone** button

### 3.2 Clone Repository in cPanel

1. **Repository URL**: `https://github.com/mbaekimathi/sheria-centric.git`
2. **Repository Path**: Choose your desired path (e.g., `public_html/sheria-centric` or `sheria-centric`)
3. **Repository Name**: `sheria-centric`
4. Click **Create**

**Note**: If the repository is private, you'll need to:
- Use SSH URL: `git@github.com:mbaekimathi/sheria-centric.git`
- Set up SSH keys in cPanel

### 3.3 Set Up Auto-Deploy (Pull on Push)

#### Option A: Using cPanel Git Hooks (Recommended)

1. After cloning, you'll see the repository in the list
2. Click **Manage** next to your repository
3. Enable **Auto-Deploy** if available
4. Set up webhook or cron job for auto-pull

#### Option B: Using cPanel Cron Jobs

1. Go to **Cron Jobs** in cPanel
2. Create a new cron job:
   - **Command**: `cd /home/username/sheria-centric && git pull origin main`
   - **Minute**: `*/5` (every 5 minutes)
   - **Hour**: `*`
   - **Day**: `*`
   - **Month**: `*`
   - **Weekday**: `*`

#### Option C: Using GitHub Webhooks (Advanced)

1. In GitHub repository, go to **Settings** > **Webhooks** > **Add webhook**
2. **Payload URL**: `https://yourdomain.com/webhook/pull` (you'll need to create this endpoint)
3. **Content type**: `application/json`
4. **Events**: Select "Just the push event"
5. Create a webhook handler in your Flask app to handle the pull

### 3.4 Manual Pull in cPanel

To manually pull updates:
1. Go to **Git Version Control** in cPanel
2. Find your repository
3. Click **Pull** button

## Step 4: Configure Environment Variables in cPanel

### 4.1 Create .env File in cPanel

After cloning, create a `.env` file in your repository directory with:

```env
FLASK_ENV=production
SECRET_KEY=your-strong-production-secret-key-here
DB_HOST=localhost
DB_USER=your_cpanel_db_user
DB_PASSWORD=your_cpanel_db_password
DB_NAME=your_cpanel_db_name
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
OAUTHLIB_INSECURE_TRANSPORT=0
```

**Important**: The `.env` file is in `.gitignore`, so it won't be pushed to GitHub.

### 4.2 Set Up Application in cPanel

1. Create or configure your Python application in cPanel
2. Point it to your repository directory
3. Set the startup file to `app.py`
4. Configure WSGI if needed

## Step 5: Workflow - Push to Deploy

### Daily Workflow

1. **Make changes locally**
   ```bash
   cd "c:\DEV OPS\ADVOCATES"
   # Make your changes
   git add .
   git commit -m "Description of changes"
   git push origin main
   ```

2. **Pull in cPanel** (if auto-deploy is not set up):
   - Go to cPanel > Git Version Control
   - Click **Pull** on your repository
   - Or wait for cron job to run

3. **Restart application** (if needed):
   - Some changes require restarting the Python app in cPanel

## Step 6: Troubleshooting

### Authentication Issues

**Problem**: Can't push to GitHub
**Solution**: 
- Use Personal Access Token instead of password
- Set up SSH keys
- Configure Git credentials: `git config --global credential.helper store`

### cPanel Git Pull Fails

**Problem**: Git pull fails in cPanel
**Solution**:
- Check file permissions
- Ensure Git is installed in cPanel
- Check repository path is correct
- Verify SSH keys if using SSH URL

### Environment Variables Not Loading

**Problem**: Application can't find environment variables
**Solution**:
- Verify `.env` file exists in cPanel directory
- Check file permissions (should be 644 or 600)
- Ensure `.env` file has correct format (no spaces around `=`)
- Restart the Python application

### Files Not Updating

**Problem**: Changes pushed but not reflecting in cPanel
**Solution**:
- Ensure you pulled the latest changes
- Clear Python cache: `find . -type d -name __pycache__ -exec rm -r {} +`
- Restart the application
- Check file permissions

## Step 7: Best Practices

1. **Always test locally** before pushing
2. **Commit frequently** with meaningful messages
3. **Never commit sensitive data** (passwords, API keys)
4. **Use branches** for major features
5. **Backup database** before major deployments
6. **Monitor logs** after deployment
7. **Use `.env` file** for all configuration
8. **Document changes** in commit messages

## Additional Resources

- [GitHub Documentation](https://docs.github.com/)
- [cPanel Git Version Control Guide](https://docs.cpanel.net/cpanel/software/git-version-control/)
- [Python Flask Deployment](https://flask.palletsprojects.com/en/latest/deploying/)

## Quick Reference Commands

```bash
# Check status
git status

# Add all changes
git add .

# Commit changes
git commit -m "Your commit message"

# Push to GitHub
git push origin main

# Pull latest changes (in cPanel terminal)
git pull origin main

# Check remote
git remote -v

# View commit history
git log --oneline
```
