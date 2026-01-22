# Google Drive OAuth Setup Guide

## Step 1: Go to Google Cloud Console

1. Visit: https://console.cloud.google.com/
2. Select your project (or create a new one)

## Step 2: Enable Required APIs

1. Go to **APIs & Services** > **Library**
2. Search for and **Enable** the following APIs:
   - **Google Drive API** (Required for file uploads)
   - **Google+ API** or **People API** (For user info)

## Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** > **OAuth consent screen**
2. Choose **External** (unless you have a Google Workspace account)
3. Fill in the required information:
   - **App name**: SHERIA CENTRIC
   - **User support email**: Your email
   - **Developer contact information**: Your email
4. Click **Save and Continue**
5. **Scopes** - Click **Add or Remove Scopes** and add:
   - `https://www.googleapis.com/auth/drive.file`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/userinfo.profile`
6. Click **Save and Continue**
7. **Test users** (for testing):
   - Add your email address as a test user
   - Click **Save and Continue**
8. **Summary** - Review and click **Back to Dashboard**

## Step 4: Create OAuth 2.0 Credentials

1. Go to **APIs & Services** > **Credentials**
2. Click **Create Credentials** > **OAuth client ID**
3. **Application type**: Select **Web application**
4. **Name**: SHERIA CENTRIC - Google Drive
5. **Authorized JavaScript origins** (Add these):
   ```
   http://127.0.0.1:5000
   http://localhost:5000
   ```
   (Add your production domain if applicable)

6. **Authorized redirect URIs** (Add these - VERY IMPORTANT):
   ```
   http://127.0.0.1:5000/api/auth/google-drive/callback
   http://localhost:5000/api/auth/google-drive/callback
   ```
   (Add your production callback URL if applicable)

7. Click **Create**
8. **Copy the Client ID and Client Secret**

## Step 5: Add Credentials to .env File

Add these to your `.env` file:

```env
GOOGLE_CLIENT_ID=your-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret-here
```

## Step 6: Important Notes

### For Local Development:
- Make sure `OAUTHLIB_INSECURE_TRANSPORT=1` is in your `.env` file
- Use `http://127.0.0.1:5000` (not `localhost`)

### For Production:
- Remove `OAUTHLIB_INSECURE_TRANSPORT` or set it to `0`
- Use HTTPS URLs in redirect URIs
- Add your production domain to authorized origins and redirect URIs

## Common Issues:

### "Access blocked: Authorization Error"
- **Check**: Redirect URI matches exactly (including http/https, port, path)
- **Check**: OAuth consent screen is published or you're added as a test user
- **Check**: Google Drive API is enabled

### "redirect_uri_mismatch"
- **Fix**: Ensure the redirect URI in Google Console matches exactly:
  - `http://127.0.0.1:5000/api/auth/google-drive/callback`
  - No trailing slashes
  - Exact match including protocol (http vs https)

### "invalid_client"
- **Fix**: Verify Client ID and Client Secret are correct in `.env` file
- **Fix**: Make sure you're using the correct OAuth client (Web application type)

### "Error 403: access_denied" - "has not completed the Google verification process"
- **Cause**: Your app is in "Testing" mode and your email is not added as a test user
- **Fix**: 
  1. Go to **APIs & Services** > **OAuth consent screen**
  2. Scroll down to **Test users** section
  3. Click **+ ADD USERS**
  4. Enter your email address: `kimathimbae1@gmail.com`
  5. Click **ADD**
  6. Click **SAVE AND CONTINUE**
  7. Try connecting again
- **Alternative**: If you want to allow all users (for production), you can publish the app, but this requires Google verification for sensitive scopes

## Testing:

1. Restart your Flask application
2. Go to: `http://127.0.0.1:5000/documents_settings`
3. Click "Connect Google Drive Account"
4. You should see Google's account picker
5. Select an account and authorize

