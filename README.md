# CASELY - Modern Advocates Firm Management System

A modern, responsive web application for managing advocates firm employees and clients built with Flask, PyMySQL, and Tailwind CSS.

## Features

- **Modern UI**: Beautiful, responsive design with professional theme
- **Employee Authentication**: Secure login with 6-digit code and password
- **Client Authentication**: Google OAuth integration for client portal
- **Employee Registration**: Complete signup process with profile picture upload
- **Client Registration**: Phone number and document verification (ID for individuals, CR-12 for corporate)
- **Role Management**: Support for multiple roles (Firm Administrator, Managing Partner, Finance Office, Associate Advocate, Clerk, IT Support, Employee)
- **Status Management**: Employee status tracking (Active, Pending Approval, Suspended)
- **Company Integration**: Display company information (e.g., BAUNI LAW GROUP)
- **Responsive Design**: Works seamlessly on mobile, tablet, and desktop devices
- **Document Management**: Upload and manage employee and client documents
- **Onboarding System**: Complete employee onboarding workflow

## Requirements

- Python 3.7+
- MySQL Server
- pip (Python package manager)

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone <your-repository-url>
   cd ADVOCATES
   ```

2. **Create and activate virtual environment:**
   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Set up MySQL Database:**
   - Create database: `casely_db` (or configure in `.env`)
   - The application will auto-initialize the schema on first run

6. **Run the application:**
   ```bash
   python app.py
   ```

7. **Access the application:**
   - Open your browser and navigate to `http://localhost:5000`
   - Employee Login: Use 6-digit code and password
   - Client Login: Sign in with Google

## Configuration

The application uses environment variables for configuration. Copy `.env.example` to `.env` and configure:

- **Database**: Set `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- **Google OAuth**: Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
- **Secret Key**: Set `SECRET_KEY` for session security

See `.env.example` for all available options.

## Deployment

For detailed deployment instructions, see [DEPLOYMENT.md](DEPLOYMENT.md)

### Quick Deployment Checklist

- [ ] Set up environment variables in `.env`
- [ ] Configure database credentials
- [ ] Set up Google OAuth credentials
- [ ] Ensure `OAUTHLIB_INSECURE_TRANSPORT=0` in production
- [ ] Configure HTTPS
- [ ] Set proper file permissions

## Usage

1. **Employee Registration:**
   - Click on the CASELY icon/logo at the top left
   - Click "Sign Up Here" on the login page
   - Fill in all required fields:
     - Full Name
     - Phone Number
     - Work Email
     - 6-Digit Employee Code
     - Password (minimum 6 characters)
     - Confirm Password
     - Profile Picture (optional)
     - Accept Terms and Conditions
   - Submit the form
   - Account will be set to "Pending Approval" status

2. **Employee Login:**
   - Enter your 6-digit employee code
   - Enter your password
   - Click Login
   - Note: Only "Active" accounts can log in

3. **Dashboard:**
   - View your employee information
   - See your current status
   - View company information

## Project Structure

```
CASELY/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── templates/            # HTML templates
│   ├── base.html         # Base template with header/footer
│   ├── login.html        # Login page
│   ├── signup.html       # Registration page
│   └── dashboard.html    # Employee dashboard
└── static/              # Static files
    └── uploads/         # Uploaded files
        └── profile_pictures/  # Employee profile pictures
```

## Database Schema

### Employees Table
- `id`: Primary key
- `full_name`: Employee's full name
- `phone_number`: Contact number
- `work_email`: Work email (unique)
- `employee_code`: 6-digit code (unique)
- `password_hash`: Hashed password
- `profile_picture`: Profile picture filename
- `role`: Employee role (enum)
- `status`: Account status (enum)
- `company_name`: Company name (default: BAUNI LAW GROUP)
- `created_at`: Account creation timestamp
- `updated_at`: Last update timestamp

## Security Features

- Password hashing using Werkzeug
- Secure file upload handling
- Session management
- Input validation and sanitization
- SQL injection protection via parameterized queries

## Customization

### Theme Colors
The green and orange theme can be customized in `templates/base.html`:
- Green: `#10b981` (primary), `#059669` (dark)
- Orange: `#f97316` (primary), `#ea580c` (dark)

### Company Name
Default company name is "BAUNI LAW GROUP". It can be changed in:
- Database default value
- Session variable
- Display templates

## License

This project is proprietary software for CASELY advocates firm management.

