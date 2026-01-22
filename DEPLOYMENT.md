# Deployment Guide

This guide covers deploying the SHERIA CENTRIC application to production using Git version control.

## Prerequisites

- Python 3.7+
- MySQL Server
- Git
- cPanel hosting (for production deployment)

## Initial Setup

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd ADVOCATES
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Environment Configuration

Copy the example environment file and configure it:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

**For Local Development:**
```env
FLASK_ENV=development
SECRET_KEY=your-generated-secret-key
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=
DB_NAME=sheria_centric_db
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
OAUTHLIB_INSECURE_TRANSPORT=1
```

**For cPanel Production:**
```env
FLASK_ENV=production
SECRET_KEY=your-strong-production-secret-key
DB_HOST=localhost
DB_USER=your_cpanel_db_user
DB_PASSWORD=your_cpanel_db_password
DB_NAME=your_cpanel_db_name
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
OAUTHLIB_INSECURE_TRANSPORT=0
```

## Database Setup

### Local Development

1. Create MySQL database:
```sql
CREATE DATABASE sheria_centric_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

2. Run the application - it will auto-initialize the database schema:
```bash
python app.py
```

### Production (cPanel)

1. Access cPanel MySQL Databases
2. Create database: `baunilaw_sheria_centric`
3. Create user: `baunilaw_sheria_centric`
4. Grant all privileges to the user
5. The application will auto-initialize the schema on first run

## Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable Google+ API
4. Create OAuth 2.0 credentials
5. Add authorized JavaScript origins:
   - Local: `http://localhost:5000`
   - Production: `https://yourdomain.com`
6. Add authorized redirect URIs:
   - Local: `http://localhost:5000/callback`
   - Production: `https://yourdomain.com/callback`
7. Copy Client ID and Secret to `.env` file

## Running the Application

### Local Development

```bash
python app.py
```

Access at: `http://localhost:5000`

### Production (cPanel)

1. Upload files via Git or cPanel File Manager
2. Set up Python application in cPanel
3. Configure environment variables in cPanel
4. Set up WSGI/passenger configuration
5. Point domain to application directory

## Git Workflow

### Initial Commit

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <your-repository-url>
git push -u origin main
```

### Making Changes

```bash
# Create a new branch
git checkout -b feature/your-feature-name

# Make your changes
# ...

# Stage changes
git add .

# Commit changes
git commit -m "Description of changes"

# Push to remote
git push origin feature/your-feature-name

# Create pull request on GitHub
```

### Updating Production

```bash
# SSH into your server or use cPanel Git Version Control
cd /path/to/application
git pull origin main

# Restart application (if using process manager)
# Or reload via cPanel
```

## Security Checklist

- [ ] `.env` file is in `.gitignore` (never commit secrets)
- [ ] `SECRET_KEY` is strong and unique
- [ ] `OAUTHLIB_INSECURE_TRANSPORT=0` in production
- [ ] Database credentials are secure
- [ ] Google OAuth credentials are configured
- [ ] HTTPS is enabled in production
- [ ] File uploads directory has proper permissions
- [ ] Database user has minimal required privileges

## File Permissions (Linux/cPanel)

```bash
# Set proper permissions
chmod 755 app.py
chmod -R 755 templates/
chmod -R 755 static/
chmod -R 775 static/uploads/
```

## Troubleshooting

### Database Connection Issues

- Verify database credentials in `.env`
- Check MySQL service is running
- Verify user has proper permissions
- Check firewall settings

### OAuth Issues

- Verify `OAUTHLIB_INSECURE_TRANSPORT` is set correctly
- Check Google OAuth redirect URIs match exactly
- Verify Client ID and Secret are correct
- Ensure HTTPS is used in production

### Upload Issues

- Check `static/uploads/` directory exists
- Verify directory permissions (775)
- Check file size limits in `app.py`

## Environment Variables Reference

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `FLASK_ENV` | Environment (development/production) | No | development |
| `SECRET_KEY` | Flask secret key | Yes | Auto-generated |
| `DB_HOST` | Database host | Yes | localhost |
| `DB_USER` | Database username | Yes | root |
| `DB_PASSWORD` | Database password | Yes | (empty) |
| `DB_NAME` | Database name | Yes | sheria_centric_db |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID | Yes | - |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Secret | Yes | - |
| `OAUTHLIB_INSECURE_TRANSPORT` | Allow HTTP for OAuth (dev only) | No | 0 |

## Support

For issues or questions, please contact the development team.


