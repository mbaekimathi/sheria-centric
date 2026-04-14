from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, send_file
import pymysql
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import hashlib
import uuid
from PIL import Image, ImageEnhance, ImageFilter
import base64
from io import BytesIO
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as google_requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient import http as googleapiclient_http
import json
import requests
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from datetime import datetime
import re
import sys

app = Flask(__name__)

# Some hosted environments (e.g. cPanel/Passenger) may default stdout/stderr to ASCII.
# Reconfigure to UTF-8 so any Unicode in logs (e.g. checkmarks) won't crash startup.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, will use environment variables directly

# Secret key from environment variable or generate one (must be fixed in production)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# Production base URL for OAuth redirects (set on hosted e.g. https://sheriacentric.com)
# Ensures redirect_uri matches Google Console and session cookie works across redirect
APP_BASE_URL = (os.environ.get('APP_BASE_URL') or '').strip().rstrip('/') or None
if APP_BASE_URL:
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PREFERRED_URL_SCHEME'] = 'https'

# Trust X-Forwarded-* when behind proxy (cPanel/Passenger) so url_for(_external=True) is correct
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0)
except ImportError:
    pass

# Kenya court hierarchy (for case registration / edit dropdowns)
COURT_RANK_OPTIONS = [
    "Supreme Court",
    "Court of Appeal",
    "High Court",
    "Environment and Land Court",
    "Employment and Labour Relations Court",
    "Magistrates' Courts",
    "Kadhi's Courts",
    "Courts Martial",
    "Small Claims Court",
    "Tribunals",
]


def validate_court_rank(value):
    """Return a valid court rank string or None."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    return v if v in COURT_RANK_OPTIONS else None


def get_public_base_url():
    """Public base URL for building external redirects (prefers APP_BASE_URL)."""
    if APP_BASE_URL:
        return APP_BASE_URL
    try:
        return request.url_root.rstrip('/')
    except Exception:
        return ''

def get_google_drive_redirect_uri():
    """Redirect URI for Google Drive OAuth; use APP_BASE_URL when hosted so it matches Google Console."""
    if APP_BASE_URL:
        return APP_BASE_URL + '/api/auth/google-drive/callback'
    return url_for('google_drive_callback', _external=True)

def verify_google_id_token(id_token_jwt: str):
    """
    Verify Google ID token with small clock-skew tolerance.
    Hosted environments can occasionally drift by ~1s which triggers 'Token used too early'.
    """
    request_session = google_requests.Request()
    try:
        return id_token.verify_oauth2_token(
            id_token_jwt,
            request_session,
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except TypeError:
        # Older google-auth versions may not support clock_skew_in_seconds.
        return id_token.verify_oauth2_token(id_token_jwt, request_session, GOOGLE_CLIENT_ID)

# Configuration
UPLOAD_FOLDER = 'static/uploads/profile_pictures'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_DOCUMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png'}
ALLOWED_ID_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Allow insecure transport for OAuth only in development
# Set OAUTHLIB_INSECURE_TRANSPORT=1 in .env for local development only
if os.environ.get('FLASK_ENV') == 'development' or os.environ.get('OAUTHLIB_INSECURE_TRANSPORT') == '1':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    print("WARNING: OAuth insecure transport enabled (development mode only)")

# Google OAuth Configuration from environment variables
# IMPORTANT: Set these in .env file - never commit secrets to git
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_DISCOVERY_URL = os.environ.get('GOOGLE_DISCOVERY_URL', "https://accounts.google.com/.well-known/openid-configuration")

# OAuth 2.0 scopes
SCOPES = ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']

# Database configuration - Auto-detect environment
import socket

def get_db_config():
    """Return DB config from environment variables only."""
    def build_env_db_config():
        return {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'user': os.environ.get('DB_USER', ''),
            'password': os.environ.get('DB_PASSWORD', ''),
            'database': os.environ.get('DB_NAME', ''),
            'charset': 'utf8mb4'
        }

    db_env = os.environ.get('DB_ENV', '').lower()
    
    if db_env == 'cpanel':
        print("[OK] Using cPanel database configuration (from environment)")
        return build_env_db_config()
    
    if db_env == 'local':
        print("[OK] Using local database configuration (from environment)")
        return build_env_db_config()
    
    db_host = os.environ.get('DB_HOST', 'localhost')
    db_user = os.environ.get('DB_USER', '')
    db_password = os.environ.get('DB_PASSWORD', '')
    db_name = os.environ.get('DB_NAME', '')
    
    if db_user and db_name:
        print("[OK] Using database configuration from environment variables")
        password_display = '*' * len(db_password) if db_password else '(empty)'
        print(f"  Host: {db_host}")
        print(f"  User: {db_user}")
        print(f"  Database: {db_name}")
        print(f"  Password: {password_display} ({len(db_password)} chars)")
        return {
            'host': db_host,
            'user': db_user,
            'password': db_password,
            'database': db_name,
            'charset': 'utf8mb4'
        }
    
    print("[WARNING] DB credentials missing in environment. Set DB_USER and DB_NAME in .env.")
    return build_env_db_config()

# Initialize DB_CONFIG
DB_CONFIG = get_db_config()

# Debug function to test database connection (can be called manually)
def test_db_connection():
    """Test database connection with current configuration"""
    try:
        print(f"\n[DEBUG] Testing database connection...")
        print(f"  Host: {DB_CONFIG['host']}")
        print(f"  User: {DB_CONFIG['user']}")
        print(f"  Database: {DB_CONFIG['database']}")
        print(f"  Password: {'*' * len(DB_CONFIG['password']) if DB_CONFIG['password'] else '(empty)'}")
        
        connection = pymysql.connect(**DB_CONFIG)
        connection.close()
        print("[OK] Database connection successful!\n")
        return True
    except pymysql.Error as e:
        print(f"[ERROR] Database connection failed: {e}\n")
        print("Troubleshooting steps:")
        print("1. Verify DB_PASSWORD in environment variables matches cPanel MySQL password")
        print("2. Check that database user has proper permissions in cPanel")
        print("3. Ensure database and user exist in cPanel MySQL Databases")
        print("4. Try resetting the database password in cPanel and update environment variable\n")
        return False

# Schema version for migrations
SCHEMA_VERSION = 15

def get_db_connection(use_database=True):
    """Create and return database connection"""
    try:
        config = DB_CONFIG.copy()
        if not use_database:
            config.pop('database', None)
        connection = pymysql.connect(**config)
        return connection
    except pymysql.Error as e:
        error_code, error_msg = e.args
        print(f"Database connection error: ({error_code}, \"{error_msg}\")")
        
        # Provide helpful troubleshooting for common errors
        if error_code == 1045:  # Access denied
            print("\n[TROUBLESHOOTING] Access Denied Error:")
            print("1. Verify DB_PASSWORD in cPanel environment variables matches MySQL password exactly")
            print("   Current password length: {} characters".format(len(config.get('password', ''))))
            print("2. Check for special characters - they may need to be escaped or quoted")
            print("3. Ensure user '{}' exists and has permissions".format(config.get('user', 'unknown')))
            print("4. In cPanel MySQL Databases, verify:")
            print("   - User exists: {}".format(config.get('user', 'unknown')))
            print("   - Database exists: {}".format(DB_CONFIG.get('database', 'unknown')))
            print("   - User is linked to database with ALL PRIVILEGES")
            print("5. Try resetting the MySQL password in cPanel and update DB_PASSWORD")
            print("6. Common password issues:")
            print("   - Extra spaces before/after password")
            print("   - Case sensitivity (passwords are case-sensitive)")
            print("   - Special characters not properly escaped")
        elif error_code == 1049:  # Unknown database
            print("\n[TROUBLESHOOTING] Database Not Found:")
            print("1. Verify DB_NAME in environment variables: {}".format(DB_CONFIG.get('database', 'unknown')))
            print("2. Create the database in cPanel MySQL Databases if it doesn't exist")
        elif error_code == 2003:  # Can't connect to server
            print("\n[TROUBLESHOOTING] Connection Failed:")
            print("1. Verify DB_HOST is correct (usually 'localhost' for cPanel)")
            print("2. Check if MySQL service is running")
        
        return None
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def database_exists():
    """Check if database exists"""
    try:
        connection = get_db_connection(use_database=False)
        if not connection:
            return False
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES LIKE %s", (DB_CONFIG['database'],))
            result = cursor.fetchone()
            return result is not None
    except Exception as e:
        print(f"Error checking database existence: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_database():
    """Create database if it doesn't exist"""
    try:
        connection = get_db_connection(use_database=False)
        if not connection:
            return False
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            connection.commit()
            print(f"[OK] Database '{DB_CONFIG['database']}' checked/created")
            return True
    except Exception as e:
        print(f"Error creating database: {e}")
        return False
    finally:
        if connection:
            connection.close()

def table_exists(table_name):
    """Check if a table exists"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = %s 
                AND table_name = %s
            """, (DB_CONFIG['database'], table_name))
            result = cursor.fetchone()
            return result[0] > 0
    except Exception as e:
        print(f"Error checking table existence: {e}")
        return False
    finally:
        if connection:
            connection.close()

def ensure_case_proceeding_advocates_table(cursor, connection=None):
    """Ensure advocates table exists (fixes DBs that missed migration 15)."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS case_proceeding_advocates (
                id INT AUTO_INCREMENT PRIMARY KEY,
                proceeding_id INT NOT NULL,
                advocate_name VARCHAR(255) NOT NULL,
                remarks TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (proceeding_id) REFERENCES case_proceedings(id) ON DELETE CASCADE,
                INDEX idx_proceeding_id (proceeding_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        if connection:
            connection.commit()
    except Exception as e:
        print(f"[WARNING] ensure_case_proceeding_advocates_table: {e}")


def ensure_google_drive_oauth_pending_table(cursor, connection=None):
    """Store OAuth state server-side so the Drive popup callback works when the session cookie is not sent."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS google_drive_oauth_pending (
                state VARCHAR(255) NOT NULL PRIMARY KEY,
                employee_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_gdrive_oauth_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        if connection:
            connection.commit()
    except Exception as e:
        print(f"[WARNING] ensure_google_drive_oauth_pending_table: {e}")


def reconcile_additive_schema(cursor, connection=None):
    """Run additive DDL on every app startup (safe after git pull / cPanel deploy).

    Numbered migrations bump schema_version, but many features also use
    CREATE TABLE IF NOT EXISTS / ALTER ADD COLUMN in ensure_* helpers.
    Calling those here guarantees production DBs stay aligned with the code
    even when schema_version is already at SCHEMA_VERSION.
    """
    try:
        ensure_task_management_table(cursor, connection)
        ensure_case_proceeding_advocates_table(cursor, connection)
        ensure_google_drive_oauth_pending_table(cursor, connection)
    except Exception as e:
        print(f"[WARNING] reconcile_additive_schema: {e}")


def verify_core_tables_present():
    """Log a concise check that expected tables exist after initialization."""
    expected = (
        'schema_version',
        'company_settings',
        'employees',
        'clients',
        'cases',
        'case_proceedings',
        'task_management',
        'matters',
    )
    connection = get_db_connection()
    if not connection:
        print("[WARNING] Could not verify core tables (no DB connection)")
        return
    try:
        placeholders = ','.join(['%s'] * len(expected))
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name IN ({placeholders})
                """,
                (DB_CONFIG['database'],) + expected,
            )
            found = {row[0] for row in cursor.fetchall()}
        missing = [t for t in expected if t not in found]
        if missing:
            print(f"[WARNING] After init, these tables are missing: {', '.join(missing)}")
        else:
            print("[OK] Core tables verified")
    except Exception as e:
        print(f"[WARNING] Core table verification failed: {e}")
    finally:
        connection.close()


def ensure_task_management_table(cursor, connection=None):
    """Ensure task management table exists for case/matter tasks."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_management (
                id INT AUTO_INCREMENT PRIMARY KEY,
                task_type ENUM('case', 'matter') NOT NULL,
                linked_id INT NOT NULL,
                task_title VARCHAR(255) NOT NULL,
                task_description TEXT,
                due_at DATETIME NOT NULL,
                reminder_intervals VARCHAR(255),
                assigned_to_id INT NULL,
                assigned_to_name VARCHAR(255) NULL,
                allow_view_case_details TINYINT(1) DEFAULT 1,
                allow_edit_case_details TINYINT(1) DEFAULT 1,
                allow_view_case_documents TINYINT(1) DEFAULT 1,
                allow_upload_case_documents TINYINT(1) DEFAULT 1,
                allow_download_case_documents TINYINT(1) DEFAULT 1,
                task_status ENUM('Pending', 'In Progress', 'Submitted', 'Completed', 'Cancelled') DEFAULT 'Pending',
                created_by_id INT NOT NULL,
                created_by_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_task_type_linked (task_type, linked_id),
                INDEX idx_due_at (due_at),
                INDEX idx_created_by (created_by_id),
                INDEX idx_assigned_to_id (assigned_to_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        # Keep status enum compatible with newly added "Submitted" state.
        try:
            cursor.execute("""
                ALTER TABLE task_management
                MODIFY COLUMN task_status ENUM('Pending', 'In Progress', 'Submitted', 'Completed', 'Cancelled') DEFAULT 'Pending'
            """)
        except Exception as enum_err:
            print(f"[WARNING] ensure_task_management_table enum update: {enum_err}")
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN assigned_to_id INT NULL")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN assigned_to_name VARCHAR(255) NULL")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD INDEX idx_assigned_to_id (assigned_to_id)")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN allow_view_case_details TINYINT(1) DEFAULT 1")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN allow_edit_case_details TINYINT(1) DEFAULT 1")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN allow_view_case_documents TINYINT(1) DEFAULT 1")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN allow_upload_case_documents TINYINT(1) DEFAULT 1")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE task_management ADD COLUMN allow_download_case_documents TINYINT(1) DEFAULT 1")
        except Exception:
            pass
        if connection:
            connection.commit()
    except Exception as e:
        print(f"[WARNING] ensure_task_management_table: {e}")

def has_active_case_task_access(cursor, case_id, employee_id, task_id=None, permission_key=None):
    """Return True when employee has an active case task allocation for this case."""
    try:
        sql = """
            SELECT 1
            FROM task_management t
            WHERE t.task_type = 'case'
              AND t.linked_id = %s
              AND t.assigned_to_id = %s
              AND t.task_status IN ('Pending', 'In Progress')
        """
        params = [case_id, employee_id]
        if task_id:
            sql += " AND t.id = %s"
            params.append(task_id)
        permission_map = {
            'view': 'allow_view_case_details',
            'edit': 'allow_edit_case_details',
            'view_documents': 'allow_view_case_documents',
            'upload_documents': 'allow_upload_case_documents',
            'download': 'allow_download_case_documents',
        }
        if permission_key in permission_map:
            sql += f" AND COALESCE(t.{permission_map[permission_key]}, 1) = 1"
        sql += " LIMIT 1"
        cursor.execute(sql, tuple(params))
        return bool(cursor.fetchone())
    except Exception:
        return False

def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.columns 
                WHERE table_schema = %s 
                AND table_name = %s 
                AND column_name = %s
            """, (DB_CONFIG['database'], table_name, column_name))
            result = cursor.fetchone()
            return result[0] > 0
    except Exception as e:
        print(f"Error checking column existence: {e}")
        return False
    finally:
        if connection:
            connection.close()

def get_schema_version():
    """Get current schema version from database"""
    try:
        connection = get_db_connection()
        if not connection:
            return 0
        if not table_exists('schema_version'):
            return 0
        with connection.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1")
            result = cursor.fetchone()
            return result[0] if result else 0
    except Exception as e:
        print(f"Error getting schema version: {e}")
        return 0
    finally:
        if connection:
            connection.close()

def update_schema_version(version):
    """Update schema version in database"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO schema_version (version, updated_at) 
                VALUES (%s, NOW())
            """, (version,))
            connection.commit()
            return True
    except Exception as e:
        print(f"Error updating schema version: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_schema_version_table():
    """Create schema_version table to track migrations"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    version INT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.commit()
            print("[OK] Schema version table checked/created")
            return True
    except Exception as e:
        print(f"Error creating schema_version table: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_company_settings_table():
    """Create company_settings table"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            if not table_exists('company_settings'):
                cursor.execute("""
                    CREATE TABLE company_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        company_name VARCHAR(255) NOT NULL DEFAULT 'BAUNI LAW GROUP',
                        email VARCHAR(255),
                        contact_number VARCHAR(20),
                        whatsapp_number VARCHAR(20),
                        tiktok_link VARCHAR(500),
                        instagram_link VARCHAR(500),
                        fb_link VARCHAR(500),
                        location_name VARCHAR(255),
                        longitude DECIMAL(10, 8),
                        latitude DECIMAL(10, 8),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Company settings table created")
                
                # Insert default company settings
                cursor.execute("""
                    INSERT INTO company_settings 
                    (company_name, email, contact_number, whatsapp_number, location_name)
                    VALUES ('BAUNI LAW GROUP', NULL, NULL, NULL, NULL)
                """)
                connection.commit()
                print("[OK] Default company settings inserted")
            else:
                print("[OK] Company settings table already exists")
                # Check and add missing columns
                columns_to_check = [
                    ('company_name', "VARCHAR(255) NOT NULL DEFAULT 'BAUNI LAW GROUP'"),
                    ('company_logo', 'VARCHAR(500)'),
                    ('company_tagline', 'VARCHAR(500)'),
                    ('registration_number', 'VARCHAR(100)'),
                    ('tax_pin_vat_number', 'VARCHAR(50)'),
                    ('year_established', 'VARCHAR(10)'),
                    ('email', 'VARCHAR(255)'),
                    ('contact_number', 'VARCHAR(20)'),
                    ('whatsapp_number', 'VARCHAR(20)'),
                    ('alternative_phone', 'VARCHAR(20)'),
                    ('customer_support_email', 'VARCHAR(255)'),
                    ('country', 'VARCHAR(100)'),
                    ('county_state', 'VARCHAR(100)'),
                    ('city_town', 'VARCHAR(100)'),
                    ('street_building', 'VARCHAR(255)'),
                    ('office_number_floor', 'VARCHAR(50)'),
                    ('postal_address', 'VARCHAR(255)'),
                    ('postal_code', 'VARCHAR(20)'),
                    ('opening_time', 'VARCHAR(20)'),
                    ('closing_time', 'VARCHAR(20)'),
                    ('working_days', 'VARCHAR(100)'),
                    ('public_holiday_status', 'VARCHAR(255)'),
                    ('public_holiday_open_time', 'VARCHAR(20)'),
                    ('public_holiday_close_time', 'VARCHAR(20)'),
                    ('website_url', 'VARCHAR(500)'),
                    ('fb_link', 'VARCHAR(500)'),
                    ('linkedin_link', 'VARCHAR(500)'),
                    ('twitter_link', 'VARCHAR(500)'),
                    ('instagram_link', 'VARCHAR(500)'),
                    ('tiktok_link', 'VARCHAR(500)'),
                    ('law_society_reg_number', 'VARCHAR(100)'),
                    ('practicing_certificate_number', 'VARCHAR(100)'),
                    ('lead_advocate_name', 'VARCHAR(255)'),
                    ('bar_association_membership', 'VARCHAR(255)'),
                    ('default_letterhead', 'VARCHAR(500)'),
                    ('document_footer_text', 'TEXT'),
                    ('stamp_seal_upload', 'VARCHAR(500)'),
                    ('default_signature_documents', 'VARCHAR(500)'),
                    ('currency', 'VARCHAR(10)'),
                    ('invoice_prefix', 'VARCHAR(20)'),
                    ('payment_terms', 'VARCHAR(100)'),
                    ('bank_account_details', 'TEXT'),
                    ('mobile_payment_mpesa', 'VARCHAR(255)'),
                    ('send_email_notifications', 'TINYINT(1) DEFAULT 1'),
                    ('send_sms_notifications', 'TINYINT(1) DEFAULT 0'),
                    ('whatsapp_notifications', 'TINYINT(1) DEFAULT 0'),
                    ('court_date_reminders', 'TINYINT(1) DEFAULT 1'),
                    ('primary_brand_color', 'VARCHAR(20)'),
                    ('secondary_color', 'VARCHAR(20)'),
                    ('favicon', 'VARCHAR(500)'),
                    ('login_page_background', 'VARCHAR(500)'),
                    ('location_name', 'VARCHAR(255)'),
                    ('longitude', 'DECIMAL(10, 8)'),
                    ('latitude', 'DECIMAL(10, 8)'),
                    ('google_drive_token', 'TEXT'),
                    ('google_drive_refresh_token', 'TEXT'),
                    ('google_drive_token_uri', 'VARCHAR(500)'),
                    ('google_drive_scopes', 'TEXT'),
                    ('google_drive_account_email', 'VARCHAR(255)'),
                    ('google_drive_account_name', 'VARCHAR(255)'),
                    ('google_drive_account_picture', 'VARCHAR(500)'),
                    ('google_drive_main_folder_id', 'VARCHAR(255)'),
                    ('created_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
                    ('updated_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')
                ]
                
                for column_name, column_def in columns_to_check:
                    if not column_exists('company_settings', column_name):
                        try:
                            cursor.execute(f"ALTER TABLE company_settings ADD COLUMN {column_name} {column_def}")
                            connection.commit()
                            print(f"[OK] Added column '{column_name}' to company_settings table")
                        except Exception as e:
                            print(f"[WARNING] Could not add column '{column_name}': {e}")
            
            return True
    except Exception as e:
        print(f"Error creating/updating company_settings table: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_employees_table():
    """Create employees table with all required columns (without company_name)"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            # Check if table exists
            if not table_exists('employees'):
                # Create table without company_name
                cursor.execute("""
                    CREATE TABLE employees (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        full_name VARCHAR(255) NOT NULL,
                        phone_number VARCHAR(20) NOT NULL,
                        work_email VARCHAR(255) UNIQUE NOT NULL,
                        employee_code VARCHAR(6) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        profile_picture VARCHAR(255),
                        role ENUM('Firm Administrator', 'Managing Partner', 'Finance Office', 'Associate Advocate', 'Clerk', 'IT Support', 'Employee') DEFAULT 'Employee',
                        status ENUM('Active', 'Pending Approval', 'Suspended') DEFAULT 'Pending Approval',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Employees table created")
            else:
                print("[OK] Employees table already exists")
                # Check and add missing columns (excluding company_name)
                columns_to_check = [
                    ('full_name', 'VARCHAR(255) NOT NULL'),
                    ('phone_number', 'VARCHAR(20) NOT NULL'),
                    ('work_email', 'VARCHAR(255) UNIQUE NOT NULL'),
                    ('employee_code', 'VARCHAR(6) UNIQUE NOT NULL'),
                    ('password_hash', 'VARCHAR(255) NOT NULL'),
                    ('profile_picture', 'VARCHAR(255)'),
                    ('role', "ENUM('Firm Administrator', 'Managing Partner', 'Finance Office', 'Associate Advocate', 'Clerk', 'IT Support', 'Employee') DEFAULT 'Employee'"),
                    ('status', "ENUM('Active', 'Pending Approval', 'Suspended') DEFAULT 'Pending Approval'"),
                    ('created_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
                    ('updated_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')
                ]
                
                for column_name, column_def in columns_to_check:
                    if not column_exists('employees', column_name):
                        try:
                            cursor.execute(f"ALTER TABLE employees ADD COLUMN {column_name} {column_def}")
                            connection.commit()
                            print(f"[OK] Added column '{column_name}' to employees table")
                        except Exception as e:
                            print(f"[WARNING] Could not add column '{column_name}': {e}")
                
                # Add onboarding columns if they don't exist
                onboarding_columns = [
                    ('account_number', 'VARCHAR(50)'),
                    ('account_name', 'VARCHAR(255)'),
                    ('salary', 'DECIMAL(12, 2)'),
                    ('salary_components', 'TEXT'),
                    ('tax_pin', 'VARCHAR(20)'),
                    ('pay_frequency', "ENUM('daily', 'weekly', 'monthly')"),
                    ('payment_method', "ENUM('Bank', 'Mobile Money')"),
                    ('bank_name', 'VARCHAR(255)'),
                    ('mobile_money_company', 'VARCHAR(255)'),
                    ('employment_contract', 'VARCHAR(255)'),
                    ('id_front', 'VARCHAR(255)'),
                    ('id_back', 'VARCHAR(255)'),
                    ('signature', 'VARCHAR(255)'),
                    ('signature_hash', 'VARCHAR(255)'),
                    ('stamp', 'VARCHAR(255)'),
                    ('stamp_hash', 'VARCHAR(255)'),
                    ('nda_accepted', 'BOOLEAN DEFAULT FALSE'),
                    ('code_of_conduct_accepted', 'BOOLEAN DEFAULT FALSE'),
                    ('health_safety_accepted', 'BOOLEAN DEFAULT FALSE'),
                    ('onboarding_completed', 'BOOLEAN DEFAULT FALSE')
                ]
                
                for column_name, column_def in onboarding_columns:
                    if not column_exists('employees', column_name):
                        try:
                            cursor.execute(f"ALTER TABLE employees ADD COLUMN {column_name} {column_def}")
                            connection.commit()
                            print(f"[OK] Added onboarding column '{column_name}' to employees table")
                        except Exception as e:
                            print(f"[WARNING] Could not add column '{column_name}': {e}")
            
            return True
    except Exception as e:
        print(f"Error creating/updating employees table: {e}")
        return False
    finally:
        if connection:
            connection.close()


def create_employee_permissions_table():
    """Create table for per-employee fine-grained permissions."""
    try:
        connection = get_db_connection()
        if not connection:
            return False

        with connection.cursor() as cursor:
            if not table_exists('employee_permissions'):
                cursor.execute("""
                    CREATE TABLE employee_permissions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL,
                        permission_key VARCHAR(100) NOT NULL,
                        allowed BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uniq_employee_permission (employee_id, permission_key),
                        CONSTRAINT fk_employee_permissions_employee
                            FOREIGN KEY (employee_id) REFERENCES employees(id)
                            ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] employee_permissions table created")
            else:
                print("[OK] employee_permissions table already exists")

        return True
    except Exception as e:
        print(f"Error creating/updating employee_permissions table: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_clients_table():
    """Create clients table for Google OAuth authenticated clients"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            if not table_exists('clients'):
                cursor.execute("""
                    CREATE TABLE clients (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        google_id VARCHAR(255) UNIQUE NOT NULL,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        full_name VARCHAR(255) NOT NULL,
                        phone_number VARCHAR(20),
                        profile_picture VARCHAR(500),
                        client_type ENUM('Pending', 'Individual', 'Corporate') DEFAULT 'Pending',
                        status ENUM('Active', 'Inactive') DEFAULT 'Active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Clients table created")
            else:
                print("[OK] Clients table already exists")
                # Check and add phone_number column if it doesn't exist
                if not column_exists('clients', 'phone_number'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN phone_number VARCHAR(20)")
                        connection.commit()
                        print("[OK] Added phone_number column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add phone_number column: {e}")
                
                # Update client_type ENUM to include 'Pending' if needed
                try:
                    cursor.execute("""
                        ALTER TABLE clients 
                        MODIFY COLUMN client_type ENUM('Pending', 'Individual', 'Corporate') DEFAULT 'Pending'
                    """)
                    connection.commit()
                    print("[OK] Updated client_type ENUM to include 'Pending'")
                except Exception as e:
                    # If error, try to check if 'Pending' already exists
                    if 'Duplicate' not in str(e) and 'already exists' not in str(e).lower():
                        print(f"[WARNING] Could not update client_type ENUM: {e}")

                # Update status ENUM to include Pending Approval for manual signups
                try:
                    cursor.execute("""
                        ALTER TABLE clients
                        MODIFY COLUMN status ENUM('Active', 'Inactive', 'Pending Approval') DEFAULT 'Active'
                    """)
                    connection.commit()
                    print("[OK] Updated clients status ENUM to include Pending Approval")
                except Exception as e:
                    if 'Duplicate' not in str(e) and 'already exists' not in str(e).lower():
                        print(f"[WARNING] Could not update clients status ENUM: {e}")

                # Manual client auth support
                if not column_exists('clients', 'password_hash'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN password_hash VARCHAR(255) NULL")
                        connection.commit()
                        print("[OK] Added password_hash column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add password_hash column: {e}")

                # Allow manual clients without Google account
                try:
                    cursor.execute("ALTER TABLE clients MODIFY COLUMN google_id VARCHAR(255) UNIQUE NULL")
                    connection.commit()
                    print("[OK] Updated clients.google_id to allow NULL")
                except Exception as e:
                    if 'Duplicate' not in str(e) and 'already exists' not in str(e).lower():
                        print(f"[WARNING] Could not update clients.google_id nullability: {e}")
                
                # Add columns for Individual client requirements (ID front and back)
                if not column_exists('clients', 'id_front'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN id_front VARCHAR(500)")
                        connection.commit()
                        print("[OK] Added id_front column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add id_front column: {e}")
                
                if not column_exists('clients', 'id_back'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN id_back VARCHAR(500)")
                        connection.commit()
                        print("[OK] Added id_back column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add id_back column: {e}")
                
                # Add columns for Corporate client requirements (CR-12 and physical address)
                if not column_exists('clients', 'cr12_certificate'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN cr12_certificate VARCHAR(500)")
                        connection.commit()
                        print("[OK] Added cr12_certificate column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add cr12_certificate column: {e}")
                
                if column_exists('clients', 'post_office_address'):
                    try:
                        cursor.execute("ALTER TABLE clients CHANGE COLUMN post_office_address physical_address TEXT")
                        connection.commit()
                        print("[OK] Renamed post_office_address to physical_address in clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not rename post_office_address column: {e}")
                elif not column_exists('clients', 'physical_address'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN physical_address TEXT")
                        connection.commit()
                        print("[OK] Added physical_address column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add physical_address column: {e}")

                if not column_exists('clients', 'national_id'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN national_id VARCHAR(50)")
                        connection.commit()
                        print("[OK] Added national_id column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add national_id column: {e}")

                if not column_exists('clients', 'kra_pin'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN kra_pin VARCHAR(50)")
                        connection.commit()
                        print("[OK] Added kra_pin column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add kra_pin column: {e}")

                if not column_exists('clients', 'client_address'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN client_address TEXT")
                        connection.commit()
                        print("[OK] Added client_address column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add client_address column: {e}")

                if not column_exists('clients', 'address_latitude'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN address_latitude DECIMAL(10, 8)")
                        connection.commit()
                        print("[OK] Added address_latitude column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add address_latitude column: {e}")

                if not column_exists('clients', 'address_longitude'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN address_longitude DECIMAL(11, 8)")
                        connection.commit()
                        print("[OK] Added address_longitude column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add address_longitude column: {e}")

                if not column_exists('clients', 'instruction_note'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN instruction_note VARCHAR(500)")
                        connection.commit()
                        print("[OK] Added instruction_note column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add instruction_note column: {e}")

                if not column_exists('clients', 'corporate_kra_pin'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN corporate_kra_pin VARCHAR(500)")
                        connection.commit()
                        print("[OK] Added corporate_kra_pin column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add corporate_kra_pin column: {e}")
            return True
    except Exception as e:
        print(f"Error creating/updating clients table: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_client_personal_documents_table():
    """Create table for client personal/registration document uploads"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            if not table_exists('client_personal_documents'):
                cursor.execute("""
                    CREATE TABLE client_personal_documents (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        document_type VARCHAR(255) NOT NULL,
                        filename VARCHAR(500) NOT NULL,
                        original_filename VARCHAR(500),
                        file_size INT DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Created client_personal_documents table")
        return True
    except Exception as e:
        print(f"Error creating client_personal_documents table: {e}")
        return False
    finally:
        if connection:
            connection.close()


def create_case_tables():
    """Create tables for case management: cases, case_types, case_categories, stations"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            # Create case_types table
            if not table_exists('case_types'):
                cursor.execute("""
                    CREATE TABLE case_types (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        type_name VARCHAR(255) UNIQUE NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Case types table created")
            else:
                print("[OK] Case types table already exists")
            
            # Create case_categories table
            if not table_exists('case_categories'):
                cursor.execute("""
                    CREATE TABLE case_categories (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        category_name VARCHAR(255) UNIQUE NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Case categories table created")
            else:
                print("[OK] Case categories table already exists")
            
            # Create stations table
            if not table_exists('stations'):
                cursor.execute("""
                    CREATE TABLE stations (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        station_name VARCHAR(255) UNIQUE NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Stations table created")
            else:
                print("[OK] Stations table already exists")
            
            # Create cases table
            if not table_exists('cases'):
                cursor.execute("""
                    CREATE TABLE cases (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        tracking_number VARCHAR(50) UNIQUE NOT NULL,
                        court_case_number VARCHAR(255),
                        client_id INT NOT NULL,
                        client_name VARCHAR(255) NOT NULL,
                        case_type VARCHAR(255) NOT NULL,
                        filing_date DATE NOT NULL,
                        case_category VARCHAR(255) NOT NULL,
                        station VARCHAR(255) NOT NULL,
                        filled_by_id INT NOT NULL,
                        filled_by_name VARCHAR(255) NOT NULL,
                        created_by_id INT NOT NULL,
                        created_by_name VARCHAR(255) NOT NULL,
                        description TEXT,
                        status ENUM('Active', 'Closed', 'Archived', 'Mediations', 'Pending', 'Consolidated', 'Pending Approval') DEFAULT 'Pending Approval',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
                        FOREIGN KEY (filled_by_id) REFERENCES employees(id) ON DELETE CASCADE,
                        FOREIGN KEY (created_by_id) REFERENCES employees(id) ON DELETE CASCADE,
                        INDEX idx_client_id (client_id),
                        INDEX idx_filled_by_id (filled_by_id),
                        INDEX idx_created_by_id (created_by_id),
                        INDEX idx_filing_date (filing_date),
                        INDEX idx_tracking_number (tracking_number)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Cases table created")
            else:
                print("[OK] Cases table already exists")
            
            # Create case_parties table
            if not table_exists('case_parties'):
                cursor.execute("""
                    CREATE TABLE case_parties (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        case_id INT NOT NULL,
                        party_name VARCHAR(255) NOT NULL,
                        party_type VARCHAR(255) NOT NULL,
                        party_category VARCHAR(255),
                        firm_agent VARCHAR(255),
                        party_phone VARCHAR(50),
                        party_email VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                        INDEX idx_case_id (case_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Case parties table created")
            else:
                print("[OK] Case parties table already exists")

            # Add missing columns to case_parties table (for existing DBs)
            if not column_exists('case_parties', 'party_phone'):
                try:
                    cursor.execute("ALTER TABLE case_parties ADD COLUMN party_phone VARCHAR(50)")
                    connection.commit()
                    print("[OK] Added party_phone column to case_parties table")
                except Exception as e:
                    print(f"[WARNING] Could not add party_phone column to case_parties: {e}")

            if not column_exists('case_parties', 'party_email'):
                try:
                    cursor.execute("ALTER TABLE case_parties ADD COLUMN party_email VARCHAR(255)")
                    connection.commit()
                    print("[OK] Added party_email column to case_parties table")
                except Exception as e:
                    print(f"[WARNING] Could not add party_email column to case_parties: {e}")
            
            # Create case_proceedings table
            if not table_exists('case_proceedings'):
                cursor.execute("""
                    CREATE TABLE case_proceedings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        case_id INT NOT NULL,
                        court_activity_type VARCHAR(255) NOT NULL,
                        court_room VARCHAR(255),
                        judicial_officer VARCHAR(255),
                        date_of_court_appeared DATE NOT NULL,
                        outcome_orders TEXT,
                        next_court_date DATE,
                        attendance VARCHAR(50),
                        reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                        INDEX idx_case_id (case_id),
                        INDEX idx_date_of_court_appeared (date_of_court_appeared)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Case proceedings table created")
            else:
                print("[OK] Case proceedings table already exists")
            
            # Create case_proceeding_materials table
            if not table_exists('case_proceeding_materials'):
                cursor.execute("""
                    CREATE TABLE case_proceeding_materials (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        proceeding_id INT NOT NULL,
                        material_description TEXT NOT NULL,
                        reminder_frequency VARCHAR(50),
                        allocated_to_id INT,
                        allocated_to_name VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (proceeding_id) REFERENCES case_proceedings(id) ON DELETE CASCADE,
                        FOREIGN KEY (allocated_to_id) REFERENCES employees(id) ON DELETE SET NULL,
                        INDEX idx_proceeding_id (proceeding_id),
                        INDEX idx_allocated_to_id (allocated_to_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Case proceeding materials table created")
            else:
                print("[OK] Case proceeding materials table already exists")
            
            # Create case_proceeding_advocates table (advocates present + what they said)
            if not table_exists('case_proceeding_advocates'):
                cursor.execute("""
                    CREATE TABLE case_proceeding_advocates (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        proceeding_id INT NOT NULL,
                        advocate_name VARCHAR(255) NOT NULL,
                        remarks TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (proceeding_id) REFERENCES case_proceedings(id) ON DELETE CASCADE,
                        INDEX idx_proceeding_id (proceeding_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Case proceeding advocates table created")
            else:
                print("[OK] Case proceeding advocates table already exists")
            
            # Check and add missing columns to cases table
            if not column_exists('cases', 'tracking_number'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN tracking_number VARCHAR(50) UNIQUE AFTER id")
                    connection.commit()
                    print("[OK] Added tracking_number column to cases table")
                except Exception as e:
                    print(f"[WARNING] Could not add tracking_number column: {e}")
            
            if not column_exists('cases', 'court_case_number'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN court_case_number VARCHAR(255) AFTER tracking_number")
                    connection.commit()
                    print("[OK] Added court_case_number column to cases table")
                except Exception as e:
                    print(f"[WARNING] Could not add court_case_number column: {e}")
            
            # Update status ENUM
            try:
                cursor.execute("""
                    ALTER TABLE cases 
                    MODIFY COLUMN status ENUM('Active', 'Closed', 'Archived', 'Mediations', 'Pending', 'Consolidated', 'Pending Approval') DEFAULT 'Pending Approval'
                """)
                connection.commit()
                print("[OK] Updated cases status ENUM")
            except Exception as e:
                print(f"[WARNING] Could not update status ENUM: {e}")
            if not column_exists('cases', 'allocation_description'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN allocation_description TEXT NULL AFTER filled_by_name")
                    connection.commit()
                    print("[OK] Added allocation_description column to cases table")
                except Exception as e:
                    print(f"[WARNING] Could not add allocation_description column: {e}")
            if not column_exists('cases', 'allocation_timeline'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN allocation_timeline VARCHAR(500) NULL AFTER allocation_description")
                    connection.commit()
                    print("[OK] Added allocation_timeline column to cases table")
                except Exception as e:
                    print(f"[WARNING] Could not add allocation_timeline column: {e}")
            if not column_exists('cases', 'court_rank'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN court_rank VARCHAR(255) NULL AFTER client_name")
                    connection.commit()
                    print("[OK] Added court_rank column to cases table")
                except Exception as e:
                    print(f"[WARNING] Could not add court_rank column: {e}")
        
        return True
    except Exception as e:
        print(f"Error creating/updating case tables: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_matters_table():
    """Create matters table for other matters management"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            # Create matters table
            if not table_exists('matters'):
                cursor.execute("""
                    CREATE TABLE matters (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        matter_reference_number VARCHAR(50) UNIQUE NOT NULL,
                        matter_title VARCHAR(500) NOT NULL,
                        matter_category VARCHAR(255) NOT NULL,
                        client_id INT NOT NULL,
                        client_name VARCHAR(255) NOT NULL,
                        client_phone VARCHAR(20),
                        client_instructions TEXT,
                        assigned_employee_id INT NOT NULL,
                        assigned_employee_name VARCHAR(255) NOT NULL,
                        date_opened DATE NOT NULL,
                        status ENUM('Open', 'In Progress', 'Pending Client', 'Completed', 'On Hold', 'Closed', 'Pending Approval') DEFAULT 'Pending Approval',
                        created_by_id INT NOT NULL,
                        created_by_name VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
                        FOREIGN KEY (assigned_employee_id) REFERENCES employees(id) ON DELETE CASCADE,
                        FOREIGN KEY (created_by_id) REFERENCES employees(id) ON DELETE CASCADE,
                        INDEX idx_client_id (client_id),
                        INDEX idx_assigned_employee_id (assigned_employee_id),
                        INDEX idx_created_by_id (created_by_id),
                        INDEX idx_date_opened (date_opened),
                        INDEX idx_matter_reference_number (matter_reference_number),
                        INDEX idx_status (status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Matters table created")
            else:
                print("[OK] Matters table already exists")
            
            # Update status ENUM to include 'Pending Approval' and set as default
            try:
                cursor.execute("""
                    ALTER TABLE matters 
                    MODIFY COLUMN status ENUM('Open', 'In Progress', 'Pending Client', 'Completed', 'On Hold', 'Closed', 'Pending Approval') DEFAULT 'Pending Approval'
                """)
                connection.commit()
                print("[OK] Updated matters status ENUM")
            except Exception as e:
                print(f"[WARNING] Could not update matters status ENUM: {e}")

            # Add allocation description/timeline columns for approval workflow
            if not column_exists('matters', 'allocation_description'):
                try:
                    cursor.execute("ALTER TABLE matters ADD COLUMN allocation_description TEXT NULL AFTER client_instructions")
                    connection.commit()
                    print("[OK] Added allocation_description column to matters")
                except Exception as e:
                    print(f"[WARNING] Could not add allocation_description column: {e}")
            if not column_exists('matters', 'allocation_timeline'):
                try:
                    cursor.execute("ALTER TABLE matters ADD COLUMN allocation_timeline VARCHAR(500) NULL AFTER allocation_description")
                    connection.commit()
                    print("[OK] Added allocation_timeline column to matters")
                except Exception as e:
                    print(f"[WARNING] Could not add allocation_timeline column: {e}")
        
        return True
    except Exception as e:
        print(f"Error creating/updating matters table: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_email_tables():
    """Create email_settings and email_accounts tables for email management"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            # Create email_settings table
            if not table_exists('email_settings'):
                cursor.execute("""
                    CREATE TABLE email_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        cpanel_user VARCHAR(255) NOT NULL,
                        cpanel_domain VARCHAR(255) NOT NULL,
                        cpanel_api_token VARCHAR(500) NOT NULL,
                        cpanel_api_port INT DEFAULT 2083,
                        main_email VARCHAR(255) NOT NULL,
                        main_email_password VARCHAR(500),
                        smtp_host VARCHAR(255) NOT NULL DEFAULT 'mail.baunilawgroup.com',
                        smtp_port INT NOT NULL DEFAULT 587,
                        smtp_use_tls BOOLEAN DEFAULT TRUE,
                        imap_host VARCHAR(255) NOT NULL DEFAULT 'mail.baunilawgroup.com',
                        imap_port INT NOT NULL DEFAULT 993,
                        imap_use_ssl BOOLEAN DEFAULT TRUE,
                        sender_name VARCHAR(255) DEFAULT 'BAUNI LAW GROUP',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY unique_settings (cpanel_user, cpanel_domain)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Email settings table created")
            else:
                print("[OK] Email settings table already exists")
            
            # Create email_accounts table for sub-emails
            if not table_exists('email_accounts'):
                cursor.execute("""
                    CREATE TABLE email_accounts (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        email_address VARCHAR(255) NOT NULL UNIQUE,
                        email_password VARCHAR(500),
                        display_name VARCHAR(255),
                        is_main BOOLEAN DEFAULT FALSE,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_by_id INT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (created_by_id) REFERENCES employees(id) ON DELETE SET NULL,
                        INDEX idx_email_address (email_address),
                        INDEX idx_is_main (is_main),
                        INDEX idx_is_active (is_active)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Email accounts table created")
            else:
                print("[OK] Email accounts table already exists")
        
        return True
    except Exception as e:
        # Use ASCII-safe message for server environments where stdout is ASCII
        err_msg = str(e).encode('ascii', 'replace').decode('ascii')
        print(f"Error creating email tables: {err_msg}")
        return False
    finally:
        if connection:
            connection.close()

def apply_migrations(current_version):
    """Apply database migrations based on version"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        
        migrations_applied = False
        
        with connection.cursor() as cursor:
            # Migration 2: Remove company_name from employees, create company_settings table
            if current_version < 2:
                print("Applying migration 2: Moving company data to company_settings table...")
                
                # Create company_settings table if it doesn't exist
                if not table_exists('company_settings'):
                    cursor.execute("""
                        CREATE TABLE company_settings (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            company_name VARCHAR(255) NOT NULL DEFAULT 'BAUNI LAW GROUP',
                            email VARCHAR(255),
                            contact_number VARCHAR(20),
                            whatsapp_number VARCHAR(20),
                            tiktok_link VARCHAR(500),
                            instagram_link VARCHAR(500),
                            fb_link VARCHAR(500),
                            location_name VARCHAR(255),
                            longitude DECIMAL(10, 8),
                            latitude DECIMAL(10, 8),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created company_settings table")
                
                # Get company name from employees table if it exists
                company_name = 'BAUNI LAW GROUP'
                if column_exists('employees', 'company_name'):
                    try:
                        cursor.execute("SELECT DISTINCT company_name FROM employees WHERE company_name IS NOT NULL LIMIT 1")
                        result = cursor.fetchone()
                        if result and result[0]:
                            company_name = result[0]
                    except:
                        pass
                
                # Insert default company settings if table is empty
                cursor.execute("SELECT COUNT(*) FROM company_settings")
                if cursor.fetchone()[0] == 0:
                    cursor.execute("""
                        INSERT INTO company_settings (company_name, email, contact_number, whatsapp_number, location_name)
                        VALUES (%s, NULL, NULL, NULL, NULL)
                    """, (company_name,))
                    connection.commit()
                    print(f"[OK] Inserted default company settings with name: {company_name}")
                
                # Remove company_name column from employees table if it exists
                if column_exists('employees', 'company_name'):
                    try:
                        cursor.execute("ALTER TABLE employees DROP COLUMN company_name")
                        connection.commit()
                        print("[OK] Removed company_name column from employees table")
                    except Exception as e:
                        print(f"[WARNING] Could not remove company_name column: {e}")
                
                migrations_applied = True
            
            # Migration 3: Add onboarding fields
            if current_version < 3:
                print("Applying migration 3: Adding onboarding fields...")
                
                onboarding_columns = [
                    ('account_number', 'VARCHAR(50)'),
                    ('account_name', 'VARCHAR(255)'),
                    ('salary', 'DECIMAL(12, 2)'),
                    ('salary_components', 'TEXT'),
                    ('tax_pin', 'VARCHAR(20)'),
                    ('pay_frequency', "ENUM('daily', 'weekly', 'monthly')"),
                    ('employment_contract', 'VARCHAR(255)'),
                    ('id_front', 'VARCHAR(255)'),
                    ('id_back', 'VARCHAR(255)'),
                    ('signature', 'VARCHAR(255)'),
                    ('signature_hash', 'VARCHAR(255)'),
                    ('stamp', 'VARCHAR(255)'),
                    ('stamp_hash', 'VARCHAR(255)'),
                    ('nda_accepted', 'BOOLEAN DEFAULT FALSE'),
                    ('code_of_conduct_accepted', 'BOOLEAN DEFAULT FALSE'),
                    ('health_safety_accepted', 'BOOLEAN DEFAULT FALSE'),
                    ('onboarding_completed', 'BOOLEAN DEFAULT FALSE')
                ]
                
                for column_name, column_def in onboarding_columns:
                    if not column_exists('employees', column_name):
                        try:
                            cursor.execute(f"ALTER TABLE employees ADD COLUMN {column_name} {column_def}")
                            connection.commit()
                            print(f"[OK] Added column '{column_name}' to employees table")
                        except Exception as e:
                            print(f"[WARNING] Could not add column '{column_name}': {e}")
                
                migrations_applied = True
            
            # Migration 4: Create case management tables
            if current_version < 4:
                print("Applying migration 4: Creating case management tables...")
                
                # Create case_types table
                if not table_exists('case_types'):
                    cursor.execute("""
                        CREATE TABLE case_types (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            type_name VARCHAR(255) UNIQUE NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created case_types table")
                
                # Create case_categories table
                if not table_exists('case_categories'):
                    cursor.execute("""
                        CREATE TABLE case_categories (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            category_name VARCHAR(255) UNIQUE NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created case_categories table")
                
                # Create stations table
                if not table_exists('stations'):
                    cursor.execute("""
                        CREATE TABLE stations (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            station_name VARCHAR(255) UNIQUE NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created stations table")
                
                # Create cases table
                if not table_exists('cases'):
                    cursor.execute("""
                        CREATE TABLE cases (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            tracking_number VARCHAR(50) UNIQUE NOT NULL,
                            court_case_number VARCHAR(255),
                            client_id INT NOT NULL,
                            client_name VARCHAR(255) NOT NULL,
                            case_type VARCHAR(255) NOT NULL,
                            filing_date DATE NOT NULL,
                            case_category VARCHAR(255) NOT NULL,
                            station VARCHAR(255) NOT NULL,
                            filled_by_id INT NOT NULL,
                            filled_by_name VARCHAR(255) NOT NULL,
                            created_by_id INT NOT NULL,
                            created_by_name VARCHAR(255) NOT NULL,
                            description TEXT,
                            status ENUM('Active', 'Closed', 'Archived', 'Mediations', 'Pending', 'Consolidated', 'Pending Approval') DEFAULT 'Pending Approval',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
                            FOREIGN KEY (filled_by_id) REFERENCES employees(id) ON DELETE CASCADE,
                            FOREIGN KEY (created_by_id) REFERENCES employees(id) ON DELETE CASCADE,
                            INDEX idx_client_id (client_id),
                            INDEX idx_filled_by_id (filled_by_id),
                            INDEX idx_created_by_id (created_by_id),
                            INDEX idx_filing_date (filing_date),
                            INDEX idx_tracking_number (tracking_number)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created cases table")
                
                migrations_applied = True
            
            # Migration 5: Add tracking_number, court_case_number and update status ENUM
            if current_version < 5:
                print("Applying migration 5: Adding tracking_number, court_case_number and updating status ENUM...")
                
                # Add tracking_number column
                if not column_exists('cases', 'tracking_number'):
                    try:
                        cursor.execute("ALTER TABLE cases ADD COLUMN tracking_number VARCHAR(50) UNIQUE AFTER id")
                        connection.commit()
                        print("[OK] Added tracking_number column to cases table")
                    except Exception as e:
                        print(f"[WARNING] Could not add tracking_number column: {e}")
                
                # Add court_case_number column
                if not column_exists('cases', 'court_case_number'):
                    try:
                        cursor.execute("ALTER TABLE cases ADD COLUMN court_case_number VARCHAR(255) AFTER tracking_number")
                        connection.commit()
                        print("[OK] Added court_case_number column to cases table")
                    except Exception as e:
                        print(f"[WARNING] Could not add court_case_number column: {e}")
                
                # Update status ENUM
                try:
                    cursor.execute("""
                        ALTER TABLE cases 
                        MODIFY COLUMN status ENUM('Active', 'Closed', 'Archived', 'Mediations', 'Pending', 'Consolidated', 'Pending Approval') DEFAULT 'Pending Approval'
                    """)
                    connection.commit()
                    print("[OK] Updated cases status ENUM")
                except Exception as e:
                    print(f"[WARNING] Could not update status ENUM: {e}")
                
                migrations_applied = True
            
            # Migration 6: Create case_parties table
            if current_version < 6:
                print("Applying migration 6: Creating case_parties table...")
                
                if not table_exists('case_parties'):
                    cursor.execute("""
                        CREATE TABLE case_parties (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            case_id INT NOT NULL,
                            party_name VARCHAR(255) NOT NULL,
                            party_type VARCHAR(255) NOT NULL,
                            party_category VARCHAR(255),
                            firm_agent VARCHAR(255),
                            party_phone VARCHAR(50),
                            party_email VARCHAR(255),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                            INDEX idx_case_id (case_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created case_parties table")
                
                migrations_applied = True
            
            # Migration 7: Create case_proceedings table
            if current_version < 7:
                print("Applying migration 7: Creating case_proceedings table...")
                
                if not table_exists('case_proceedings'):
                    cursor.execute("""
                        CREATE TABLE case_proceedings (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            case_id INT NOT NULL,
                            court_activity_type VARCHAR(255) NOT NULL,
                            court_room VARCHAR(255),
                            judicial_officer VARCHAR(255),
                            date_of_court_appeared DATE NOT NULL,
                            outcome_orders TEXT,
                            outcome_details TEXT,
                            next_court_date DATE,
                            attendance VARCHAR(50),
                            reason TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                            INDEX idx_case_id (case_id),
                            INDEX idx_date_of_court_appeared (date_of_court_appeared)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created case_proceedings table")
                
                migrations_applied = True
            
            # Migration 8: Create case_proceeding_materials table
            if current_version < 8:
                print("Applying migration 8: Creating case_proceeding_materials table...")
                
                if not table_exists('case_proceeding_materials'):
                    cursor.execute("""
                        CREATE TABLE case_proceeding_materials (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            proceeding_id INT NOT NULL,
                            material_description TEXT NOT NULL,
                            reminder_frequency VARCHAR(50),
                            allocated_to_id INT,
                            allocated_to_name VARCHAR(255),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (proceeding_id) REFERENCES case_proceedings(id) ON DELETE CASCADE,
                            FOREIGN KEY (allocated_to_id) REFERENCES employees(id) ON DELETE SET NULL,
                            INDEX idx_proceeding_id (proceeding_id),
                            INDEX idx_allocated_to_id (allocated_to_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created case_proceeding_materials table")
                
                migrations_applied = True
            
            # Migration 9: Add outcome_details column to case_proceedings
            if current_version < 9:
                print("Applying migration 9: Adding outcome_details column to case_proceedings table...")
                
                if not column_exists('case_proceedings', 'outcome_details'):
                    cursor.execute("""
                        ALTER TABLE case_proceedings 
                        ADD COLUMN outcome_details TEXT AFTER outcome_orders
                    """)
                    connection.commit()
                    print("[OK] Added outcome_details column to case_proceedings table")
                else:
                    print("[OK] outcome_details column already exists")
                
                migrations_applied = True
            
            # Migration 10: Add next_attendance column to case_proceedings
            if current_version < 10:
                print("Applying migration 10: Adding next_attendance column to case_proceedings table...")
                
                if not column_exists('case_proceedings', 'next_attendance'):
                    cursor.execute("""
                        ALTER TABLE case_proceedings 
                        ADD COLUMN next_attendance VARCHAR(50) AFTER attendance
                    """)
                    connection.commit()
                    print("[OK] Added next_attendance column to case_proceedings table")
                else:
                    print("[OK] next_attendance column already exists")
                
                migrations_applied = True
            
            # Migration 11: Add virtual_link column to case_proceedings
            if current_version < 11:
                print("Applying migration 11: Adding virtual_link column to case_proceedings table...")
                
                if not column_exists('case_proceedings', 'virtual_link'):
                    cursor.execute("""
                        ALTER TABLE case_proceedings 
                        ADD COLUMN virtual_link VARCHAR(500) AFTER next_attendance
                    """)
                    connection.commit()
                    print("[OK] Added virtual_link column to case_proceedings table")
                else:
                    print("[OK] virtual_link column already exists")
                
                migrations_applied = True
            
            # Migration 12: Add previous_proceeding_id column to case_proceedings for history tracking
            if current_version < 12:
                print("Applying migration 12: Adding previous_proceeding_id column to case_proceedings table...")
                
                if not column_exists('case_proceedings', 'previous_proceeding_id'):
                    cursor.execute("""
                        ALTER TABLE case_proceedings 
                        ADD COLUMN previous_proceeding_id INT NULL AFTER id,
                        ADD INDEX idx_previous_proceeding_id (previous_proceeding_id),
                        ADD FOREIGN KEY (previous_proceeding_id) REFERENCES case_proceedings(id) ON DELETE SET NULL
                    """)
                    connection.commit()
                    print("[OK] Added previous_proceeding_id column to case_proceedings table")
                else:
                    print("[OK] previous_proceeding_id column already exists")
                
                migrations_applied = True
            
            # Migration 13: Make court_activity_type nullable in case_proceedings
            if current_version < 13:
                print("Applying migration 13: Making court_activity_type nullable in case_proceedings table...")
                
                try:
                    cursor.execute("""
                        ALTER TABLE case_proceedings 
                        MODIFY COLUMN court_activity_type VARCHAR(255) NULL
                    """)
                    connection.commit()
                    print("[OK] Made court_activity_type nullable in case_proceedings table")
                except Exception as e:
                    print(f"[WARNING] Could not modify court_activity_type column: {e}")
                
                migrations_applied = True
            
            # Migration 14: Create email_settings and email_accounts tables
            if current_version < 14:
                print("Applying migration 14: Creating email management tables...")
                migrations_applied = True
            
            # Migration 15: Create case_proceeding_advocates table
            if current_version < 15:
                print("Applying migration 15: Creating case_proceeding_advocates table...")
                if not table_exists('case_proceeding_advocates'):
                    cursor.execute("""
                        CREATE TABLE case_proceeding_advocates (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            proceeding_id INT NOT NULL,
                            advocate_name VARCHAR(255) NOT NULL,
                            remarks TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (proceeding_id) REFERENCES case_proceedings(id) ON DELETE CASCADE,
                            INDEX idx_proceeding_id (proceeding_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("[OK] Created case_proceeding_advocates table")
                else:
                    print("[OK] case_proceeding_advocates table already exists")
                migrations_applied = True
            
            # Migration 1: Ensure all required columns exist (for older versions)
            if current_version < 1:
                print("Applying migration 1: Schema updates...")
                migrations_applied = True
            
            if migrations_applied:
                connection.commit()
                update_schema_version(SCHEMA_VERSION)
                print(f"[OK] Migrations applied. Schema version updated to {SCHEMA_VERSION}")
        
        return True
    except Exception as e:
        print(f"Error applying migrations: {e}")
        return False
    finally:
        if connection:
            connection.close()

def create_webapp_messages_table():
    """Create webapp_messages and whatsapp_settings tables for client-employee messaging"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            if not table_exists('webapp_messages'):
                cursor.execute("""
                    CREATE TABLE webapp_messages (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        employee_id INT,
                        subject VARCHAR(500),
                        message TEXT,
                        attachment_file VARCHAR(500),
                        attachment_type VARCHAR(50),
                        sender_type ENUM('client', 'employee') NOT NULL DEFAULT 'client',
                        delivery_channel ENUM('web', 'whatsapp') NOT NULL DEFAULT 'web',
                        whatsapp_message_id VARCHAR(255),
                        whatsapp_status VARCHAR(50),
                        is_read TINYINT(1) DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_client_id (client_id),
                        INDEX idx_sender_type (sender_type),
                        INDEX idx_created_at (created_at),
                        INDEX idx_wa_msg_id (whatsapp_message_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Created webapp_messages table")
            else:
                # Ensure WhatsApp columns exist on older installs
                for col, defn in [
                    ('delivery_channel', "ENUM('web','whatsapp') NOT NULL DEFAULT 'web'"),
                    ('whatsapp_message_id', 'VARCHAR(255)'),
                    ('whatsapp_status', 'VARCHAR(50)')
                ]:
                    if not column_exists('webapp_messages', col):
                        try:
                            cursor.execute(f"ALTER TABLE webapp_messages ADD COLUMN {col} {defn}")
                            connection.commit()
                            print(f"[OK] Added {col} column to webapp_messages")
                        except Exception:
                            pass
                print("[OK] webapp_messages table exists")

            if not table_exists('whatsapp_settings'):
                cursor.execute("""
                    CREATE TABLE whatsapp_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        access_token TEXT NOT NULL,
                        phone_number_id VARCHAR(255) NOT NULL,
                        whatsapp_business_account_id VARCHAR(255),
                        webhook_verify_token VARCHAR(255) NOT NULL,
                        display_phone_number VARCHAR(30),
                        api_version VARCHAR(10) NOT NULL DEFAULT 'v21.0',
                        is_active TINYINT(1) DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Created whatsapp_settings table")
            else:
                print("[OK] whatsapp_settings table exists")

            if not table_exists('sms_settings'):
                cursor.execute("""
                    CREATE TABLE sms_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        provider ENUM('africas_talking','twilio','vonage','custom') NOT NULL DEFAULT 'africas_talking',
                        api_key VARCHAR(500) NOT NULL,
                        api_secret VARCHAR(500),
                        sender_id VARCHAR(50),
                        username VARCHAR(255),
                        default_country_code VARCHAR(10) DEFAULT '+254',
                        custom_api_url VARCHAR(500),
                        is_active TINYINT(1) DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
                print("[OK] Created sms_settings table")
            else:
                print("[OK] sms_settings table exists")

        return True
    except Exception as e:
        print(f"[ERROR] Failed to create messaging tables: {e}")
        return False
    finally:
        if connection:
            connection.close()

def init_database():
    """Initialize database system: check, create, and update as needed"""
    print("\n" + "="*50)
    print("SHERIA CENTRIC Database Initialization")
    print("="*50)
    
    # Step 1: Check and create database
    if not database_exists():
        print(f"Database '{DB_CONFIG['database']}' not found. Creating...")
        if not create_database():
            print("[ERROR] Failed to create database.")
            print("        On cPanel, create the database in MySQL Databases and assign the user;")
            print("        many hosts do not allow CREATE DATABASE from the app user.")
            return False
    else:
        print(f"[OK] Database '{DB_CONFIG['database']}' exists")
    
    # Step 2: Create schema version table
    if not create_schema_version_table():
        print("[ERROR] Failed to create schema_version table")
        return False
    
    # Step 3: Create company_settings table
    if not create_company_settings_table():
        print("[ERROR] Failed to create/update company_settings table")
        return False
    
    # Step 4: Create employees table
    if not create_employees_table():
        print("[ERROR] Failed to create/update employees table")
        return False

    # Step 4b: Create employee permissions table
    if not create_employee_permissions_table():
        print("[ERROR] Failed to create/update employee_permissions table")
        return False
    
    # Step 5: Create clients table
    if not create_clients_table():
        print("[ERROR] Failed to create/update clients table")
        return False
    
    # Step 5b: Create client personal documents table
    if not create_client_personal_documents_table():
        print("[WARNING] Failed to create client_personal_documents table")

    # Step 6: Create case management tables
    if not create_case_tables():
        print("[ERROR] Failed to create/update case management tables")
        return False
    
    # Step 7: Create matters table
    if not create_matters_table():
        print("[ERROR] Failed to create/update matters table")
        return False
    
    # Step 8: Create email tables
    if not create_email_tables():
        print("[ERROR] Failed to create/update email tables")
        return False
    
    # Step 9: Create webapp_messages table
    if not create_webapp_messages_table():
        print("[ERROR] Failed to create/update webapp_messages table")
        return False
    
    # Step 10: Check schema version and apply migrations
    current_version = get_schema_version()
    print(f"Current schema version: {current_version}")
    print(f"Target schema version: {SCHEMA_VERSION}")
    
    if current_version < SCHEMA_VERSION:
        print("Schema updates detected. Applying migrations...")
        if not apply_migrations(current_version):
            print("[ERROR] Failed to apply migrations")
            return False
    elif current_version == SCHEMA_VERSION:
        print("[OK] Database schema version matches application (numbered migrations)")
    else:
        print(f"[WARNING] Database schema version ({current_version}) is newer than application version ({SCHEMA_VERSION})")

    # Always reconcile additive schema (tables/columns maintained outside migration blocks).
    # Ensures cPanel/Git deploys pick up new DDL without bumping SCHEMA_VERSION every time.
    print("Reconciling additive schema (ensure_* tables/columns)...")
    _reconcile_conn = get_db_connection()
    if not _reconcile_conn:
        print("[ERROR] Failed to connect for additive schema reconciliation")
        return False
    try:
        with _reconcile_conn.cursor(pymysql.cursors.DictCursor) as _rec_cursor:
            reconcile_additive_schema(_rec_cursor, _reconcile_conn)
    except Exception as e:
        print(f"[ERROR] Additive schema reconciliation failed: {e}")
        return False
    finally:
        _reconcile_conn.close()

    verify_core_tables_present()

    print("="*50)
    print("[OK] Database initialization completed successfully")
    print("="*50 + "\n")
    return True

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_document_file(filename):
    """Check if document file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_DOCUMENT_EXTENSIONS

def allowed_id_file(filename):
    """Check if ID/passport file extension is allowed (images or PDF)"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_ID_EXTENSIONS

def process_signature_image(image_file):
    """Process and clean signature/stamp image with optimized algorithms"""
    try:
        # Try to use numpy/scipy for advanced processing
        import numpy as np
        from scipy import ndimage
        USE_NUMPY = True
    except ImportError:
        USE_NUMPY = False
    
    try:
        # Open image with optimization
        img = Image.open(image_file)
        
        # Convert to RGB if necessary (faster than RGBA for initial processing)
        if img.mode not in ('RGB', 'RGBA'):
            if img.mode == 'P' and 'transparency' in img.info:
                img = img.convert('RGBA')
            else:
                img = img.convert('RGB')
        
        # Resize if too large (max 400x200) - use high-quality resampling
        max_width, max_height = 400, 200
        if img.width > max_width or img.height > max_height:
            # Maintain aspect ratio with high-quality resampling
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        
        # Convert to grayscale for efficient processing
        gray = img.convert('L')
        
        if USE_NUMPY:
            # Use numpy for faster processing
            gray_array = np.array(gray, dtype=np.uint8)
            
            # Adaptive thresholding with Gaussian blur for better edge detection
            blurred = ndimage.gaussian_filter(gray_array, sigma=1.0)
            threshold = blurred + 15  # Adaptive threshold
            mask = gray_array < threshold
            
            # Find bounding box of actual content (signature/stamp)
            # Get coordinates of all non-background pixels
            rows = np.any(mask, axis=1)
            cols = np.any(mask, axis=0)
            
            if np.any(rows) and np.any(cols):
                # Calculate bounding box with padding
                padding = 5  # Add small padding around content
                top = max(0, np.argmax(rows) - padding)
                bottom = min(gray_array.shape[0], len(rows) - np.argmax(rows[::-1]) + padding)
                left = max(0, np.argmax(cols) - padding)
                right = min(gray_array.shape[1], len(cols) - np.argmax(cols[::-1]) + padding)
                
                # Crop to bounding box
                gray_array = gray_array[top:bottom, left:right]
                mask = mask[top:bottom, left:right]
            
            # Create result array with transparency
            result_array = np.ones((gray_array.shape[0], gray_array.shape[1], 4), dtype=np.uint8) * 255
            
            # Process signature pixels
            signature_pixels = gray_array[mask]
            if len(signature_pixels) > 0:
                min_val = signature_pixels.min()
                max_val = signature_pixels.max()
                if max_val > min_val:
                    # Normalize and enhance contrast
                    normalized = ((gray_array[mask] - min_val) / (max_val - min_val) * 255).astype(np.uint8)
                    result_array[mask, 0] = 0  # R
                    result_array[mask, 1] = 0  # G
                    result_array[mask, 2] = 0  # B
                    result_array[mask, 3] = 255 - normalized  # Alpha
                else:
                    result_array[mask, :3] = 0
                    result_array[mask, 3] = 255
            
            result = Image.fromarray(result_array, 'RGBA')
        else:
            # Fallback: Use PIL point operations (faster than pixel-by-pixel)
            # Enhanced contrast
            enhancer = ImageEnhance.Contrast(gray)
            gray = enhancer.enhance(1.8)
            
            # Use point operation for efficient thresholding
            threshold = 240
            # Create mask for signature pixels (dark areas)
            signature_mask = gray.point(lambda p: 255 if p < threshold else 0, mode='1')
            
            # Find bounding box of actual content
            bbox = signature_mask.getbbox()
            if bbox:
                # Add padding around content
                padding = 5
                left = max(0, bbox[0] - padding)
                top = max(0, bbox[1] - padding)
                right = min(gray.width, bbox[2] + padding)
                bottom = min(gray.height, bbox[3] + padding)
                
                # Crop to bounding box
                gray = gray.crop((left, top, right, bottom))
                signature_mask = signature_mask.crop((left, top, right, bottom))
            
            # Create result with transparency
            result = Image.new('RGBA', gray.size, (255, 255, 255, 0))
            
            # Process signature pixels with opacity based on darkness
            signature_data = gray.point(lambda p: 255 - p if p < threshold else 0)
            result.paste((0, 0, 0, 255), mask=signature_mask)
            
            # Apply opacity based on pixel darkness
            result_pixels = result.load()
            gray_pixels = gray.load()
            width, height = gray.size
            for x in range(width):
                for y in range(height):
                    if gray_pixels[x, y] < threshold:
                        opacity = 255 - gray_pixels[x, y]
                        result_pixels[x, y] = (0, 0, 0, opacity)
        
        # Apply slight sharpening for better quality
        try:
            result = result.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
        except:
            pass  # Skip if filter fails
        
        # Optimize PNG compression
        output = BytesIO()
        result.save(output, format='PNG', optimize=True, compress_level=6)
        output.seek(0)
        
        return output
    except Exception as e:
        print(f"Error processing signature: {e}")
        return None

def generate_signature_hash(signature_data):
    """Generate hash for signature for digital signing"""
    return hashlib.sha256(signature_data).hexdigest()

def get_company_settings():
    """Get company settings from database"""
    try:
        connection = get_db_connection()
        if not connection:
            return None
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM company_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            return settings
    except Exception as e:
        print(f"Error getting company settings: {e}")
        return None
    finally:
        if connection:
            connection.close()


@app.context_processor
def inject_global_theme_settings():
    """Inject company settings into all templates for consistent theming."""
    settings = get_company_settings()
    if not settings:
        settings = {'company_name': 'BAUNI LAW GROUP'}
    return {'company_settings': settings}

# ==================== WHATSAPP CLOUD API HELPERS ====================

def get_whatsapp_settings():
    """Retrieve active WhatsApp settings from the database"""
    try:
        connection = get_db_connection()
        if not connection:
            return None
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM whatsapp_settings WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
            return cursor.fetchone()
    except Exception as e:
        print(f"Error getting WhatsApp settings: {e}")
        return None
    finally:
        if connection:
            connection.close()


def send_whatsapp_message(to_phone, text_body, settings=None):
    """Send a text message via the WhatsApp Cloud API.
    Returns (success: bool, wa_message_id_or_error: str)
    """

    if not settings:
        settings = get_whatsapp_settings()
    if not settings:
        return False, 'WhatsApp not configured'

    # Normalise phone: strip spaces/dashes, ensure leading +
    phone = to_phone.strip().replace(' ', '').replace('-', '')
    if not phone.startswith('+'):
        phone = '+' + phone

    url = f"https://graph.facebook.com/{settings['api_version']}/{settings['phone_number_id']}/messages"
    headers = {
        'Authorization': f"Bearer {settings['access_token']}",
        'Content-Type': 'application/json'
    }
    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': phone.lstrip('+'),
        'type': 'text',
        'text': {'preview_url': False, 'body': text_body}
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        data = resp.json()
        if resp.status_code in (200, 201) and 'messages' in data:
            wa_id = data['messages'][0].get('id', '')
            return True, wa_id
        error_msg = data.get('error', {}).get('message', resp.text[:200])
        print(f"WhatsApp API error: {error_msg}")
        return False, error_msg
    except Exception as e:
        print(f"WhatsApp send exception: {e}")
        return False, str(e)


def send_whatsapp_media(to_phone, media_url, caption='', media_type='document', settings=None):
    """Send an image or document via WhatsApp Cloud API."""

    if not settings:
        settings = get_whatsapp_settings()
    if not settings:
        return False, 'WhatsApp not configured'

    phone = to_phone.strip().replace(' ', '').replace('-', '')
    if not phone.startswith('+'):
        phone = '+' + phone

    url = f"https://graph.facebook.com/{settings['api_version']}/{settings['phone_number_id']}/messages"
    headers = {
        'Authorization': f"Bearer {settings['access_token']}",
        'Content-Type': 'application/json'
    }

    # media_type should be 'image' or 'document'
    media_obj = {'link': media_url}
    if caption:
        media_obj['caption'] = caption
    if media_type == 'document':
        media_obj['filename'] = caption or 'attachment'

    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': phone.lstrip('+'),
        'type': media_type,
        media_type: media_obj
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        if resp.status_code in (200, 201) and 'messages' in data:
            return True, data['messages'][0].get('id', '')
        error_msg = data.get('error', {}).get('message', resp.text[:200])
        print(f"WhatsApp media API error: {error_msg}")
        return False, error_msg
    except Exception as e:
        print(f"WhatsApp media send exception: {e}")
        return False, str(e)


# ==================== SMS HELPERS ====================

def get_sms_settings():
    """Retrieve active SMS settings from the database"""
    try:
        connection = get_db_connection()
        if not connection:
            return None
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM sms_settings WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
            return cursor.fetchone()
    except Exception as e:
        print(f"Error getting SMS settings: {e}")
        return None
    finally:
        if connection:
            connection.close()


def send_sms(to_phone, text_body, settings=None):
    """Send an SMS via the configured provider.
    Returns (success: bool, result_or_error: str)
    """
    if not settings:
        settings = get_sms_settings()
    if not settings:
        return False, 'SMS not configured'

    provider = settings.get('provider', '')
    api_key = settings.get('api_key', '')
    api_secret = settings.get('api_secret', '')
    sender_id = settings.get('sender_id', '')
    username = settings.get('username', '')
    country_code = settings.get('default_country_code', '+254')

    # Normalise phone
    phone = to_phone.strip().replace(' ', '').replace('-', '')
    if not phone.startswith('+') and not phone.startswith('0'):
        phone = country_code + phone
    elif phone.startswith('0'):
        phone = country_code + phone[1:]

    try:
        if provider == 'africas_talking':
            return _send_sms_africas_talking(phone, text_body, api_key, username, sender_id)
        elif provider == 'twilio':
            return _send_sms_twilio(phone, text_body, api_key, api_secret, sender_id)
        elif provider == 'vonage':
            return _send_sms_vonage(phone, text_body, api_key, api_secret, sender_id)
        elif provider == 'custom':
            custom_url = settings.get('custom_api_url', '')
            return _send_sms_custom(phone, text_body, api_key, api_secret, sender_id, custom_url)
        else:
            return False, f'Unknown SMS provider: {provider}'
    except Exception as e:
        print(f"SMS send exception: {e}")
        return False, str(e)


def _send_sms_africas_talking(phone, text, api_key, username, sender_id):
    """Send SMS via Africa's Talking API"""
    url = 'https://api.africastalking.com/version1/messaging'
    if username == 'sandbox':
        url = 'https://api.sandbox.africastalking.com/version1/messaging'

    headers = {
        'apiKey': api_key,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    data = {
        'username': username,
        'to': phone,
        'message': text,
    }
    if sender_id:
        data['from'] = sender_id

    resp = requests.post(url, headers=headers, data=data, timeout=15)
    result = resp.json()

    recipients = result.get('SMSMessageData', {}).get('Recipients', [])
    if recipients:
        status = recipients[0].get('status', '')
        msg_id = recipients[0].get('messageId', '')
        if status == 'Success':
            return True, msg_id
        return False, f"AT status: {status} — {recipients[0].get('statusCode', '')}"

    error_msg = result.get('SMSMessageData', {}).get('Message', resp.text[:200])
    return False, error_msg


def _send_sms_twilio(phone, text, account_sid, auth_token, from_number):
    """Send SMS via Twilio API"""
    url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json'
    data = {
        'To': phone,
        'From': from_number,
        'Body': text,
    }
    resp = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=15)
    result = resp.json()
    if resp.status_code in (200, 201):
        return True, result.get('sid', '')
    return False, result.get('message', resp.text[:200])


def _send_sms_vonage(phone, text, api_key, api_secret, sender_id):
    """Send SMS via Vonage (Nexmo) API"""
    url = 'https://rest.nexmo.com/sms/json'
    payload = {
        'api_key': api_key,
        'api_secret': api_secret,
        'to': phone.lstrip('+'),
        'from': sender_id or 'SHERIA',
        'text': text,
    }
    resp = requests.post(url, json=payload, timeout=15)
    result = resp.json()
    msgs = result.get('messages', [])
    if msgs and msgs[0].get('status') == '0':
        return True, msgs[0].get('message-id', '')
    error = msgs[0].get('error-text', resp.text[:200]) if msgs else resp.text[:200]
    return False, error


def _send_sms_custom(phone, text, api_key, api_secret, sender_id, custom_url):
    """Send SMS via a custom API endpoint (POST JSON)"""
    if not custom_url:
        return False, 'Custom API URL not set'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    payload = {
        'to': phone,
        'message': text,
        'from': sender_id,
        'api_key': api_key,
        'api_secret': api_secret,
    }
    resp = requests.post(custom_url, json=payload, headers=headers, timeout=15)
    if resp.status_code in (200, 201):
        try:
            data = resp.json()
            return True, data.get('id', data.get('message_id', 'OK'))
        except Exception:
            return True, 'OK'
    return False, resp.text[:200]


@app.route('/')
def index():
    """Public landing page for visitors; logged-in users go to their dashboard."""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))
    return render_template('index.html')


def _redirect_authenticated_user_from_public_site():
    """Redirect signed-in users away from public marketing pages."""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))
    return None


@app.route('/platform')
def website_platform():
    """Public website platform page."""
    signed_in_redirect = _redirect_authenticated_user_from_public_site()
    if signed_in_redirect:
        return signed_in_redirect
    return render_template('platform.html')


@app.route('/features')
def website_features():
    """Public website features page."""
    signed_in_redirect = _redirect_authenticated_user_from_public_site()
    if signed_in_redirect:
        return signed_in_redirect
    return render_template('features.html')


@app.route('/pricing')
def website_pricing():
    """Public website pricing page."""
    signed_in_redirect = _redirect_authenticated_user_from_public_site()
    if signed_in_redirect:
        return signed_in_redirect
    return render_template('pricing.html')


@app.route('/security')
def website_security():
    """Public website security page."""
    signed_in_redirect = _redirect_authenticated_user_from_public_site()
    if signed_in_redirect:
        return signed_in_redirect
    return render_template('security.html')


@app.route('/contact')
def website_contact():
    """Public website contact page."""
    signed_in_redirect = _redirect_authenticated_user_from_public_site()
    if signed_in_redirect:
        return signed_in_redirect
    return render_template('contact.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page with employee and client options"""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))
    
    if request.method == 'POST':
        employee_code = request.form.get('employee_code', '').strip()
        password = request.form.get('password', '')
        
        if not employee_code or not password:
            flash('Please enter both employee code and password', 'error')
            return render_template('login.html')
        
        connection = get_db_connection()
        if not connection:
            flash('Database connection error. Please try again later.', 'error')
            return render_template('login.html')
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT * FROM employees 
                    WHERE employee_code = %s
                """, (employee_code,))
                employee = cursor.fetchone()
                
                if employee and check_password_hash(employee['password_hash'], password):
                    if employee['status'] == 'Suspended':
                        flash('Your account has been suspended. Please contact administrator.', 'error')
                        return render_template('login.html')
                    elif employee['status'] == 'Pending Approval':
                        # Allow login only if onboarding is NOT completed
                        if employee.get('onboarding_completed'):
                            flash('Your onboarding has been submitted and is pending approval. Please wait for administrator approval.', 'warning')
                            return render_template('login.html')
                        else:
                            # Allow login to complete onboarding
                            session['employee_id'] = employee['id']
                            session['employee_name'] = employee['full_name']
                            session['employee_role'] = employee['role']
                            session['profile_picture'] = employee.get('profile_picture', '')
                            # Get company name from company_settings
                            company_settings = get_company_settings()
                            if company_settings:
                                session['company_name'] = company_settings.get('company_name', 'BAUNI LAW GROUP')
                            else:
                                session['company_name'] = 'BAUNI LAW GROUP'
                            
                            # Redirect to onboarding if not completed
                            if not employee.get('onboarding_completed'):
                                return redirect(url_for('onboarding'))
                            
                            return redirect(url_for('dashboard'))
                    else:
                        # Active status - normal login
                        session['employee_id'] = employee['id']
                        session['employee_name'] = employee['full_name']
                        session['employee_role'] = employee['role']
                        session['profile_picture'] = employee.get('profile_picture', '')
                        # Get company name from company_settings
                        company_settings = get_company_settings()
                        if company_settings:
                            session['company_name'] = company_settings.get('company_name', 'BAUNI LAW GROUP')
                        else:
                            session['company_name'] = 'BAUNI LAW GROUP'
                        
                        # Check if onboarding is completed
                        if not employee.get('onboarding_completed'):
                            return redirect(url_for('onboarding'))
                        
                        return redirect(url_for('dashboard'))
                else:
                    flash('Invalid employee code or password', 'error')
        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred during login. Please try again.', 'error')
        finally:
            connection.close()
    
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """Signup page"""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip().upper()
        phone_number_local = request.form.get('phone_number', '').strip()
        country_code = request.form.get('country_code', '').strip()
        phone_number_local = re.sub(r'\D', '', phone_number_local)
        country_digits = re.sub(r'\D', '', country_code or '')
        # Prevent accidental double-prefixing if the posted number already includes dial digits.
        if country_digits and phone_number_local.startswith(country_digits) and len(phone_number_local) > len(country_digits):
            phone_number_local = phone_number_local[len(country_digits):]
        phone_number = f"+{country_digits}{phone_number_local}" if country_digits and phone_number_local else phone_number_local
        work_email = request.form.get('work_email', '').strip().lower()
        employee_code = request.form.get('employee_code', '').strip().upper()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        terms_accepted = request.form.get('terms_accepted')
        
        # Validation
        errors = []
        if not full_name:
            errors.append('Full name is required')
        if not phone_number_local:
            errors.append('Phone number is required')
        if not work_email:
            errors.append('Email is required')
        if not employee_code or len(employee_code) != 6:
            errors.append('Firm code must be 6 digits')
        if not password or len(password) < 6:
            errors.append('Password must be at least 6 characters (letters and numbers allowed)')
        if password != confirm_password:
            errors.append('Passwords do not match')
        if not terms_accepted:
            errors.append('You must accept the terms and conditions')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('signup.html')
        
        # Handle file upload
        profile_picture = None
        if 'profile_picture' in request.files:
            file = request.files['profile_picture']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Create unique filename
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{employee_code}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                profile_picture = unique_filename
        
        # Save to database
        connection = get_db_connection()
        if not connection:
            flash('Database connection error. Please try again later.', 'error')
            return render_template('signup.html')
        
        try:
            with connection.cursor() as cursor:
                password_hash = generate_password_hash(password)
                cursor.execute("""
                    INSERT INTO employees 
                    (full_name, phone_number, work_email, employee_code, password_hash, profile_picture, role, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'Employee', 'Pending Approval')
                """, (full_name, phone_number, work_email, employee_code, password_hash, profile_picture))
                connection.commit()
                flash('Registration successful! Your account is pending approval.', 'success')
                return redirect(url_for('login'))
        except pymysql.IntegrityError as e:
            if 'employee_code' in str(e):
                flash('Firm code already exists', 'error')
            elif 'work_email' in str(e):
                flash('Email already registered', 'error')
            else:
                flash('Registration failed. Please try again.', 'error')
        except Exception as e:
            print(f"Signup error: {e}")
            flash('An error occurred during registration. Please try again.', 'error')
        finally:
            connection.close()
    
    return render_template('signup.html')

@app.route('/check_employee_code', methods=['POST'])
def check_employee_code():
    """Check if firm code is already in use"""
    try:
        data = request.get_json()
        employee_code = data.get('employee_code', '').strip().upper()
        
        if not employee_code or len(employee_code) != 6:
            return jsonify({'available': False, 'message': 'Firm code must be 6 digits'})
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'available': False, 'message': 'Database connection error'})
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT id FROM employees WHERE employee_code = %s", (employee_code,))
                result = cursor.fetchone()
                
                if result:
                    return jsonify({'available': False, 'message': 'Firm code already in use'})
                else:
                    return jsonify({'available': True, 'message': 'Firm code is available'})
        except Exception as e:
            print(f"Error checking firm code: {e}")
            return jsonify({'available': False, 'message': 'Error checking firm code'})
        finally:
            connection.close()
    except Exception as e:
        print(f"Error in check_employee_code endpoint: {e}")
        return jsonify({'available': False, 'message': 'Server error'})

@app.route('/dashboard')
def dashboard():
    """Employee dashboard"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('login'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM employees WHERE id = %s
            """, (session['employee_id'],))
            employee = cursor.fetchone()
            
            if not employee:
                session.clear()
                flash('Employee not found', 'error')
                return redirect(url_for('login'))
            
            # If status is Pending Approval and onboarding is completed, block access
            if employee.get('status') == 'Pending Approval' and employee.get('onboarding_completed'):
                session.clear()
                flash('Your onboarding has been submitted and is pending approval. Please wait for administrator approval.', 'warning')
                return redirect(url_for('login'))
            
            # Check if onboarding is completed, redirect if not (for Active employees)
            if employee.get('status') == 'Active' and not employee.get('onboarding_completed'):
                return redirect(url_for('onboarding'))
            
            # If status is Pending Approval and onboarding not completed, allow access to onboarding
            if employee.get('status') == 'Pending Approval' and not employee.get('onboarding_completed'):
                return redirect(url_for('onboarding'))
            
            # Get company settings
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            # Initialize variables
            individual_clients = []
            corporate_clients = []
            pending_clients = []
            all_employees = []
            active_clients = []
            
            # Check role - handle both string and potential case variations
            user_role = employee.get('role', '') or session.get('employee_role', '')
            
            # Fetch active clients for ALL roles
            cursor.execute("""
                SELECT 
                    id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE status = 'Active'
                ORDER BY created_at DESC
            """)
            active_clients_data = cursor.fetchall()
            
            # Format active clients
            for client in active_clients_data:
                formatted_client = dict(client)
                if formatted_client.get('created_at'):
                    formatted_client['created_at'] = formatted_client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                active_clients.append(formatted_client)
            
            if user_role in ('Clerk', 'Firm Administrator', 'Managing Partner'):
                # Fetch all clients and categorize by type
                cursor.execute("""
                    SELECT 
                        id,
                        full_name,
                        email,
                        phone_number,
                        profile_picture,
                        client_type,
                        status,
                        created_at
                    FROM clients
                    ORDER BY created_at DESC
                """)
                all_clients = cursor.fetchall()
                
                formatted_all_clients = []
                for client in all_clients:
                    formatted_client = dict(client)
                    
                    if formatted_client.get('created_at'):
                        formatted_client['created_at'] = formatted_client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    
                    formatted_all_clients.append(formatted_client)
                    
                    client_type = formatted_client.get('client_type', 'Pending')
                    
                    if client_type == 'Individual':
                        individual_clients.append(formatted_client)
                    elif client_type == 'Corporate':
                        corporate_clients.append(formatted_client)
                    else:
                        pending_clients.append(formatted_client)
                
                # Fetch all employees
                cursor.execute("""
                    SELECT 
                        id,
                        full_name,
                        work_email,
                        phone_number,
                        employee_code,
                        role,
                        status,
                        profile_picture,
                        created_at
                    FROM employees
                    ORDER BY created_at DESC
                """)
                all_employees = cursor.fetchall()
                
                for emp in all_employees:
                    if emp.get('created_at'):
                        emp['created_at'] = emp['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            return render_template('dashboard.html', 
                                 employee=employee, 
                                 user_role=user_role,
                                 company_settings=company_settings,
                                 individual_clients=individual_clients,
                                 corporate_clients=corporate_clients,
                                 pending_clients=pending_clients,
                                 all_clients=formatted_all_clients if user_role in ('Clerk', 'Firm Administrator', 'Managing Partner') else [],
                                 all_employees=all_employees,
                                 active_clients=active_clients)
    except Exception as e:
        print(f"Dashboard error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('login'))
    finally:
        connection.close()

@app.route('/user_management')
def user_management():
    """User Management hub with live stats and role-based modules."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            company_settings = get_company_settings() or {'company_name': 'BAUNI LAW GROUP'}

            cursor.execute("SELECT COUNT(*) AS total FROM employees WHERE status = 'Active'")
            total_active_employees = (cursor.fetchone() or {}).get('total', 0)

            cursor.execute("SELECT COUNT(*) AS total FROM employees WHERE status = 'Pending Approval'")
            pending_employee_approvals = (cursor.fetchone() or {}).get('total', 0)

            cursor.execute("""
                SELECT COUNT(DISTINCT role) AS total
                FROM employees
                WHERE status = 'Active' AND role IS NOT NULL AND role <> ''
            """)
            active_roles_count = (cursor.fetchone() or {}).get('total', 0)

            cursor.execute("SELECT COUNT(*) AS total FROM clients WHERE status = 'Pending Approval'")
            pending_client_approvals = (cursor.fetchone() or {}).get('total', 0)

            modules = [
                {
                    'title': 'Employee Records',
                    'description': 'View, filter, edit, and maintain employee records.',
                    'icon': 'fa-user-tie',
                    'endpoint': 'employee_management',
                    'accent': 'green'
                },
                {
                    'title': 'Roles & Permissions',
                    'description': 'Define role access and enforce permission boundaries.',
                    'icon': 'fa-shield-alt',
                    'endpoint': 'roles_permissions',
                    'accent': 'amber'
                },
                {
                    'title': 'Client Management',
                    'description': 'Manage client profiles, types, and onboarding flow.',
                    'icon': 'fa-user-friends',
                    'endpoint': 'client_management',
                    'accent': 'blue'
                },
            ]

            return render_template(
                'user_management.html',
                company_settings=company_settings,
                user_role=user_role,
                original_role=original_role,
                module_cards=modules,
                user_mgmt_stats={
                    'total_active_employees': int(total_active_employees or 0),
                    'pending_employee_approvals': int(pending_employee_approvals or 0),
                    'active_roles_count': int(active_roles_count or 0),
                    'pending_client_approvals': int(pending_client_approvals or 0),
                }
            )
    except Exception as e:
        print(f"User management error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/employee_management')
def employee_management():
    """Employee Management page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        # Get company settings
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        return render_template('employee_management.html', company_settings=company_settings)
    except Exception as e:
        print(f"Employee Management error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/api/get_pending_approvals')
def get_pending_approvals():
    """Get all employees with pending approval status"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return {'error': 'Forbidden'}, 403
    
    connection = get_db_connection()
    if not connection:
        return {'error': 'Database error'}, 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, phone_number, work_email, employee_code,
                       role, status, created_at, onboarding_completed, profile_picture
                FROM employees 
                WHERE status = 'Pending Approval'
                ORDER BY onboarding_completed DESC, created_at DESC
            """)
            employees = cursor.fetchall()
            
            for emp in employees:
                if emp.get('created_at'):
                    emp['created_at'] = emp['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                emp['onboarding_completed'] = bool(emp.get('onboarding_completed'))
            
            total = len(employees)
            onboarding_done = sum(1 for e in employees if e.get('onboarding_completed'))
            onboarding_pending = total - onboarding_done
            
            return {
                'success': True,
                'employees': employees,
                'count': total,
                'onboarding_done': onboarding_done,
                'onboarding_pending': onboarding_pending
            }
    except Exception as e:
        print(f"Error fetching pending approvals: {e}")
        return {'error': str(e)}, 500
    finally:
        connection.close()

@app.route('/api/assign_role_and_approve', methods=['POST'])
def assign_role_and_approve():
    """Assign a role to an employee and approve them"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return jsonify({'error': 'Forbidden'}), 403
    
    data = request.get_json()
    employee_id = data.get('employee_id')
    new_role = data.get('role')
    
    if not employee_id:
        return jsonify({'success': False, 'error': 'Employee ID required'}), 400
    
    valid_roles = ['Firm Administrator', 'Managing Partner', 'Finance Office', 'Associate Advocate', 'Clerk', 'IT Support', 'Employee']
    if new_role and new_role not in valid_roles:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500

    # Fine-grained permission: approve employee
    deny = enforce_permission(connection, 'employee_approve')
    if deny:
        return jsonify({'success': False, 'error': 'Forbidden'}), 403
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT onboarding_completed FROM employees WHERE id = %s", (employee_id,))
            employee = cursor.fetchone()
            if not employee:
                return jsonify({'success': False, 'error': 'Employee not found'}), 404
            if not employee.get('onboarding_completed'):
                return jsonify({'success': False, 'error': 'Cannot approve. Onboarding must be completed first.'}), 400
            
            if new_role:
                cursor.execute("UPDATE employees SET role = %s, status = 'Active' WHERE id = %s", (new_role, employee_id))
            else:
                cursor.execute("UPDATE employees SET status = 'Active' WHERE id = %s", (employee_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Employee approved successfully'})
    except Exception as e:
        print(f"Error assigning role and approving: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/get_all_employees')
def get_all_employees():
    """Get all employees"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return {'error': 'Forbidden'}, 403
    
    connection = get_db_connection()
    if not connection:
        return {'error': 'Database error'}, 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, phone_number, work_email, employee_code, role, status, created_at
                FROM employees 
                ORDER BY created_at DESC
            """)
            employees = cursor.fetchall()
            
            for emp in employees:
                if emp.get('created_at'):
                    emp['created_at'] = emp['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            return {'success': True, 'employees': employees}
    except Exception as e:
        print(f"Error fetching employees: {e}")
        return {'error': str(e)}, 500
    finally:
        connection.close()

@app.route('/api/get_active_employees')
def get_active_employees():
    """Get all approved/active employees with role breakdown"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return jsonify({'error': 'Forbidden'}), 403
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, phone_number, work_email, employee_code,
                       role, status, profile_picture, created_at
                FROM employees
                WHERE status = 'Active'
                ORDER BY role, full_name
            """)
            employees = cursor.fetchall()
            
            role_counts = {}
            for emp in employees:
                if emp.get('created_at'):
                    emp['created_at'] = emp['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                r = emp.get('role') or 'Employee'
                role_counts[r] = role_counts.get(r, 0) + 1
            
            return jsonify({
                'success': True,
                'employees': employees,
                'total': len(employees),
                'role_counts': role_counts
            })
    except Exception as e:
        print(f"Error fetching active employees: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/get_employee')
def get_employee():
    """Get single employee by ID"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    # Check permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return {'error': 'Forbidden'}, 403
    
    employee_id = request.args.get('id')
    if not employee_id:
        return {'error': 'Employee ID required'}, 400
    
    connection = get_db_connection()
    if not connection:
        return {'error': 'Database error'}, 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, phone_number, work_email, employee_code, role, status
                FROM employees 
                WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return {'error': 'Employee not found'}, 404
            
            return jsonify({'success': True, 'employee': employee})
    except Exception as e:
        print(f"Error fetching employee: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/get_employee_onboarding_details')
def get_employee_onboarding_details():
    """Get employee onboarding details for approval review"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return jsonify({'error': 'Forbidden'}), 403
    
    employee_id = request.args.get('id')
    if not employee_id:
        return jsonify({'error': 'Employee ID required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if not column_exists('employees', 'kra_pin_document'):
                try:
                    cursor.execute("ALTER TABLE employees ADD COLUMN kra_pin_document VARCHAR(255)")
                    connection.commit()
                except Exception as e:
                    print(f"Could not add kra_pin_document column: {e}")
            cursor.execute("""
                SELECT 
                    id, full_name, phone_number, work_email, employee_code, role, status,
                    account_number, account_name, salary, salary_components, tax_pin, pay_frequency,
                    payment_method, bank_name, mobile_money_company,
                    employment_contract, id_front, id_back, kra_pin_document, signature, stamp,
                    onboarding_completed, created_at
                FROM employees 
                WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return jsonify({'error': 'Employee not found'}), 404
            
            # Convert datetime to string
            if employee.get('created_at'):
                employee['created_at'] = employee['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            return jsonify({'success': True, 'employee': employee})
    except Exception as e:
        print(f"Error fetching employee onboarding details: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/update_employee_onboarding', methods=['POST'])
def update_employee_onboarding():
    """Update employee onboarding details (admin from approvals page). Supports multipart form + optional file uploads."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    if user_role not in allowed_roles and original_role != 'IT Support':
        return jsonify({'success': False, 'error': 'Forbidden'}), 403

    employee_id = request.form.get('employee_id')
    if not employee_id:
        return jsonify({'success': False, 'error': 'Employee ID required'}), 400
    try:
        employee_id = int(employee_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid employee ID'}), 400

    full_name = request.form.get('full_name', '').strip()
    employee_code = request.form.get('employee_code', '').strip()
    work_email = request.form.get('work_email', '').strip()
    phone_number = request.form.get('phone_number', '').strip()
    role = request.form.get('role', 'Employee').strip()
    status = request.form.get('status', 'Pending Approval').strip()
    payment_method = request.form.get('payment_method', '').strip() or None
    bank_name = request.form.get('bank_name', '').strip() or None
    mobile_money_company = request.form.get('mobile_money_company', '').strip() or None
    account_number = request.form.get('account_number', '').strip() or None
    account_name = request.form.get('account_name', '').strip() or None
    pay_frequency = request.form.get('pay_frequency', '').strip() or None
    salary = request.form.get('salary', '').strip() or None
    salary_components = request.form.get('salary_components', '').strip() or None
    if not full_name or not work_email or not phone_number:
        return jsonify({'success': False, 'error': 'Full name, email and phone are required'}), 400

    upload_folder = app.config['UPLOAD_FOLDER']
    employment_contract = None
    if 'employment_contract' in request.files:
        f = request.files['employment_contract']
        if f and f.filename and allowed_document_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            name = f"contract_{employee_id}_{secrets.token_hex(8)}.{ext}"
            f.save(os.path.join(upload_folder, name))
            employment_contract = name

    id_front = None
    if 'id_front' in request.files:
        f = request.files['id_front']
        if f and f.filename and allowed_id_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            name = f"id_document_{employee_id}_{secrets.token_hex(8)}.{ext}"
            f.save(os.path.join(upload_folder, name))
            id_front = name

    kra_pin_document = None
    if 'kra_pin_document' in request.files:
        f = request.files['kra_pin_document']
        if f and f.filename and (allowed_id_file(f.filename) or allowed_document_file(f.filename)):
            ext = f.filename.rsplit('.', 1)[1].lower()
            name = f"kra_pin_{employee_id}_{secrets.token_hex(8)}.{ext}"
            f.save(os.path.join(upload_folder, name))
            kra_pin_document = name

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500

    # Fine-grained permission: edit employee details via onboarding update
    deny = enforce_permission(connection, 'employee_edit')
    if deny:
        return jsonify({'success': False, 'error': 'Forbidden'}), 403
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT id, employment_contract, id_front, kra_pin_document FROM employees WHERE id = %s", (employee_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': 'Employee not found'}), 404
            cur.execute("SELECT id FROM employees WHERE work_email = %s AND id != %s", (work_email, employee_id))
            if cur.fetchone():
                return jsonify({'success': False, 'error': 'Work email already in use by another employee'}), 400

            set_parts = [
                "full_name = %s", "work_email = %s", "phone_number = %s", "role = %s", "status = %s",
                "payment_method = %s", "bank_name = %s", "mobile_money_company = %s",
                "account_number = %s", "account_name = %s",
                "pay_frequency = %s", "salary = %s", "salary_components = %s"
            ]
            params = [
                full_name, work_email, phone_number, role, status,
                payment_method, bank_name, mobile_money_company,
                account_number, account_name,
                pay_frequency, salary, salary_components
            ]
            if employee_code:
                set_parts.append("employee_code = %s")
                params.append(employee_code)
            if employment_contract is not None:
                set_parts.append("employment_contract = %s")
                params.append(employment_contract)
            elif row[1]:
                set_parts.append("employment_contract = %s")
                params.append(row[1])
            if id_front is not None:
                set_parts.append("id_front = %s")
                params.append(id_front)
            elif row[2]:
                set_parts.append("id_front = %s")
                params.append(row[2])
            if kra_pin_document is not None:
                set_parts.append("kra_pin_document = %s")
                params.append(kra_pin_document)
            elif len(row) > 3 and row[3]:
                set_parts.append("kra_pin_document = %s")
                params.append(row[3])
            params.append(employee_id)
            cur.execute(
                "UPDATE employees SET " + ", ".join(set_parts) + " WHERE id = %s",
                params
            )
            connection.commit()
        return jsonify({'success': True, 'message': 'Onboarding details updated successfully'})
    except Exception as e:
        print(f"Error updating employee onboarding: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/update_employee_status', methods=['POST'])
def update_employee_status():
    """Update employee status (Active/Suspended/Pending Approval)"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    # Check permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return {'error': 'Forbidden'}, 403
    
    employee_id = request.args.get('id')
    new_status = request.args.get('status')
    
    if not employee_id or not new_status:
        return jsonify({'success': False, 'error': 'Employee ID and status required'}), 400
    
    if new_status not in ['Active', 'Suspended', 'Pending Approval']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500

    # Fine-grained permission: approve/suspend employees
    deny = enforce_permission(connection, 'employee_suspend')
    if deny:
        return jsonify({'success': False, 'error': 'Forbidden'}), 403
    
    try:
        # If approving, check if onboarding is completed
        if new_status == 'Active':
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT onboarding_completed FROM employees WHERE id = %s
                """, (employee_id,))
                employee = cursor.fetchone()
                if not employee:
                    return jsonify({'success': False, 'error': 'Employee not found'}), 404
                if not employee.get('onboarding_completed'):
                    return jsonify({'success': False, 'error': 'Cannot approve employee. Onboarding must be completed first.'}), 400
        
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE employees 
                SET status = %s 
                WHERE id = %s
            """, (new_status, employee_id))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Status updated successfully'})
    except Exception as e:
        print(f"Error updating employee status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/update_employee', methods=['POST'])
def update_employee():
    """Update employee details"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    # Check permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return {'error': 'Forbidden'}, 403
    
    data = request.get_json()
    employee_id = data.get('employee_id')
    
    if not employee_id:
        return {'error': 'Employee ID required'}, 400
    
    full_name = data.get('full_name', '').strip()
    phone_number = data.get('phone_number', '').strip()
    work_email = data.get('work_email', '').strip()
    role = data.get('role', 'Employee')
    status = data.get('status', 'Pending Approval')
    
    # Validation
    if not full_name or not phone_number or not work_email:
        return {'error': 'All fields are required'}, 400
    
    connection = get_db_connection()
    if not connection:
        return {'error': 'Database error'}, 500

    # Fine-grained permission: edit employee details
    deny = enforce_permission(connection, 'employee_edit')
    if deny:
        return {'error': 'Forbidden'}, 403
    
    try:
        with connection.cursor() as cursor:
            # Check if email is already taken by another user
            cursor.execute("""
                SELECT id FROM employees 
                WHERE work_email = %s AND id != %s
            """, (work_email, employee_id))
            if cursor.fetchone():
                return {'error': 'Work email is already registered by another user'}, 400
            
            # Update employee
            cursor.execute("""
                UPDATE employees 
                SET full_name = %s, phone_number = %s, work_email = %s, role = %s, status = %s
                WHERE id = %s
            """, (full_name, phone_number, work_email, role, status, employee_id))
            connection.commit()
            
            return {'success': True, 'message': 'Employee updated successfully'}
    except Exception as e:
        print(f"Error updating employee: {e}")
        return {'error': str(e)}, 500
    finally:
        connection.close()

@app.route('/api/delete_employee', methods=['POST'])
def delete_employee():
    """Delete an employee"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    # Check permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return {'error': 'Forbidden'}, 403
    
    employee_id = request.args.get('id')
    if not employee_id:
        return {'error': 'Employee ID required'}, 400
    
    # Prevent deleting yourself
    if int(employee_id) == session.get('employee_id'):
        return {'error': 'You cannot delete your own account'}, 400
    
    connection = get_db_connection()
    if not connection:
        return {'error': 'Database error'}, 500

    # Fine-grained permission: delete employee
    deny = enforce_permission(connection, 'employee_delete')
    if deny:
        return {'error': 'Forbidden'}, 403
    
    try:
        with connection.cursor() as cursor:
            # Get profile picture to delete file
            cursor.execute("SELECT profile_picture FROM employees WHERE id = %s", (employee_id,))
            result = cursor.fetchone()
            if result and result[0]:
                profile_picture = result[0]
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], profile_picture)
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except:
                        pass
            
            # Delete employee
            cursor.execute("DELETE FROM employees WHERE id = %s", (employee_id,))
            connection.commit()
            
            return {'success': True, 'message': 'Employee deleted successfully'}
    except Exception as e:
        print(f"Error deleting employee: {e}")
        return {'error': str(e)}, 500
    finally:
        connection.close()

@app.route('/roles_permissions')
def roles_permissions():
    """Roles & Permissions page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        # Get company settings
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        return render_template('roles_permissions.html', company_settings=company_settings)
    except Exception as e:
        print(f"Roles & Permissions error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/individual_client_management')
def individual_client_management():
    """Individual Client Management page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        # Get company settings
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        return render_template('individual_client_management.html', company_settings=company_settings)
    except Exception as e:
        print(f"Individual Client Management error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/corporate_client_management')
def corporate_client_management():
    """Corporate Client Management page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has permission
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        # Get company settings
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        return render_template('corporate_client_management.html', company_settings=company_settings)
    except Exception as e:
        print(f"Corporate Client Management error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/client_management')
def client_management():
    """Unified Client Management page with Individual/Corporate tabs"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')

    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}

            cursor.execute("""
                SELECT id, full_name, email, phone_number, profile_picture,
                       client_type, status, created_at
                FROM clients
                ORDER BY created_at DESC
            """)
            all_clients = cursor.fetchall()

            individual_clients = []
            corporate_clients = []
            pending_clients = []

            for client in all_clients:
                formatted = dict(client)
                if formatted.get('created_at'):
                    formatted['created_at'] = formatted['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                ctype = formatted.get('client_type', 'Pending')
                if ctype == 'Individual':
                    individual_clients.append(formatted)
                elif ctype == 'Corporate':
                    corporate_clients.append(formatted)
                else:
                    pending_clients.append(formatted)

            return render_template('client_management.html',
                                   company_settings=company_settings,
                                   individual_clients=individual_clients,
                                   corporate_clients=corporate_clients,
                                   pending_clients=pending_clients,
                                   total_clients=len(all_clients))
    except Exception as e:
        print(f"Client Management error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()


@app.route('/client_management/register', methods=['POST'])
def register_client():
    """Register a new client (basic info only, client completes registration via portal)"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to register clients', 'error')
        return redirect(url_for('client_management'))

    full_name = request.form.get('full_name', '').strip()
    email_addr = request.form.get('email', '').strip()

    if not full_name or not email_addr:
        flash('Full name and email are required', 'error')
        return redirect(url_for('client_management'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('client_management'))

    # Fine-grained permission: register client
    deny = enforce_permission(connection, 'client_register', redirect_endpoint='client_management')
    if deny:
        return deny

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM clients WHERE email = %s", (email_addr,))
            if cursor.fetchone():
                flash('A client with this email already exists', 'error')
                return redirect(url_for('client_management'))

            placeholder_google_id = f"manual_{uuid.uuid4().hex}"

            cursor.execute("""
                INSERT INTO clients (google_id, email, full_name, client_type, status)
                VALUES (%s, %s, %s, 'Pending', 'Active')
            """, (placeholder_google_id, email_addr, full_name))
            connection.commit()

            flash(f'Client "{full_name}" registered successfully. They can complete registration via the client portal.', 'success')

    except Exception as e:
        print(f"Register client error: {e}")
        flash('An error occurred while registering the client', 'error')
    finally:
        connection.close()

    return redirect(url_for('client_management'))


@app.route('/client_management/onboard/<int:client_id>')
def onboard_client(client_id):
    """Show onboarding form for a pending client"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to onboard clients', 'error')
        return redirect(url_for('client_management'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('client_management'))

    # Fine-grained permission: approve/onboard client
    deny = enforce_permission(connection, 'client_approve', redirect_endpoint='client_management')
    if deny:
        return deny

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_management'))

            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}

            return render_template('client_onboarding.html',
                                   client=client,
                                   company_settings=company_settings)
    except Exception as e:
        print(f"Onboard client error: {e}")
        flash('An error occurred', 'error')
        return redirect(url_for('client_management'))
    finally:
        connection.close()


@app.route('/client_management/onboard/<int:client_id>', methods=['POST'])
def submit_onboard_client(client_id):
    """Process client onboarding form"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to onboard clients', 'error')
        return redirect(url_for('client_management'))

    client_type = request.form.get('client_type', '').strip()
    phone_number = request.form.get('phone_number', '').strip().replace(' ', '')
    kra_pin = request.form.get('kra_pin', '').strip().upper()
    client_address = request.form.get('client_address', '').strip()
    national_id = request.form.get('national_id', '').strip()
    id_number = request.form.get('id_number', '').strip()

    if not client_type or client_type not in ('Individual', 'Corporate'):
        flash('Please select a valid client type', 'error')
        return redirect(url_for('onboard_client', client_id=client_id))

    if not phone_number:
        flash('Phone number is required', 'error')
        return redirect(url_for('onboard_client', client_id=client_id))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('client_management'))

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_management'))

            update_fields = ['client_type = %s', 'phone_number = %s']
            update_values = [client_type, phone_number]

            if kra_pin:
                update_fields.append('kra_pin = %s')
                update_values.append(kra_pin)
            if client_address:
                update_fields.append('client_address = %s')
                update_values.append(client_address)
            if national_id or id_number:
                update_fields.append('national_id = %s')
                update_values.append(national_id or id_number)

            # Handle file uploads
            upload_files = {}
            file_fields = {
                'id_front': 'id_front',
                'id_back': 'id_back',
                'cr12_certificate': 'cr12_certificate',
                'corporate_kra_pin': 'corporate_kra_pin',
                'instruction_note': 'instruction_note'
            }
            for form_field, label in file_fields.items():
                if form_field in request.files:
                    file = request.files[form_field]
                    if file and file.filename:
                        filename = secure_filename(file.filename)
                        file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'bin'
                        unique_filename = f"{label}_{client_id}_{secrets.token_hex(8)}.{file_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(filepath)
                        upload_files[label] = unique_filename

            for col in ('id_front', 'id_back', 'cr12_certificate', 'instruction_note'):
                if col in upload_files:
                    update_fields.append(f'{col} = %s')
                    update_values.append(upload_files[col])

            update_values.append(client_id)
            query = f"UPDATE clients SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, tuple(update_values))
            connection.commit()

            flash(f'Client "{client["full_name"]}" has been successfully onboarded as {client_type}', 'success')

    except Exception as e:
        print(f"Onboard client submit error: {e}")
        flash('An error occurred during onboarding', 'error')
    finally:
        connection.close()

    return redirect(url_for('client_management'))


@app.route('/client_management/edit/<int:client_id>')
def edit_client_page(client_id):
    """Show edit page for a client with all details and uploads"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to edit clients', 'error')
        return redirect(url_for('client_management'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('client_management'))

    # Fine-grained permission: edit client details (view/edit page)
    deny = enforce_permission(connection, 'client_edit', redirect_endpoint='client_management')
    if deny:
        return deny

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_management'))

            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}

            return render_template('client_edit.html',
                                   client=client,
                                   company_settings=company_settings)
    except Exception as e:
        print(f"Edit client page error: {e}")
        flash('An error occurred', 'error')
        return redirect(url_for('client_management'))
    finally:
        connection.close()


@app.route('/client_management/edit/<int:client_id>', methods=['POST'])
def edit_client(client_id):
    """Process client edit form with all details and uploads"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to perform this action', 'error')
        return redirect(url_for('client_management'))

    full_name = request.form.get('full_name', '').strip()
    email_addr = request.form.get('email', '').strip()
    phone_number = request.form.get('phone_number', '').strip()
    client_type = request.form.get('client_type', '').strip()
    kra_pin = request.form.get('kra_pin', '').strip().upper()
    national_id = request.form.get('national_id', '').strip()
    client_address = request.form.get('client_address', '').strip()

    if not full_name or not email_addr:
        flash('Full name and email are required', 'error')
        return redirect(url_for('edit_client_page', client_id=client_id))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_management'))

    # Fine-grained permission: edit client details (submit)
    deny = enforce_permission(connection, 'client_edit', redirect_endpoint='client_management')
    if deny:
        return deny

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM clients WHERE email = %s AND id != %s", (email_addr, client_id))
            if cursor.fetchone():
                flash('Another client with this email already exists', 'error')
                return redirect(url_for('edit_client_page', client_id=client_id))

            update_fields = [
                'full_name = %s', 'email = %s', 'phone_number = %s',
                'client_type = %s', 'kra_pin = %s', 'national_id = %s',
                'client_address = %s'
            ]
            update_values = [
                full_name, email_addr, phone_number or None,
                client_type or 'Pending', kra_pin or None, national_id or None,
                client_address or None
            ]

            file_fields = {
                'id_front': 'id_front',
                'id_back': 'id_back',
                'cr12_certificate': 'cr12_certificate',
                'corporate_kra_pin': 'corporate_kra_pin',
                'instruction_note': 'instruction_note'
            }
            for form_field, db_col in file_fields.items():
                if form_field in request.files:
                    file = request.files[form_field]
                    if file and file.filename:
                        filename = secure_filename(file.filename)
                        file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'bin'
                        unique_filename = f"{db_col}_{client_id}_{secrets.token_hex(8)}.{file_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(filepath)
                        update_fields.append(f'{db_col} = %s')
                        update_values.append(unique_filename)

            update_values.append(client_id)
            query = f"UPDATE clients SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, tuple(update_values))
            connection.commit()
            flash(f'Client "{full_name}" updated successfully', 'success')
    except Exception as e:
        print(f"Edit client error: {e}")
        flash('An error occurred while updating the client', 'error')
    finally:
        connection.close()

    return redirect(url_for('client_management'))

@app.route('/client_management/suspend/<int:client_id>', methods=['POST'])
def suspend_client(client_id):
    """Toggle client suspension (Active <-> Inactive)"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to perform this action', 'error')
        return redirect(url_for('client_management'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_management'))

    # Fine-grained permission: suspend client
    deny = enforce_permission(connection, 'client_suspend', redirect_endpoint='client_management')
    if deny:
        return deny

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, full_name, status FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_management'))

            new_status = 'Inactive' if client['status'] == 'Active' else 'Active'
            cursor.execute("UPDATE clients SET status = %s WHERE id = %s", (new_status, client_id))
            connection.commit()

            action = 'suspended' if new_status == 'Inactive' else 'reactivated'
            flash(f'Client "{client["full_name"]}" has been {action}', 'success')
    except Exception as e:
        print(f"Suspend client error: {e}")
        flash('An error occurred while updating client status', 'error')
    finally:
        connection.close()

    return redirect(url_for('client_management'))

@app.route('/client_management/delete/<int:client_id>', methods=['POST'])
def delete_client(client_id):
    """Permanently delete a client from the system"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to delete clients', 'error')
        return redirect(url_for('client_management'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_management'))

    # Fine-grained permission: delete client
    deny = enforce_permission(connection, 'client_delete', redirect_endpoint='client_management')
    if deny:
        return deny

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, full_name FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_management'))

            cursor.execute("DELETE FROM clients WHERE id = %s", (client_id,))
            connection.commit()
            flash(f'Client "{client["full_name"]}" has been permanently deleted', 'success')
    except Exception as e:
        print(f"Delete client error: {e}")
        flash('An error occurred while deleting the client', 'error')
    finally:
        connection.close()

    return redirect(url_for('client_management'))

@app.route('/logout')
def logout():
    """Logout user"""
    # Clear all session data including role switch
    session.clear()
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/client_login')
def client_login():
    """Client login page with Google OAuth"""
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('client_login.html', company_settings=company_settings)

@app.route('/client_manual_login', methods=['GET', 'POST'])
def client_manual_login():
    """Manual client login using email and password"""
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template('client_manual_login.html', company_settings=company_settings)

        connection = get_db_connection()
        if not connection:
            flash('Database connection error. Please try again later.', 'error')
            return render_template('client_manual_login.html', company_settings=company_settings)

        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT id, full_name, email, profile_picture, client_type, status, password_hash
                    FROM clients
                    WHERE LOWER(email) = %s
                    LIMIT 1
                """, (email,))
                client = cursor.fetchone()

                if not client or not client.get('password_hash') or not check_password_hash(client['password_hash'], password):
                    flash('Invalid email or password.', 'error')
                    return render_template('client_manual_login.html', company_settings=company_settings)

                if client.get('status') == 'Pending Approval':
                    flash('Your account is pending approval. Please wait for administrator approval.', 'warning')
                    return render_template('client_manual_login.html', company_settings=company_settings)

                if client.get('status') == 'Inactive':
                    flash('Your account is inactive. Please contact support.', 'error')
                    return render_template('client_manual_login.html', company_settings=company_settings)

                session['client_id'] = client['id']
                session['client_name'] = client.get('full_name', '')
                session['client_email'] = client.get('email', '')
                session['client_profile_picture'] = client.get('profile_picture', '')
                session['client_type'] = client.get('client_type', 'Pending')

                session['company_name'] = company_settings.get('company_name', 'BAUNI LAW GROUP')
                flash('Successfully logged in!', 'success')
                return redirect(url_for('client_dashboard'))
        except Exception as e:
            print(f"Manual client login error: {e}")
            flash('An error occurred during login. Please try again.', 'error')
        finally:
            connection.close()

    return render_template('client_manual_login.html', company_settings=company_settings)

@app.route('/client_signup', methods=['GET', 'POST'])
def client_signup():
    """Client signup with active status"""
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        phone_number = (request.form.get('phone_number') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if not full_name or not phone_number or not email or not password or not confirm_password:
            flash('Please fill all required fields.', 'error')
            return render_template('client_signup.html', company_settings=company_settings)

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('client_signup.html', company_settings=company_settings)

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('client_signup.html', company_settings=company_settings)

        profile_picture = None
        if 'profile_picture' in request.files:
            file = request.files['profile_picture']
            if file and file.filename:
                if not allowed_file(file.filename):
                    flash('Invalid profile image format. Use PNG, JPG, JPEG, or GIF.', 'error')
                    return render_template('client_signup.html', company_settings=company_settings)
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"client_signup_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                profile_picture = unique_filename

        connection = get_db_connection()
        if not connection:
            flash('Database connection error. Please try again later.', 'error')
            return render_template('client_signup.html', company_settings=company_settings)

        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT id FROM clients WHERE LOWER(email) = %s LIMIT 1", (email,))
                existing = cursor.fetchone()
                if existing:
                    flash('An account with this email already exists.', 'error')
                    return render_template('client_signup.html', company_settings=company_settings)

                password_hash = generate_password_hash(password)
                cursor.execute("""
                    INSERT INTO clients (
                        google_id, email, full_name, phone_number, profile_picture,
                        client_type, status, password_hash
                    ) VALUES (%s, %s, %s, %s, %s, 'Pending', 'Active', %s)
                """, (
                    f"manual_{secrets.token_hex(12)}",
                    email,
                    full_name,
                    phone_number,
                    profile_picture,
                    password_hash
                ))
                connection.commit()

                flash('Signup successful. Your account is now active.', 'success')
                return redirect(url_for('client_manual_login'))
        except Exception as e:
            print(f"Client signup error: {e}")
            flash('An error occurred during signup. Please try again.', 'error')
        finally:
            connection.close()

    return render_template('client_signup.html', company_settings=company_settings)

@app.route('/api/client_auth/check_registration', methods=['POST'])
def api_client_auth_check_registration():
    """Check whether client email exists and whether email/password combination is already used."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get('email') or '').strip().lower()
    password = payload.get('password') or ''

    if not email:
        return jsonify({'email_exists': False, 'credentials_exists': False})

    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, password_hash
                FROM clients
                WHERE LOWER(email) = %s
                """,
                (email,),
            )
            rows = cursor.fetchall() or []

            email_exists = len(rows) > 0
            credentials_exists = False
            if email_exists and password:
                for row in rows:
                    stored_hash = row.get('password_hash')
                    if stored_hash and check_password_hash(stored_hash, password):
                        credentials_exists = True
                        break

            return jsonify({
                'email_exists': email_exists,
                'credentials_exists': credentials_exists
            })
    except Exception as e:
        print(f"Error checking client registration: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/google_login')
def google_login():
    """Initiate Google OAuth flow"""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        flash('Google login is not configured. Please contact support.', 'error')
        return redirect(url_for('client_login'))

    redirect_uri = (get_public_base_url() + '/callback') if APP_BASE_URL else url_for('google_callback', _external=True)
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri]
            }
        },
        scopes=SCOPES,
        autogenerate_code_verifier=False
    )
    flow.redirect_uri = redirect_uri
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='select_account consent'
    )
    
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
        if request.args.get('error'):
            google_error = request.args.get('error')
            google_error_description = request.args.get('error_description', '')
            print(f"OAuth callback rejected by Google: {google_error} - {google_error_description}")
            flash('Google sign-in was cancelled or denied. Please try again.', 'error')
            return redirect(url_for('client_login'))

        session_state = session.get('state')
        request_state = request.args.get('state')
        if session_state and request_state and request_state != session_state:
            print(f"OAuth state mismatch: session={session_state}, request={request_state}")
            flash('Authentication session expired. Please sign in again.', 'error')
            return redirect(url_for('client_login'))

        callback_state = session_state or request_state
        redirect_uri = (get_public_base_url() + '/callback') if APP_BASE_URL else url_for('google_callback', _external=True)

        # Behind a reverse-proxy (cPanel/Passenger) request.url may still use http://
        # even though the real external URL is https://. Rewrite to match redirect_uri.
        authorization_response = request.url
        if APP_BASE_URL and APP_BASE_URL.startswith('https://') and authorization_response.startswith('http://'):
            authorization_response = 'https://' + authorization_response[len('http://'):]

        # Extract actual scopes from the callback URL and normalize them
        returned_scopes_raw = request.args.get('scope', '').split()
        # Normalize shorthand scopes to full URLs
        scope_mapping = {
            'email': 'https://www.googleapis.com/auth/userinfo.email',
            'profile': 'https://www.googleapis.com/auth/userinfo.profile',
            'openid': 'openid'
        }
        normalized_scopes = []
        for scope in returned_scopes_raw:
            normalized_scopes.append(scope_mapping.get(scope, scope))
        
        # Use normalized returned scopes if available, otherwise use our requested scopes
        # Google may return additional scopes (like drive.file) if previously granted
        # We need to use what Google actually returned to avoid scope mismatch errors
        scopes_to_use = normalized_scopes if normalized_scopes and len(normalized_scopes) > 0 else SCOPES
        
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri]
                }
            },
            scopes=scopes_to_use,
            state=callback_state,
            autogenerate_code_verifier=False
        )
        flow.redirect_uri = redirect_uri
        
        try:
            flow.fetch_token(authorization_response=authorization_response)
        except Exception as scope_error:
            # If scope validation fails, try with the exact scopes Google returned
            if 'Scope has changed' in str(scope_error):
                # Recreate flow with exact normalized scopes from Google
                flow = Flow.from_client_config(
                    {
                        "web": {
                            "client_id": GOOGLE_CLIENT_ID,
                            "client_secret": GOOGLE_CLIENT_SECRET,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "redirect_uris": [redirect_uri]
                        }
                    },
                    scopes=normalized_scopes,  # Use exact normalized scopes from Google
                    state=callback_state,
                    autogenerate_code_verifier=False
                )
                flow.redirect_uri = redirect_uri
                flow.fetch_token(authorization_response=authorization_response)
            else:
                raise
        
        credentials = flow.credentials
        id_info = verify_google_id_token(credentials.id_token)
        
        # Extract user information
        google_id = id_info.get('sub')
        email = id_info.get('email')
        full_name = id_info.get('name', '')
        profile_picture = id_info.get('picture', '')
        
        # Check if client exists, if not create new client
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT * FROM clients WHERE google_id = %s OR email = %s
                    """, (google_id, email))
                    client = cursor.fetchone()
                    
                    if not client:
                        # Create new client with 'Pending' client_type
                        cursor.execute("""
                            INSERT INTO clients (google_id, email, full_name, profile_picture, client_type)
                            VALUES (%s, %s, %s, %s, 'Pending')
                        """, (google_id, email, full_name, profile_picture))
                        connection.commit()
                        cursor.execute("SELECT * FROM clients WHERE google_id = %s", (google_id,))
                        client = cursor.fetchone()
                    
                    # Set session
                    session['client_id'] = client['id']
                    session['client_name'] = client['full_name']
                    session['client_email'] = client['email']
                    session['client_profile_picture'] = client.get('profile_picture', '')
                    session['client_type'] = client.get('client_type', 'Pending')
                    
                    # Set company name in session for header display
                    company_settings = get_company_settings()
                    if company_settings:
                        session['company_name'] = company_settings.get('company_name', 'BAUNI LAW GROUP')
                    else:
                        session['company_name'] = 'BAUNI LAW GROUP'
                    
                    # Check if client has completed registration based on client type
                    client_type = client.get('client_type', 'Pending')
                    if client_type == 'Pending':
                        session.pop('state', None)
                        flash('Please complete your registration', 'info')
                        return redirect(url_for('client_registration'))
                    
                    # Check basic registration requirement
                    if client_type in ('Individual', 'Corporate') and not client.get('phone_number'):
                        session.pop('state', None)
                        flash('Please complete your registration by providing your phone number', 'info')
                        return redirect(url_for('client_registration'))
                    
                    session.pop('state', None)
                    flash('Successfully logged in!', 'success')
                    return redirect(url_for('client_dashboard'))
            except Exception as e:
                print(f"Error processing client login: {e}")
                flash('An error occurred during login. Please try again.', 'error')
            finally:
                connection.close()
        
        return redirect(url_for('client_login'))
    except Exception as e:
        import traceback
        print(f"OAuth callback error: {e}")
        traceback.print_exc()
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('client_login'))

@app.route('/client_dashboard')
def client_dashboard():
    """Client dashboard page"""
    # Allow access if client_id is in session OR if viewing as client from employee account
    if 'client_id' not in session and 'original_employee_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_login'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch full client data and check registration
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    id_front,
                    id_back,
                    cr12_certificate,
                    physical_address,
                    created_at
                FROM clients
                WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_login'))
            
            # Check if client has completed registration
            client_type = client.get('client_type', 'Pending')
            if client_type == 'Pending':
                return redirect(url_for('client_registration'))
            
            # Check basic registration requirement
            if client_type in ('Individual', 'Corporate') and not client.get('phone_number'):
                return redirect(url_for('client_registration'))
            
            # Fetch cases for this client
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.client_name,
                    c.case_type,
                    c.filing_date,
                    c.case_category,
                    c.station,
                    c.filled_by_name,
                    c.created_by_name,
                    c.description,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM cases c
                WHERE c.client_id = %s
                ORDER BY c.filing_date DESC, c.created_at DESC
            """, (session['client_id'],))
            cases = cursor.fetchall()
            
            # Fetch matters for this client
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_id,
                    m.client_name,
                    m.client_instructions,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status,
                    m.created_at,
                    m.updated_at
                FROM matters m
                WHERE m.client_id = %s
                ORDER BY m.date_opened DESC, m.created_at DESC
            """, (session['client_id'],))
            matters = cursor.fetchall()
            
            # Convert date objects to strings
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            for case in cases:
                if case.get('filing_date'):
                    case['filing_date'] = case['filing_date'].strftime('%Y-%m-%d')
                if case.get('created_at'):
                    case['created_at'] = case['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if case.get('updated_at'):
                    case['updated_at'] = case['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            for matter in matters:
                if matter.get('date_opened'):
                    matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d')
                if matter.get('created_at'):
                    matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if matter.get('updated_at'):
                    matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            # Set company name in session for header display
            session['company_name'] = company_settings.get('company_name', 'BAUNI LAW GROUP')
            
            is_employee_viewing = 'original_employee_id' in session
            
            return render_template('client_dashboard.html', 
                                 client=client,
                                 cases=cases,
                                 matters=matters,
                                 company_settings=company_settings,
                                 is_employee_viewing=is_employee_viewing)
    except Exception as e:
        print(f"Error fetching client dashboard data: {e}")
        flash('An error occurred while loading the dashboard.', 'error')
        return redirect(url_for('client_login'))
    finally:
        connection.close()

@app.route('/my_tools')
def my_tools():
    """My Tools page - for employees to upload signature and stamp"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('my_tools.html', company_settings=company_settings)

@app.route('/my_tasks')
def my_tasks():
    """My Tasks page - all case/matter tasks allocated to the logged-in user."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))

    employee_id = session.get('employee_id')
    tasks = []
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)
            cursor.execute("""
                SELECT
                    t.id,
                    t.task_type,
                    t.linked_id,
                    t.task_title,
                    t.task_description,
                    t.due_at,
                    t.reminder_intervals,
                    t.task_status,
                    t.assigned_to_id,
                    t.assigned_to_name,
                    t.allow_view_case_details,
                    t.allow_edit_case_details,
                    t.allow_view_case_documents,
                    t.allow_upload_case_documents,
                    t.allow_download_case_documents,
                    t.created_by_name,
                    t.created_at,
                    c.tracking_number AS case_tracking_number,
                    c.client_name AS case_client_name,
                    m.matter_reference_number,
                    m.matter_title,
                    'task_management' AS task_source,
                    NULL AS proceeding_id
                FROM task_management t
                LEFT JOIN cases c
                    ON t.task_type = 'case' AND t.linked_id = c.id
                LEFT JOIN matters m
                    ON t.task_type = 'matter' AND t.linked_id = m.id
                WHERE
                    (t.task_type = 'case' AND ((t.assigned_to_id IS NOT NULL AND t.assigned_to_id = %s) OR (t.assigned_to_id IS NULL AND c.filled_by_id = %s)))
                    OR
                    (t.task_type = 'matter' AND m.assigned_employee_id = %s)
                ORDER BY t.due_at ASC, t.created_at DESC
            """, (employee_id, employee_id, employee_id))
            tasks = list(cursor.fetchall() or [])

            # Include allocated court-session items (case proceeding materials) assigned to this employee
            cursor.execute("""
                SELECT
                    m.id,
                    'case' AS task_type,
                    p.case_id AS linked_id,
                    COALESCE(NULLIF(m.material_description, ''), 'Allocated Session Item') AS task_title,
                    m.material_description AS task_description,
                    COALESCE(p.next_court_date, p.date_of_court_appeared, m.created_at) AS due_at,
                    m.reminder_frequency AS reminder_intervals,
                    'Pending' AS task_status,
                    m.allocated_to_name AS created_by_name,
                    m.created_at,
                    c.tracking_number AS case_tracking_number,
                    c.client_name AS case_client_name,
                    NULL AS matter_reference_number,
                    NULL AS matter_title,
                    'session_allocation' AS task_source,
                    p.id AS proceeding_id
                FROM case_proceeding_materials m
                INNER JOIN case_proceedings p ON p.id = m.proceeding_id
                INNER JOIN cases c ON c.id = p.case_id
                WHERE m.allocated_to_id = %s
                ORDER BY COALESCE(p.next_court_date, p.date_of_court_appeared, m.created_at) ASC, m.created_at DESC
            """, (employee_id,))
            session_tasks = list(cursor.fetchall() or [])

            if session_tasks:
                tasks.extend(session_tasks)

            # Normalize date display for mixed task sources
            for t in tasks:
                due_val = t.get('due_at')
                if due_val:
                    try:
                        t['due_at'] = due_val.strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        t['due_at'] = str(due_val)
                else:
                    t['due_at'] = '-'

                created_val = t.get('created_at')
                if created_val:
                    try:
                        t['created_at'] = created_val.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        t['created_at'] = str(created_val)

            tasks.sort(key=lambda x: (x.get('due_at') or '9999-12-31 23:59', x.get('created_at') or ''), reverse=False)
    except Exception as e:
        print(f"Error loading my tasks: {e}")
        flash('An error occurred while loading your tasks.', 'error')
    finally:
        connection.close()

    return render_template('my_tasks.html', company_settings=company_settings, tasks=tasks)

@app.route('/my_tasks/<int:task_id>/accept', methods=['POST'])
def accept_my_task(task_id):
    """Accept a task assigned to the logged-in user and set it In Progress."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('my_tasks'))

    employee_id = session.get('employee_id')
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)
            cursor.execute("""
                SELECT t.id, t.task_status
                FROM task_management t
                LEFT JOIN cases c ON t.task_type = 'case' AND t.linked_id = c.id
                LEFT JOIN matters m ON t.task_type = 'matter' AND t.linked_id = m.id
                WHERE t.id = %s
                  AND (
                    (t.task_type = 'case' AND ((t.assigned_to_id IS NOT NULL AND t.assigned_to_id = %s) OR (t.assigned_to_id IS NULL AND c.filled_by_id = %s)))
                    OR
                    (t.task_type = 'matter' AND m.assigned_employee_id = %s)
                  )
                LIMIT 1
            """, (task_id, employee_id, employee_id, employee_id))
            task = cursor.fetchone()
            if not task:
                flash('Task not found or not allocated to your account.', 'error')
                return redirect(url_for('my_tasks'))
            if task.get('task_status') != 'Pending':
                flash('Only pending tasks can be accepted.', 'error')
                return redirect(url_for('my_tasks'))

            cursor.execute("UPDATE task_management SET task_status = 'In Progress' WHERE id = %s", (task_id,))
            connection.commit()
            flash('Task accepted and moved to In Progress.', 'success')
    except Exception as e:
        print(f"Accept task error: {e}")
        flash('An error occurred while accepting the task.', 'error')
    finally:
        connection.close()

    return redirect(url_for('my_tasks'))

@app.route('/my_tasks/<int:task_id>')
def my_task_view(task_id):
    """View a single allocated task."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('my_tasks'))

    employee_id = session.get('employee_id')
    task = None
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)
            cursor.execute("""
                SELECT
                    t.id,
                    t.task_type,
                    t.linked_id,
                    t.task_title,
                    t.task_description,
                    t.due_at,
                    t.reminder_intervals,
                    t.task_status,
                    t.assigned_to_id,
                    t.assigned_to_name,
                    t.allow_view_case_details,
                    t.allow_edit_case_details,
                    t.allow_view_case_documents,
                    t.allow_upload_case_documents,
                    t.allow_download_case_documents,
                    t.created_by_name,
                    t.created_at,
                    c.tracking_number AS case_tracking_number,
                    c.client_name AS case_client_name,
                    m.matter_reference_number,
                    m.matter_title
                FROM task_management t
                LEFT JOIN cases c ON t.task_type = 'case' AND t.linked_id = c.id
                LEFT JOIN matters m ON t.task_type = 'matter' AND t.linked_id = m.id
                WHERE t.id = %s
                  AND (
                    (t.task_type = 'case' AND ((t.assigned_to_id IS NOT NULL AND t.assigned_to_id = %s) OR (t.assigned_to_id IS NULL AND c.filled_by_id = %s)))
                    OR
                    (t.task_type = 'matter' AND m.assigned_employee_id = %s)
                  )
                LIMIT 1
            """, (task_id, employee_id, employee_id, employee_id))
            task = cursor.fetchone()
    except Exception as e:
        print(f"My task view error: {e}")
        flash('An error occurred while loading the task.', 'error')
        return redirect(url_for('my_tasks'))
    finally:
        connection.close()

    if not task:
        flash('Task not found or not allocated to your account.', 'error')
        return redirect(url_for('my_tasks'))

    return render_template('my_task_view.html', company_settings=company_settings, task=task)

@app.route('/my_tasks/<int:task_id>/complete', methods=['POST'])
def complete_my_task(task_id):
    """Submit an in-progress task."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('my_tasks'))

    employee_id = session.get('employee_id')
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)
            cursor.execute("""
                SELECT t.id, t.task_status
                FROM task_management t
                LEFT JOIN cases c ON t.task_type = 'case' AND t.linked_id = c.id
                LEFT JOIN matters m ON t.task_type = 'matter' AND t.linked_id = m.id
                WHERE t.id = %s
                  AND (
                    (t.task_type = 'case' AND ((t.assigned_to_id IS NOT NULL AND t.assigned_to_id = %s) OR (t.assigned_to_id IS NULL AND c.filled_by_id = %s)))
                    OR
                    (t.task_type = 'matter' AND m.assigned_employee_id = %s)
                  )
                LIMIT 1
            """, (task_id, employee_id, employee_id, employee_id))
            task = cursor.fetchone()
            if not task:
                flash('Task not found or not allocated to your account.', 'error')
                return redirect(url_for('my_tasks'))
            if task.get('task_status') != 'In Progress':
                flash('Only in-progress tasks can be submitted.', 'error')
                return redirect(url_for('my_task_view', task_id=task_id))

            cursor.execute("UPDATE task_management SET task_status = 'Submitted' WHERE id = %s", (task_id,))
            connection.commit()
            flash('Task submitted successfully.', 'success')
    except Exception as e:
        print(f"Complete task error: {e}")
        flash('An error occurred while submitting the task.', 'error')
    finally:
        connection.close()

    return redirect(url_for('my_tasks'))

@app.route('/notifications')
def notifications():
    """Unified notifications for the logged-in employee."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))

    employee_id = session.get('employee_id')
    notifications_feed = []
    try:
        from datetime import date
        today = date.today()
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)

            cursor.execute("""
                SELECT
                    t.id,
                    t.task_type,
                    t.linked_id,
                    t.task_title,
                    t.task_status,
                    t.due_at,
                    c.tracking_number AS case_tracking_number,
                    c.client_name AS case_client_name,
                    m.matter_reference_number,
                    m.matter_title
                FROM task_management t
                LEFT JOIN cases c ON t.task_type = 'case' AND t.linked_id = c.id
                LEFT JOIN matters m ON t.task_type = 'matter' AND t.linked_id = m.id
                WHERE
                    t.task_status IN ('Pending', 'In Progress')
                    AND (
                        (t.task_type = 'case' AND ((t.assigned_to_id IS NOT NULL AND t.assigned_to_id = %s) OR (t.assigned_to_id IS NULL AND c.filled_by_id = %s)))
                        OR
                        (t.task_type = 'matter' AND m.assigned_employee_id = %s)
                    )
                ORDER BY t.due_at ASC, t.created_at DESC
            """, (employee_id, employee_id, employee_id))
            active_tasks = list(cursor.fetchall() or [])
            for task in active_tasks:
                due_val = task.get('due_at')
                due_text = '-'
                if due_val:
                    try:
                        due_text = due_val.strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        due_text = str(due_val)
                if task.get('task_type') == 'case':
                    ref = f"{task.get('case_tracking_number') or '-'} - {task.get('case_client_name') or 'Case'}"
                else:
                    ref = f"{task.get('matter_reference_number') or '-'} - {task.get('matter_title') or 'Matter'}"
                notifications_feed.append({
                    'type': 'task',
                    'icon': 'fa-tasks',
                    'title': task.get('task_title') or 'Assigned task',
                    'subtitle': ref,
                    'meta': f"Status: {task.get('task_status') or '-'} | Due: {due_text}",
                    'link': url_for('my_task_view', task_id=task['id']) if task.get('task_status') == 'In Progress' else url_for('my_tasks'),
                    'sort_key': due_text if due_text != '-' else '9999-12-31 23:59'
                })

            cursor.execute("""
                SELECT
                    m.id,
                    m.material_description,
                    m.reminder_frequency,
                    p.case_id,
                    p.next_court_date,
                    c.tracking_number,
                    c.client_name
                FROM case_proceeding_materials m
                INNER JOIN case_proceedings p ON p.id = m.proceeding_id
                INNER JOIN cases c ON c.id = p.case_id
                WHERE m.allocated_to_id = %s
                ORDER BY COALESCE(p.next_court_date, m.created_at) ASC
            """, (employee_id,))
            allocated_materials = list(cursor.fetchall() or [])
            for material in allocated_materials:
                next_date = material.get('next_court_date')
                next_text = '-'
                if next_date:
                    try:
                        next_text = next_date.strftime('%Y-%m-%d')
                    except Exception:
                        next_text = str(next_date)
                notifications_feed.append({
                    'type': 'reminder',
                    'icon': 'fa-bell',
                    'title': material.get('material_description') or 'Session reminder',
                    'subtitle': f"{material.get('tracking_number') or '-'} - {material.get('client_name') or 'Case'}",
                    'meta': f"Reminder: {material.get('reminder_frequency') or '-'} | Next court: {next_text}",
                    'link': url_for('case_details', case_id=material.get('case_id')),
                    'sort_key': next_text if next_text != '-' else '9999-12-31'
                })

            cursor.execute("""
                SELECT
                    p.case_id,
                    p.next_court_date,
                    p.next_attendance,
                    c.tracking_number,
                    c.client_name
                FROM case_proceedings p
                INNER JOIN cases c ON c.id = p.case_id
                WHERE p.next_court_date IS NOT NULL
                  AND p.next_court_date >= %s
                  AND c.filled_by_id = %s
                ORDER BY p.next_court_date ASC
                LIMIT 50
            """, (today, employee_id))
            upcoming_calendar = list(cursor.fetchall() or [])
            for cal in upcoming_calendar:
                next_date = cal.get('next_court_date')
                next_text = '-'
                if next_date:
                    try:
                        next_text = next_date.strftime('%Y-%m-%d')
                    except Exception:
                        next_text = str(next_date)
                notifications_feed.append({
                    'type': 'calendar',
                    'icon': 'fa-calendar-alt',
                    'title': cal.get('next_attendance') or 'Upcoming court date',
                    'subtitle': f"{cal.get('tracking_number') or '-'} - {cal.get('client_name') or 'Case'}",
                    'meta': f"Scheduled: {next_text}",
                    'link': url_for('case_calendar', case_id=cal.get('case_id')),
                    'sort_key': next_text if next_text != '-' else '9999-12-31'
                })

            notifications_feed.sort(key=lambda x: x.get('sort_key') or '9999-12-31 23:59')
            notifications_feed = notifications_feed[:120]
    except Exception as e:
        print(f"Error loading notifications: {e}")
        flash('An error occurred while loading notifications.', 'error')
    finally:
        connection.close()

    return render_template(
        'notifications.html',
        company_settings=company_settings,
        notifications=notifications_feed
    )

@app.route('/upload_signature_stamp', methods=['POST'])
def upload_signature_stamp():
    """Handle signature or stamp upload"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    employee_id = session['employee_id']
    upload_type = request.form.get('upload_type')
    
    if upload_type not in ['signature', 'stamp']:
        return jsonify({'success': False, 'error': 'Invalid upload type'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        file_field = 'signature' if upload_type == 'signature' else 'stamp'
        hash_field = 'signature_hash' if upload_type == 'signature' else 'stamp_hash'
        
        if file_field not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files[file_field]
        if not file or not file.filename:
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        # Check if processed data is available (from frontend)
        processed_data = request.form.get(f'{file_field}_processed', '')
        
        saved_filename = None
        file_hash = None
        
        if processed_data:
            try:
                # Decode base64 image
                header, encoded = processed_data.split(',', 1)
                file_bytes = base64.b64decode(encoded)
                
                # Process image
                processed_img = process_signature_image(BytesIO(file_bytes))
                
                if processed_img:
                    # Generate hash
                    processed_img.seek(0)
                    file_hash = generate_signature_hash(processed_img.read())
                    
                    # Save processed image
                    filename = secure_filename(file.filename)
                    file_ext = 'png'  # Always save as PNG after processing
                    unique_filename = f"{file_field}_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    
                    processed_img.seek(0)
                    with open(filepath, 'wb') as f:
                        f.write(processed_img.read())
                    
                    saved_filename = unique_filename
            except Exception as e:
                print(f"Error processing {upload_type}: {e}")
                # Fallback to original file if processing fails
                if file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file_ext = filename.rsplit('.', 1)[1].lower()
                    unique_filename = f"{file_field}_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(filepath)
                    saved_filename = unique_filename
                    
                    # Generate hash from saved file
                    with open(filepath, 'rb') as f:
                        file_hash = generate_signature_hash(f.read())
        else:
            # No processed data, save original file
            if file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{file_field}_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                saved_filename = unique_filename
                
                # Generate hash from saved file
                with open(filepath, 'rb') as f:
                    file_hash = generate_signature_hash(f.read())
        
        if not saved_filename:
            return jsonify({'success': False, 'error': 'Failed to save file'}), 500
        
        # Update database
        with connection.cursor() as cursor:
            cursor.execute(f"""
                UPDATE employees 
                SET {file_field} = %s,
                    {hash_field} = %s
                WHERE id = %s
            """, (saved_filename, file_hash, employee_id))
            connection.commit()
        
        return jsonify({
            'success': True,
            'message': f'{upload_type.capitalize()} uploaded successfully!'
        })
        
    except Exception as e:
        print(f"Error uploading {upload_type}: {e}")
        connection.rollback()
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500
    finally:
        connection.close()

@app.route('/client_documents')
def client_documents():
    """Client documents page - clients can view their own documents"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_dashboard'))
            
            # Convert date objects to strings
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_documents.html',
                                 client=client,
                                 client_id=session['client_id'],
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client documents: {e}")
        flash('An error occurred while fetching client information.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/client_documents/<document_type>')
def client_document_type(document_type):
    """View documents for a specific client by document type (client access) - fetched from Google Drive"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    valid_types = ['CLIENT_PERSONAL_DOCUMENT', 'CLIENT_CASE_DOCUMENT']
    if document_type not in valid_types:
        flash('Invalid document type', 'error')
        return redirect(url_for('client_documents'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, google_id, full_name, email, phone_number, profile_picture,
                       client_type, status, created_at,
                       id_front, id_back, instruction_note, cr12_certificate, corporate_kra_pin
                FROM clients WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_dashboard'))
            
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')

            personal_uploads = []
            if document_type == 'CLIENT_PERSONAL_DOCUMENT':
                upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))

                doc_fields = {
                    'id_front': {'label': 'NATIONAL ID / PASSPORT', 'icon': 'fa-id-card'},
                    'id_back': {'label': 'KRA PIN DOCUMENT', 'icon': 'fa-file-invoice'},
                    'instruction_note': {'label': 'SIGNED INSTRUCTION NOTE', 'icon': 'fa-file-signature'},
                    'cr12_certificate': {'label': 'CR12/CR13 CERTIFICATE', 'icon': 'fa-certificate'},
                    'corporate_kra_pin': {'label': 'CORPORATE KRA PIN', 'icon': 'fa-file-invoice-dollar'},
                }
                for field, meta in doc_fields.items():
                    filename = client.get(field)
                    if filename:
                        filepath = os.path.join(upload_folder, filename)
                        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                        size_str = 'N/A'
                        size_int = 0
                        if os.path.exists(filepath):
                            size_int = os.path.getsize(filepath)
                            if size_int < 1024:
                                size_str = f"{size_int} B"
                            elif size_int < 1024 * 1024:
                                size_str = f"{size_int / 1024:.1f} KB"
                            else:
                                size_str = f"{size_int / (1024 * 1024):.1f} MB"
                        personal_uploads.append({
                            'id': None,
                            'field': field,
                            'label': meta['label'],
                            'icon': meta['icon'],
                            'filename': filename,
                            'file_extension': ext,
                            'size': size_int,
                            'size_display': size_str,
                            'url': url_for('static', filename='uploads/profile_pictures/' + filename),
                            'source': 'legacy',
                        })

            custom_uploads = []
            if document_type == 'CLIENT_PERSONAL_DOCUMENT':
                cursor.execute("""
                    SELECT id, document_type, filename, original_filename, file_size, created_at
                    FROM client_personal_documents
                    WHERE client_id = %s ORDER BY created_at DESC
                """, (session['client_id'],))
                custom_docs = cursor.fetchall()
                for doc in custom_docs:
                    filename = doc['filename']
                    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                    size_int = doc.get('file_size', 0) or 0
                    if size_int < 1024:
                        size_str = f"{size_int} B"
                    elif size_int < 1024 * 1024:
                        size_str = f"{size_int / 1024:.1f} KB"
                    else:
                        size_str = f"{size_int / (1024 * 1024):.1f} MB"
                    created_time = ''
                    if doc.get('created_at'):
                        created_time = doc['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    custom_uploads.append({
                        'id': doc['id'],
                        'label': doc['document_type'],
                        'icon': 'fa-file-alt',
                        'filename': filename,
                        'file_extension': ext,
                        'size': size_int,
                        'size_display': size_str,
                        'created_time': created_time,
                        'url': url_for('static', filename='uploads/profile_pictures/' + filename),
                    })

            # Load Google Drive credentials directly from company_settings
            google_drive_connected = False
            cursor.execute("""
                SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                       google_drive_scopes, google_drive_main_folder_id
                FROM company_settings ORDER BY id DESC LIMIT 1
            """)
            drive_settings = cursor.fetchone()
            
            if drive_settings and drive_settings.get('google_drive_token') and drive_settings.get('google_drive_refresh_token'):
                google_drive_connected = True
            
            documents = []
            
            if google_drive_connected:
                try:
                    scopes = json.loads(drive_settings['google_drive_scopes']) if drive_settings.get('google_drive_scopes') else []
                    credentials = Credentials(
                        token=drive_settings['google_drive_token'],
                        refresh_token=drive_settings['google_drive_refresh_token'],
                        token_uri=drive_settings.get('google_drive_token_uri'),
                        client_id=GOOGLE_CLIENT_ID,
                        client_secret=GOOGLE_CLIENT_SECRET,
                        scopes=scopes
                    )
                    
                    if credentials.expired and credentials.refresh_token:
                        from google.auth.transport.requests import Request
                        credentials.refresh(Request())
                        cursor.execute("""
                            UPDATE company_settings SET google_drive_token = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                        """, (credentials.token,))
                        connection.commit()
                    
                    service = build('drive', 'v3', credentials=credentials)
                    main_folder_id = drive_settings.get('google_drive_main_folder_id')
                    
                    if service and main_folder_id:
                        client_folder_name = get_user_folder_name(client.get('phone_number'), client.get('full_name'), 'client')
                        client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                        target_folder_id = get_or_create_folder(service, client_folder_id, document_type)
                        
                        if target_folder_id:
                            query = f"'{target_folder_id}' in parents and trashed=false"
                            results = service.files().list(
                                q=query, spaces='drive',
                                fields='files(id, name, createdTime, modifiedTime, webViewLink, size, mimeType)',
                                orderBy='modifiedTime desc'
                            ).execute()
                            
                            for file in results.get('files', []):
                                if file.get('mimeType') == 'application/vnd.google-apps.folder':
                                    continue
                                
                                created_time = file.get('createdTime', '')
                                modified_time = file.get('modifiedTime', '')
                                if created_time:
                                    try:
                                        from datetime import datetime
                                        dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                                        created_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                    except:
                                        pass
                                if modified_time:
                                    try:
                                        from datetime import datetime
                                        dt = datetime.fromisoformat(modified_time.replace('Z', '+00:00'))
                                        modified_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                    except:
                                        pass
                                
                                size_val = file.get('size', '0')
                                try:
                                    size_int = int(size_val) if size_val else 0
                                    if size_int < 1024:
                                        size_str = f"{size_int} B"
                                    elif size_int < 1024 * 1024:
                                        size_str = f"{size_int / 1024:.1f} KB"
                                    else:
                                        size_str = f"{size_int / (1024 * 1024):.1f} MB"
                                except:
                                    size_int = 0
                                    size_str = "Unknown"
                                
                                fname = file.get('name', 'Unknown')
                                ext = fname.rsplit('.', 1)[1].lower() if '.' in fname else ''
                                
                                documents.append({
                                    'id': file.get('id'),
                                    'name': fname,
                                    'file_extension': ext,
                                    'created_time': created_time,
                                    'modified_time': modified_time,
                                    'url': file.get('webViewLink', ''),
                                    'size': size_int,
                                    'size_display': size_str,
                                    'mime_type': file.get('mimeType', '')
                                })
                except Exception as e:
                    print(f"Error fetching documents from Google Drive: {e}")
                    import traceback
                    traceback.print_exc()
            
            document_type_names = {
                'CLIENT_PERSONAL_DOCUMENT': 'Personal Documents',
                'CLIENT_CASE_DOCUMENT': 'Case Documents'
            }
            document_type_name = document_type_names.get(document_type, document_type)
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_document_type.html',
                                 client=client,
                                 client_id=session['client_id'],
                                 document_type=document_type,
                                 document_type_name=document_type_name,
                                 documents=documents,
                                 personal_uploads=personal_uploads,
                                 custom_uploads=custom_uploads,
                                 google_drive_connected=google_drive_connected,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client documents: {e}")
        flash('An error occurred while fetching client information.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/api/client_documents/<document_type>/upload', methods=['POST'])
def client_upload_document(document_type):
    """Upload a client document to Google Drive using company-level credentials"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    valid_types = ['CLIENT_PERSONAL_DOCUMENT', 'CLIENT_CASE_DOCUMENT']
    if document_type not in valid_types:
        return jsonify({'success': False, 'error': 'Invalid document type'}), 400
    
    try:
        # Always load Google Drive credentials from company_settings (not client session)
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Load Drive credentials from company_settings
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                           google_drive_scopes, google_drive_main_folder_id
                    FROM company_settings ORDER BY id DESC LIMIT 1
                """)
                settings = cursor.fetchone()
                
                if not settings or not settings.get('google_drive_token') or not settings.get('google_drive_refresh_token'):
                    print("ERROR: Google Drive credentials not found in company_settings")
                    return jsonify({'success': False, 'error': 'Google Drive not connected. Please ask the administrator to connect Google Drive in Documents Settings.'}), 400
                
                # Build credentials and service
                scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                credentials = Credentials(
                    token=settings['google_drive_token'],
                    refresh_token=settings['google_drive_refresh_token'],
                    token_uri=settings.get('google_drive_token_uri'),
                    client_id=GOOGLE_CLIENT_ID,
                    client_secret=GOOGLE_CLIENT_SECRET,
                    scopes=scopes
                )
                
                # Refresh token if expired
                if credentials.expired and credentials.refresh_token:
                    from google.auth.transport.requests import Request
                    credentials.refresh(Request())
                    # Save refreshed token back to DB
                    cursor.execute("""
                        UPDATE company_settings SET google_drive_token = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                    """, (credentials.token,))
                    connection.commit()
                    print("[OK] Refreshed Google Drive token for client upload")
                
                service = build('drive', 'v3', credentials=credentials)
                
                # Get main folder ID
                main_folder_id = settings.get('google_drive_main_folder_id')
                if not main_folder_id:
                    print("ERROR: google_drive_main_folder_id not set in company_settings")
                    return jsonify({'success': False, 'error': 'Google Drive main folder not set up. Please ask the administrator to create the main folder in Documents Settings.'}), 400
                
                # Get client info
                cursor.execute("SELECT id, full_name, phone_number FROM clients WHERE id = %s", (session['client_id'],))
                client = cursor.fetchone()
                if not client:
                    return jsonify({'success': False, 'error': 'Client not found'}), 404
                
                # Navigate/create folder structure: SHERIA CENTRIC > [client folder] > [document_type]
                client_folder_name = get_user_folder_name(client.get('phone_number'), client.get('full_name'), 'client')
                print(f"INFO: Client upload - folder: {client_folder_name}/{document_type}")
                client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                target_folder_id = get_or_create_folder(service, client_folder_id, document_type)
                
                # Handle file
                if 'document_file' not in request.files:
                    return jsonify({'success': False, 'error': 'No file provided'}), 400
                
                file = request.files['document_file']
                if not file or file.filename == '':
                    return jsonify({'success': False, 'error': 'No file selected'}), 400
                
                file_content = file.read()
                file_name = secure_filename(file.filename)
                description = request.form.get('description', '').strip()
                
                file_ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''
                mime_types = {
                    'pdf': 'application/pdf',
                    'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif',
                    'xls': 'application/vnd.ms-excel',
                    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'csv': 'text/csv', 'txt': 'text/plain'
                }
                mime_type = mime_types.get(file_ext, 'application/octet-stream')
                
                file_metadata = {
                    'name': file_name,
                    'parents': [target_folder_id]
                }
                if description:
                    file_metadata['description'] = description
                
                media = MediaIoBaseUpload(BytesIO(file_content), mimetype=mime_type, resumable=True)
                
                uploaded_file = service.files().create(
                    body=file_metadata, media_body=media,
                    fields='id, name, webViewLink, webContentLink'
                ).execute()
                
                file_id = uploaded_file.get('id')
                file_url = uploaded_file.get('webViewLink', f"https://drive.google.com/file/d/{file_id}/view")
                print(f"[OK] Client uploaded document: {file_name} -> {file_id}")
                
                return jsonify({
                    'success': True,
                    'message': 'Document uploaded successfully to Google Drive',
                    'file_id': file_id,
                    'file_name': file_name,
                    'file_url': file_url
                })
        finally:
            connection.close()
    
    except HttpError as error:
        print(f"Google Drive API error during client upload: {error}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Google Drive error: {str(error)}'}), 500
    except Exception as e:
        print(f"Error uploading client document: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Upload failed: {str(e)}'}), 500

@app.route('/api/client_documents/delete/<file_id>', methods=['POST'])
def client_delete_document(file_id):
    """Delete a client document from Google Drive (move to trash) using company credentials"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri, google_drive_scopes
                    FROM company_settings ORDER BY id DESC LIMIT 1
                """)
                settings = cursor.fetchone()
                
                if not settings or not settings.get('google_drive_token') or not settings.get('google_drive_refresh_token'):
                    return jsonify({'success': False, 'error': 'Google Drive not connected'}), 400
                
                scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                credentials = Credentials(
                    token=settings['google_drive_token'],
                    refresh_token=settings['google_drive_refresh_token'],
                    token_uri=settings.get('google_drive_token_uri'),
                    client_id=GOOGLE_CLIENT_ID,
                    client_secret=GOOGLE_CLIENT_SECRET,
                    scopes=scopes
                )
                
                if credentials.expired and credentials.refresh_token:
                    from google.auth.transport.requests import Request
                    credentials.refresh(Request())
                    cursor.execute("""
                        UPDATE company_settings SET google_drive_token = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                    """, (credentials.token,))
                    connection.commit()
                
                service = build('drive', 'v3', credentials=credentials)
                service.files().update(fileId=file_id, body={'trashed': True}).execute()
                print(f"[OK] Client deleted document: {file_id}")
                
                return jsonify({'success': True, 'message': 'Document deleted successfully'})
        finally:
            connection.close()
    except HttpError as error:
        print(f"Google Drive API error deleting file: {error}")
        return jsonify({'success': False, 'error': f'Failed to delete: {str(error)}'}), 500
    except Exception as e:
        print(f"Error deleting client document: {e}")
        return jsonify({'success': False, 'error': f'Delete failed: {str(e)}'}), 500

@app.route('/api/client_personal_documents/upload', methods=['POST'])
def client_upload_personal_document():
    """Upload a custom registration document with a free-text document type"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    document_type_label = request.form.get('document_type', '').strip().upper()
    if not document_type_label:
        return jsonify({'success': False, 'error': 'Document type is required'}), 400

    if 'document_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    file = request.files['document_file']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            client_id = session['client_id']
            original_filename = secure_filename(file.filename)
            file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'bin'
            unique_filename = f"personal_doc_{client_id}_{secrets.token_hex(8)}.{file_ext}"
            upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
            filepath = os.path.join(upload_folder, unique_filename)
            file.save(filepath)

            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            cursor.execute("""
                INSERT INTO client_personal_documents (client_id, document_type, filename, original_filename, file_size)
                VALUES (%s, %s, %s, %s, %s)
            """, (client_id, document_type_label, unique_filename, original_filename, file_size))
            connection.commit()

            return jsonify({'success': True, 'message': 'Document uploaded successfully', 'filename': unique_filename})
    except Exception as e:
        print(f"Error uploading personal document: {e}")
        return jsonify({'success': False, 'error': f'Upload failed: {str(e)}'}), 500
    finally:
        connection.close()


@app.route('/api/client_personal_documents/delete/<int:doc_id>', methods=['POST'])
def client_delete_personal_document(doc_id):
    """Delete a custom registration document"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT filename FROM client_personal_documents WHERE id = %s AND client_id = %s",
                           (doc_id, session['client_id']))
            doc = cursor.fetchone()
            if not doc:
                return jsonify({'success': False, 'error': 'Document not found'}), 404

            cursor.execute("DELETE FROM client_personal_documents WHERE id = %s AND client_id = %s",
                           (doc_id, session['client_id']))
            connection.commit()

            upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
            old_path = os.path.join(upload_folder, doc['filename'])
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

            return jsonify({'success': True, 'message': 'Document deleted successfully'})
    except Exception as e:
        print(f"Error deleting personal document: {e}")
        return jsonify({'success': False, 'error': f'Delete failed: {str(e)}'}), 500
    finally:
        connection.close()


@app.route('/api/client_personal_documents/update/<int:doc_id>', methods=['POST'])
def client_update_personal_document(doc_id):
    """Replace a custom registration document"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    if 'document_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    file = request.files['document_file']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            client_id = session['client_id']
            cursor.execute("SELECT filename FROM client_personal_documents WHERE id = %s AND client_id = %s",
                           (doc_id, client_id))
            doc = cursor.fetchone()
            if not doc:
                return jsonify({'success': False, 'error': 'Document not found'}), 404

            old_filename = doc['filename']
            original_filename = secure_filename(file.filename)
            file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'bin'
            unique_filename = f"personal_doc_{client_id}_{secrets.token_hex(8)}.{file_ext}"
            upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
            filepath = os.path.join(upload_folder, unique_filename)
            file.save(filepath)

            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            cursor.execute("""
                UPDATE client_personal_documents SET filename = %s, original_filename = %s, file_size = %s
                WHERE id = %s AND client_id = %s
            """, (unique_filename, original_filename, file_size, doc_id, client_id))
            connection.commit()

            old_path = os.path.join(upload_folder, old_filename)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

            return jsonify({'success': True, 'message': 'Document updated successfully', 'filename': unique_filename})
    except Exception as e:
        print(f"Error updating personal document: {e}")
        return jsonify({'success': False, 'error': f'Update failed: {str(e)}'}), 500
    finally:
        connection.close()


@app.route('/api/client_personal_documents/download/<int:doc_id>')
def client_download_personal_document(doc_id):
    """Download a custom registration document"""
    if 'client_id' not in session:
        flash('Unauthorized', 'error')
        return redirect(url_for('client_login'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('client_documents'))

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT filename, original_filename FROM client_personal_documents WHERE id = %s AND client_id = %s",
                           (doc_id, session['client_id']))
            doc = cursor.fetchone()
            if not doc:
                flash('Document not found', 'error')
                return redirect(url_for('client_documents'))

            upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
            return send_from_directory(
                os.path.abspath(upload_folder),
                doc['filename'],
                as_attachment=True,
                download_name=doc.get('original_filename') or doc['filename']
            )
    except Exception as e:
        print(f"Error downloading personal document: {e}")
        flash('Download failed', 'error')
        return redirect(url_for('client_documents'))
    finally:
        connection.close()


@app.route('/api/client_personal_upload/update/<field>', methods=['POST'])
def client_update_personal_upload(field):
    """Replace a personal upload document for the logged-in client"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    valid_fields = ['id_front', 'id_back', 'instruction_note', 'cr12_certificate', 'corporate_kra_pin']
    if field not in valid_fields:
        return jsonify({'success': False, 'error': 'Invalid document field'}), 400

    if 'document_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400

    file = request.files['document_file']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            client_id = session['client_id']
            cursor.execute(f"SELECT {field} FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                return jsonify({'success': False, 'error': 'Client not found'}), 404

            old_filename = client.get(field)

            filename = secure_filename(file.filename)
            file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'bin'
            unique_filename = f"{field}_{client_id}_{secrets.token_hex(8)}.{file_ext}"
            upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
            filepath = os.path.join(upload_folder, unique_filename)
            file.save(filepath)

            cursor.execute(f"UPDATE clients SET {field} = %s WHERE id = %s", (unique_filename, client_id))
            connection.commit()

            if old_filename:
                old_path = os.path.join(upload_folder, old_filename)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass

            return jsonify({'success': True, 'message': 'Document updated successfully', 'filename': unique_filename})
    except Exception as e:
        print(f"Error updating personal upload: {e}")
        return jsonify({'success': False, 'error': f'Update failed: {str(e)}'}), 500
    finally:
        connection.close()


@app.route('/api/client_personal_upload/delete/<field>', methods=['POST'])
def client_delete_personal_upload(field):
    """Delete a personal upload document for the logged-in client"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    valid_fields = ['id_front', 'id_back', 'instruction_note', 'cr12_certificate', 'corporate_kra_pin']
    if field not in valid_fields:
        return jsonify({'success': False, 'error': 'Invalid document field'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            client_id = session['client_id']
            cursor.execute(f"SELECT {field} FROM clients WHERE id = %s", (client_id,))
            client = cursor.fetchone()
            if not client:
                return jsonify({'success': False, 'error': 'Client not found'}), 404

            old_filename = client.get(field)

            cursor.execute(f"UPDATE clients SET {field} = NULL WHERE id = %s", (client_id,))
            connection.commit()

            if old_filename:
                upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
                old_path = os.path.join(upload_folder, old_filename)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception:
                        pass

            return jsonify({'success': True, 'message': 'Document deleted successfully'})
    except Exception as e:
        print(f"Error deleting personal upload: {e}")
        return jsonify({'success': False, 'error': f'Delete failed: {str(e)}'}), 500
    finally:
        connection.close()


@app.route('/api/client_personal_upload/download/<field>')
def client_download_personal_upload(field):
    """Download a personal upload document for the logged-in client"""
    if 'client_id' not in session:
        flash('Unauthorized', 'error')
        return redirect(url_for('client_login'))

    valid_fields = ['id_front', 'id_back', 'instruction_note', 'cr12_certificate', 'corporate_kra_pin']
    if field not in valid_fields:
        flash('Invalid document field', 'error')
        return redirect(url_for('client_documents'))

    connection = get_db_connection()
    if not connection:
        flash('Database connection error', 'error')
        return redirect(url_for('client_documents'))

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(f"SELECT {field} FROM clients WHERE id = %s", (session['client_id'],))
            client = cursor.fetchone()
            if not client or not client.get(field):
                flash('Document not found', 'error')
                return redirect(url_for('client_documents'))

            upload_folder = app.config.get('UPLOAD_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
            return send_from_directory(
                os.path.abspath(upload_folder),
                client[field],
                as_attachment=True
            )
    except Exception as e:
        print(f"Error downloading personal upload: {e}")
        flash('Download failed', 'error')
        return redirect(url_for('client_documents'))
    finally:
        connection.close()


@app.route('/client_cases')
def client_cases():
    """Client cases page - clients can view their own cases"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_dashboard'))
            
            # Fetch all cases for this client
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.client_name,
                    c.case_type,
                    c.filing_date,
                    c.case_category,
                    c.station,
                    c.filled_by_name,
                    c.created_by_name,
                    c.description,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM cases c
                WHERE c.client_id = %s
                ORDER BY c.filing_date DESC, c.created_at DESC
            """, (session['client_id'],))
            cases = cursor.fetchall()
            
            # Convert date objects to strings
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            for case in cases:
                if case.get('filing_date'):
                    case['filing_date'] = case['filing_date'].strftime('%Y-%m-%d')
                if case.get('created_at'):
                    case['created_at'] = case['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if case.get('updated_at'):
                    case['updated_at'] = case['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_cases.html',
                                 client=client,
                                 cases=cases,
                                 client_id=session['client_id'],
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client cases: {e}")
        flash('An error occurred while fetching cases.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/client_cases/<int:case_id>')
def client_case_details(case_id):
    """Client case details page - shows all case information and proceedings"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        from datetime import date
        today = date.today()
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch case details and verify it belongs to the client
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.client_name,
                    c.case_type,
                    c.filing_date,
                    c.case_category,
                    c.station,
                    c.filled_by_id,
                    c.filled_by_name,
                    c.created_by_id,
                    c.created_by_name,
                    c.description,
                    c.status,
                    c.created_at,
                    c.updated_at,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone,
                    cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type as client_type,
                    cl.status as client_status
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE c.id = %s AND c.client_id = %s
            """, (case_id, session['client_id']))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found or you do not have permission to view this case', 'error')
                return redirect(url_for('client_cases'))
            
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            # Fetch all proceedings for this case
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.outcome_orders,
                    p.outcome_details,
                    p.next_court_date,
                    p.attendance,
                    p.next_attendance,
                    p.virtual_link,
                    p.reason,
                    p.created_at,
                    CASE 
                        WHEN EXISTS (
                            SELECT 1 FROM case_proceedings p2 
                            WHERE p2.previous_proceeding_id = p.id
                        ) THEN 0
                        ELSE 1
                    END as is_latest
                FROM case_proceedings p
                WHERE p.case_id = %s
                ORDER BY 
                    CASE 
                        WHEN EXISTS (
                            SELECT 1 FROM case_proceedings p2 
                            WHERE p2.previous_proceeding_id = p.id
                        ) THEN 0 
                        ELSE 1 
                    END DESC,
                    p.date_of_court_appeared DESC,
                    p.created_at DESC
            """, (case_id,))
            all_proceedings = cursor.fetchall()
            
            # Separate upcoming and past proceedings
            upcoming_proceedings = []
            past_proceedings = []
            
            for proceeding in all_proceedings:
                # Convert dates
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    days_until = (next_date - today).days
                    proceeding['days_until'] = days_until
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Categorize as upcoming or past
                if proceeding.get('next_court_date'):
                    next_date_obj = date.fromisoformat(proceeding['next_court_date'])
                    if next_date_obj >= today:
                        upcoming_proceedings.append(proceeding)
                    else:
                        past_proceedings.append(proceeding)
                elif proceeding.get('date_of_court_appeared'):
                    appeared_date_obj = date.fromisoformat(proceeding['date_of_court_appeared'])
                    if appeared_date_obj < today:
                        past_proceedings.append(proceeding)
                    else:
                        upcoming_proceedings.append(proceeding)
                else:
                    # If no dates, consider it past
                    past_proceedings.append(proceeding)
            
            # Sort upcoming by next_court_date (ascending), past by date_of_court_appeared (descending)
            upcoming_proceedings.sort(key=lambda x: x.get('next_court_date', '9999-99-99'))
            past_proceedings.sort(key=lambda x: x.get('date_of_court_appeared', '0000-00-00'), reverse=True)
            
            # Convert case dates
            if case_data.get('filing_date'):
                case_data['filing_date'] = case_data['filing_date'].strftime('%Y-%m-%d')
            if case_data.get('created_at'):
                case_data['created_at'] = case_data['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if case_data.get('updated_at'):
                case_data['updated_at'] = case_data['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Convert client dates
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_case_details.html',
                                 case_data=case_data,
                                 client=client,
                                 case_id=case_id,
                                 upcoming_proceedings=upcoming_proceedings,
                                 past_proceedings=past_proceedings,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client case details: {e}")
        flash('An error occurred while fetching case details.', 'error')
        return redirect(url_for('client_cases'))
    finally:
        connection.close()

@app.route('/client_calendar')
def client_calendar():
    """Client calendar page - displays upcoming court dates for client's cases"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        from datetime import date
        today = date.today()
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_dashboard'))
            
            # Fetch all upcoming court dates for this client's cases
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.next_court_date,
                    p.next_attendance,
                    p.virtual_link,
                    p.outcome_orders,
                    c.tracking_number,
                    c.client_name,
                    c.id as case_table_id
                FROM case_proceedings p
                JOIN cases c ON p.case_id = c.id
                WHERE c.client_id = %s AND p.next_court_date IS NOT NULL AND p.next_court_date >= %s
                ORDER BY p.next_court_date ASC
            """, (session['client_id'], today))
            all_upcoming_proceedings = list(cursor.fetchall() or [])
            
            # Convert dates and calculate days until
            for proceeding in all_upcoming_proceedings:
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    days_until = (next_date - today).days
                    proceeding['days_until'] = days_until
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Organize calendar events by date
            calendar_events = {}
            for proceeding in all_upcoming_proceedings:
                if proceeding.get('next_court_date'):
                    date_key = proceeding['next_court_date']
                    if date_key not in calendar_events:
                        calendar_events[date_key] = []
                    calendar_events[date_key].append(proceeding)
            
            # Convert client date
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_calendar.html', 
                                 company_settings=company_settings,
                                 client=client,
                                 all_upcoming_proceedings=all_upcoming_proceedings,
                                 calendar_events=calendar_events)
    except Exception as e:
        print(f"Error fetching client calendar: {e}")
        flash('An error occurred while fetching calendar.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/client_reminders')
def client_reminders():
    """Client reminders page - displays all materials/reminders for client's cases"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        from datetime import date
        today = date.today()
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('client_dashboard'))
            
            # Fetch all upcoming court dates for this client's cases
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.next_court_date,
                    p.next_attendance,
                    p.virtual_link,
                    p.outcome_orders,
                    c.tracking_number,
                    c.client_name,
                    c.id as case_table_id
                FROM case_proceedings p
                JOIN cases c ON p.case_id = c.id
                WHERE c.client_id = %s AND p.next_court_date IS NOT NULL AND p.next_court_date >= %s
                ORDER BY p.next_court_date ASC
            """, (session['client_id'], today))
            all_upcoming_proceedings = list(cursor.fetchall() or [])
            
            # Convert dates and calculate days until
            proceedings_with_materials = []
            all_reminders = []
            for proceeding in all_upcoming_proceedings:
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    days_until = (next_date - today).days
                    proceeding['days_until'] = days_until
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Fetch materials for this specific proceeding
                cursor.execute("""
                    SELECT 
                        m.id,
                        m.proceeding_id,
                        m.material_description,
                        m.reminder_frequency,
                        m.allocated_to_id,
                        m.allocated_to_name,
                        m.created_at,
                        m.updated_at
                    FROM case_proceeding_materials m
                    WHERE m.proceeding_id = %s
                    ORDER BY m.created_at ASC
                """, (proceeding['id'],))
                materials = cursor.fetchall()
                
                # Convert dates to strings
                for material in materials:
                    if material.get('created_at'):
                        material['created_at'] = material['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if material.get('updated_at'):
                        material['updated_at'] = material['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Attach materials to proceeding
                proceeding['materials'] = materials
                if materials:
                    proceedings_with_materials.append(proceeding)
                    all_reminders.extend(materials)
            
            # Convert client date
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_reminders.html', 
                                 company_settings=company_settings,
                                 client=client,
                                 proceedings_with_materials=proceedings_with_materials,
                                 all_reminders=all_reminders)
    except Exception as e:
        print(f"Error fetching client reminders: {e}")
        flash('An error occurred while fetching reminders.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/client_messages')
def client_messages():
    """Client inbox - displays conversations grouped by employee, plus chat view"""
    if 'client_id' not in session and 'original_employee_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            messages = []
            conversations = []
            try:
                cursor.execute("""
                    SELECT 
                        m.id,
                        m.client_id,
                        m.employee_id,
                        m.subject,
                        m.message,
                        m.attachment_file,
                        m.attachment_type,
                        m.sender_type,
                        m.delivery_channel,
                        m.whatsapp_message_id,
                        m.whatsapp_status,
                        m.is_read,
                        m.created_at,
                        e.full_name as employee_name,
                        e.profile_picture as employee_profile_picture
                    FROM webapp_messages m
                    LEFT JOIN employees e ON m.employee_id = e.id
                    WHERE m.client_id = %s
                    ORDER BY m.created_at ASC
                """, (session['client_id'],))
                messages = cursor.fetchall()
                
                for msg in messages:
                    if msg.get('created_at'):
                        if hasattr(msg['created_at'], 'strftime'):
                            msg['created_at'] = msg['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            msg['created_at'] = str(msg['created_at'])
                    if msg.get('is_read') is None:
                        msg['is_read'] = 0

                # Build conversation summaries grouped by employee_id
                conv_map = {}
                for msg in messages:
                    eid = msg.get('employee_id')
                    if not eid:
                        continue
                    if eid not in conv_map:
                        conv_map[eid] = {
                            'employee_id': eid,
                            'employee_name': msg.get('employee_name') or 'Staff',
                            'employee_profile_picture': msg.get('employee_profile_picture'),
                            'last_message': msg.get('message', ''),
                            'last_time': msg.get('created_at', ''),
                            'last_sender_type': msg.get('sender_type', ''),
                            'unread_count': 0,
                            'total_count': 0,
                            'has_attachment': bool(msg.get('attachment_file')),
                        }
                    c = conv_map[eid]
                    c['last_message'] = msg.get('message', '') or ''
                    c['last_time'] = msg.get('created_at', '')
                    c['last_sender_type'] = msg.get('sender_type', '')
                    c['total_count'] += 1
                    if msg.get('attachment_file'):
                        c['has_attachment'] = True
                    if msg.get('sender_type') == 'employee' and not msg.get('is_read'):
                        c['unread_count'] += 1

                conversations = sorted(conv_map.values(), key=lambda x: x['last_time'], reverse=True)

            except Exception as e:
                print(f"Messages table may not exist or error fetching: {e}")
                messages = []
                conversations = []
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            is_employee_viewing = 'original_employee_id' in session
            employee_name = session.get('original_employee_name', '')
            
            client_info = None
            try:
                cursor.execute("SELECT id, full_name, email, profile_picture FROM clients WHERE id = %s", (session['client_id'],))
                client_info = cursor.fetchone()
            except Exception:
                pass
            
            return render_template('client_messages.html',
                                 messages=messages,
                                 conversations=conversations,
                                 company_settings=company_settings,
                                 is_employee_viewing=is_employee_viewing,
                                 employee_name=employee_name,
                                 client_info=client_info)
    except Exception as e:
        print(f"Error fetching client messages: {e}")
        flash('An error occurred while loading messages.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/api/client/messages/<int:message_id>/read', methods=['POST'])
def mark_client_message_read(message_id):
    """Mark a single message as read for the logged-in client"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE webapp_messages SET is_read = 1 WHERE id = %s AND client_id = %s",
                (message_id, session['client_id'])
            )
            connection.commit()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error marking message read: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/client/messages/unread-count')
def client_unread_count():
    """Return the unread message count for the logged-in client"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'unread_count': 0})
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': True, 'unread_count': 0})
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM webapp_messages WHERE client_id = %s AND sender_type = 'employee' AND (is_read = 0 OR is_read IS NULL)",
                (session['client_id'],)
            )
            row = cursor.fetchone()
            count = row['cnt'] if isinstance(row, dict) else (row[0] if row else 0)
        return jsonify({'success': True, 'unread_count': count})
    except Exception:
        return jsonify({'success': True, 'unread_count': 0})
    finally:
        connection.close()

@app.route('/api/client/messages/send', methods=['POST'])
def send_client_message():
    """Send a message — as client (sender_type=client) or employee viewing as client (sender_type=employee)"""
    if 'client_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    client_id = session['client_id']
    is_employee = 'original_employee_id' in session
    sender_type = 'employee' if is_employee else 'client'
    if is_employee:
        employee_id = session.get('original_employee_id')
    else:
        employee_id = request.form.get('employee_id')
        if employee_id:
            try:
                employee_id = int(employee_id)
            except (ValueError, TypeError):
                employee_id = None

    subject = request.form.get('subject', '').strip() or 'Message'
    message = request.form.get('message', '').strip()
    attachment = request.files.get('attachment')

    if not message and not attachment:
        return jsonify({'success': False, 'error': 'Please enter a message or attach a file'}), 400

    attachment_file = None
    attachment_type = None
    if attachment and attachment.filename:
        import uuid as _uuid
        ext = os.path.splitext(attachment.filename)[1].lower()
        safe_name = f"{_uuid.uuid4().hex}{ext}"
        upload_dir = os.path.join('static', 'uploads', 'message_attachments')
        os.makedirs(upload_dir, exist_ok=True)
        attachment.save(os.path.join(upload_dir, safe_name))
        attachment_file = safe_name
        attachment_type = 'image' if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'] else 'document'

    # Determine delivery channel: also send via WhatsApp if configured
    send_via_whatsapp = request.form.get('send_whatsapp', '1')
    delivery_channel = 'web'
    wa_msg_id = None
    wa_status = None
    wa_error = None

    print(f"[WhatsApp Debug] send_whatsapp flag = '{send_via_whatsapp}', client_id = {client_id}")

    if send_via_whatsapp == '1':
        ws = get_whatsapp_settings()
        if not ws:
            wa_error = 'WhatsApp not configured — go to Communication Settings to set it up'
            print(f"[WhatsApp] {wa_error}")
        else:
            print(f"[WhatsApp Debug] Settings found, phone_number_id = {ws.get('phone_number_id')}")
            conn_tmp = get_db_connection()
            client_phone = None
            if conn_tmp:
                try:
                    with conn_tmp.cursor(pymysql.cursors.DictCursor) as cur:
                        cur.execute("SELECT phone_number, full_name FROM clients WHERE id = %s", (client_id,))
                        row = cur.fetchone()
                        if row:
                            client_phone = row.get('phone_number')
                            print(f"[WhatsApp Debug] Client '{row.get('full_name')}' phone = '{client_phone}'")
                        else:
                            print(f"[WhatsApp] No client found with id {client_id}")
                except Exception as e:
                    print(f"[WhatsApp] Error looking up client phone: {e}")
                finally:
                    conn_tmp.close()

            if not client_phone:
                wa_error = f'Client (id={client_id}) has no phone number — update their profile first'
                print(f"[WhatsApp] {wa_error}")
            else:
                text_to_send = message or ''
                if subject and subject != 'Message':
                    text_to_send = f"*{subject}*\n\n{text_to_send}"

                if attachment_file and attachment_type:
                    base_url = os.environ.get('APP_BASE_URL', request.url_root.rstrip('/'))
                    media_url = f"{base_url}/static/uploads/message_attachments/{attachment_file}"
                    print(f"[WhatsApp Debug] Sending media to {client_phone}: {media_url}")
                    ok, result = send_whatsapp_media(client_phone, media_url, caption=text_to_send, media_type=attachment_type, settings=ws)
                elif text_to_send:
                    print(f"[WhatsApp Debug] Sending text to {client_phone}: {text_to_send[:80]}...")
                    ok, result = send_whatsapp_message(client_phone, text_to_send, ws)
                else:
                    ok, result = False, 'Nothing to send'

                if ok:
                    delivery_channel = 'whatsapp'
                    wa_msg_id = result
                    wa_status = 'sent'
                    print(f"[WhatsApp] Message sent! wa_id = {wa_msg_id}")
                else:
                    wa_error = result
                    print(f"[WhatsApp] FAILED to send to {client_phone}: {result}")

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO webapp_messages
                    (client_id, employee_id, subject, message, attachment_file, attachment_type,
                     sender_type, delivery_channel, whatsapp_message_id, whatsapp_status, is_read, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, NOW())
            """, (client_id, employee_id, subject, message, attachment_file, attachment_type,
                  sender_type, delivery_channel, wa_msg_id, wa_status))
            connection.commit()
        resp = {'success': True, 'delivery_channel': delivery_channel}
        if wa_error:
            resp['whatsapp_error'] = wa_error
        return jsonify(resp)
    except Exception as e:
        print(f"Error sending message: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

# ==================== WHATSAPP API ROUTES ====================

@app.route('/api/whatsapp/settings/save', methods=['POST'])
def save_whatsapp_settings():
    """Save or update WhatsApp Cloud API settings"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    access_token = data.get('access_token', '').strip()
    phone_number_id = data.get('phone_number_id', '').strip()
    webhook_verify_token = data.get('webhook_verify_token', '').strip()
    waba_id = data.get('whatsapp_business_account_id', '').strip()
    display_phone = data.get('display_phone_number', '').strip()
    api_version = data.get('api_version', 'v21.0').strip()

    if not access_token or not phone_number_id or not webhook_verify_token:
        return jsonify({'success': False, 'error': 'Access Token, Phone Number ID, and Webhook Verify Token are required'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM whatsapp_settings LIMIT 1")
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE whatsapp_settings SET
                        access_token=%s, phone_number_id=%s, whatsapp_business_account_id=%s,
                        webhook_verify_token=%s, display_phone_number=%s, api_version=%s, is_active=1
                    WHERE id=%s
                """, (access_token, phone_number_id, waba_id, webhook_verify_token, display_phone, api_version, existing['id']))
            else:
                cursor.execute("""
                    INSERT INTO whatsapp_settings
                        (access_token, phone_number_id, whatsapp_business_account_id, webhook_verify_token, display_phone_number, api_version, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,1)
                """, (access_token, phone_number_id, waba_id, webhook_verify_token, display_phone, api_version))
            connection.commit()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error saving WhatsApp settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/whatsapp/settings', methods=['GET'])
def get_whatsapp_settings_api():
    """Return current WhatsApp settings (tokens masked)"""
    if 'employee_id' not in session:
        return jsonify({'success': False}), 401
    ws = get_whatsapp_settings()
    if not ws:
        return jsonify({'success': True, 'settings': None})
    return jsonify({'success': True, 'settings': {
        'phone_number_id': ws.get('phone_number_id', ''),
        'whatsapp_business_account_id': ws.get('whatsapp_business_account_id', ''),
        'display_phone_number': ws.get('display_phone_number', ''),
        'api_version': ws.get('api_version', 'v21.0'),
        'webhook_verify_token': ws.get('webhook_verify_token', ''),
        'has_access_token': bool(ws.get('access_token')),
        'is_active': bool(ws.get('is_active'))
    }})


@app.route('/api/whatsapp/test', methods=['POST'])
def test_whatsapp_connection():
    """Test WhatsApp connection by sending a test message to the configured number"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    ws = get_whatsapp_settings()
    if not ws:
        return jsonify({'success': False, 'error': 'WhatsApp not configured'}), 400

    test_phone = request.get_json().get('test_phone', ws.get('display_phone_number', ''))
    if not test_phone:
        return jsonify({'success': False, 'error': 'No phone number to test'}), 400

    ok, result = send_whatsapp_message(test_phone, 'Test message from SHERIA CENTRIC. WhatsApp integration is working!', ws)
    if ok:
        return jsonify({'success': True, 'message': f'Test message sent! (ID: {result})'})
    return jsonify({'success': False, 'error': result}), 400


@app.route('/api/whatsapp/send-direct', methods=['POST'])
def whatsapp_send_direct():
    """Send a WhatsApp message directly to any phone number"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    phone = data.get('phone', '').strip()
    msg_text = data.get('message', '').strip()

    if not phone:
        return jsonify({'success': False, 'error': 'Phone number is required'}), 400
    if not msg_text:
        return jsonify({'success': False, 'error': 'Message text is required'}), 400

    ws = get_whatsapp_settings()
    if not ws:
        return jsonify({'success': False, 'error': 'WhatsApp is not configured. Fill in and save your settings first.'}), 400

    print(f"[WhatsApp Direct] Sending to {phone}: {msg_text[:80]}...")
    ok, result = send_whatsapp_message(phone, msg_text, ws)
    if ok:
        print(f"[WhatsApp Direct] Sent! ID: {result}")
        return jsonify({'success': True, 'message': f'Message sent to {phone}! (ID: {result})'})

    print(f"[WhatsApp Direct] FAILED: {result}")
    return jsonify({'success': False, 'error': result}), 400


@app.route('/webhook/whatsapp', methods=['GET'])
def whatsapp_webhook_verify():
    """Verify the webhook with Meta (hub.challenge handshake)"""
    ws = get_whatsapp_settings()
    verify_token = ws.get('webhook_verify_token', '') if ws else ''
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == verify_token and verify_token:
        print("[WhatsApp] Webhook verified")
        return challenge, 200
    return 'Forbidden', 403


@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook_receive():
    """Receive incoming WhatsApp messages and status updates"""
    payload = request.get_json(silent=True)
    if not payload:
        return 'OK', 200

    try:
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})

                # --- Status updates (sent / delivered / read / failed) ---
                for status in value.get('statuses', []):
                    wa_id = status.get('id')
                    wa_status = status.get('status')  # sent, delivered, read, failed
                    if wa_id and wa_status:
                        conn = get_db_connection()
                        if conn:
                            try:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "UPDATE webapp_messages SET whatsapp_status=%s WHERE whatsapp_message_id=%s",
                                        (wa_status, wa_id))
                                    conn.commit()
                            except Exception as e:
                                print(f"[WhatsApp] Status update error: {e}")
                            finally:
                                conn.close()

                # --- Incoming messages ---
                for msg in value.get('messages', []):
                    from_phone = msg.get('from', '')  # sender phone without +
                    wa_msg_id = msg.get('id', '')
                    msg_type = msg.get('type', '')
                    timestamp = msg.get('timestamp', '')

                    text_body = ''
                    attachment_file = None
                    attachment_type = None

                    if msg_type == 'text':
                        text_body = msg.get('text', {}).get('body', '')
                    elif msg_type in ('image', 'document', 'video', 'audio'):
                        media = msg.get(msg_type, {})
                        text_body = media.get('caption', '')
                        attachment_type = 'image' if msg_type == 'image' else 'document'

                    if not from_phone:
                        continue

                    # Find client by phone number
                    conn = get_db_connection()
                    if not conn:
                        continue
                    try:
                        with conn.cursor(pymysql.cursors.DictCursor) as cur:
                            # Try matching phone (with or without country code prefix)
                            cur.execute("""
                                SELECT id FROM clients
                                WHERE REPLACE(REPLACE(REPLACE(phone_number,' ',''),'-',''),'+','') = %s
                                   OR REPLACE(REPLACE(REPLACE(phone_number,' ',''),'-',''),'+','') LIKE %s
                                LIMIT 1
                            """, (from_phone, '%' + from_phone[-9:]))
                            client = cur.fetchone()

                            if not client:
                                print(f"[WhatsApp] No client found for phone {from_phone}")
                                continue

                            # Check for duplicate
                            cur.execute("SELECT id FROM webapp_messages WHERE whatsapp_message_id=%s LIMIT 1", (wa_msg_id,))
                            if cur.fetchone():
                                continue

                            cur.execute("""
                                INSERT INTO webapp_messages
                                    (client_id, subject, message, attachment_file, attachment_type,
                                     sender_type, delivery_channel, whatsapp_message_id, whatsapp_status, is_read, created_at)
                                VALUES (%s, %s, %s, %s, %s, 'client', 'whatsapp', %s, 'received', 0, NOW())
                            """, (client['id'], 'WhatsApp Message', text_body, attachment_file, attachment_type, wa_msg_id))
                            conn.commit()
                            print(f"[WhatsApp] Saved incoming message from {from_phone} for client {client['id']}")
                    except Exception as e:
                        print(f"[WhatsApp] Error saving incoming message: {e}")
                    finally:
                        conn.close()
    except Exception as e:
        print(f"[WhatsApp] Webhook processing error: {e}")

    return 'OK', 200


# ==================== SMS API ROUTES ====================

@app.route('/api/sms/settings/save', methods=['POST'])
def save_sms_settings():
    """Save or update SMS gateway settings"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    provider = data.get('provider', '').strip()
    api_key = data.get('api_key', '').strip()
    api_secret = data.get('api_secret', '').strip()
    sender_id = data.get('sender_id', '').strip()
    username = data.get('username', '').strip()
    country_code = data.get('default_country_code', '+254').strip()
    custom_url = data.get('custom_api_url', '').strip()

    if not provider or not api_key:
        return jsonify({'success': False, 'error': 'Provider and API Key are required'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM sms_settings LIMIT 1")
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE sms_settings SET
                        provider=%s, api_key=%s, api_secret=%s, sender_id=%s,
                        username=%s, default_country_code=%s, custom_api_url=%s, is_active=1
                    WHERE id=%s
                """, (provider, api_key, api_secret, sender_id, username, country_code, custom_url, existing['id']))
            else:
                cursor.execute("""
                    INSERT INTO sms_settings
                        (provider, api_key, api_secret, sender_id, username, default_country_code, custom_api_url, is_active)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,1)
                """, (provider, api_key, api_secret, sender_id, username, country_code, custom_url))
            connection.commit()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error saving SMS settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/sms/settings', methods=['GET'])
def get_sms_settings_api():
    """Return current SMS settings (keys masked)"""
    if 'employee_id' not in session:
        return jsonify({'success': False}), 401
    ss = get_sms_settings()
    if not ss:
        return jsonify({'success': True, 'settings': None})
    return jsonify({'success': True, 'settings': {
        'provider': ss.get('provider', ''),
        'sender_id': ss.get('sender_id', ''),
        'username': ss.get('username', ''),
        'default_country_code': ss.get('default_country_code', '+254'),
        'custom_api_url': ss.get('custom_api_url', ''),
        'has_api_key': bool(ss.get('api_key')),
        'has_api_secret': bool(ss.get('api_secret')),
        'is_active': bool(ss.get('is_active'))
    }})


@app.route('/api/sms/send-direct', methods=['POST'])
def sms_send_direct():
    """Send an SMS directly to any phone number"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    phone = data.get('phone', '').strip()
    msg_text = data.get('message', '').strip()

    if not phone:
        return jsonify({'success': False, 'error': 'Phone number is required'}), 400
    if not msg_text:
        return jsonify({'success': False, 'error': 'Message text is required'}), 400

    ss = get_sms_settings()
    if not ss:
        return jsonify({'success': False, 'error': 'SMS is not configured. Fill in and save your settings first.'}), 400

    print(f"[SMS Direct] Sending to {phone} via {ss.get('provider')}: {msg_text[:80]}...")
    ok, result = send_sms(phone, msg_text, ss)
    if ok:
        print(f"[SMS Direct] Sent! ID: {result}")
        return jsonify({'success': True, 'message': f'SMS sent to {phone}! (ID: {result})'})

    print(f"[SMS Direct] FAILED: {result}")
    return jsonify({'success': False, 'error': result}), 400


@app.route('/client_registration')
def client_registration():
    """Client registration page - complete profile with phone number"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    client = {}
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT
                        id,
                        full_name,
                        email,
                        phone_number,
                        client_type,
                        client_address,
                        national_id,
                        profile_picture,
                        id_front,
                        id_back,
                        cr12_certificate
                    FROM clients
                    WHERE id = %s
                """, (session['client_id'],))
                client = cursor.fetchone() or {}
        except Exception as e:
            print(f"Error loading client registration data: {e}")
            client = {}
        finally:
            connection.close()
    
    return render_template('client_registration.html', company_settings=company_settings, client=client)

@app.route('/submit_client_registration', methods=['POST'])
def submit_client_registration():
    """Handle client registration form submission"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    phone_number = request.form.get('phone_number', '').strip().replace(' ', '')
    client_type = request.form.get('client_type', '').strip()
    
    if not phone_number:
        flash('Phone number is required', 'error')
        return redirect(url_for('client_registration'))
    
    if not client_type or client_type not in ['Individual', 'Corporate']:
        flash('Please select a client type', 'error')
        return redirect(url_for('client_registration'))
    
    # Validate phone number format (Kenyan format: starts with 07 or +254)
    if not (phone_number.startswith('07') or phone_number.startswith('+254')):
        flash('Please enter a valid Kenyan phone number (starting with 07)', 'error')
        return redirect(url_for('client_registration'))
    
    # Handle profile picture upload (optional)
    profile_picture = None
    if 'profile_picture' in request.files:
        file = request.files['profile_picture']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Create unique filename
            file_ext = filename.rsplit('.', 1)[1].lower()
            unique_filename = f"client_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            profile_picture = unique_filename
    
    # Handle Individual client requirements (all optional uploads)
    id_front = None
    id_back = None
    id_number = None
    instruction_note = None
    if client_type == 'Individual':
        id_number = request.form.get('id_number', '').strip()
        
        if 'id_front' in request.files:
            file = request.files['id_front']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"id_front_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                id_front = unique_filename
        
        if 'id_back' in request.files:
            file = request.files['id_back']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"id_back_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                id_back = unique_filename
        
        if 'instruction_note' in request.files:
            file = request.files['instruction_note']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"instruction_note_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                instruction_note = unique_filename
    
    # Handle Corporate client requirements (all optional uploads)
    cr12_certificate = None
    corporate_kra_pin_file = None
    if client_type == 'Corporate':
        if 'cr12_certificate' in request.files:
            file = request.files['cr12_certificate']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"cr12_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                cr12_certificate = unique_filename
        
        if 'corporate_kra_pin' in request.files:
            file = request.files['corporate_kra_pin']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"kra_pin_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                corporate_kra_pin_file = unique_filename
        
        if 'instruction_note' in request.files:
            file = request.files['instruction_note']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"instruction_note_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                instruction_note = unique_filename
    
    # Update client in database
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Build update query based on client type and provided data
                update_fields = ['phone_number = %s', 'client_type = %s']
                update_values = [phone_number, client_type]

                physical_address = request.form.get('physical_address', '').strip()
                if physical_address:
                    update_fields.append('client_address = %s')
                    update_values.append(physical_address)

                if profile_picture:
                    update_fields.append('profile_picture = %s')
                    update_values.append(profile_picture)
                    session['client_profile_picture'] = profile_picture

                if client_type == 'Individual':
                    if id_number:
                        update_fields.append('national_id = %s')
                        update_values.append(id_number)
                    if id_front:
                        update_fields.append('id_front = %s')
                        update_values.append(id_front)
                    if id_back:
                        update_fields.append('id_back = %s')
                        update_values.append(id_back)
                elif client_type == 'Corporate':
                    if cr12_certificate:
                        update_fields.append('cr12_certificate = %s')
                        update_values.append(cr12_certificate)
                
                update_values.append(session['client_id'])
                
                query = f"UPDATE clients SET {', '.join(update_fields)} WHERE id = %s"
                cursor.execute(query, tuple(update_values))
                
                # If no profile picture uploaded, keep existing one
                if not profile_picture:
                    cursor.execute("SELECT profile_picture FROM clients WHERE id = %s", (session['client_id'],))
                    client = cursor.fetchone()
                    if client and client.get('profile_picture'):
                        if client['profile_picture'].startswith('http'):
                            session['client_profile_picture'] = client['profile_picture']
                        else:
                            session['client_profile_picture'] = client['profile_picture']
                
                # Update session with client_type
                session['client_type'] = client_type
                
                connection.commit()
                flash('Registration completed successfully!', 'success')
                return redirect(url_for('client_dashboard'))
        except Exception as e:
            print(f"Error updating client registration: {e}")
            flash('An error occurred. Please try again.', 'error')
        finally:
            connection.close()
    
    return redirect(url_for('client_registration'))

@app.route('/client_profile')
def client_profile():
    """Client profile page"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_login'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM clients WHERE id = %s
            """, (session['client_id'],))
            client = cursor.fetchone()
            
            if not client:
                session.clear()
                flash('Client not found', 'error')
                return redirect(url_for('client_login'))
            
            # Get company settings
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_profile.html', client=client, company_settings=company_settings)
    except Exception as e:
        print(f"Client profile error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('client_login'))
    finally:
        connection.close()

@app.route('/update_client_profile', methods=['POST'])
def update_client_profile():
    """Update client profile"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_profile'))
    
    try:
        full_name = request.form.get('full_name', '').strip().upper()
        phone_number = request.form.get('phone_number', '').strip().replace(' ', '')
        national_id = request.form.get('national_id', '').strip()
        kra_pin = request.form.get('kra_pin', '').strip().upper()
        client_address = request.form.get('client_address', '').strip()
        address_latitude = request.form.get('address_latitude', '').strip()
        address_longitude = request.form.get('address_longitude', '').strip()
        
        # Validation
        errors = []
        if not full_name:
            errors.append('Full name is required')
        if not phone_number:
            errors.append('Phone number is required')
        
        # Validate phone number format (Kenyan format: starts with 07)
        if phone_number and not (phone_number.startswith('07') or phone_number.startswith('+254')):
            errors.append('Please enter a valid Kenyan phone number (starting with 07)')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return redirect(url_for('client_profile'))
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Handle profile picture upload
            profile_picture = None
            old_profile_picture = None
            
            # Get current profile picture
            cursor.execute("SELECT profile_picture FROM clients WHERE id = %s", (session['client_id'],))
            current_client = cursor.fetchone()
            if current_client:
                old_profile_picture = current_client.get('profile_picture')
            
            if 'profile_picture' in request.files:
                file = request.files['profile_picture']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file_ext = filename.rsplit('.', 1)[1].lower()
                    unique_filename = f"client_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(filepath)
                    profile_picture = unique_filename
                    
                    # Delete old uploaded profile picture if exists (not Google URL)
                    if old_profile_picture and not old_profile_picture.startswith('http'):
                        old_filepath = os.path.join(app.config['UPLOAD_FOLDER'], old_profile_picture)
                        if os.path.exists(old_filepath):
                            try:
                                os.remove(old_filepath)
                            except Exception as e:
                                print(f"Error deleting old profile picture: {e}")
            
            # Update client in database
            lat_val = float(address_latitude) if address_latitude else None
            lng_val = float(address_longitude) if address_longitude else None

            if profile_picture:
                cursor.execute("""
                    UPDATE clients 
                    SET full_name = %s, phone_number = %s, national_id = %s, kra_pin = %s, 
                        client_address = %s, address_latitude = %s, address_longitude = %s, profile_picture = %s
                    WHERE id = %s
                """, (full_name, phone_number, national_id or None, kra_pin or None, client_address or None, lat_val, lng_val, profile_picture, session['client_id']))
                session['client_profile_picture'] = profile_picture
            else:
                cursor.execute("""
                    UPDATE clients 
                    SET full_name = %s, phone_number = %s, national_id = %s, kra_pin = %s, 
                        client_address = %s, address_latitude = %s, address_longitude = %s
                    WHERE id = %s
                """, (full_name, phone_number, national_id or None, kra_pin or None, client_address or None, lat_val, lng_val, session['client_id']))
                if old_profile_picture:
                    session['client_profile_picture'] = old_profile_picture
            
            connection.commit()
            
            # Update session
            session['client_name'] = full_name
            
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('client_profile'))
    except Exception as e:
        print(f"Error updating client profile: {e}")
        flash('An error occurred while updating your profile. Please try again.', 'error')
        return redirect(url_for('client_profile'))
    finally:
        connection.close()

@app.route('/client_logout')
def client_logout():
    """Client logout"""
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_email', None)
    session.pop('client_profile_picture', None)
    session.pop('client_type', None)
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('index'))

@app.route('/profile')
def profile():
    """Employee profile page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('login'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT * FROM employees WHERE id = %s
            """, (session['employee_id'],))
            employee = cursor.fetchone()
            
            if not employee:
                session.clear()
                flash('Employee not found', 'error')
                return redirect(url_for('login'))
            
            # Get company settings
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            # Check if role is switched
            original_role = session.get('original_role')
            current_role = session.get('employee_role')
            is_role_switched = original_role == 'IT Support' and current_role != 'IT Support'
            
            return render_template('profile.html', employee=employee, company_settings=company_settings, 
                                 is_role_switched=is_role_switched, switched_role=current_role if is_role_switched else None)
    except Exception as e:
        print(f"Profile error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('login'))
    finally:
        connection.close()

@app.route('/update_profile', methods=['POST'])
def update_profile():
    """Update employee profile"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('profile'))
    
    try:
        full_name = request.form.get('full_name', '').strip()
        phone_number = request.form.get('phone_number', '').strip()
        work_email = request.form.get('work_email', '').strip()
        
        # Validation
        errors = []
        if not full_name:
            errors.append('Full name is required')
        if not phone_number:
            errors.append('Phone number is required')
        if not work_email:
            errors.append('Work email is required')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return redirect(url_for('profile'))
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if email is already taken by another user
            cursor.execute("""
                SELECT id FROM employees 
                WHERE work_email = %s AND id != %s
            """, (work_email, session['employee_id']))
            if cursor.fetchone():
                flash('Work email is already registered by another user', 'error')
                return redirect(url_for('profile'))
            
            # Handle profile picture upload
            profile_picture = None
            if 'profile_picture' in request.files:
                file = request.files['profile_picture']
                if file and file.filename and allowed_file(file.filename):
                    # Get current employee code for filename
                    cursor.execute("SELECT employee_code FROM employees WHERE id = %s", (session['employee_id'],))
                    emp_data = cursor.fetchone()
                    employee_code = emp_data['employee_code'] if emp_data else 'user'
                    
                    filename = secure_filename(file.filename)
                    file_ext = filename.rsplit('.', 1)[1].lower()
                    unique_filename = f"{employee_code}_{secrets.token_hex(8)}.{file_ext}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(filepath)
                    profile_picture = unique_filename
                    
                    # Delete old profile picture if exists
                    cursor.execute("SELECT profile_picture FROM employees WHERE id = %s", (session['employee_id'],))
                    old_pic = cursor.fetchone()
                    if old_pic and old_pic['profile_picture']:
                        old_filepath = os.path.join(app.config['UPLOAD_FOLDER'], old_pic['profile_picture'])
                        if os.path.exists(old_filepath):
                            try:
                                os.remove(old_filepath)
                            except:
                                pass
            
            # Handle password change
            current_password = request.form.get('current_password', '').strip()
            new_password = request.form.get('new_password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()
            
            password_updated = False
            if current_password or new_password or confirm_password:
                # All password fields must be filled if changing password
                if not current_password or not new_password or not confirm_password:
                    flash('All password fields are required to change password', 'error')
                    return redirect(url_for('profile'))
                
                if len(new_password) < 6:
                    flash('New password must be at least 6 characters long (letters and numbers allowed)', 'error')
                    return redirect(url_for('profile'))
                
                if new_password != confirm_password:
                    flash('New password and confirm password do not match', 'error')
                    return redirect(url_for('profile'))
                
                # Verify current password
                cursor.execute("SELECT password_hash FROM employees WHERE id = %s", (session['employee_id'],))
                emp_data = cursor.fetchone()
                if not emp_data or not check_password_hash(emp_data['password_hash'], current_password):
                    flash('Current password is incorrect', 'error')
                    return redirect(url_for('profile'))
                
                # Update password
                new_password_hash = generate_password_hash(new_password)
                password_updated = True
            
            # Update employee data
            if profile_picture and password_updated:
                cursor.execute("""
                    UPDATE employees 
                    SET full_name = %s, phone_number = %s, work_email = %s, profile_picture = %s, password_hash = %s
                    WHERE id = %s
                """, (full_name, phone_number, work_email, profile_picture, new_password_hash, session['employee_id']))
                session['profile_picture'] = profile_picture
            elif profile_picture:
                cursor.execute("""
                    UPDATE employees 
                    SET full_name = %s, phone_number = %s, work_email = %s, profile_picture = %s
                    WHERE id = %s
                """, (full_name, phone_number, work_email, profile_picture, session['employee_id']))
                session['profile_picture'] = profile_picture
            elif password_updated:
                cursor.execute("""
                    UPDATE employees 
                    SET full_name = %s, phone_number = %s, work_email = %s, password_hash = %s
                    WHERE id = %s
                """, (full_name, phone_number, work_email, new_password_hash, session['employee_id']))
            else:
                cursor.execute("""
                    UPDATE employees 
                    SET full_name = %s, phone_number = %s, work_email = %s
                    WHERE id = %s
                """, (full_name, phone_number, work_email, session['employee_id']))
            
            connection.commit()
            
            # Update session
            session['employee_name'] = full_name
            
            success_msg = 'Profile updated successfully!'
            if password_updated:
                success_msg += ' Your password has been changed.'
            flash(success_msg, 'success')
            return redirect(url_for('profile'))
            
    except pymysql.IntegrityError as e:
        if 'work_email' in str(e):
            flash('Work email is already registered', 'error')
        else:
            flash('An error occurred while updating profile', 'error')
    except Exception as e:
        print(f"Profile update error: {e}")
        flash('An error occurred while updating profile', 'error')
    finally:
        connection.close()
    
    return redirect(url_for('profile'))

@app.route('/switch_role/<role_name>')
def switch_role(role_name):
    """Switch role for IT Support technicians"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user is IT Support or already in role switch mode
    original_role = session.get('original_role')
    current_role = session.get('employee_role')
    
    # If not already in role switch, check if current role is IT Support
    if not original_role:
        if current_role != 'IT Support':
            flash('Only IT Support technicians can switch roles', 'error')
            return redirect(url_for('dashboard'))
        # Store original role
        session['original_role'] = current_role
    
    # Validate role name
    valid_roles = ['Firm Administrator', 'Managing Partner', 'Finance Office', 
                   'Associate Advocate', 'Clerk', 'IT Support', 'Employee']
    
    if role_name not in valid_roles:
        flash('Invalid role selected', 'error')
        return redirect(url_for('dashboard'))
    
    # Switch to the selected role
    session['employee_role'] = role_name
    flash(f'Switched to {role_name} role', 'success')
    return redirect(url_for('dashboard'))

@app.route('/exit_role_switch')
def exit_role_switch():
    """Exit role switch and return to original IT Support role"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    original_role = session.get('original_role')
    if not original_role or original_role != 'IT Support':
        flash('No active role switch session', 'error')
        return redirect(url_for('dashboard'))
    
    # Restore original role
    session['employee_role'] = original_role
    session.pop('original_role', None)
    flash('Returned to IT Support role', 'success')
    return redirect(url_for('dashboard'))

@app.route('/view_as_client/<int:client_id>')
def view_as_client(client_id):
    """Switch to client view - allows employees to view client portal"""
    if 'employee_id' not in session:
        flash('You must be logged in as an employee to view client portals', 'error')
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Verify client exists and is active
            cursor.execute("""
                SELECT id, full_name, email, status
                FROM clients
                WHERE id = %s
            """, (client_id,))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('dashboard'))
            
            if client.get('status') != 'Active':
                flash('Can only view active clients', 'error')
                return redirect(url_for('dashboard'))
            
            # Store employee session info for later restoration
            if 'client_id' not in session:  # Only store if not already in client view
                session['original_employee_id'] = session.get('employee_id')
                session['original_employee_name'] = session.get('employee_name')
                session['original_employee_role'] = session.get('employee_role')
                session['original_profile_picture'] = session.get('profile_picture')
            
            # Switch to client session
            session['client_id'] = client['id']
            session['client_name'] = client['full_name']
            session['client_email'] = client['email']
            
            # Clear employee session (but keep original for restoration)
            session.pop('employee_id', None)
            session.pop('employee_name', None)
            session.pop('employee_role', None)
            session.pop('profile_picture', None)
            
            flash(f'Viewing as client: {client["full_name"]}', 'success')
            return redirect(url_for('client_dashboard'))
    except Exception as e:
        print(f"Error switching to client view: {e}")
        flash('An error occurred while switching to client view', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/exit_client_view')
def exit_client_view():
    """Exit client view and return to employee dashboard"""
    if 'original_employee_id' not in session:
        flash('No active client view session', 'error')
        return redirect(url_for('client_login'))
    
    # Restore employee session
    session['employee_id'] = session.get('original_employee_id')
    session['employee_name'] = session.get('original_employee_name')
    session['employee_role'] = session.get('original_employee_role')
    session['profile_picture'] = session.get('original_profile_picture')
    
    # Clear client session and original employee info
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_email', None)
    session.pop('original_employee_id', None)
    session.pop('original_employee_name', None)
    session.pop('original_employee_role', None)
    session.pop('original_profile_picture', None)
    
    flash('Returned to employee dashboard', 'success')
    return redirect(url_for('dashboard'))

@app.route('/employee_communications')
def employee_communications():
    """Employee Communications page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Check if employee_id is provided in query params
    employee_id = request.args.get('employee_id')
    employee = None
    email_communications = []
    whatsapp_communications = []
    sms_communications = []
    
    connection = get_db_connection()
    employees = []
    
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                if employee_id:
                    # Get specific employee details
                    cursor.execute("""
                        SELECT id, full_name, phone_number, work_email, employee_code, role, status, profile_picture
                        FROM employees 
                        WHERE id = %s
                    """, (employee_id,))
                    employee = cursor.fetchone()
                else:
                    # Fetch all active employees
                    cursor.execute("""
                        SELECT id, full_name, phone_number, work_email, employee_code, role, status, profile_picture
                        FROM employees 
                        WHERE status = 'Active'
                        ORDER BY full_name ASC
                    """)
                    employees = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching employees: {e}")
        finally:
            connection.close()
    
    # Fetch all email accounts from cPanel and database
    email_accounts = get_email_accounts_from_db()
    email_settings = get_email_settings()
    
    # Also fetch from cPanel if settings are configured
    cpanel_emails = []
    if email_settings:
        try:
            result = list_email_accounts(
                email_settings['cpanel_api_token'],
                email_settings['cpanel_domain'],
                email_settings['cpanel_user'],
                email_settings['cpanel_api_port']
            )
            if result.get('status') == 1 and 'data' in result:
                for account in result['data']:
                    email_addr = account.get('email', '')
                    if email_addr:
                        # Check if already in email_accounts
                        if not any(ea.get('email_address') == email_addr for ea in email_accounts):
                            cpanel_emails.append({
                                'email_address': email_addr,
                                'is_cpanel': True,
                                'disk_used': account.get('humandiskused', '0 MB'),
                                'disk_quota': account.get('humandiskquota', '250 MB')
                            })
        except Exception as e:
            print(f"Error fetching cPanel emails: {e}")
    
    # Combine all emails
    all_emails = email_accounts + cpanel_emails
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('employee_communications.html',
                         company_settings=company_settings,
                         employees=employees,
                         email_accounts=all_emails,
                         email_settings=email_settings,
                         employee=employee,
                         email_communications=email_communications,
                         whatsapp_communications=whatsapp_communications,
                         sms_communications=sms_communications)

@app.route('/employee_communications/<int:employee_id>/email/<path:contact_email>')
def employee_email_conversation(employee_id, contact_email):
    """Email conversation page for a specific contact"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Decode the email
    contact_email = contact_email.replace('%40', '@')
    
    connection = get_db_connection()
    employee = None
    emails = []
    
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Get employee details
                cursor.execute("""
                    SELECT id, full_name, phone_number, work_email, employee_code, role, status, profile_picture
                    FROM employees 
                    WHERE id = %s
                """, (employee_id,))
                employee = cursor.fetchone()
                
                if employee and employee.get('work_email'):
                    # Get email settings
                    email_settings = get_email_settings()
                    if email_settings:
                        # Get password for the email
                        password = email_settings['main_email_password']
                        cursor.execute("SELECT email_password FROM email_accounts WHERE email_address = %s", (employee['work_email'],))
                        account = cursor.fetchone()
                        if account and account.get('email_password'):
                            password = account['email_password']
                        
                        # Fetch all emails
                        all_emails = fetch_emails_from_imap(
                            employee['work_email'], password,
                            email_settings['imap_host'], email_settings['imap_port'],
                            email_settings['imap_use_ssl'], 200
                        )
                        
                        # Filter emails for this contact (both sent and received)
                        contact_email_lower = contact_email.lower()
                        for email in all_emails:
                            email_from = email.get('from', '').lower()
                            email_to = email.get('to', '').lower()
                            # Check if email is from or to this contact
                            if contact_email_lower in email_from or contact_email_lower in email_to:
                                emails.append(email)
                        
                        # Sort by date (newest first)
                        emails.sort(key=lambda x: x.get('date', ''), reverse=True)
        except Exception as e:
            print(f"Error fetching email conversation: {e}")
            import traceback
            print(traceback.format_exc())
        finally:
            if connection:
                connection.close()
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('employee_email_conversation.html',
                         company_settings=company_settings,
                         employee=employee,
                         contact_email=contact_email,
                         emails=emails)

@app.route('/onboarding_approvals')
def onboarding_approvals():
    """Onboarding & Approvals page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('onboarding_approvals.html', company_settings=company_settings)

@app.route('/onboarding')
def onboarding():
    """Employee onboarding form page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    employee_id = session['employee_id']
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, status, onboarding_completed
                FROM employees WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                flash('Employee not found', 'error')
                return redirect(url_for('dashboard'))
            
            # Check if already completed onboarding
            if employee.get('onboarding_completed'):
                flash('You have already completed onboarding. Please wait for administrator approval.', 'info')
                return redirect(url_for('dashboard'))
            
            # Allow onboarding for Pending Approval status (not just Active)
            if employee.get('status') not in ['Active', 'Pending Approval']:
                flash('Your account must be in pending approval or active status to complete onboarding.', 'error')
                return redirect(url_for('dashboard'))
            
            # Get employee contract status
            cursor.execute("""
                SELECT employment_contract FROM employees WHERE id = %s
            """, (employee_id,))
            contract_data = cursor.fetchone()
            has_contract = bool(contract_data and contract_data.get('employment_contract'))
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('onboarding.html', employee=employee, company_settings=company_settings, has_contract=has_contract)
    except Exception as e:
        print(f"Onboarding page error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/submit_onboarding', methods=['POST'])
def submit_onboarding():
    """Handle onboarding form submission"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    employee_id = session['employee_id']
    
    # Get form data (tax_pin removed from form; kept in DB for legacy)
    tax_pin = request.form.get('tax_pin', '').strip().upper() or None
    payment_method = request.form.get('payment_method', '').strip()
    account_number = request.form.get('account_number', '').strip().upper()
    account_name = request.form.get('account_name', '').strip().upper()
    bank_name = request.form.get('bank_name', '').strip()
    mobile_money_company = request.form.get('mobile_money_company', '').strip()
    
    # Validation
    errors = []
    if not payment_method:
        errors.append('Payment method is required')
    elif payment_method == 'Bank':
        if not bank_name:
            errors.append('Bank name is required for bank payment method')
        if not account_number:
            errors.append('Account number is required')
        if not account_name:
            errors.append('Account name is required')
    elif payment_method == 'Mobile Money':
        if not mobile_money_company:
            errors.append('Mobile money company name is required')
        if not account_number:
            errors.append('Phone number/Account number is required')
        if not account_name:
            errors.append('Account name is required')
    
    if errors:
        for error in errors:
            flash(error, 'error')
        return redirect(url_for('onboarding'))
    
    # Handle employment contract file upload
    employment_contract = None
    if 'employment_contract' in request.files:
        file = request.files['employment_contract']
        if file and file.filename and allowed_document_file(file.filename):
            filename = secure_filename(file.filename)
            # Create unique filename
            file_ext = filename.rsplit('.', 1)[1].lower()
            unique_filename = f"contract_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            employment_contract = unique_filename
    
    if not employment_contract:
        flash('Employment contract upload is required', 'error')
        return redirect(url_for('onboarding'))
    
    # Handle ID front file upload
    id_front = None
    if 'id_front' in request.files:
        file = request.files['id_front']
        if file and file.filename and allowed_id_file(file.filename):
            filename = secure_filename(file.filename)
            # Create unique filename
            file_ext = filename.rsplit('.', 1)[1].lower()
            unique_filename = f"id_document_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            id_front = unique_filename
    
    if not id_front:
        flash('National ID or Passport upload is required', 'error')
        return redirect(url_for('onboarding'))
    
    # Single ID document: id_back no longer required (combined into national ID or passport)
    id_back = None
    
    # Handle KRA PIN certificate upload
    kra_pin_document = None
    if 'kra_pin_document' in request.files:
        file = request.files['kra_pin_document']
        if file and file.filename and (allowed_id_file(file.filename) or allowed_document_file(file.filename)):
            filename = secure_filename(file.filename)
            file_ext = filename.rsplit('.', 1)[1].lower()
            unique_filename = f"kra_pin_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            kra_pin_document = unique_filename
    
    if not kra_pin_document:
        flash('KRA PIN certificate upload is required', 'error')
        return redirect(url_for('onboarding'))
    
    # Handle signature upload (optional)
    signature = None
    signature_hash = None
    if 'signature' in request.files:
        file = request.files['signature']
        if file and file.filename:
            # Check if processed data is available (from frontend)
            processed_data = request.form.get('signature_processed', '')
            
            if processed_data:
                try:
                    # Decode base64 image
                    header, encoded = processed_data.split(',', 1)
                    signature_bytes = base64.b64decode(encoded)
                    
                    # Process signature image
                    processed_img = process_signature_image(BytesIO(signature_bytes))
                    
                    if processed_img:
                        # Generate hash
                        processed_img.seek(0)
                        signature_hash = generate_signature_hash(processed_img.read())
                        
                        # Save processed signature
                        filename = secure_filename(file.filename)
                        file_ext = 'png'  # Always save as PNG after processing
                        unique_filename = f"signature_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        
                        processed_img.seek(0)
                        with open(filepath, 'wb') as f:
                            f.write(processed_img.read())
                        
                        signature = unique_filename
                except Exception as e:
                    print(f"Error processing signature: {e}")
                    # Fallback to original file if processing fails
                    if file.filename and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        file_ext = filename.rsplit('.', 1)[1].lower()
                        unique_filename = f"signature_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(filepath)
                        signature = unique_filename
                        
                        # Generate hash from saved file
                        with open(filepath, 'rb') as f:
                            signature_hash = generate_signature_hash(f.read())
    
    # Handle stamp upload (optional)
    stamp = None
    stamp_hash = None
    if 'stamp' in request.files:
        file = request.files['stamp']
        if file and file.filename:
            # Check if processed data is available (from frontend)
            processed_data = request.form.get('stamp_processed', '')
            
            if processed_data:
                try:
                    # Decode base64 image
                    header, encoded = processed_data.split(',', 1)
                    stamp_bytes = base64.b64decode(encoded)
                    
                    # Process stamp image
                    processed_img = process_signature_image(BytesIO(stamp_bytes))
                    
                    if processed_img:
                        # Generate hash
                        processed_img.seek(0)
                        stamp_hash = generate_signature_hash(processed_img.read())
                        
                        # Save processed stamp
                        filename = secure_filename(file.filename)
                        file_ext = 'png'  # Always save as PNG after processing
                        unique_filename = f"stamp_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        
                        processed_img.seek(0)
                        with open(filepath, 'wb') as f:
                            f.write(processed_img.read())
                        
                        stamp = unique_filename
                except Exception as e:
                    print(f"Error processing stamp: {e}")
                    # Fallback to original file if processing fails
                    if file.filename and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        file_ext = filename.rsplit('.', 1)[1].lower()
                        unique_filename = f"stamp_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(filepath)
                        stamp = unique_filename
                        
                        # Generate hash from saved file
                        with open(filepath, 'rb') as f:
                            stamp_hash = generate_signature_hash(f.read())
    
    # Save to database
    connection = get_db_connection()
    if not connection:
        flash('Database connection error. Please try again later.', 'error')
        return redirect(url_for('onboarding'))
    
    try:
        # Ensure onboarding columns exist (in case migration hasn't run)
        with connection.cursor() as cursor:
            onboarding_columns = [
                ('id_front', 'VARCHAR(255)'),
                ('id_back', 'VARCHAR(255)'),
                ('kra_pin_document', 'VARCHAR(255)'),
                ('signature', 'VARCHAR(255)'),
                ('signature_hash', 'VARCHAR(255)'),
                ('stamp', 'VARCHAR(255)'),
                ('stamp_hash', 'VARCHAR(255)'),
                ('payment_method', "ENUM('Bank', 'Mobile Money')"),
                ('bank_name', 'VARCHAR(255)'),
                ('mobile_money_company', 'VARCHAR(255)'),
            ]
            
            for col_name, col_def in onboarding_columns:
                if not column_exists('employees', col_name):
                    try:
                        cursor.execute(f"ALTER TABLE employees ADD COLUMN {col_name} {col_def}")
                        connection.commit()
                        print(f"Added missing column '{col_name}' during onboarding")
                    except Exception as e:
                        print(f"Could not add column '{col_name}': {e}")
        
        # Now perform the update
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE employees 
                SET account_number = %s,
                    account_name = %s,
                    tax_pin = %s,
                    payment_method = %s,
                    bank_name = %s,
                    mobile_money_company = %s,
                    employment_contract = %s,
                    id_front = %s,
                    id_back = %s,
                    kra_pin_document = %s,
                    signature = %s,
                    signature_hash = %s,
                    stamp = %s,
                    stamp_hash = %s,
                    nda_accepted = FALSE,
                    code_of_conduct_accepted = FALSE,
                    health_safety_accepted = FALSE,
                    onboarding_completed = TRUE
                WHERE id = %s
            """, (account_number, account_name, tax_pin, payment_method, 
                  bank_name if payment_method == 'Bank' else None,
                  mobile_money_company if payment_method == 'Mobile Money' else None,
                  employment_contract, id_front, id_back, kra_pin_document, signature, signature_hash, 
                  stamp, stamp_hash, employee_id))
            connection.commit()
            flash('Onboarding information submitted successfully!', 'success')
            return redirect(url_for('dashboard'))
    except Exception as e:
        print(f"Onboarding submission error: {e}")
        flash('An error occurred during submission. Please try again.', 'error')
        return redirect(url_for('onboarding'))
    finally:
        connection.close()

@app.route('/hr_roles_permissions')
def hr_roles_permissions():
    """HR Roles & Permissions page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        from collections import defaultdict
        
        # Get company settings
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        # Fetch all employees with their roles
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    id,
                    full_name,
                    phone_number,
                    work_email,
                    employee_code,
                    role,
                    status,
                    profile_picture
                FROM employees
                ORDER BY 
                    CASE 
                        WHEN role IS NULL OR role = '' THEN 1 
                        ELSE 0 
                    END,
                    role ASC,
                    full_name ASC
            """)
            employees = cursor.fetchall()
        
        # Group employees by role
        roles_map = defaultdict(list)
        for emp in employees:
            role_name = emp.get('role') or 'Unassigned'
            roles_map[role_name].append(emp)
        
        # Transform into sorted list of role blocks
        roles_with_employees = []
        for role_name, emps in roles_map.items():
            active_count = sum(1 for e in emps if (e.get('status') or '').lower() == 'active')
            roles_with_employees.append({
                'role_name': role_name,
                'employees': emps,
                'total_count': len(emps),
                'active_count': active_count,
            })
        
        # Sort: named roles alphabetically, keep "Unassigned" last
        roles_with_employees.sort(key=lambda r: (r['role_name'] == 'Unassigned', r['role_name'].lower()))
        
        return render_template(
            'hr_roles_permissions.html',
            company_settings=company_settings,
            roles_with_employees=roles_with_employees
        )
    except Exception as e:
        print(f"HR Roles & Permissions error: {e}")
        flash('An error occurred while loading HR roles & permissions.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

PERMISSION_KEYS = [
    # Employee management
    'employee_create',
    'employee_approve',
    'employee_edit',
    'employee_suspend',
    'employee_delete',
    # Client management
    'client_register',
    'client_approve',
    'client_edit',
    'client_suspend',
    'client_delete',
    # Matter management
    'matter_register_case',
    'matter_register_other',
    'matter_edit',
    'matter_change_status',
    'matter_allocate',
    'matter_documents',
    'matter_audit',
    # Finance & billing
    'finance_view_dashboard',
    'finance_create_invoices',
    'finance_record_payments',
    'finance_view_reports',
    # Calendar & reminders
    'calendar_personal',
    'calendar_shared',
    'calendar_case_reminders',
    # System & communication settings
    'system_manage_settings',
    'system_manage_document_settings',
    'system_manage_channels',
]


def get_employee_permissions_map(connection, employee_id):
    """Return dict(permission_key -> bool) for given employee_id."""
    permissions = {}
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                """
                SELECT permission_key, allowed
                FROM employee_permissions
                WHERE employee_id = %s
                """,
                (employee_id,),
            )
            rows = cursor.fetchall()
            for row in rows:
                permissions[row['permission_key']] = bool(row['allowed'])
    except Exception as e:
        print(f"Error loading employee permissions for {employee_id}: {e}")
    return permissions


def save_employee_permissions(connection, employee_id, form_data):
    """Persist permissions from form for a given employee."""
    try:
        # Ensure the backing table exists (in case init_database hasn't been run yet)
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS employee_permissions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL,
                        permission_key VARCHAR(100) NOT NULL,
                        allowed BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uniq_employee_permission (employee_id, permission_key),
                        CONSTRAINT fk_employee_permissions_employee
                            FOREIGN KEY (employee_id) REFERENCES employees(id)
                            ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                connection.commit()
        except Exception as e:
            print(f"Error ensuring employee_permissions table exists: {e}")

        with connection.cursor() as cursor:
            for key in PERMISSION_KEYS:
                field_name = f"perm_{key}"
                allowed = 1 if field_name in form_data else 0

                cursor.execute(
                    """
                    INSERT INTO employee_permissions (employee_id, permission_key, allowed)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE allowed = VALUES(allowed)
                    """,
                    (employee_id, key, allowed),
                )
        connection.commit()
    except Exception as e:
        print(f"Error saving employee permissions for {employee_id}: {e}")
        connection.rollback()


def current_user_has_permission(connection, permission_key):
    """Check if the logged-in employee has a given permission.

    Falls back to True when:
      - no employee_id in session, or
      - no explicit record exists for that permission_key.
    """
    employee_id = session.get('employee_id')
    if not employee_id:
        return True

    # Non-configured keys are treated as allowed by default
    if permission_key not in PERMISSION_KEYS:
        return True

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                """
                SELECT allowed
                FROM employee_permissions
                WHERE employee_id = %s AND permission_key = %s
                """,
                (employee_id, permission_key),
            )
            row = cursor.fetchone()
            if row is None:
                return True
            return bool(row['allowed'])
    except Exception as e:
        print(f"Permission check failed for employee {employee_id}, key {permission_key}: {e}")
        return True


def enforce_permission(connection, permission_key, redirect_endpoint='dashboard'):
    """Enforce a permission; returns a redirect response or None if allowed.
    When denied, redirects back to the same page (referrer) when safe, so the user
    stays in context and sees the permission popup there; otherwise uses redirect_endpoint.
    """
    if not current_user_has_permission(connection, permission_key):
        flash('You do not have permission to perform this action.', 'error')
        from urllib.parse import urlparse
        referrer = request.referrer
        if referrer:
            try:
                parsed = urlparse(referrer)
                if parsed.netloc == request.host and parsed.path.startswith('/'):
                    return redirect(referrer)
            except Exception:
                pass
        return redirect(url_for(redirect_endpoint))
    return None


@app.route('/employee_permissions', methods=['GET', 'POST'])
def employee_permissions():
    """Employee Permissions overview page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    employee_id = request.args.get('employee_id')
    if not employee_id:
        flash('Employee not specified.', 'error')
        return redirect(url_for('hr_roles_permissions'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                """
                SELECT 
                    id,
                    full_name,
                    phone_number,
                    work_email,
                    employee_code,
                    role,
                    status,
                    profile_picture
                FROM employees
                WHERE id = %s
                """,
                (employee_id,),
            )
            employee = cursor.fetchone()

        if not employee:
            flash('Employee not found.', 'error')
            return redirect(url_for('hr_roles_permissions'))

        # If form was submitted, save permissions
        if request.method == 'POST':
            save_employee_permissions(connection, employee['id'], request.form)
            flash('Permissions updated for this employee.', 'success')

        # Load permissions map (after any updates)
        permissions_map = get_employee_permissions_map(connection, employee['id'])

        return render_template(
            'employee_permissions.html',
            company_settings=company_settings,
            employee=employee,
            permissions=permissions_map,
        )
    except Exception as e:
        print(f"Employee permissions error: {e}")
        flash('An error occurred while loading employee permissions.', 'error')
        return redirect(url_for('hr_roles_permissions'))
    finally:
        connection.close()

@app.route('/leave_availability')
def leave_availability():
    """Leave & Availability page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('leave_availability.html', company_settings=company_settings)

@app.route('/performance_compliance')
def performance_compliance():
    """Performance & Compliance page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('performance_compliance.html', company_settings=company_settings)

@app.route('/training_certification')
def training_certification():
    """Training & Certification page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('training_certification.html', company_settings=company_settings)

@app.route('/payroll_expenses')
def payroll_expenses():
    """Payroll & Expenses page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('payroll_expenses.html', company_settings=company_settings)

@app.route('/audit_offboarding')
def audit_offboarding():
    """Audit & Offboarding page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('audit_offboarding.html', company_settings=company_settings)

@app.route('/finance_billing')
def finance_billing():
    """Finance & Billing page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    # Fine-grained permission: view finance dashboard
    connection = get_db_connection()
    if connection:
        deny = enforce_permission(connection, 'finance_view_dashboard')
        connection.close()
        if deny:
            return deny

    return render_template('finance_billing.html', company_settings=company_settings)

@app.route('/matter_management')
def matter_management():
    """Matter Management landing page with links to Case Management and Other Matters"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    return render_template('matter_management.html', company_settings=company_settings,
                           user_role=user_role, current_employee_id=session.get('employee_id'))

@app.route('/case_management')
def case_management():
    """Case Management page – shows only cases allocated to the current employee"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    # Get current employee info for the form
    connection = get_db_connection()
    employee_name = session.get('employee_name', 'Unknown')
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT full_name FROM employees WHERE id = %s", (session['employee_id'],))
                employee = cursor.fetchone()
                if employee:
                    employee_name = employee['full_name']
        except:
            pass
        finally:
            connection.close()
    
    return render_template('case_management.html', company_settings=company_settings, employee_name=employee_name, user_role=user_role)

@app.route('/case_management/tasks', methods=['GET', 'POST'])
def case_task_management():
    """Case task management page."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')

    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))

    current_employee_id = session.get('employee_id')
    selected_case_id = (request.args.get('case_id') or '').strip()
    edit_task_query = (request.args.get('edit_task') or '').strip()
    cases = []
    employees = []
    case_tasks = []
    editing_task = None
    reminder_options = [
        ('10m', '10 minutes before'),
        ('30m', '30 minutes before'),
        ('1h', '1 hour before'),
        ('6h', '6 hours before'),
        ('12h', '12 hours before'),
        ('1d', '1 day before'),
        ('2d', '2 days before'),
        ('7d', '1 week before'),
    ]

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)

            if user_role == 'IT Support' or original_role == 'IT Support':
                cursor.execute("""
                    SELECT id, tracking_number, client_name, filled_by_name, created_by_name, status
                    FROM cases
                    ORDER BY updated_at DESC
                """)
            else:
                cursor.execute("""
                    SELECT id, tracking_number, client_name, filled_by_name, created_by_name, status
                    FROM cases
                    WHERE filled_by_id = %s
                    ORDER BY updated_at DESC
                """, (current_employee_id,))
            cases = cursor.fetchall()
            case_ids = {str(c['id']) for c in cases}

            cursor.execute("""
                SELECT id, full_name, role
                FROM employees
                WHERE status = 'Active'
                ORDER BY full_name ASC
            """)
            employees = cursor.fetchall()
            employee_map = {str(e['id']): e for e in employees}

            if request.method == 'GET' and edit_task_query:
                try:
                    _edit_tid = int(edit_task_query)
                except (ValueError, TypeError):
                    _edit_tid = None
                if _edit_tid:
                    cursor.execute("""
                        SELECT t.id, t.linked_id, t.task_title, t.task_description, t.due_at,
                               t.reminder_intervals, t.assigned_to_id, t.assigned_to_name, t.task_status,
                               t.allow_view_case_details, t.allow_edit_case_details, t.allow_view_case_documents,
                               t.allow_upload_case_documents, t.allow_download_case_documents
                        FROM task_management t
                        WHERE t.id = %s AND t.task_type = 'case'
                    """, (_edit_tid,))
                    _et_row = cursor.fetchone()
                    if _et_row and str(_et_row['linked_id']) in case_ids:
                        editing_task = _et_row
                        selected_case_id = str(_et_row['linked_id'])
                        _da = editing_task.get('due_at')
                        if hasattr(_da, 'strftime'):
                            editing_task['due_at_input'] = _da.strftime('%Y-%m-%dT%H:%M')
                        else:
                            _ds = str(_da or '')
                            editing_task['due_at_input'] = (_ds[:16].replace(' ', 'T') if len(_ds) >= 16 else _ds)
                        _rim = (editing_task.get('reminder_intervals') or '').strip()
                        editing_task['reminder_set'] = {x.strip() for x in _rim.split(',') if x.strip()}
                    elif _et_row:
                        flash('You cannot edit this task.', 'error')

            if request.method == 'POST':
                linked_case_id = (request.form.get('linked_case_id') or '').strip()
                selected_case_id = linked_case_id or selected_case_id
                edit_task_id = (request.form.get('edit_task_id') or '').strip()
                assigned_employee_id = (request.form.get('assigned_employee_id') or '').strip()
                allow_view_case_details = 1 if request.form.get('allow_view_case_details') else 0
                allow_edit_case_details = 1 if request.form.get('allow_edit_case_details') else 0
                allow_view_case_documents = 1 if request.form.get('allow_view_case_documents') else 0
                allow_upload_case_documents = 1 if request.form.get('allow_upload_case_documents') else 0
                allow_download_case_documents = 1 if request.form.get('allow_download_case_documents') else 0
                task_title = (request.form.get('task_title') or '').strip()
                task_description = (request.form.get('task_description') or '').strip()
                due_at = (request.form.get('due_at') or '').strip()
                reminder_intervals = request.form.getlist('reminder_intervals')
                task_status_val = (request.form.get('task_status') or '').strip()

                errors = []
                if not linked_case_id:
                    errors.append('Please select a case.')
                elif linked_case_id not in case_ids:
                    errors.append('Selected case is not available for your account.')
                if not assigned_employee_id:
                    errors.append('Please select the employee to allocate this task to.')
                elif assigned_employee_id not in employee_map:
                    errors.append('Selected employee is not available.')
                if not task_title:
                    errors.append('Task title is required.')
                if not due_at:
                    errors.append('Task timeline is required.')
                if not reminder_intervals:
                    errors.append('Select at least one reminder interval.')
                allowed_status = {'Pending', 'In Progress', 'Submitted', 'Completed', 'Cancelled'}
                if edit_task_id:
                    if task_status_val not in allowed_status:
                        errors.append('Task status is required.')
                elif task_status_val and task_status_val not in allowed_status:
                    errors.append('Invalid task status.')

                if errors:
                    for err in errors:
                        flash(err, 'error')
                    if edit_task_id:
                        try:
                            _post_eid = int(edit_task_id)
                        except (ValueError, TypeError):
                            _post_eid = None
                        if _post_eid:
                            cursor.execute("""
                                SELECT t.id, t.linked_id, t.task_title, t.task_description, t.due_at,
                                       t.reminder_intervals, t.assigned_to_id, t.assigned_to_name, t.task_status,
                                       t.allow_view_case_details, t.allow_edit_case_details, t.allow_view_case_documents,
                                       t.allow_upload_case_documents, t.allow_download_case_documents
                                FROM task_management t
                                WHERE t.id = %s AND t.task_type = 'case'
                            """, (_post_eid,))
                            _et_retry = cursor.fetchone()
                            if _et_retry and str(_et_retry['linked_id']) in case_ids:
                                editing_task = dict(_et_retry)
                                selected_case_id = linked_case_id or str(_et_retry['linked_id'])
                                editing_task['task_title'] = task_title
                                editing_task['task_description'] = task_description
                                editing_task['due_at_input'] = due_at
                                editing_task['reminder_set'] = set(reminder_intervals)
                                try:
                                    editing_task['assigned_to_id'] = int(assigned_employee_id) if assigned_employee_id else _et_retry['assigned_to_id']
                                except (ValueError, TypeError):
                                    editing_task['assigned_to_id'] = _et_retry['assigned_to_id']
                                editing_task['allow_view_case_details'] = allow_view_case_details
                                editing_task['allow_edit_case_details'] = allow_edit_case_details
                                editing_task['allow_view_case_documents'] = allow_view_case_documents
                                editing_task['allow_upload_case_documents'] = allow_upload_case_documents
                                editing_task['allow_download_case_documents'] = allow_download_case_documents
                                editing_task['task_status'] = task_status_val if task_status_val in allowed_status else _et_retry.get('task_status')
                else:
                    if edit_task_id:
                        try:
                            _upd_tid = int(edit_task_id)
                        except (ValueError, TypeError):
                            _upd_tid = None
                        if not _upd_tid:
                            flash('Invalid task reference.', 'error')
                        else:
                            cursor.execute("""
                                SELECT id, linked_id, task_type
                                FROM task_management
                                WHERE id = %s AND task_type = 'case'
                            """, (_upd_tid,))
                            existing = cursor.fetchone()
                            if not existing:
                                flash('Task not found.', 'error')
                            elif str(existing['linked_id']) not in case_ids:
                                flash('You cannot edit this task.', 'error')
                            else:
                                cursor.execute("""
                                    UPDATE task_management
                                    SET linked_id = %s,
                                        task_title = %s,
                                        task_description = %s,
                                        due_at = %s,
                                        reminder_intervals = %s,
                                        assigned_to_id = %s,
                                        assigned_to_name = %s,
                                        allow_view_case_details = %s,
                                        allow_edit_case_details = %s,
                                        allow_view_case_documents = %s,
                                        allow_upload_case_documents = %s,
                                        allow_download_case_documents = %s,
                                        task_status = %s
                                    WHERE id = %s AND task_type = 'case'
                                """, (
                                    int(linked_case_id),
                                    task_title,
                                    task_description,
                                    due_at.replace('T', ' '),
                                    ','.join(reminder_intervals),
                                    int(assigned_employee_id),
                                    employee_map[assigned_employee_id]['full_name'],
                                    allow_view_case_details,
                                    allow_edit_case_details,
                                    allow_view_case_documents,
                                    allow_upload_case_documents,
                                    allow_download_case_documents,
                                    task_status_val,
                                    _upd_tid,
                                ))
                                connection.commit()
                                flash('Case task updated successfully.', 'success')
                                return redirect(url_for('case_task_management', case_id=linked_case_id))
                    else:
                        cursor.execute("""
                            INSERT INTO task_management
                            (task_type, linked_id, task_title, task_description, due_at, reminder_intervals, assigned_to_id, assigned_to_name, allow_view_case_details, allow_edit_case_details, allow_view_case_documents, allow_upload_case_documents, allow_download_case_documents, created_by_id, created_by_name)
                            VALUES ('case', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            int(linked_case_id),
                            task_title,
                            task_description,
                            due_at.replace('T', ' '),
                            ','.join(reminder_intervals),
                            int(assigned_employee_id),
                            employee_map[assigned_employee_id]['full_name'],
                            allow_view_case_details,
                            allow_edit_case_details,
                            allow_view_case_documents,
                            allow_upload_case_documents,
                            allow_download_case_documents,
                            current_employee_id,
                            session.get('employee_name') or 'Unknown'
                        ))
                        connection.commit()
                        flash('Case task created successfully.', 'success')
                        return redirect(url_for('case_task_management'))

            if case_ids:
                cursor.execute("""
                    SELECT
                        t.id,
                        t.task_title,
                        t.task_description,
                        t.due_at,
                        t.reminder_intervals,
                        t.task_status,
                        t.assigned_to_name,
                        t.allow_view_case_details,
                        t.allow_edit_case_details,
                        t.allow_view_case_documents,
                        t.allow_upload_case_documents,
                        t.allow_download_case_documents,
                        t.created_by_name,
                        c.id AS case_id,
                        c.tracking_number,
                        c.client_name
                    FROM task_management t
                    INNER JOIN cases c ON c.id = t.linked_id
                    WHERE t.task_type = 'case'
                    ORDER BY t.created_at DESC
                    LIMIT 100
                """)
                task_rows = cursor.fetchall()
                case_tasks = [r for r in task_rows if str(r.get('case_id')) in case_ids]
            else:
                case_tasks = []
    except Exception as e:
        print(f"Case task management error: {e}")
        flash('An error occurred while loading case tasks.', 'error')
    finally:
        connection.close()

    return render_template(
        'case_task_management.html',
        company_settings=company_settings,
        user_role=user_role,
        current_employee_id=current_employee_id,
        selected_case_id=selected_case_id,
        cases=cases,
        employees=employees,
        case_tasks=case_tasks,
        reminder_options=reminder_options,
        editing_task=editing_task,
        task_status_options=['Pending', 'In Progress', 'Submitted', 'Completed', 'Cancelled']
    )

@app.route('/case_management/case_tracking')
def case_tracking():
    """All court attendances (proceedings) across cases the user can access."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')

    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    current_employee_id = session['employee_id']
    is_mp = user_role == 'Managing Partner'

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))

    rows = []
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            mp_clause = ""
            params = ()
            if is_mp:
                mp_clause = " AND c.filled_by_id = %s AND c.status = 'Active' "
                params = (current_employee_id,)

            cursor.execute(
                """
                SELECT
                    p.id AS proceeding_id,
                    p.case_id,
                    c.tracking_number,
                    c.client_name,
                    cl.full_name AS client_full_name,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.outcome_orders,
                    p.outcome_details,
                    p.next_court_date,
                    p.attendance,
                    p.next_attendance,
                    p.virtual_link,
                    p.reason,
                    p.created_at,
                    p.updated_at
                FROM case_proceedings p
                INNER JOIN cases c ON c.id = p.case_id
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE 1=1
                  AND p.id = (
                      SELECT p2.id
                      FROM case_proceedings p2
                      WHERE p2.case_id = p.case_id
                      ORDER BY COALESCE(p2.updated_at, p2.created_at) DESC, p2.id DESC
                      LIMIT 1
                  )
                """
                + mp_clause
                + """
                ORDER BY
                    COALESCE(p.updated_at, p.created_at) DESC,
                    p.created_at DESC
                """,
                params,
            )
            rows = cursor.fetchall()

            for r in rows:
                if r.get('date_of_court_appeared'):
                    r['date_of_court_appeared'] = r['date_of_court_appeared'].strftime('%Y-%m-%d')
                if r.get('next_court_date'):
                    r['next_court_date'] = r['next_court_date'].strftime('%Y-%m-%d')
                if r.get('created_at'):
                    r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M')
    except Exception as e:
        print(f"Error loading case tracking: {e}")
        flash('An error occurred while loading court attendances.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

    return render_template(
        'case_tracking.html',
        company_settings=company_settings,
        user_role=user_role,
        attendances=rows,
        attendance_count=len(rows),
    )

@app.route('/matter_tracking')
def matter_tracking():
    """Matter tracking page - lists matters accessible to the current user."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')

    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))

    current_employee_id = session.get('employee_id')
    matters = []
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if user_role == 'IT Support' or original_role == 'IT Support':
                cursor.execute("""
                    SELECT
                        id,
                        matter_reference_number,
                        matter_title,
                        client_name,
                        assigned_employee_name,
                        status,
                        date_opened,
                        created_at,
                        updated_at
                    FROM matters
                    ORDER BY updated_at DESC, created_at DESC
                """)
            else:
                cursor.execute("""
                    SELECT
                        id,
                        matter_reference_number,
                        matter_title,
                        client_name,
                        assigned_employee_name,
                        status,
                        date_opened,
                        created_at,
                        updated_at
                    FROM matters
                    WHERE assigned_employee_id = %s
                    ORDER BY updated_at DESC, created_at DESC
                """, (current_employee_id,))
            matters = cursor.fetchall()

            for m in matters:
                if m.get('date_opened'):
                    try:
                        m['date_opened'] = m['date_opened'].strftime('%Y-%m-%d')
                    except Exception:
                        pass
                if m.get('created_at'):
                    try:
                        m['created_at'] = m['created_at'].strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        pass
                if m.get('updated_at'):
                    try:
                        m['updated_at'] = m['updated_at'].strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        pass
    except Exception as e:
        print(f"Error loading matter tracking: {e}")
        flash('An error occurred while loading matters.', 'error')
        matters = []
    finally:
        connection.close()

    return render_template(
        'matter_tracking.html',
        company_settings=company_settings,
        user_role=user_role,
        matters=matters,
        matter_count=len(matters),
    )

@app.route('/case_management/<int:case_id>')
def case_details(case_id):
    """Case Details page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            employee_id = session.get('employee_id')
            task_id = (request.args.get('task_id') or '').strip()
            is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
            # Fetch case details with client and employee information
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.client_name,
                    c.court_rank,
                    c.case_type,
                    c.filing_date,
                    c.case_category,
                    c.station,
                    c.filled_by_id,
                    c.filled_by_name,
                    c.created_by_id,
                    c.created_by_name,
                    c.description,
                    c.status,
                    c.created_at,
                    c.updated_at,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone,
                    cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type as client_type,
                    cl.status as client_status,
                    cl.google_id as client_google_id,
                    cl.created_at as client_created_at,
                    e_filled.id as filled_by_employee_id,
                    e_filled.full_name as filled_by_full_name,
                    e_filled.employee_code as filled_by_code,
                    e_filled.work_email as filled_by_email,
                    e_filled.role as filled_by_role,
                    e_created.id as created_by_employee_id,
                    e_created.full_name as created_by_full_name,
                    e_created.employee_code as created_by_code,
                    e_created.work_email as created_by_email,
                    e_created.role as created_by_role
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                LEFT JOIN employees e_filled ON c.filled_by_id = e_filled.id
                LEFT JOIN employees e_created ON c.created_by_id = e_created.id
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))

            is_case_owner = str(case_data.get('filled_by_id') or '') == str(employee_id)
            if not is_it_support and not is_case_owner:
                ensure_task_management_table(cursor, connection)
                if not has_active_case_task_access(cursor, case_id, employee_id, task_id or None, permission_key='view'):
                    flash('You can only access this case while your allocated task is active.', 'error')
                    return redirect(url_for('my_tasks'))
            
            # Fetch parties for this case
            cursor.execute("""
                SELECT 
                    id,
                    party_name,
                    party_type,
                    party_category,
                    firm_agent,
                    party_phone,
                    party_email,
                    created_at,
                    updated_at
                FROM case_parties
                WHERE case_id = %s
                ORDER BY id ASC
            """, (case_id,))
            parties = cursor.fetchall()
            
            # Convert date objects to strings
            if case_data.get('filing_date'):
                case_data['filing_date'] = case_data['filing_date'].strftime('%Y-%m-%d')
            if case_data.get('created_at'):
                case_data['created_at'] = case_data['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if case_data.get('updated_at'):
                case_data['updated_at'] = case_data['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            if case_data.get('client_created_at'):
                case_data['client_created_at'] = case_data['client_created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Convert party date objects to strings
            for party in parties:
                if party.get('created_at'):
                    party['created_at'] = party['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if party.get('updated_at'):
                    party['updated_at'] = party['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Fetch all proceedings for this case (including previous versions / full history)
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.outcome_orders,
                    p.outcome_details,
                    p.next_court_date,
                    p.attendance,
                    p.next_attendance,
                    p.virtual_link,
                    p.reason,
                    p.created_at
                FROM case_proceedings p
                WHERE p.case_id = %s
                ORDER BY 
                    CASE 
                        WHEN EXISTS (
                            SELECT 1 FROM case_proceedings p2 
                            WHERE p2.previous_proceeding_id = p.id
                        ) THEN 0 
                        ELSE 1 
                    END DESC,
                    p.date_of_court_appeared DESC,
                    p.created_at DESC
            """, (case_id,))
            all_proceedings = cursor.fetchall()
            
            # Format all proceedings (no filter - show full history including previous ones)
            proceedings = []
            for proc in all_proceedings:
                formatted_proc = dict(proc)
                if formatted_proc.get('date_of_court_appeared'):
                    formatted_proc['date_of_court_appeared'] = formatted_proc['date_of_court_appeared'].strftime('%Y-%m-%d')
                if formatted_proc.get('next_court_date'):
                    formatted_proc['next_court_date'] = formatted_proc['next_court_date'].strftime('%Y-%m-%d')
                if formatted_proc.get('created_at'):
                    formatted_proc['created_at'] = formatted_proc['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                proceedings.append(formatted_proc)
            
            # Fetch case documents from Google Drive (for display on case details page)
            google_drive_connected = False
            documents = []
            cursor.execute("""
                SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                       google_drive_scopes, google_drive_main_folder_id
                FROM company_settings 
                ORDER BY id DESC LIMIT 1
            """)
            drive_settings = cursor.fetchone()
            if drive_settings and drive_settings.get('google_drive_token') and drive_settings.get('google_drive_refresh_token'):
                google_drive_connected = True
                if 'google_drive_credentials' not in session:
                    scopes = json.loads(drive_settings['google_drive_scopes']) if drive_settings.get('google_drive_scopes') else []
                    session['google_drive_credentials'] = {
                        'token': drive_settings['google_drive_token'],
                        'refresh_token': drive_settings['google_drive_refresh_token'],
                        'token_uri': drive_settings.get('google_drive_token_uri'),
                        'client_id': GOOGLE_CLIENT_ID,
                        'client_secret': GOOGLE_CLIENT_SECRET,
                        'scopes': scopes
                    }
                if drive_settings.get('google_drive_main_folder_id'):
                    session['google_drive_main_folder_id'] = drive_settings['google_drive_main_folder_id']
            if google_drive_connected and case_data.get('client_table_id'):
                try:
                    service = get_google_drive_service()
                    if service:
                        main_folder_id = session.get('google_drive_main_folder_id') or (drive_settings and drive_settings.get('google_drive_main_folder_id'))
                        if main_folder_id:
                            client_folder_name = get_user_folder_name(
                                case_data.get('client_phone'),
                                case_data.get('client_full_name'),
                                'client'
                            )
                            client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                            case_folder_name = get_case_drive_folder_name(case_data, case_id)
                            case_doc_folder_id = get_or_create_folder(service, client_folder_id, case_folder_name)
                            if case_doc_folder_id:
                                query = f"'{case_doc_folder_id}' in parents and trashed=false"
                                all_files = []
                                page_token = None
                                while True:
                                    results = service.files().list(
                                        q=query,
                                        spaces='drive',
                                        fields='nextPageToken, files(id, name, description, createdTime, modifiedTime, webViewLink, size, mimeType, properties, owners(displayName,emailAddress), lastModifyingUser(displayName,emailAddress))',
                                        orderBy='modifiedTime desc',
                                        pageSize=100,
                                        pageToken=page_token
                                    ).execute()
                                    all_files.extend(results.get('files', []))
                                    page_token = results.get('nextPageToken')
                                    if not page_token:
                                        break
                                for file in all_files:
                                    if file.get('mimeType') == 'application/vnd.google-apps.folder':
                                        continue
                                    created_time = file.get('createdTime', '')
                                    modified_time = file.get('modifiedTime', '')
                                    if created_time:
                                        try:
                                            dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                                            created_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except Exception:
                                            pass
                                    if modified_time:
                                        try:
                                            dt = datetime.fromisoformat(modified_time.replace('Z', '+00:00'))
                                            modified_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except Exception:
                                            pass
                                    size = file.get('size', '0')
                                    try:
                                        size_int = int(size) if size else 0
                                        size_str = f"{size_int} B" if size_int < 1024 else (f"{size_int / 1024:.2f} KB" if size_int < 1024 * 1024 else f"{size_int / (1024 * 1024):.2f} MB")
                                    except Exception:
                                        size_str = "Unknown"

                                    # Uploaded/modified by info
                                    uploaded_by = None
                                    modified_by = None
                                    file_properties = file.get('properties') or {}
                                    uploaded_by = (
                                        file_properties.get('uploaded_by_name')
                                        or file_properties.get('created_by_name')
                                    )
                                    if not uploaded_by:
                                        owners = file.get('owners') or []
                                        if owners:
                                            owner = owners[0] or {}
                                            uploaded_by = owner.get('displayName') or owner.get('emailAddress')

                                    modifying_user = file.get('lastModifyingUser') or {}
                                    modified_by = (
                                        file_properties.get('updated_by_name')
                                        or modifying_user.get('displayName')
                                        or modifying_user.get('emailAddress')
                                    )
                                    if not modified_by:
                                        modified_by = uploaded_by

                                    documents.append({
                                        'id': file.get('id'),
                                        'name': file.get('name', 'Unknown'),
                                        'description': (file.get('description') or '').strip(),
                                        'created_time': created_time,
                                        'modified_time': modified_time,
                                        'url': file.get('webViewLink', ''),
                                        'size': size_str,
                                        'mime_type': file.get('mimeType', ''),
                                        'uploaded_by': uploaded_by or 'Unknown',
                                        'modified_by': modified_by or 'Unknown'
                                    })
                except Exception as e:
                    print(f"Error fetching documents for case details: {e}")
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            # Suggested doc title: your name + case tracking (no file created until user saves in app)
            emp_name = (session.get('employee_name') or '').strip()
            if not emp_name and session.get('employee_id'):
                cursor.execute("SELECT full_name FROM employees WHERE id = %s", (session['employee_id'],))
                emp_row = cursor.fetchone()
                emp_name = (emp_row.get('full_name') or '').strip() if emp_row else ''
            tracking = (case_data.get('tracking_number') or '').strip() or f'Case-{case_id}'
            suggested_doc_title = f"{emp_name or 'Document'} - {tracking}"
            return render_template('case_details.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 access_task_id=task_id,
                                 parties=parties,
                                 proceedings=proceedings,
                                 documents=documents,
                                 google_drive_connected=google_drive_connected,
                                 company_settings=company_settings,
                                 suggested_doc_title=suggested_doc_title)
    except Exception as e:
        print(f"Error fetching case details: {e}")
        flash('An error occurred while fetching case details.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/edit')
def case_edit(case_id):
    """Case Edit page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))

    # Fine-grained permission: edit matter / case details
    deny = enforce_permission(connection, 'matter_edit', redirect_endpoint='case_management')
    if deny:
        return deny
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            employee_id = session.get('employee_id')
            task_id = (request.args.get('task_id') or '').strip()
            is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
            # Fetch case details
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.client_name,
                    c.court_rank,
                    c.case_type,
                    c.filing_date,
                    c.case_category,
                    c.station,
                    c.filled_by_id,
                    c.filled_by_name,
                    c.description,
                    c.status,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone,
                    cl.email as client_email
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))

            is_case_owner = str(case_data.get('filled_by_id') or '') == str(employee_id)
            if not is_it_support and not is_case_owner:
                ensure_task_management_table(cursor, connection)
                if not has_active_case_task_access(cursor, case_id, employee_id, task_id or None, permission_key='edit'):
                    flash('This task does not allow editing case details or is no longer active.', 'error')
                    return redirect(url_for('my_tasks'))
            
            # Fetch parties for this case
            cursor.execute("""
                SELECT 
                    id,
                    party_name,
                    party_type,
                    party_category,
                    firm_agent,
                    party_phone,
                    party_email
                FROM case_parties
                WHERE case_id = %s
                ORDER BY id ASC
            """, (case_id,))
            parties = cursor.fetchall()
            
            # Convert date objects to strings
            if case_data.get('filing_date'):
                case_data['filing_date'] = case_data['filing_date'].strftime('%Y-%m-%d')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            employee_name = session.get('employee_name', 'Unknown')
            
            return render_template('case_edit.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 parties=parties,
                                 company_settings=company_settings,
                                 employee_name=employee_name,
                                 court_rank_options=COURT_RANK_OPTIONS)
    except Exception as e:
        print(f"Error fetching case for edit: {e}")
        flash('An error occurred while fetching case details.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/documents')
def case_documents(case_id):
    """Case Documents page - allows uploading documents for a specific case"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            employee_id = session.get('employee_id')
            task_id = (request.args.get('task_id') or '').strip()
            is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
            # Fetch case details with client information
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.filled_by_id,
                    c.client_name,
                    c.case_type,
                    c.status,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone,
                    cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))

            is_case_owner = str(case_data.get('filled_by_id') or '') == str(employee_id)
            if not is_it_support and not is_case_owner:
                ensure_task_management_table(cursor, connection)
                if not has_active_case_task_access(cursor, case_id, employee_id, task_id or None, permission_key='view_documents'):
                    flash('You can only access case documents while your allocated task is active.', 'error')
                    return redirect(url_for('my_tasks'))
            
            # Check if Google Drive is connected and load credentials
            google_drive_connected = False
            if 'google_drive_credentials' in session:
                google_drive_connected = True
            else:
                # Check database and load credentials
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                           google_drive_scopes
                    FROM company_settings 
                    ORDER BY id DESC LIMIT 1
                """)
                settings = cursor.fetchone()
                if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                    google_drive_connected = True
                    # Load credentials into session for document fetching
                    scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                    session['google_drive_credentials'] = {
                        'token': settings['google_drive_token'],
                        'refresh_token': settings['google_drive_refresh_token'],
                        'token_uri': settings.get('google_drive_token_uri'),
                        'client_id': GOOGLE_CLIENT_ID,
                        'client_secret': GOOGLE_CLIENT_SECRET,
                        'scopes': scopes
                    }
            
            # Fetch documents from Google Drive if connected
            documents = []
            if google_drive_connected:
                try:
                    service = get_google_drive_service()
                    
                    if service:
                        # Get main folder ID
                        main_folder_id = session.get('google_drive_main_folder_id')
                        if not main_folder_id:
                            cursor.execute("""
                                SELECT google_drive_main_folder_id
                                FROM company_settings 
                                ORDER BY id DESC LIMIT 1
                            """)
                            settings = cursor.fetchone()
                            if settings and settings.get('google_drive_main_folder_id'):
                                main_folder_id = settings['google_drive_main_folder_id']
                                session['google_drive_main_folder_id'] = main_folder_id
                        
                        if main_folder_id and case_data.get('client_table_id'):
                            # Get client folder
                            client_folder_name = get_user_folder_name(
                                case_data.get('client_phone'),
                                case_data.get('client_full_name'),
                                'client'
                            )
                            client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                            
                            case_folder_name = get_case_drive_folder_name(case_data, case_id)
                            case_doc_folder_id = get_or_create_folder(service, client_folder_id, case_folder_name)
                            
                            if case_doc_folder_id:
                                # List all files in the folder (paginate to get every uploaded document)
                                query = f"'{case_doc_folder_id}' in parents and trashed=false"
                                files = []
                                page_token = None
                                while True:
                                    results = service.files().list(
                                        q=query,
                                        spaces='drive',
                                        fields='nextPageToken, files(id, name, description, createdTime, modifiedTime, webViewLink, size, mimeType, properties, owners(displayName,emailAddress), lastModifyingUser(displayName,emailAddress))',
                                        orderBy='modifiedTime desc',
                                        pageSize=100,
                                        pageToken=page_token
                                    ).execute()
                                    files.extend(results.get('files', []))
                                    page_token = results.get('nextPageToken')
                                    if not page_token:
                                        break
                                for file in files:
                                    # Skip folders
                                    if file.get('mimeType') == 'application/vnd.google-apps.folder':
                                        continue
                                    
                                    # Format dates
                                    created_time = file.get('createdTime', '')
                                    modified_time = file.get('modifiedTime', '')
                                    if created_time:
                                        try:
                                            from datetime import datetime
                                            dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                                            created_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except:
                                            pass
                                    if modified_time:
                                        try:
                                            from datetime import datetime
                                            dt = datetime.fromisoformat(modified_time.replace('Z', '+00:00'))
                                            modified_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except:
                                            pass
                                    
                                    # Format file size
                                    size = file.get('size', '0')
                                    if size:
                                        try:
                                            size_int = int(size)
                                            if size_int < 1024:
                                                size_str = f"{size_int} B"
                                            elif size_int < 1024 * 1024:
                                                size_str = f"{size_int / 1024:.2f} KB"
                                            else:
                                                size_str = f"{size_int / (1024 * 1024):.2f} MB"
                                        except:
                                            size_str = "Unknown"
                                    else:
                                        size_str = "Unknown"

                                    # Uploaded/modified by info
                                    uploaded_by = None
                                    modified_by = None
                                    file_properties = file.get('properties') or {}

                                    # Prefer explicit uploader metadata we set during upload/create.
                                    uploaded_by = (
                                        file_properties.get('uploaded_by_name')
                                        or file_properties.get('created_by_name')
                                    )
                                    if not uploaded_by:
                                        owners = file.get('owners') or []
                                        if owners:
                                            owner = owners[0] or {}
                                            uploaded_by = owner.get('displayName') or owner.get('emailAddress')

                                    modifying_user = file.get('lastModifyingUser') or {}
                                    modified_by = (
                                        file_properties.get('updated_by_name')
                                        or modifying_user.get('displayName')
                                        or modifying_user.get('emailAddress')
                                    )
                                    if not modified_by:
                                        modified_by = uploaded_by
                                    
                                    documents.append({
                                        'id': file.get('id'),
                                        'name': file.get('name', 'Unknown'),
                                        'description': (file.get('description') or '').strip(),
                                        'created_time': created_time,
                                        'modified_time': modified_time,
                                        'url': file.get('webViewLink', ''),
                                        'size': size_str,
                                        'mime_type': file.get('mimeType', ''),
                                        'uploaded_by': uploaded_by or 'Unknown',
                                        'modified_by': modified_by or 'Unknown'
                                    })
                except Exception as e:
                    print(f"Error fetching documents from Google Drive: {e}")
                    import traceback
                    traceback.print_exc()
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_documents.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 access_task_id=task_id,
                                 google_drive_connected=google_drive_connected,
                                 documents=documents,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case documents page: {e}")
        flash('An error occurred while fetching case information.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/proceedings')
def case_proceedings(case_id):
    """Case Proceedings page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Verify case exists
            cursor.execute("SELECT id, tracking_number, client_name FROM cases WHERE id = %s", (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))
            
            # Fetch all proceedings for this case (including all versions/history)
            cursor.execute("""
                SELECT 
                    id,
                    previous_proceeding_id,
                    court_activity_type,
                    court_room,
                    judicial_officer,
                    date_of_court_appeared,
                    outcome_orders,
                    outcome_details,
                    next_court_date,
                    attendance,
                    next_attendance,
                    virtual_link,
                    reason,
                    created_at,
                    updated_at,
                    CASE 
                        WHEN EXISTS (
                            SELECT 1 FROM case_proceedings p2 
                            WHERE p2.previous_proceeding_id = p.id
                        ) THEN 0
                        ELSE 1
                    END as is_latest
                FROM case_proceedings p
                WHERE p.case_id = %s
                ORDER BY 
                    CASE 
                        WHEN EXISTS (
                            SELECT 1 FROM case_proceedings p2 
                            WHERE p2.previous_proceeding_id = p.id
                        ) THEN 0 
                        ELSE 1 
                    END DESC,
                    p.date_of_court_appeared DESC,
                    p.created_at DESC
            """, (case_id,))
            all_proceedings = cursor.fetchall()
            
            # Separate latest and historical proceedings
            latest_proceedings = [p for p in all_proceedings if p.get('is_latest', 1) == 1]
            historical_proceedings = [p for p in all_proceedings if p.get('is_latest', 1) == 0]
            
            # Build history chains - group historical by previous_proceeding_id
            history_map = {}
            for proc in historical_proceedings:
                prev_id = proc.get('previous_proceeding_id')
                if prev_id:
                    if prev_id not in history_map:
                        history_map[prev_id] = []
                    history_map[prev_id].append(proc)
            
            # Attach history to latest proceedings and sort history by created_at
            for proc in latest_proceedings:
                proc_id = proc['id']
                proc['history'] = []
                if proc_id in history_map:
                    # Get all versions in chronological order (oldest first)
                    proc['history'] = sorted(history_map[proc_id], key=lambda x: x.get('created_at', ''))
            
            # Use all proceedings for display (both latest and historical)
            proceedings = all_proceedings
            
            ensure_case_proceeding_advocates_table(cursor, connection)
            
            # Fetch materials for each proceeding (both latest and historical)
            for proceeding in all_proceedings:
                cursor.execute("""
                    SELECT 
                        id,
                        material_description,
                        reminder_frequency,
                        allocated_to_id,
                        allocated_to_name,
                        created_at,
                        updated_at
                    FROM case_proceeding_materials
                    WHERE proceeding_id = %s
                    ORDER BY created_at ASC
                """, (proceeding['id'],))
                proceeding['materials'] = cursor.fetchall()
                
                cursor.execute("""
                    SELECT id, advocate_name, remarks, created_at, updated_at
                    FROM case_proceeding_advocates
                    WHERE proceeding_id = %s
                    ORDER BY id ASC
                """, (proceeding['id'],))
                proceeding['advocates'] = cursor.fetchall()
                
                # Convert date objects to strings
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    proceeding['next_court_date'] = proceeding['next_court_date'].strftime('%Y-%m-%d')
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if proceeding.get('updated_at'):
                    proceeding['updated_at'] = proceeding['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Convert material dates
                for material in proceeding['materials']:
                    if material.get('created_at'):
                        material['created_at'] = material['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if material.get('updated_at'):
                        material['updated_at'] = material['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                for adv in proceeding.get('advocates') or []:
                    if adv.get('created_at'):
                        adv['created_at'] = adv['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if adv.get('updated_at'):
                        adv['updated_at'] = adv['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_proceedings.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 proceedings=proceedings,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case proceedings: {e}")
        flash('An error occurred while fetching case proceedings.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/reminders')
def case_reminders(case_id):
    """Case Reminders page - displays all materials/reminders for the case"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Verify case exists and get case info
            cursor.execute("SELECT id, tracking_number, client_name FROM cases WHERE id = %s", (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))
            
            # Fetch upcoming court dates (proceedings with next_court_date in the future or today)
            from datetime import datetime, date
            today = date.today()
            
            cursor.execute("""
                SELECT 
                    id,
                    court_activity_type,
                    court_room,
                    judicial_officer,
                    date_of_court_appeared,
                    next_court_date,
                    next_attendance,
                    virtual_link,
                    outcome_orders,
                    created_at
                FROM case_proceedings
                WHERE case_id = %s AND next_court_date IS NOT NULL AND next_court_date >= %s
                ORDER BY next_court_date ASC
            """, (case_id, today))
            upcoming_proceedings = cursor.fetchall()
            
            # Convert dates to strings for upcoming proceedings and calculate days until
            # Also fetch materials for each proceeding and attach them
            for proceeding in upcoming_proceedings:
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    # Calculate days until court date
                    days_until = (next_date - today).days
                    proceeding['days_until'] = days_until
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Fetch materials for this specific proceeding
                cursor.execute("""
                    SELECT 
                        m.id,
                        m.proceeding_id,
                        m.material_description,
                        m.reminder_frequency,
                        m.allocated_to_id,
                        m.allocated_to_name,
                        m.created_at,
                        m.updated_at
                    FROM case_proceeding_materials m
                    WHERE m.proceeding_id = %s
                    ORDER BY m.created_at ASC
                """, (proceeding['id'],))
                materials = cursor.fetchall()
                
                # Convert dates to strings for materials
                for material in materials:
                    if material.get('created_at'):
                        material['created_at'] = material['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if material.get('updated_at'):
                        material['updated_at'] = material['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Attach materials to this proceeding
                proceeding['materials'] = materials
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_reminders.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 upcoming_proceedings=upcoming_proceedings,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case reminders: {e}")
        flash('An error occurred while fetching case reminders.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/calendar')
def case_calendar(case_id):
    """Case Calendar page - displays next court dates"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Verify case exists and get case info
            cursor.execute("SELECT id, tracking_number, client_name FROM cases WHERE id = %s", (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))
            
            # Fetch all proceedings with next court dates (upcoming)
            cursor.execute("""
                SELECT 
                    id,
                    court_activity_type,
                    court_room,
                    judicial_officer,
                    date_of_court_appeared,
                    next_court_date,
                    attendance,
                    next_attendance,
                    virtual_link,
                    outcome_orders,
                    created_at
                FROM case_proceedings
                WHERE case_id = %s AND next_court_date IS NOT NULL
                ORDER BY next_court_date ASC
            """, (case_id,))
            upcoming_proceedings = cursor.fetchall()
            
            # Fetch all proceedings (for all court appearance dates)
            cursor.execute("""
                SELECT 
                    id,
                    court_activity_type,
                    court_room,
                    judicial_officer,
                    date_of_court_appeared,
                    next_court_date,
                    attendance,
                    next_attendance,
                    virtual_link,
                    outcome_orders,
                    created_at
                FROM case_proceedings
                WHERE case_id = %s
                ORDER BY date_of_court_appeared DESC
            """, (case_id,))
            all_proceedings = cursor.fetchall()
            
            # Convert date objects to strings and organize by date for calendar
            calendar_events = {}
            appearance_events = {}
            
            # Organize next court dates
            for proceeding in upcoming_proceedings:
                if proceeding.get('next_court_date'):
                    date_str = proceeding['next_court_date'].strftime('%Y-%m-%d')
                    if date_str not in calendar_events:
                        calendar_events[date_str] = []
                    calendar_events[date_str].append({'type': 'next', 'proceeding': proceeding})
                    
                    # Convert dates to strings for display
                    proceeding['next_court_date'] = date_str
                    if proceeding.get('date_of_court_appeared'):
                        proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                    if proceeding.get('created_at'):
                        proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Organize court appearance dates
            for proceeding in all_proceedings:
                if proceeding.get('date_of_court_appeared'):
                    date_str = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                    if date_str not in appearance_events:
                        appearance_events[date_str] = []
                    appearance_events[date_str].append(proceeding)
                    
                    # Also add to calendar_events if not already there
                    if date_str not in calendar_events:
                        calendar_events[date_str] = []
                    calendar_events[date_str].append({'type': 'appeared', 'proceeding': proceeding})
                    
                    # Convert dates to strings for display
                    proceeding['date_of_court_appeared'] = date_str
                    if proceeding.get('next_court_date'):
                        proceeding['next_court_date'] = proceeding['next_court_date'].strftime('%Y-%m-%d')
                    if proceeding.get('created_at'):
                        proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_calendar.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 calendar_events=calendar_events,
                                 appearance_events=appearance_events,
                                 upcoming_proceedings=upcoming_proceedings,
                                 all_proceedings=all_proceedings,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case calendar: {e}")
        flash('An error occurred while fetching case calendar.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/status')
def case_status(case_id):
    """Case Status Change page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch case details
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.status
                FROM cases c
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_status.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case status page: {e}")
        flash('An error occurred while fetching case details.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/allocate')
def case_allocate(case_id):
    """Case Allocation Change page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch case details (include allocation_description, allocation_timeline if columns exist)
            cursor.execute("SELECT id, tracking_number, filled_by_id, filled_by_name FROM cases WHERE id = %s", (case_id,))
            case_data = cursor.fetchone()
            if case_data and column_exists('cases', 'allocation_description'):
                cursor.execute("SELECT allocation_description, allocation_timeline FROM cases WHERE id = %s", (case_id,))
                extra = cursor.fetchone()
                if extra:
                    case_data['allocation_description'] = extra.get('allocation_description') or ''
                    case_data['allocation_timeline'] = extra.get('allocation_timeline') or ''
            if case_data and 'allocation_description' not in case_data:
                case_data['allocation_description'] = ''
                case_data['allocation_timeline'] = ''
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))
            
            # Case handlers: employees whose role is Managing Partner or Associate Advocate (active)
            cursor.execute("""
                SELECT id, full_name, employee_code, role
                FROM employees
                WHERE status = 'Active' AND role IN ('Managing Partner', 'Associate Advocate')
                ORDER BY role ASC, full_name ASC
            """)
            case_handlers = cursor.fetchall() or []
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_allocate.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 case_handlers=case_handlers,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case allocate page: {e}")
        flash('An error occurred while fetching case details.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/case_management/<int:case_id>/audit')
def case_audit_progress(case_id):
    """Case Audit Progress page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch case details
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_name,
                    c.filled_by_name,
                    c.created_by_name,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM cases c
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            
            if not case_data:
                flash('Case not found', 'error')
                return redirect(url_for('case_management'))
            
            # Build audit trail from case creation, updates, and status changes
            audit_items = []
            
            # Case creation
            if case_data.get('created_at'):
                created_at = case_data['created_at']
                if hasattr(created_at, 'strftime'):
                    created_at_str = created_at.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    created_at_str = str(created_at)
                
                audit_items.append({
                    'title': 'Case Created',
                    'description': f'Case "{case_data.get("tracking_number", "N/A")}" was created',
                    'timestamp': created_at_str,
                    'user': case_data.get('created_by_name', 'Unknown'),
                    'color': 'bg-blue-500',
                    'icon': 'fa-plus-circle'
                })
            
            # Case updates
            if case_data.get('updated_at') and case_data.get('created_at'):
                updated_at = case_data['updated_at']
                created_at = case_data['created_at']
                if hasattr(updated_at, 'strftime') and hasattr(created_at, 'strftime'):
                    if updated_at != created_at:
                        updated_at_str = updated_at.strftime('%Y-%m-%d %H:%M:%S')
                        audit_items.append({
                            'title': 'Case Updated',
                            'description': f'Case details were updated',
                            'timestamp': updated_at_str,
                            'user': 'System',
                            'color': 'bg-yellow-500',
                            'icon': 'fa-edit'
                        })
            
            # Sort by timestamp descending
            audit_items.sort(key=lambda x: x['timestamp'], reverse=True)
            
            # Convert date objects to strings
            if case_data.get('created_at'):
                case_data['created_at'] = case_data['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(case_data['created_at'], 'strftime') else str(case_data['created_at'])
            if case_data.get('updated_at'):
                case_data['updated_at'] = case_data['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(case_data['updated_at'], 'strftime') else str(case_data['updated_at'])
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_audit_progress.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 audit_items=audit_items,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching case audit: {e}")
        flash('An error occurred while fetching case audit.', 'error')
        return redirect(url_for('case_management'))
    finally:
        connection.close()

@app.route('/api/proceedings/court-activity-types/search', methods=['GET'])
def api_court_activity_types_search():
    """API endpoint to search court activity types from existing proceedings"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT DISTINCT court_activity_type
                    FROM case_proceedings 
                    WHERE court_activity_type LIKE %s AND court_activity_type IS NOT NULL AND court_activity_type != ''
                    ORDER BY court_activity_type ASC
                    LIMIT 10
                """, (f'%{query}%',))
            else:
                cursor.execute("""
                    SELECT DISTINCT court_activity_type
                    FROM case_proceedings 
                    WHERE court_activity_type IS NOT NULL AND court_activity_type != ''
                    ORDER BY court_activity_type ASC
                    LIMIT 50
                """)
            
            results = cursor.fetchall()
            types = [row['court_activity_type'] for row in results if row['court_activity_type']]
            return jsonify({'types': types})
    except Exception as e:
        print(f"Error searching court activity types: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/proceedings/court-rooms/search', methods=['GET'])
def api_court_rooms_search():
    """API endpoint to search court rooms from existing proceedings"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT DISTINCT court_room
                    FROM case_proceedings 
                    WHERE court_room LIKE %s AND court_room IS NOT NULL AND court_room != ''
                    ORDER BY court_room ASC
                    LIMIT 10
                """, (f'%{query}%',))
            else:
                cursor.execute("""
                    SELECT DISTINCT court_room
                    FROM case_proceedings 
                    WHERE court_room IS NOT NULL AND court_room != ''
                    ORDER BY court_room ASC
                    LIMIT 50
                """)
            
            results = cursor.fetchall()
            rooms = [row['court_room'] for row in results if row['court_room']]
            return jsonify({'rooms': rooms})
    except Exception as e:
        print(f"Error searching court rooms: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/proceedings/judicial-officers/search', methods=['GET'])
def api_judicial_officers_search():
    """API endpoint to search judicial officers from existing proceedings"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT DISTINCT judicial_officer
                    FROM case_proceedings 
                    WHERE judicial_officer LIKE %s AND judicial_officer IS NOT NULL AND judicial_officer != ''
                    ORDER BY judicial_officer ASC
                    LIMIT 10
                """, (f'%{query}%',))
            else:
                cursor.execute("""
                    SELECT DISTINCT judicial_officer
                    FROM case_proceedings 
                    WHERE judicial_officer IS NOT NULL AND judicial_officer != ''
                    ORDER BY judicial_officer ASC
                    LIMIT 50
                """)
            
            results = cursor.fetchall()
            officers = [row['judicial_officer'] for row in results if row['judicial_officer']]
            return jsonify({'officers': officers})
    except Exception as e:
        print(f"Error searching judicial officers: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/proceedings/outcomes/search', methods=['GET'])
def api_outcomes_search():
    """API endpoint to search case outcomes from existing proceedings"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT DISTINCT outcome_orders
                    FROM case_proceedings 
                    WHERE outcome_orders LIKE %s AND outcome_orders IS NOT NULL AND outcome_orders != ''
                    ORDER BY outcome_orders ASC
                    LIMIT 10
                """, (f'%{query}%',))
            else:
                cursor.execute("""
                    SELECT DISTINCT outcome_orders
                    FROM case_proceedings 
                    WHERE outcome_orders IS NOT NULL AND outcome_orders != ''
                    ORDER BY outcome_orders ASC
                    LIMIT 50
                """)
            
            results = cursor.fetchall()
            outcomes = [row['outcome_orders'] for row in results if row['outcome_orders']]
            return jsonify({'outcomes': outcomes})
    except Exception as e:
        print(f"Error searching outcomes: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/cases/proceedings/add', methods=['POST'])
def api_add_proceeding():
    """Add a new case proceeding row. Always inserts (never overwrites).
    Optional JSON `previous_proceeding_id` links the new row to the prior tip of the chain for audit history.
    """
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        data = {}
    
    # Normalize advocates list (must be a list for save)
    _adv = data.get('advocates')
    if _adv is None:
        data['advocates'] = []
    elif not isinstance(_adv, list):
        data['advocates'] = []
    
    # Validate required fields
    if not data.get('case_id'):
        return jsonify({'error': 'Case ID is required'}), 400
    if not data.get('date_of_court_appeared'):
        return jsonify({'error': 'Date of Court Appeared is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Verify case exists
            cursor.execute("SELECT id FROM cases WHERE id = %s", (data['case_id'],))
            if not cursor.fetchone():
                return jsonify({'error': 'Case not found'}), 404
            
            # Optional chain: new row links to previous leaf for audit trail (never overwrite in place)
            prev_raw = data.get('previous_proceeding_id')
            prev_id = None
            if prev_raw is not None and prev_raw != '':
                try:
                    prev_id = int(prev_raw)
                except (TypeError, ValueError):
                    prev_id = None
            if prev_id:
                cursor.execute(
                    "SELECT id, case_id FROM case_proceedings WHERE id = %s",
                    (prev_id,),
                )
                prow = cursor.fetchone()
                if not prow or int(prow[1]) != int(data['case_id']):
                    return jsonify({'error': 'Invalid previous proceeding for this case'}), 400
                # Only append to the current tip of the chain (no child yet)
                cursor.execute(
                    """
                    SELECT 1 FROM case_proceedings
                    WHERE previous_proceeding_id = %s LIMIT 1
                    """,
                    (prev_id,),
                )
                if cursor.fetchone():
                    return jsonify({
                        'error': 'That attendance already has a newer record. Refresh the page and try again.',
                    }), 400
            
            # Insert proceeding (always a new row)
            cursor.execute("""
                INSERT INTO case_proceedings (
                    case_id, previous_proceeding_id, court_activity_type, court_room, judicial_officer,
                    date_of_court_appeared, outcome_orders, outcome_details, next_court_date, attendance, next_attendance, virtual_link, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data['case_id'],
                prev_id,
                data.get('court_activity_type') if data.get('court_activity_type') else None,
                data.get('court_room') if data.get('court_room') else None,
                data.get('judicial_officer') if data.get('judicial_officer') else None,
                data['date_of_court_appeared'],
                data.get('outcome_orders') if data.get('outcome_orders') else None,
                data.get('outcome_details') if data.get('outcome_details') else None,
                data.get('next_court_date') if data.get('next_court_date') else None,
                data.get('attendance') if data.get('attendance') else None,
                data.get('next_attendance') if data.get('next_attendance') else None,
                data.get('virtual_link') if data.get('virtual_link') else None,
                data.get('reason') if data.get('reason') else None
            ))
            connection.commit()
            proceeding_id = cursor.lastrowid
            
            ensure_case_proceeding_advocates_table(cursor, connection)
            
            # Insert materials if provided
            materials_added = 0
            if data.get('materials') and isinstance(data['materials'], list):
                for material in data['materials']:
                    if material.get('material_description'):
                        cursor.execute("""
                            INSERT INTO case_proceeding_materials (
                                proceeding_id, material_description, reminder_frequency,
                                allocated_to_id, allocated_to_name
                            ) VALUES (%s, %s, %s, %s, %s)
                        """, (
                            proceeding_id,
                            material['material_description'],
                            material.get('reminder_frequency') if material.get('reminder_frequency') else None,
                            material.get('allocated_to_id') if material.get('allocated_to_id') else None,
                            material.get('allocated_to_name') if material.get('allocated_to_name') else None
                        ))
                        materials_added += 1
                connection.commit()
            
            advocates_added = 0
            for adv in data.get('advocates') or []:
                if not isinstance(adv, dict):
                    continue
                aname = (adv.get('advocate_name') or adv.get('advocateName') or '').strip()
                remarks = (adv.get('remarks') or adv.get('what_they_said') or '').strip()
                if aname:
                    cursor.execute("""
                        INSERT INTO case_proceeding_advocates (
                            proceeding_id, advocate_name, remarks
                        ) VALUES (%s, %s, %s)
                    """, (proceeding_id, aname, remarks or None))
                    advocates_added += 1
            connection.commit()
            
            message = 'New court attendance saved (previous records kept for reference)'
            if materials_added > 0:
                message += f' with {materials_added} material(s)'
            if advocates_added > 0:
                message += f' with {advocates_added} advocate(s)'
            
            return jsonify({
                'success': True,
                'message': message,
                'proceeding_id': proceeding_id
            })
    except Exception as e:
        print(f"Error adding proceeding: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/api/cases/proceedings/update/<int:proceeding_id>', methods=['PUT'])
def api_update_proceeding(proceeding_id):
    """API endpoint to update an existing case proceeding in place (no new record created)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        data = {}
    _adv = data.get('advocates')
    if _adv is None:
        data['advocates'] = []
    elif not isinstance(_adv, list):
        data['advocates'] = []
    
    # Validate required fields
    if not data.get('date_of_court_appeared'):
        return jsonify({'error': 'Date of Court Appeared is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Verify proceeding exists
            cursor.execute("SELECT id FROM case_proceedings WHERE id = %s", (proceeding_id,))
            if not cursor.fetchone():
                return jsonify({'error': 'Proceeding not found'}), 404
            
            # Update the existing proceeding in place
            cursor.execute("""
                UPDATE case_proceedings SET
                    court_activity_type = %s,
                    court_room = %s,
                    judicial_officer = %s,
                    date_of_court_appeared = %s,
                    outcome_orders = %s,
                    outcome_details = %s,
                    next_court_date = %s,
                    attendance = %s,
                    next_attendance = %s,
                    virtual_link = %s,
                    reason = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                data.get('court_activity_type') if data.get('court_activity_type') else None,
                data.get('court_room') if data.get('court_room') else None,
                data.get('judicial_officer') if data.get('judicial_officer') else None,
                data['date_of_court_appeared'],
                data.get('outcome_orders') if data.get('outcome_orders') else None,
                data.get('outcome_details') if data.get('outcome_details') else None,
                data.get('next_court_date') if data.get('next_court_date') else None,
                data.get('attendance') if data.get('attendance') else None,
                data.get('next_attendance') if data.get('next_attendance') else None,
                data.get('virtual_link') if data.get('virtual_link') else None,
                data.get('reason') if data.get('reason') else None,
                proceeding_id
            ))
            connection.commit()
            
            ensure_case_proceeding_advocates_table(cursor, connection)
            
            # Replace advocates: delete existing and insert from form
            cursor.execute("DELETE FROM case_proceeding_advocates WHERE proceeding_id = %s", (proceeding_id,))
            for adv in data.get('advocates') or []:
                if not isinstance(adv, dict):
                    continue
                aname = (adv.get('advocate_name') or adv.get('advocateName') or '').strip()
                remarks = (adv.get('remarks') or adv.get('what_they_said') or '').strip()
                if aname:
                    cursor.execute("""
                        INSERT INTO case_proceeding_advocates (
                            proceeding_id, advocate_name, remarks
                        ) VALUES (%s, %s, %s)
                    """, (proceeding_id, aname, remarks or None))
            connection.commit()
            
            # Replace materials: delete existing and insert from form
            cursor.execute("DELETE FROM case_proceeding_materials WHERE proceeding_id = %s", (proceeding_id,))
            if data.get('materials') and isinstance(data['materials'], list):
                for material in data['materials']:
                    if material.get('material_description'):
                        cursor.execute("""
                            INSERT INTO case_proceeding_materials (
                                proceeding_id, material_description, reminder_frequency,
                                allocated_to_id, allocated_to_name
                            ) VALUES (%s, %s, %s, %s, %s)
                        """, (
                            proceeding_id,
                            material['material_description'],
                            material.get('reminder_frequency') if material.get('reminder_frequency') else None,
                            material.get('allocated_to_id') if material.get('allocated_to_id') else None,
                            material.get('allocated_to_name') if material.get('allocated_to_name') else None
                        ))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Proceeding updated successfully',
                'proceeding_id': proceeding_id
            })
    except Exception as e:
        print(f"Error updating proceeding: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/api/cases/proceedings/delete/<int:proceeding_id>', methods=['DELETE'])
def api_delete_proceeding(proceeding_id):
    """API endpoint to delete a case proceeding"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Verify proceeding exists
            cursor.execute("SELECT id FROM case_proceedings WHERE id = %s", (proceeding_id,))
            if not cursor.fetchone():
                return jsonify({'error': 'Proceeding not found'}), 404
            
            # Delete proceeding
            cursor.execute("DELETE FROM case_proceedings WHERE id = %s", (proceeding_id,))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Proceeding deleted successfully'
            })
    except Exception as e:
        print(f"Error deleting proceeding: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/api/cases/search', methods=['GET'])
def api_cases_search():
    """Search cases by client name and/or phone (?q=). Empty q lists all (role-filtered). Managing Partners see only their active allocated cases."""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # Search by client name and/or phone (q= preferred; phone= kept for compatibility)
    search_term = (request.args.get('q') or request.args.get('phone') or '').strip()
    current_employee_id = session['employee_id']
    user_role = session.get('employee_role')
    is_mp = (user_role == 'Managing Partner')

    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            client = None
            cols = "c.id, c.tracking_number, c.court_case_number, c.client_id, c.client_name, c.court_rank, c.case_type, c.filing_date, c.case_category, c.station, c.filled_by_name, c.filled_by_id, c.created_by_name, c.description, c.status, c.created_at, c.updated_at"
            if column_exists('cases', 'allocation_description'):
                cols += ", c.allocation_description, c.allocation_timeline"

            # Managing Partner restriction clause
            mp_clause = "AND c.filled_by_id = %s AND c.status = 'Active'" if is_mp else ""
            mp_params_extra = (current_employee_id,) if is_mp else ()

            if search_term:
                cursor.execute("""
                    SELECT id, google_id, full_name, phone_number, email,
                           profile_picture, client_type, status, created_at, updated_at
                    FROM clients
                    WHERE status = 'Active'
                      AND (phone_number LIKE %s OR full_name LIKE %s)
                    ORDER BY full_name ASC
                """, (f'%{search_term}%', f'%{search_term}%'))
                matching_clients = cursor.fetchall()

                if not matching_clients:
                    return jsonify({
                        'cases': [],
                        'client': None,
                        'message': 'No client found matching that name or phone number',
                    })

                if len(matching_clients) == 1:
                    client = matching_clients[0]
                else:
                    client = None

                client_ids = [c['id'] for c in matching_clients]
                placeholders = ','.join(['%s'] * len(client_ids))
                params = tuple(client_ids)
                if is_mp:
                    params = params + (current_employee_id,)

                cursor.execute("""
                    SELECT """ + cols + """,
                        cl.id as client_table_id,
                        cl.full_name as client_full_name,
                        cl.phone_number as client_phone,
                        cl.email as client_email,
                        cl.profile_picture as client_profile_picture,
                        cl.client_type as client_type,
                        cl.status as client_status,
                        cl.created_at as client_created_at
                    FROM cases c
                    LEFT JOIN clients cl ON c.client_id = cl.id
                    WHERE c.client_id IN (""" + placeholders + """) """ + mp_clause + """
                    ORDER BY CASE WHEN c.status = 'Pending Approval' THEN 0 ELSE 1 END ASC,
                             c.filing_date DESC, c.created_at DESC
                """, params)
                cases = cursor.fetchall()
                if len(matching_clients) == 1:
                    message = f'Found {len(cases)} case(s) for {matching_clients[0]["full_name"]}'
                else:
                    message = (
                        f'Found {len(cases)} case(s) across {len(matching_clients)} matching clients'
                    )
            else:
                if is_mp:
                    cursor.execute("""
                        SELECT """ + cols + """,
                            cl.id as client_table_id,
                            cl.full_name as client_full_name,
                            cl.phone_number as client_phone,
                            cl.email as client_email,
                            cl.profile_picture as client_profile_picture,
                            cl.client_type as client_type,
                            cl.status as client_status,
                            cl.created_at as client_created_at
                        FROM cases c
                        LEFT JOIN clients cl ON c.client_id = cl.id
                        WHERE c.filled_by_id = %s AND c.status = 'Active'
                        ORDER BY c.filing_date DESC, c.created_at DESC
                    """, (current_employee_id,))
                    message = 'Displaying your active allocated cases'
                else:
                    cursor.execute("""
                        SELECT """ + cols + """,
                            cl.id as client_table_id,
                            cl.full_name as client_full_name,
                            cl.phone_number as client_phone,
                            cl.email as client_email,
                            cl.profile_picture as client_profile_picture,
                            cl.client_type as client_type,
                            cl.status as client_status,
                            cl.created_at as client_created_at
                        FROM cases c
                        LEFT JOIN clients cl ON c.client_id = cl.id
                        ORDER BY CASE WHEN c.status = 'Pending Approval' THEN 0 ELSE 1 END ASC,
                                 c.filing_date DESC, c.created_at DESC
                    """)
                    message = 'Displaying all cases'
                cases = cursor.fetchall()
            
            # Convert date objects to strings for JSON serialization
            for case in cases:
                if case.get('filing_date'):
                    case['filing_date'] = case['filing_date'].strftime('%Y-%m-%d')
                if case.get('created_at'):
                    case['created_at'] = case['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if case.get('updated_at'):
                    case['updated_at'] = case['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                if case.get('client_created_at'):
                    case['client_created_at'] = case['client_created_at'].strftime('%Y-%m-%d %H:%M:%S')
                case['allocation_description'] = case.get('allocation_description') or ''
                case['allocation_timeline'] = case.get('allocation_timeline') or ''
            
            # Convert client date objects to strings if client exists
            if client:
                if client.get('created_at'):
                    client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if client.get('updated_at'):
                    client['updated_at'] = client['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            return jsonify({
                'cases': cases,
                'client': client,
                'message': message
            })
    except Exception as e:
        print(f"Error searching cases: {e}")
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/api/cases/<int:case_id>/approve', methods=['POST'])
def api_approve_case(case_id):
    """Firm Administrator or Managing Partner approves a case: allocates it to an employee, sets status to Active, and optionally creates a calendar/reminder entry."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    if not ((user_role in allowed_roles) or (original_role == 'IT Support')):
        return jsonify({'success': False, 'error': 'Only Firm Administrators or Managing Partners can approve cases'}), 403

    data = request.get_json() or {}
    alloc_employee_id = data.get('employee_id')
    instructions = (data.get('instructions') or '').strip() or None
    due_date = (data.get('due_date') or '').strip() or None
    timeline = (data.get('timeline') or '').strip() or None

    if not alloc_employee_id:
        return jsonify({'success': False, 'error': 'Please select an employee to allocate the case to'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, status, tracking_number FROM cases WHERE id = %s", (case_id,))
            case = cursor.fetchone()
            if not case:
                return jsonify({'success': False, 'error': 'Case not found'}), 404
            if case.get('status') != 'Pending Approval':
                return jsonify({'success': False, 'error': 'Case is not pending approval'}), 400

            # Fetch the employee to allocate to
            cursor.execute("SELECT id, full_name FROM employees WHERE id = %s AND status = 'Active'", (alloc_employee_id,))
            employee = cursor.fetchone()
            if not employee:
                return jsonify({'success': False, 'error': 'Selected employee not found or is not active'}), 400
            employee_name = employee['full_name']

            # Ensure allocation columns exist
            if not column_exists('cases', 'allocation_description'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN allocation_description TEXT NULL")
                    connection.commit()
                except Exception:
                    pass
            if not column_exists('cases', 'allocation_timeline'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN allocation_timeline VARCHAR(500) NULL")
                    connection.commit()
                except Exception:
                    pass

            # Allocate + approve the case in one update
            cursor.execute("""
                UPDATE cases
                SET filled_by_id = %s,
                    filled_by_name = %s,
                    allocation_description = %s,
                    allocation_timeline = %s,
                    status = 'Active',
                    updated_at = NOW()
                WHERE id = %s
            """, (alloc_employee_id, employee_name, instructions, timeline, case_id))

            # If a due_date is provided, create a calendar/reminder entry for the allocated employee
            if due_date:
                from datetime import date as _date
                today_str = _date.today().isoformat()
                activity_type = 'Case Assignment'
                orders_text = instructions or f'Case {case.get("tracking_number", "")} allocated to {employee_name}'
                if timeline:
                    orders_text += f'\nTimeline: {timeline}'
                # Insert a case_proceedings entry so it appears on the calendar
                cursor.execute("""
                    INSERT INTO case_proceedings
                        (case_id, court_activity_type, date_of_court_appeared, next_court_date,
                         next_attendance, outcome_orders, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (case_id, activity_type, today_str, due_date, employee_name, orders_text))
                proceeding_id = cursor.lastrowid

                # Insert a material linked to that proceeding for the reminder feed
                cursor.execute("""
                    INSERT INTO case_proceeding_materials
                        (proceeding_id, material_description, allocated_to_id, allocated_to_name, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                """, (proceeding_id, orders_text, alloc_employee_id, employee_name))

            connection.commit()
            return jsonify({'success': True, 'message': f'Case approved and allocated to {employee_name} successfully'})
    except Exception as e:
        if connection:
            connection.rollback()
        print(f"Error approving case: {e}")
        return jsonify({'success': False, 'error': 'Server error: ' + str(e)}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/cases/pending-approval', methods=['GET'])
def api_cases_pending_approval():
    """API endpoint to fetch all cases with status Pending Approval (for Case Allocation)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    c.id,
                    c.tracking_number,
                    c.court_case_number,
                    c.client_id,
                    c.client_name,
                    c.case_type,
                    c.filing_date,
                    c.case_category,
                    c.station,
                    c.filled_by_name,
                    c.created_by_name,
                    c.description,
                    c.status,
                    c.created_at,
                    c.updated_at,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone,
                    cl.email as client_email
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE c.status = 'Pending Approval'
                ORDER BY c.filing_date DESC, c.created_at DESC
            """)
            cases = cursor.fetchall()
            for case in cases:
                if case.get('filing_date'):
                    case['filing_date'] = case['filing_date'].strftime('%Y-%m-%d')
                if case.get('created_at'):
                    case['created_at'] = case['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if case.get('updated_at'):
                    case['updated_at'] = case['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            return jsonify({'cases': cases, 'message': f'Found {len(cases)} case(s) pending approval'})
    except Exception as e:
        print(f"Error fetching pending approval cases: {e}")
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/case_management/register')
def register_case():
    """Case Registration page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    connection = get_db_connection()
    if connection:
        # Fine-grained permission: register litigation matter / case
        deny = enforce_permission(connection, 'matter_register_case')
        if deny:
            connection.close()
            return deny

    employee_name = session.get('employee_name', 'Unknown')
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT full_name FROM employees WHERE id = %s", (session['employee_id'],))
                employee = cursor.fetchone()
                if employee:
                    employee_name = employee['full_name']
        except Exception:
            pass
        finally:
            connection.close()
    
    employee_id = session.get('employee_id')
    return render_template(
        'register_case.html',
        company_settings=company_settings,
        employee_name=employee_name,
        employee_id=employee_id,
        court_rank_options=COURT_RANK_OPTIONS,
    )

@app.route('/api/clients/search', methods=['GET'])
def api_clients_search():
    """API endpoint to search clients for dropdown"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT id, full_name, email, phone_number, client_type
                    FROM clients 
                    WHERE status = 'Active' 
                    AND (full_name LIKE %s OR email LIKE %s OR phone_number LIKE %s)
                    ORDER BY full_name ASC
                    LIMIT 20
                """, (f'%{query}%', f'%{query}%', f'%{query}%'))
            else:
                cursor.execute("""
                    SELECT id, full_name, email, phone_number, client_type
                    FROM clients 
                    WHERE status = 'Active'
                    ORDER BY full_name ASC
                    LIMIT 50
                """)
            clients = cursor.fetchall()
            return jsonify({'clients': clients})
    except Exception as e:
        print(f"Error searching clients: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/employees/search', methods=['GET'])
def api_employees_search():
    """API endpoint to search employees for dropdown"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT id, full_name, employee_code, work_email, role
                    FROM employees 
                    WHERE status = 'Active' 
                    AND (full_name LIKE %s OR employee_code LIKE %s OR work_email LIKE %s)
                    ORDER BY full_name ASC
                    LIMIT 20
                """, (f'%{query}%', f'%{query}%', f'%{query}%'))
            else:
                cursor.execute("""
                    SELECT id, full_name, employee_code, work_email, role
                    FROM employees 
                    WHERE status = 'Active'
                    ORDER BY full_name ASC
                    LIMIT 50
                """)
            employees = cursor.fetchall()
            return jsonify({'employees': employees})
    except Exception as e:
        print(f"Error searching employees: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/cases/recent-options', methods=['GET'])
def api_cases_recent_options():
    """Return recently used case_type, case_category, station from registered cases (most recent first, up to 50 each)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    max_recent = 50  # max unique values to return per field
    fetch_limit = 500  # scan this many most recent cases to build "recently used" list
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT case_type FROM cases
                WHERE case_type IS NOT NULL AND case_type != ''
                ORDER BY id DESC LIMIT %s
            """, (fetch_limit,))
            seen = set()
            case_types = []
            for r in cursor.fetchall():
                v = (r['case_type'] or '').strip()
                if v and v not in seen:
                    seen.add(v)
                    case_types.append(v)
                    if len(case_types) >= max_recent:
                        break
            cursor.execute("""
                SELECT case_category FROM cases
                WHERE case_category IS NOT NULL AND case_category != ''
                ORDER BY id DESC LIMIT %s
            """, (fetch_limit,))
            seen = set()
            case_categories = []
            for r in cursor.fetchall():
                v = (r['case_category'] or '').strip()
                if v and v not in seen:
                    seen.add(v)
                    case_categories.append(v)
                    if len(case_categories) >= max_recent:
                        break
            cursor.execute("""
                SELECT station FROM cases
                WHERE station IS NOT NULL AND station != ''
                ORDER BY id DESC LIMIT %s
            """, (fetch_limit,))
            seen = set()
            stations = []
            for r in cursor.fetchall():
                v = (r['station'] or '').strip()
                if v and v not in seen:
                    seen.add(v)
                    stations.append(v)
                    if len(stations) >= max_recent:
                        break
            return jsonify({
                'case_types': case_types,
                'case_categories': case_categories,
                'stations': stations
            })
    except Exception as e:
        print(f"Error fetching recent options: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/case-types/search', methods=['GET'])
def api_case_types_search():
    """API endpoint to search case types with auto-create"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT id, type_name
                    FROM case_types 
                    WHERE type_name LIKE %s
                    ORDER BY type_name ASC
                    LIMIT 10
                """, (f'%{query}%',))
                types = cursor.fetchall()
                return jsonify({'types': types})
            else:
                cursor.execute("""
                    SELECT id, type_name
                    FROM case_types 
                    ORDER BY type_name ASC
                    LIMIT 50
                """)
                types = cursor.fetchall()
                return jsonify({'types': types})
    except Exception as e:
        print(f"Error searching case types: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/case-types/create', methods=['POST'])
def api_case_types_create():
    """API endpoint to create a new case type"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    type_name = data.get('type_name', '').strip().upper()
    
    if not type_name:
        return jsonify({'error': 'Type name is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if already exists
            cursor.execute("SELECT id, type_name FROM case_types WHERE type_name = %s", (type_name,))
            existing = cursor.fetchone()
            if existing:
                return jsonify({'type': existing})
            
            # Create new
            cursor.execute("INSERT INTO case_types (type_name) VALUES (%s)", (type_name,))
            connection.commit()
            new_id = cursor.lastrowid
            return jsonify({'type': {'id': new_id, 'type_name': type_name}})
    except Exception as e:
        print(f"Error creating case type: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/case-categories/search', methods=['GET'])
def api_case_categories_search():
    """API endpoint to search case categories with auto-create"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT id, category_name
                    FROM case_categories 
                    WHERE category_name LIKE %s
                    ORDER BY category_name ASC
                    LIMIT 10
                """, (f'%{query}%',))
                categories = cursor.fetchall()
                return jsonify({'categories': categories})
            else:
                cursor.execute("""
                    SELECT id, category_name
                    FROM case_categories 
                    ORDER BY category_name ASC
                    LIMIT 50
                """)
                categories = cursor.fetchall()
                return jsonify({'categories': categories})
    except Exception as e:
        print(f"Error searching case categories: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/case-categories/create', methods=['POST'])
def api_case_categories_create():
    """API endpoint to create a new case category"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    category_name = data.get('category_name', '').strip().upper()
    
    if not category_name:
        return jsonify({'error': 'Category name is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if already exists
            cursor.execute("SELECT id, category_name FROM case_categories WHERE category_name = %s", (category_name,))
            existing = cursor.fetchone()
            if existing:
                return jsonify({'category': existing})
            
            # Create new
            cursor.execute("INSERT INTO case_categories (category_name) VALUES (%s)", (category_name,))
            connection.commit()
            new_id = cursor.lastrowid
            return jsonify({'category': {'id': new_id, 'category_name': category_name}})
    except Exception as e:
        print(f"Error creating case category: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/stations/search', methods=['GET'])
def api_stations_search():
    """API endpoint to search stations with auto-create"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT id, station_name
                    FROM stations 
                    WHERE station_name LIKE %s
                    ORDER BY station_name ASC
                    LIMIT 10
                """, (f'%{query}%',))
                stations = cursor.fetchall()
                return jsonify({'stations': stations})
            else:
                cursor.execute("""
                    SELECT id, station_name
                    FROM stations 
                    ORDER BY station_name ASC
                    LIMIT 50
                """)
                stations = cursor.fetchall()
                return jsonify({'stations': stations})
    except Exception as e:
        print(f"Error searching stations: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/stations/create', methods=['POST'])
def api_stations_create():
    """API endpoint to create a new station"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    station_name = data.get('station_name', '').strip().upper()
    
    if not station_name:
        return jsonify({'error': 'Station name is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if already exists
            cursor.execute("SELECT id, station_name FROM stations WHERE station_name = %s", (station_name,))
            existing = cursor.fetchone()
            if existing:
                return jsonify({'station': existing})
            
            # Create new
            cursor.execute("INSERT INTO stations (station_name) VALUES (%s)", (station_name,))
            connection.commit()
            new_id = cursor.lastrowid
            return jsonify({'station': {'id': new_id, 'station_name': station_name}})
    except Exception as e:
        print(f"Error creating station: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

def generate_tracking_number(filing_date):
    """Generate a unique sequential tracking number in format: xxx-month-year"""
    from datetime import datetime
    
    try:
        # Parse the filing date
        if isinstance(filing_date, str):
            date_obj = datetime.strptime(filing_date, '%Y-%m-%d')
        else:
            date_obj = filing_date
        
        month = date_obj.strftime('%m')
        year = date_obj.strftime('%Y')
        
        connection = get_db_connection()
        if not connection:
            return None
        
        try:
            with connection.cursor() as cursor:
                # Get the count of cases for this month-year
                cursor.execute("""
                    SELECT COUNT(*) FROM cases 
                    WHERE YEAR(filing_date) = %s AND MONTH(filing_date) = %s
                """, (year, month))
                count = cursor.fetchone()[0]
                
                # Generate sequential number (001, 002, etc.)
                sequential_num = str(count + 1).zfill(3)
                
                # Format: xxx-month-year (e.g., 001-01-2024)
                tracking_number = f"{sequential_num}-{month}-{year}"
                
                # Ensure uniqueness (in case of race condition)
                max_attempts = 10
                attempt = 0
                while attempt < max_attempts:
                    cursor.execute("SELECT id FROM cases WHERE tracking_number = %s", (tracking_number,))
                    if cursor.fetchone():
                        count += 1
                        sequential_num = str(count + 1).zfill(3)
                        tracking_number = f"{sequential_num}-{month}-{year}"
                        attempt += 1
                    else:
                        break
                
                return tracking_number
        finally:
            connection.close()
    except Exception as e:
        print(f"Error generating tracking number: {e}")
        return None

@app.route('/api/cases/register', methods=['POST'])
def api_cases_register():
    """API endpoint to register a new case"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    # Validate required fields
    required_fields = ['client_id', 'client_name', 'court_rank', 'case_type', 'filing_date', 'case_category', 'station', 'filled_by_id', 'filled_by_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    
    court_rank = validate_court_rank(data.get('court_rank'))
    if not court_rank:
        return jsonify({'error': 'court_rank must be one of the listed court ranks'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Get current user info
            created_by_id = session['employee_id']
            created_by_name = session.get('employee_name', 'Unknown')
            
            # Generate tracking number
            tracking_number = generate_tracking_number(data['filing_date'])
            if not tracking_number:
                return jsonify({'error': 'Failed to generate tracking number'}), 500
            
            # Insert case with status 'Pending Approval'
            cursor.execute("""
                INSERT INTO cases (
                    tracking_number, court_case_number, client_id, client_name, court_rank, case_type, filing_date, case_category, 
                    station, filled_by_id, filled_by_name, created_by_id, created_by_name, description, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                tracking_number,
                data.get('court_case_number', '').upper() if data.get('court_case_number') else None,
                data['client_id'],
                data['client_name'].upper(),
                court_rank,
                data['case_type'].upper(),
                data['filing_date'],
                data['case_category'].upper(),
                data['station'].upper(),
                data['filled_by_id'],
                data['filled_by_name'].upper(),
                created_by_id,
                created_by_name.upper(),
                data.get('description', ''),
                'Pending Approval'
            ))
            connection.commit()
            case_id = cursor.lastrowid
            
            # Insert parties if provided
            if data.get('parties') and isinstance(data.get('parties'), list):
                for party in data.get('parties', []):
                    if party.get('party_name') and party.get('party_type'):
                        cursor.execute("""
                            INSERT INTO case_parties (
                                case_id, party_name, party_type, party_category, firm_agent, party_phone, party_email
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            case_id,
                            party['party_name'],
                            party['party_type'],
                            party.get('party_category') if party.get('party_category') else None,
                            party.get('firm_agent') if party.get('firm_agent') else None,
                            party.get('party_phone') if party.get('party_phone') else None,
                            party.get('party_email') if party.get('party_email') else None
                        ))
                connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Case registered successfully with tracking number: {tracking_number}',
                'case_id': case_id,
                'tracking_number': tracking_number
            })
    except Exception as e:
        print(f"Error registering case: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/api/cases/update/<int:case_id>', methods=['PUT'])
def api_cases_update(case_id):
    """API endpoint to update an existing case"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    # Validate required fields
    required_fields = ['client_id', 'client_name', 'court_rank', 'case_type', 'filing_date', 'case_category', 'station', 'filled_by_id', 'filled_by_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    
    court_rank = validate_court_rank(data.get('court_rank'))
    if not court_rank:
        return jsonify({'error': 'court_rank must be one of the listed court ranks'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if case exists
            cursor.execute("SELECT id FROM cases WHERE id = %s", (case_id,))
            if not cursor.fetchone():
                return jsonify({'error': 'Case not found'}), 404
            
            # Update case
            cursor.execute("""
                UPDATE cases SET
                    court_case_number = %s,
                    client_id = %s,
                    client_name = %s,
                    court_rank = %s,
                    case_type = %s,
                    filing_date = %s,
                    case_category = %s,
                    station = %s,
                    filled_by_id = %s,
                    filled_by_name = %s,
                    description = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                data.get('court_case_number', '').upper() if data.get('court_case_number') else None,
                data['client_id'],
                data['client_name'].upper(),
                court_rank,
                data['case_type'].upper(),
                data['filing_date'],
                data['case_category'].upper(),
                data['station'].upper(),
                data['filled_by_id'],
                data['filled_by_name'].upper(),
                data.get('description', ''),
                case_id
            ))
            connection.commit()
            
            # Delete existing parties
            cursor.execute("DELETE FROM case_parties WHERE case_id = %s", (case_id,))
            connection.commit()
            
            # Insert updated parties if provided
            if data.get('parties') and isinstance(data.get('parties'), list):
                for party in data.get('parties', []):
                    if party.get('party_name') and party.get('party_type'):
                        cursor.execute("""
                            INSERT INTO case_parties (
                                case_id, party_name, party_type, party_category, firm_agent, party_phone, party_email
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            case_id,
                            party['party_name'].upper() if isinstance(party['party_name'], str) else party['party_name'],
                            party['party_type'].upper() if isinstance(party['party_type'], str) else party['party_type'],
                            party.get('party_category').upper() if party.get('party_category') and isinstance(party.get('party_category'), str) else (party.get('party_category') if party.get('party_category') else None),
                            party.get('firm_agent').upper() if party.get('firm_agent') and isinstance(party.get('firm_agent'), str) else (party.get('firm_agent') if party.get('firm_agent') else None),
                            party.get('party_phone') if party.get('party_phone') else None,
                            party.get('party_email') if party.get('party_email') else None
                        ))
                connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Case updated successfully',
                'case_id': case_id
            })
    except Exception as e:
        print(f"Error updating case: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error: ' + str(e)}), 500
    finally:
        connection.close()

@app.route('/api/update_case_status/<int:case_id>', methods=['POST'])
def api_update_case_status(case_id):
    """API endpoint to update case status"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'status' not in data:
        return jsonify({'success': False, 'error': 'Status is required'}), 400
    
    new_status = data['status']
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if case exists
            cursor.execute("SELECT id, status FROM cases WHERE id = %s", (case_id,))
            case = cursor.fetchone()
            
            if not case:
                return jsonify({'success': False, 'error': 'Case not found'}), 404
            
            # Update the case status
            cursor.execute("""
                UPDATE cases 
                SET status = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_status, case_id))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Case status updated to {new_status} successfully'
            })
    except Exception as e:
        print(f"Error updating case status: {e}")
        connection.rollback()
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/allocate_case/<int:case_id>', methods=['POST'])
def api_allocate_case(case_id):
    """API endpoint to allocate a case to a case handler (employee) with optional description and timeline"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'employee_id' not in data:
        return jsonify({'success': False, 'error': 'Case handler (employee) ID is required'}), 400
    
    employee_id = data['employee_id']
    allocation_description = (data.get('allocation_description') or '').strip() or None
    allocation_timeline = (data.get('allocation_timeline') or '').strip() or None
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if not column_exists('cases', 'allocation_description'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN allocation_description TEXT NULL")
                    connection.commit()
                except Exception:
                    pass
            if not column_exists('cases', 'allocation_timeline'):
                try:
                    cursor.execute("ALTER TABLE cases ADD COLUMN allocation_timeline VARCHAR(500) NULL")
                    connection.commit()
                except Exception:
                    pass
            # Check if case exists
            cursor.execute("SELECT id FROM cases WHERE id = %s", (case_id,))
            case = cursor.fetchone()
            
            if not case:
                return jsonify({'success': False, 'error': 'Case not found'}), 404
            
            # Get employee name
            cursor.execute("SELECT full_name FROM employees WHERE id = %s", (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return jsonify({'success': False, 'error': 'Case handler not found'}), 400
            
            employee_name = employee['full_name']
            
            # Update the case allocation (with optional description and timeline)
            cursor.execute("""
                UPDATE cases 
                SET filled_by_id = %s, 
                    filled_by_name = %s,
                    allocation_description = %s,
                    allocation_timeline = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (employee_id, employee_name, allocation_description, allocation_timeline, case_id))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Case allocated to {employee_name} successfully'
            })
    except Exception as e:
        print(f"Error allocating case: {e}")
        connection.rollback()
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/document_management')
def document_management():
    """Document Management page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch all clients and employees
    connection = get_db_connection()
    clients = []
    employees = []
    
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Fetch clients
                cursor.execute("""
                    SELECT 
                        id,
                        google_id,
                        full_name,
                        email,
                        phone_number,
                        profile_picture,
                        client_type,
                        status,
                        created_at,
                        updated_at
                    FROM clients
                    ORDER BY full_name ASC
                """)
                clients = cursor.fetchall()
                
                # Convert date objects to strings for clients
                for client in clients:
                    if client.get('created_at'):
                        client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if client.get('updated_at'):
                        client['updated_at'] = client['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Fetch employees
                cursor.execute("""
                    SELECT 
                        id,
                        full_name,
                        phone_number,
                        work_email,
                        employee_code,
                        profile_picture,
                        role,
                        status,
                        created_at,
                        updated_at
                    FROM employees
                    ORDER BY full_name ASC
                """)
                employees = cursor.fetchall()
                
                # Convert date objects to strings for employees
                for employee in employees:
                    if employee.get('created_at'):
                        employee['created_at'] = employee['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if employee.get('updated_at'):
                        employee['updated_at'] = employee['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            print(f"Error fetching clients/employees: {e}")
            flash('An error occurred while fetching data.', 'error')
        finally:
            connection.close()
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('document_management.html', 
                         company_settings=company_settings,
                         clients=clients,
                         employees=employees)


def _ensure_google_drive_oauth_pending(connection):
    try:
        with connection.cursor() as cursor:
            ensure_google_drive_oauth_pending_table(cursor, connection)
    except Exception as e:
        print(f"[WARNING] _ensure_google_drive_oauth_pending: {e}")


def _store_google_drive_oauth_state(state, employee_id):
    """Bind OAuth state to employee in DB so the popup callback works without a session cookie."""
    connection = get_db_connection()
    if not connection:
        return False
    try:
        _ensure_google_drive_oauth_pending(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM google_drive_oauth_pending WHERE created_at < DATE_SUB(NOW(), INTERVAL 2 HOUR)"
            )
            cursor.execute(
                """
                INSERT INTO google_drive_oauth_pending (state, employee_id)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE employee_id = VALUES(employee_id), created_at = CURRENT_TIMESTAMP
                """,
                (state, employee_id),
            )
            connection.commit()
        return True
    except Exception as e:
        print(f"[WARNING] _store_google_drive_oauth_state: {e}")
        return False
    finally:
        connection.close()


def _pop_google_drive_oauth_employee(state):
    """Consume OAuth state and return the employee_id that started the flow, or None."""
    if not state:
        return None
    connection = get_db_connection()
    if not connection:
        return None
    try:
        _ensure_google_drive_oauth_pending(connection)
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                """
                SELECT employee_id FROM google_drive_oauth_pending
                WHERE state = %s AND created_at >= DATE_SUB(NOW(), INTERVAL 2 HOUR)
                """,
                (state,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute("DELETE FROM google_drive_oauth_pending WHERE state = %s", (state,))
            connection.commit()
            return row["employee_id"]
    except Exception as e:
        print(f"[WARNING] _pop_google_drive_oauth_employee: {e}")
        return None
    finally:
        connection.close()


@app.route('/api/auth/google-drive/authorize')
def google_drive_authorize():
    """Initiate Google Drive OAuth flow with account selection"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Google Drive API scopes + Docs API for inserting company header into new docs
        drive_scopes = [
            'openid',
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/documents',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile'
        ]
        
        # Use APP_BASE_URL when hosted so redirect_uri matches Google Console exactly
        redirect_uri = get_google_drive_redirect_uri()
        
        # Disable PKCE so token exchange works across redirect (session/cookie issues in popup).
        # Confidential client (client_secret) does not require PKCE.
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri]
                }
            },
            scopes=drive_scopes,
            autogenerate_code_verifier=False
        )
        flow.redirect_uri = redirect_uri
        
        # Generate authorization URL with account selection
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='select_account consent'  # Force account selection and consent
        )
        session['google_drive_oauth_state'] = state
        session.modified = True
        if not _store_google_drive_oauth_state(state, session['employee_id']):
            return jsonify({'error': 'Could not start Google Drive link. Database unavailable; try again.'}), 500
        
        # Return authorization URL for popup window
        return jsonify({
            'success': True,
            'auth_url': authorization_url
        })
    except Exception as e:
        print(f"Error initiating Google Drive OAuth: {e}")
        return jsonify({'error': 'Failed to initiate OAuth flow'}), 500

@app.route('/api/auth/google-drive/callback')
def google_drive_callback():
    """Handle Google Drive OAuth callback"""
    if request.args.get('error'):
        g_err = request.args.get('error', '')
        g_desc = request.args.get('error_description', '')
        print(f"Google Drive OAuth error from Google: {g_err} {g_desc}")
        if g_err == 'access_denied':
            user_msg = 'Google access was cancelled or denied.'
        else:
            user_msg = g_desc or g_err or 'Google OAuth error'
        return (
            '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: '
            + json.dumps(user_msg)
            + '}, "*"); window.close();</script>',
            400,
        )

    try:
        request_state = request.args.get('state')
        if not request_state:
            return '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: "Missing OAuth state"}, "*"); window.close();</script>', 400

        # Popup return often does not include the Flask session cookie (host mismatch or browser).
        # We persist state -> employee_id in DB on authorize and resolve it here.
        oauth_employee_id = _pop_google_drive_oauth_employee(request_state)
        session_employee_id = session.get('employee_id')
        session_oauth_state = session.get('google_drive_oauth_state')

        resolved_employee_id = None
        if oauth_employee_id is not None:
            resolved_employee_id = oauth_employee_id
            if session_employee_id is not None and int(session_employee_id) != int(oauth_employee_id):
                return '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: "Session mismatch. Refresh Documents Settings and try again."}, "*"); window.close();</script>', 400
        elif session_employee_id is not None and session_oauth_state and request_state == session_oauth_state:
            resolved_employee_id = session_employee_id

        if resolved_employee_id is None:
            return '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: "Session expired or invalid. Refresh this page, then connect Google Drive again."}, "*"); window.close();</script>', 401

        print(f"[OK] Google Drive OAuth callback validated for employee_id={resolved_employee_id}")

        # Google Drive API scopes (must match authorize function, including openid)
        drive_scopes = [
            'openid',
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/documents',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile'
        ]
        
        # Must match authorize (use APP_BASE_URL when hosted)
        redirect_uri = get_google_drive_redirect_uri()
        
        # Extract actual scopes from the callback URL and normalize them
        returned_scopes_raw = request.args.get('scope', '').split()
        # Normalize shorthand scopes to full URLs
        scope_mapping = {
            'email': 'https://www.googleapis.com/auth/userinfo.email',
            'profile': 'https://www.googleapis.com/auth/userinfo.profile',
            'openid': 'openid'
        }
        normalized_scopes = []
        for scope in returned_scopes_raw:
            normalized_scopes.append(scope_mapping.get(scope, scope))
        
        # Use normalized returned scopes if available, otherwise use our requested scopes
        # Always use normalized scopes to match what Google actually returned
        scopes_to_use = normalized_scopes if normalized_scopes and len(normalized_scopes) > 0 else drive_scopes
        
        # Match authorize: no PKCE (autogenerate_code_verifier=False) for confidential client
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri]
                }
            },
            scopes=scopes_to_use,
            autogenerate_code_verifier=False
        )
        flow.redirect_uri = redirect_uri
        
        # Behind a reverse-proxy request.url may still use http://; rewrite to match redirect_uri
        authorization_response = request.url
        if APP_BASE_URL and APP_BASE_URL.startswith('https://') and authorization_response.startswith('http://'):
            authorization_response = 'https://' + authorization_response[len('http://'):]
        
        try:
            flow.fetch_token(authorization_response=authorization_response)
        except Exception as scope_error:
            # If scope validation fails, try with the exact scopes Google returned
            if 'Scope has changed' in str(scope_error):
                # Recreate flow with exact returned scopes (already normalized)
                flow = Flow.from_client_config(
                    {
                        "web": {
                            "client_id": GOOGLE_CLIENT_ID,
                            "client_secret": GOOGLE_CLIENT_SECRET,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "redirect_uris": [redirect_uri]
                        }
                    },
                    scopes=normalized_scopes,  # Use exact returned scopes from Google
                    autogenerate_code_verifier=False
                )
                flow.redirect_uri = redirect_uri
                flow.fetch_token(authorization_response=authorization_response)
            else:
                raise
        
        credentials = flow.credentials
        
        # Get user info (with small clock-skew tolerance)
        id_info = verify_google_id_token(credentials.id_token)
        
        # Store credentials in database (persistent storage)
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor() as cursor:
                    # Update company_settings with Google Drive credentials
                    cursor.execute("""
                        UPDATE company_settings 
                        SET google_drive_token = %s,
                            google_drive_refresh_token = %s,
                            google_drive_token_uri = %s,
                            google_drive_scopes = %s,
                            google_drive_account_email = %s,
                            google_drive_account_name = %s,
                            google_drive_account_picture = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                    """, (
                        credentials.token,
                        credentials.refresh_token,
                        credentials.token_uri,
                        json.dumps(credentials.scopes) if credentials.scopes else None,
                        id_info.get('email'),
                        id_info.get('name'),
                        id_info.get('picture')
                    ))
                    connection.commit()
                    print("[OK] Google Drive credentials saved to database")
            except Exception as e:
                print(f"Error saving Google Drive credentials to database: {e}")
            finally:
                connection.close()
        
        # Also store in session for immediate use
        session['google_drive_credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        session['google_drive_account'] = {
            'email': id_info.get('email'),
            'name': id_info.get('name'),
            'picture': id_info.get('picture')
        }
        
        # Clear state
        session.pop('google_drive_oauth_state', None)
        
        # Send success message to opener window
        account_data = {
            'email': id_info.get('email'),
            'name': id_info.get('name')
        }
        return f'''
        <script>
            window.opener.postMessage({{
                type: 'GOOGLE_DRIVE_CONNECTED',
                account: {json.dumps(account_data)}
            }}, '*');
            window.close();
        </script>
        '''
    except Exception as e:
        print(f"Google Drive OAuth callback error: {e}")
        error_msg = str(e)
        return f'''
        <script>
            window.opener.postMessage({{
                type: 'GOOGLE_DRIVE_ERROR',
                error: {json.dumps(error_msg)}
            }}, '*');
            window.close();
        </script>
        ''', 500

@app.route('/api/auth/google-drive/status', methods=['GET'])
def google_drive_status():
    """Check Google Drive connection status"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # First check database for persistent credentials
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                           google_drive_scopes, google_drive_account_email, google_drive_account_name,
                           google_drive_account_picture, google_drive_main_folder_id
                    FROM company_settings 
                    ORDER BY id DESC LIMIT 1
                """)
                settings = cursor.fetchone()
                
                if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                    # Load credentials into session for use
                    scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                    session['google_drive_credentials'] = {
                        'token': settings['google_drive_token'],
                        'refresh_token': settings['google_drive_refresh_token'],
                        'token_uri': settings.get('google_drive_token_uri'),
                        'client_id': GOOGLE_CLIENT_ID,
                        'client_secret': GOOGLE_CLIENT_SECRET,
                        'scopes': scopes
                    }
                    session['google_drive_account'] = {
                        'email': settings.get('google_drive_account_email'),
                        'name': settings.get('google_drive_account_name'),
                        'picture': settings.get('google_drive_account_picture')
                    }
                    if settings.get('google_drive_main_folder_id'):
                        session['google_drive_main_folder_id'] = settings['google_drive_main_folder_id']
                    
                    return jsonify({
                        'connected': True,
                        'account': {
                            'email': settings.get('google_drive_account_email'),
                            'name': settings.get('google_drive_account_name'),
                            'picture': settings.get('google_drive_account_picture')
                        }
                    })
        except Exception as e:
            print(f"Error checking Google Drive status from database: {e}")
        finally:
            connection.close()
    
    # Fallback to session check
    if 'google_drive_credentials' in session and 'google_drive_account' in session:
        return jsonify({
            'connected': True,
            'account': session['google_drive_account']
        })
    else:
        return jsonify({
            'connected': False,
            'account': None
        })

@app.route('/api/auth/google-drive/disconnect', methods=['POST'])
def google_drive_disconnect():
    """Disconnect Google Drive account"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Clear from database
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE company_settings 
                    SET google_drive_token = NULL,
                        google_drive_refresh_token = NULL,
                        google_drive_token_uri = NULL,
                        google_drive_scopes = NULL,
                        google_drive_account_email = NULL,
                        google_drive_account_name = NULL,
                        google_drive_account_picture = NULL,
                        google_drive_main_folder_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                """)
                connection.commit()
                print("[OK] Google Drive credentials cleared from database")
        except Exception as e:
            print(f"Error clearing Google Drive credentials from database: {e}")
        finally:
            connection.close()
    
    # Clear from session
    session.pop('google_drive_credentials', None)
    session.pop('google_drive_account', None)
    session.pop('google_drive_main_folder_id', None)
    
    return jsonify({
        'success': True,
        'message': 'Google Drive disconnected successfully'
    })

def get_user_folder_name(phone_number, full_name, user_type='client'):
    """
    Generate folder name for clients and employees in format: [Phone Number] - [Name]
    
    Args:
        phone_number: User's phone number (can be None or empty)
        full_name: User's full name
        user_type: 'client' or 'employee' (for logging purposes)
    
    Returns:
        str: Folder name in format "[Phone Number] - [Full Name]" or "[Full Name]" if no phone
    """
    # Clean phone number - remove spaces and format
    if phone_number:
        phone_clean = phone_number.strip().replace(' ', '').replace('-', '')
        # Format phone number for display (keep original format if it starts with +)
        if phone_clean.startswith('+'):
            phone_display = phone_clean
        elif phone_clean.startswith('254'):
            phone_display = f"+{phone_clean}"
        elif phone_clean.startswith('0'):
            phone_display = f"+254{phone_clean[1:]}"
        else:
            phone_display = phone_clean
        return f"{phone_display} - {full_name}"
    else:
        # If no phone number, use just the name
        return full_name

def get_google_drive_service():
    """Get Google Drive service from stored credentials (database or session)"""
    # First try to load from database if not in session
    if 'google_drive_credentials' not in session:
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                               google_drive_scopes
                        FROM company_settings 
                        ORDER BY id DESC LIMIT 1
                    """)
                    settings = cursor.fetchone()
                    
                    if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                        scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                        session['google_drive_credentials'] = {
                            'token': settings['google_drive_token'],
                            'refresh_token': settings['google_drive_refresh_token'],
                            'token_uri': settings.get('google_drive_token_uri'),
                            'client_id': GOOGLE_CLIENT_ID,
                            'client_secret': GOOGLE_CLIENT_SECRET,
                            'scopes': scopes
                        }
            except Exception as e:
                print(f"Error loading Google Drive credentials from database: {e}")
            finally:
                connection.close()
    
    if 'google_drive_credentials' not in session:
        return None
    
    try:
        creds_dict = session['google_drive_credentials']
        credentials = Credentials(
            token=creds_dict.get('token'),
            refresh_token=creds_dict.get('refresh_token'),
            token_uri=creds_dict.get('token_uri'),
            client_id=creds_dict.get('client_id'),
            client_secret=creds_dict.get('client_secret'),
            scopes=creds_dict.get('scopes')
        )
        
        # Build and return the Drive service
        service = build('drive', 'v3', credentials=credentials)
        return service
    except Exception as e:
        print(f"Error building Google Drive service: {e}")
        return None

@app.route('/api/documents/create-main-folder', methods=['POST'])
def create_main_folder():
    """Create the main SHERIA CENTRIC folder in Google Drive"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check if Google Drive is connected (try loading from database first)
    if 'google_drive_credentials' not in session:
        # Try to load from database
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                               google_drive_scopes, google_drive_account_email, google_drive_account_name,
                               google_drive_account_picture, google_drive_main_folder_id
                        FROM company_settings 
                        ORDER BY id DESC LIMIT 1
                    """)
                    settings = cursor.fetchone()
                    
                    if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                        scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                        session['google_drive_credentials'] = {
                            'token': settings['google_drive_token'],
                            'refresh_token': settings['google_drive_refresh_token'],
                            'token_uri': settings.get('google_drive_token_uri'),
                            'client_id': GOOGLE_CLIENT_ID,
                            'client_secret': GOOGLE_CLIENT_SECRET,
                            'scopes': scopes
                        }
                        session['google_drive_account'] = {
                            'email': settings.get('google_drive_account_email'),
                            'name': settings.get('google_drive_account_name'),
                            'picture': settings.get('google_drive_account_picture')
                        }
                        if settings.get('google_drive_main_folder_id'):
                            session['google_drive_main_folder_id'] = settings['google_drive_main_folder_id']
            except Exception as e:
                print(f"Error loading Google Drive credentials: {e}")
            finally:
                connection.close()
    
    if 'google_drive_credentials' not in session:
        return jsonify({'error': 'Google Drive not connected'}), 400
    
    try:
        service = get_google_drive_service()
        if not service:
            return jsonify({'error': 'Failed to initialize Google Drive service'}), 500
        
        # Check if folder already exists (check database first, then session)
        folder_name = 'SHERIA CENTRIC'
        existing_folder_id = None
        
        # Check database
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT google_drive_main_folder_id
                        FROM company_settings 
                        ORDER BY id DESC LIMIT 1
                    """)
                    settings = cursor.fetchone()
                    if settings and settings.get('google_drive_main_folder_id'):
                        existing_folder_id = settings['google_drive_main_folder_id']
                        session['google_drive_main_folder_id'] = existing_folder_id
            except Exception as e:
                print(f"Error checking folder ID in database: {e}")
            finally:
                connection.close()
        
        # Fallback to session
        if not existing_folder_id:
            existing_folder_id = session.get('google_drive_main_folder_id')
        
        if existing_folder_id:
            # Verify folder still exists
            try:
                folder = service.files().get(fileId=existing_folder_id).execute()
                folder_url = f"https://drive.google.com/drive/folders/{existing_folder_id}"
                return jsonify({
                    'success': True,
                    'message': 'Folder already exists',
                    'folder_id': existing_folder_id,
                    'folder_url': folder_url
                })
            except HttpError:
                # Folder doesn't exist, create new one
                pass
        
        # Create the folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        folder = service.files().create(
            body=file_metadata,
            fields='id, name, webViewLink'
        ).execute()
        
        folder_id = folder.get('id')
        folder_url = folder.get('webViewLink', f"https://drive.google.com/drive/folders/{folder_id}")
        
        # Store folder ID in session and database
        session['google_drive_main_folder_id'] = folder_id
        
        # Also save to database
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("""
                        UPDATE company_settings 
                        SET google_drive_main_folder_id = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                    """, (folder_id,))
                    connection.commit()
            except Exception as e:
                print(f"Error saving folder ID to database: {e}")
            finally:
                connection.close()
        
        return jsonify({
            'success': True,
            'message': f'{folder_name} folder created successfully',
            'folder_id': folder_id,
            'folder_url': folder_url
        })
        
    except HttpError as error:
        print(f"Google Drive API error: {error}")
        error_details = error.error_details[0] if error.error_details else {}
        error_reason = error_details.get('reason', 'Unknown error')
        return jsonify({
            'error': f'Google Drive API error: {error_reason}',
            'details': str(error)
        }), 500
    except Exception as e:
        print(f"Error creating Google Drive folder: {e}")
        return jsonify({
            'error': 'Failed to create folder',
            'details': str(e)
        }), 500

def get_case_drive_folder_name(case_data, case_id):
    """Return the case folder name for Drive: Client Name - Tracking Number (Drive-safe)."""
    client_name = (case_data.get('client_full_name') or case_data.get('client_name') or 'Case').strip()
    tracking = (case_data.get('tracking_number') or '').strip() or f'Case-{case_id}'
    tracking = re.sub(r'[\\/:*?"<>|]', '_', tracking)
    client_safe = re.sub(r'[\\/:*?"<>|]', '_', client_name).strip()
    name = f"{client_safe} - {tracking}".strip()
    return name if name else f"Case {tracking}"

def get_or_create_folder(service, parent_folder_id, folder_name):
    """Get or create a folder in Google Drive"""
    try:
        # Escape single quotes in folder name for query
        escaped_folder_name = folder_name.replace("'", "\\'")
        
        # Search for existing folder
        query = f"name='{escaped_folder_name}' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        folders = results.get('files', [])
        
        if folders:
            return folders[0]['id']
        
        # Create folder if it doesn't exist
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id]
        }
        folder = service.files().create(body=file_metadata, fields='id, name').execute()
        return folder.get('id')
    except Exception as e:
        print(f"Error getting/creating folder {folder_name}: {e}")
        raise

@app.route('/api/case/<int:case_id>/upload-document', methods=['POST'])
def upload_case_document(case_id):
    """Upload a document for a specific case to Google Drive"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    try:
        # Get Google Drive service
        service = get_google_drive_service()
        if not service:
            # Try loading from database
            connection = get_db_connection()
            if connection:
                try:
                    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                        cursor.execute("""
                            SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                                   google_drive_scopes
                            FROM company_settings 
                            ORDER BY id DESC LIMIT 1
                        """)
                        settings = cursor.fetchone()
                        if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                            scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                            session['google_drive_credentials'] = {
                                'token': settings['google_drive_token'],
                                'refresh_token': settings['google_drive_refresh_token'],
                                'token_uri': settings.get('google_drive_token_uri'),
                                'client_id': GOOGLE_CLIENT_ID,
                                'client_secret': GOOGLE_CLIENT_SECRET,
                                'scopes': scopes
                            }
                            service = get_google_drive_service()
                except Exception as e:
                    print(f"Error loading credentials: {e}")
                finally:
                    connection.close()
        
        if not service:
            print("ERROR: Google Drive service not available")
            return jsonify({'success': False, 'error': 'Google Drive not connected. Please connect Google Drive in Documents Settings.'}), 400
        
        # Get case and client information
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT 
                        c.id,
                        c.tracking_number,
                        c.client_id,
                        c.filled_by_id,
                        cl.id as client_table_id,
                        cl.full_name as client_full_name,
                        cl.phone_number as client_phone
                    FROM cases c
                    LEFT JOIN clients cl ON c.client_id = cl.id
                    WHERE c.id = %s
                """, (case_id,))
                case_data = cursor.fetchone()
                
                if not case_data:
                    return jsonify({'success': False, 'error': 'Case not found'}), 404
                
                if not case_data.get('client_table_id'):
                    return jsonify({'success': False, 'error': 'Client information not found for this case'}), 404

                employee_id = session.get('employee_id')
                user_role = session.get('employee_role')
                original_role = session.get('original_role')
                is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
                is_case_owner = str(case_data.get('filled_by_id') or '') == str(employee_id)
                task_id = (request.form.get('task_id') or request.args.get('task_id') or '').strip()
                if not is_it_support and not is_case_owner:
                    ensure_task_management_table(cursor, connection)
                    if not has_active_case_task_access(cursor, case_id, employee_id, task_id or None, permission_key='upload_documents'):
                        return jsonify({'success': False, 'error': 'You can only upload while your allocated task is active.'}), 403
                
                # Get main folder ID
                main_folder_id = session.get('google_drive_main_folder_id')
                if not main_folder_id:
                    cursor.execute("""
                        SELECT google_drive_main_folder_id
                        FROM company_settings 
                        ORDER BY id DESC LIMIT 1
                    """)
                    settings = cursor.fetchone()
                    if settings and settings.get('google_drive_main_folder_id'):
                        main_folder_id = settings['google_drive_main_folder_id']
                        session['google_drive_main_folder_id'] = main_folder_id
                
                # If folder doesn't exist, create it automatically
                if not main_folder_id:
                    print("INFO: SHERIA CENTRIC folder not found, creating it automatically...")
                    try:
                        folder_name = 'SHERIA CENTRIC'
                        file_metadata = {
                            'name': folder_name,
                            'mimeType': 'application/vnd.google-apps.folder'
                        }
                        folder = service.files().create(
                            body=file_metadata,
                            fields='id, name, webViewLink'
                        ).execute()
                        
                        main_folder_id = folder.get('id')
                        session['google_drive_main_folder_id'] = main_folder_id
                        
                        # Save to database
                        cursor.execute("""
                            UPDATE company_settings 
                            SET google_drive_main_folder_id = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                        """, (main_folder_id,))
                        connection.commit()
                        print(f"INFO: Created SHERIA CENTRIC folder with ID: {main_folder_id}")
                    except Exception as create_error:
                        print(f"ERROR: Failed to create SHERIA CENTRIC folder: {create_error}")
                        err_str = str(create_error).lower()
                        if 'invalid_grant' in err_str or 'expired' in err_str or 'revoked' in err_str:
                            return jsonify({
                                'success': False,
                                'error': 'Google Drive session expired or revoked. Please reconnect Google Drive in Documents Settings.',
                                'settings_url': url_for('documents_settings')
                            }), 400
                        return jsonify({
                            'success': False,
                            'error': f'Failed to create SHERIA CENTRIC folder: {str(create_error)}'
                        }), 500
                
                # Get or create client folder
                client_folder_name = get_user_folder_name(
                    case_data.get('client_phone'),
                    case_data.get('client_full_name'),
                    'client'
                )
                client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                
                case_folder_name = get_case_drive_folder_name(case_data, case_id)
                case_doc_folder_id = get_or_create_folder(service, client_folder_id, case_folder_name)
                
                # Handle file upload
                print(f"DEBUG: request.files keys: {list(request.files.keys())}")
                print(f"DEBUG: request.form keys: {list(request.form.keys())}")
                
                if 'document_file' not in request.files:
                    print("ERROR: 'document_file' not in request.files")
                    return jsonify({'success': False, 'error': 'No file provided'}), 400
                
                file = request.files['document_file']
                print(f"DEBUG: file object: {file}, filename: {file.filename if file else 'None'}")
                
                if not file or file.filename == '':
                    print("ERROR: No file selected or empty filename")
                    return jsonify({'success': False, 'error': 'No file selected'}), 400
                
                # Validate file
                if not allowed_document_file(file.filename):
                    print(f"ERROR: Invalid file type: {file.filename}")
                    return jsonify({'success': False, 'error': 'Invalid file type. Allowed: PDF, DOC, DOCX, JPG, JPEG, PNG'}), 400
                
                # Read file content
                file_content = file.read()
                file_name = secure_filename(file.filename)
                description = request.form.get('description', '').strip()
                if not description:
                    return jsonify({'success': False, 'error': 'Description is required.'}), 400
                desc_words = description.split()
                if len(desc_words) > 5:
                    return jsonify({'success': False, 'error': 'Description must be 5 words or fewer.'}), 400
                
                # Determine MIME type
                file_ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''
                mime_types = {
                    'pdf': 'application/pdf',
                    'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'png': 'image/png'
                }
                mime_type = mime_types.get(file_ext, 'application/octet-stream')
                
                # Create file metadata
                file_metadata = {
                    'name': file_name,
                    'parents': [case_doc_folder_id],
                    'properties': {
                        'uploaded_by_id': str(session.get('employee_id') or ''),
                        'uploaded_by_name': session.get('employee_name') or ''
                    }
                }
                file_metadata['description'] = description
                
                # Upload to Google Drive
                media = MediaIoBaseUpload(
                    BytesIO(file_content),
                    mimetype=mime_type,
                    resumable=True
                )
                
                uploaded_file = service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name, webViewLink, webContentLink'
                ).execute()
                
                file_id = uploaded_file.get('id')
                file_url = uploaded_file.get('webViewLink', f"https://drive.google.com/file/d/{file_id}/view")
                
                return jsonify({
                    'success': True,
                    'message': 'Document uploaded successfully',
                    'file_id': file_id,
                    'file_name': file_name,
                    'file_url': file_url
                })
        finally:
            connection.close()
            
    except HttpError as error:
        print(f"Google Drive API error: {error}")
        error_details = error.error_details[0] if error.error_details else {}
        error_reason = error_details.get('reason', str(error))
        return jsonify({
            'success': False,
            'error': f'Google Drive API error: {error_reason}'
        }), 500
    except Exception as e:
        print(f"Error uploading document: {e}")
        import traceback
        traceback.print_exc()
        error_message = str(e).lower()
        # Token expired/revoked: tell user to reconnect Google Drive
        if 'invalid_grant' in error_message or 'token' in error_message and ('expired' in error_message or 'revoked' in error_message):
            return jsonify({
                'success': False,
                'error': 'Google Drive session expired or revoked. Please reconnect Google Drive in Documents Settings.',
                'settings_url': url_for('documents_settings')
            }), 400
        # Other client-friendly errors
        status_code = 500
        if 'not found' in error_message or 'not connected' in error_message:
            status_code = 400
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        }), status_code

@app.route('/api/case/<int:case_id>/drive-folder')
def get_case_drive_folder(case_id):
    """Get or create the case folder in Google Drive and return its URL. No document creation."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    service = get_google_drive_service()
    if not service:
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri, google_drive_scopes
                        FROM company_settings ORDER BY id DESC LIMIT 1
                    """)
                    row = cursor.fetchone()
                    if row and row.get('google_drive_token') and row.get('google_drive_refresh_token'):
                        scopes = json.loads(row['google_drive_scopes']) if row.get('google_drive_scopes') else []
                        session['google_drive_credentials'] = {
                            'token': row['google_drive_token'],
                            'refresh_token': row['google_drive_refresh_token'],
                            'token_uri': row.get('google_drive_token_uri'),
                            'client_id': GOOGLE_CLIENT_ID,
                            'client_secret': GOOGLE_CLIENT_SECRET,
                            'scopes': scopes
                        }
                        service = get_google_drive_service()
            except Exception as e:
                print(f"Error loading credentials: {e}")
            finally:
                connection.close()
    if not service:
        return jsonify({'success': False, 'error': 'Google Drive not connected. Connect in Documents Settings.'}), 400
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT c.id, c.tracking_number, c.client_id,
                       cl.id as client_table_id, cl.full_name as client_full_name, cl.phone_number as client_phone
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            if not case_data:
                return jsonify({'success': False, 'error': 'Case not found'}), 404
            if not case_data.get('client_table_id'):
                return jsonify({'success': False, 'error': 'Client not found for this case'}), 404
            main_folder_id = session.get('google_drive_main_folder_id')
            if not main_folder_id:
                cursor.execute("SELECT google_drive_main_folder_id FROM company_settings ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row and row.get('google_drive_main_folder_id'):
                    main_folder_id = row['google_drive_main_folder_id']
                    session['google_drive_main_folder_id'] = main_folder_id
            if not main_folder_id:
                return jsonify({'success': False, 'error': 'Google Drive main folder not configured'}), 400
            client_folder_name = get_user_folder_name(
                case_data.get('client_phone'),
                case_data.get('client_full_name'),
                'client'
            )
            client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
            case_folder_name = get_case_drive_folder_name(case_data, case_id)
            case_doc_folder_id = get_or_create_folder(service, client_folder_id, case_folder_name)
            folder_url = f'https://drive.google.com/drive/folders/{case_doc_folder_id}'
            return jsonify({
                'success': True,
                'folder_id': case_doc_folder_id,
                'folder_url': folder_url,
                'folder_name': case_folder_name
            })
    except Exception as e:
        print(f"Error getting case drive folder: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

@app.route('/api/case/<int:case_id>/create-google-file', methods=['POST'])
def create_case_google_file(case_id):
    """Create a new Google Doc, Sheet, or Slides in the case's Drive folder and return the edit link."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        data = request.get_json() or {}
        file_type = (data.get('type') or '').strip().lower()
        file_name = (data.get('name') or '').strip()
        task_id = str(data.get('task_id') or '').strip()
        if file_type not in ('doc', 'sheet', 'slides', 'notebook'):
            return jsonify({'success': False, 'error': 'Invalid type. Use doc, sheet, slides, or notebook.'}), 400
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                           google_drive_scopes, google_drive_main_folder_id
                    FROM company_settings ORDER BY id DESC LIMIT 1
                """)
                drive_settings = cursor.fetchone()
                if not drive_settings or not drive_settings.get('google_drive_token') or not drive_settings.get('google_drive_refresh_token'):
                    return jsonify({'success': False, 'error': 'Google Drive not connected. Connect in Documents Settings.'}), 400

                drive_scopes = json.loads(drive_settings['google_drive_scopes']) if drive_settings.get('google_drive_scopes') else []
                company_creds_dict = {
                    'token': drive_settings['google_drive_token'],
                    'refresh_token': drive_settings['google_drive_refresh_token'],
                    'token_uri': drive_settings.get('google_drive_token_uri'),
                    'client_id': GOOGLE_CLIENT_ID,
                    'client_secret': GOOGLE_CLIENT_SECRET,
                    'scopes': drive_scopes
                }

                credentials = Credentials(
                    token=company_creds_dict.get('token'),
                    refresh_token=company_creds_dict.get('refresh_token'),
                    token_uri=company_creds_dict.get('token_uri'),
                    client_id=company_creds_dict.get('client_id'),
                    client_secret=company_creds_dict.get('client_secret'),
                    scopes=company_creds_dict.get('scopes')
                )
                if credentials.expired and credentials.refresh_token:
                    from google.auth.transport.requests import Request
                    credentials.refresh(Request())
                    cursor.execute("""
                        UPDATE company_settings
                        SET google_drive_token = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                    """, (credentials.token,))
                    connection.commit()
                    company_creds_dict['token'] = credentials.token

                service = build('drive', 'v3', credentials=credentials)

                cursor.execute("""
                    SELECT c.id, c.tracking_number, c.client_id, c.filled_by_id,
                           cl.id as client_table_id, cl.full_name as client_full_name, cl.phone_number as client_phone
                    FROM cases c
                    LEFT JOIN clients cl ON c.client_id = cl.id
                    WHERE c.id = %s
                """, (case_id,))
                case_data = cursor.fetchone()
                if not case_data:
                    return jsonify({'success': False, 'error': 'Case not found'}), 404
                if not case_data.get('client_table_id'):
                    return jsonify({'success': False, 'error': 'Client not found for this case'}), 404

                employee_id = session.get('employee_id')
                user_role = session.get('employee_role')
                original_role = session.get('original_role')
                is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
                is_case_owner = str(case_data.get('filled_by_id') or '') == str(employee_id)
                if not is_it_support and not is_case_owner:
                    ensure_task_management_table(cursor, connection)
                    if not task_id:
                        return jsonify({'success': False, 'error': 'task_id is required for task-based case access.'}), 403
                    if not has_active_case_task_access(cursor, case_id, employee_id, task_id, permission_key='upload_documents'):
                        return jsonify({'success': False, 'error': 'You can only create files while your allocated task is active.'}), 403
                main_folder_id = drive_settings.get('google_drive_main_folder_id')
                if not main_folder_id:
                    return jsonify({'success': False, 'error': 'Google Drive main folder not configured'}), 400
                client_folder_name = get_user_folder_name(
                    case_data.get('client_phone'),
                    case_data.get('client_full_name'),
                    'client'
                )
                client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                case_folder_name = get_case_drive_folder_name(case_data, case_id)
                case_doc_folder_id = get_or_create_folder(service, client_folder_id, case_folder_name)
                tracking = (case_data.get('tracking_number') or '').strip() or f'Case-{case_id}'
                tracking = re.sub(r'[\\/:*?"<>|]', '_', tracking)
                company_settings = get_company_settings()
                company_name = (company_settings.get('company_name') or 'Company').strip() if company_settings else 'Company'
                employee_name = session.get('employee_name') or ''
                if not employee_name and session.get('employee_id'):
                    cursor.execute("SELECT full_name FROM employees WHERE id = %s", (session['employee_id'],))
                    emp_row = cursor.fetchone()
                    employee_name = (emp_row.get('full_name') or '').strip() if emp_row else ''
                # Document title: user's name - document follow-up number (case tracking)
                auto_title = f"{employee_name or 'Document'} - {tracking}"
                mime_and_default = {
                    'doc': ('application/vnd.google-apps.document', auto_title),
                    'sheet': ('application/vnd.google-apps.spreadsheet', auto_title),
                    'slides': ('application/vnd.google-apps.presentation', auto_title),
                    'notebook': ('application/vnd.google-apps.document', f"{auto_title} - NOTEBOOK"),
                }
                mime_type, default_name = mime_and_default[file_type]
                name = file_name if file_name else default_name
                file_metadata = {
                    'name': name,
                    'mimeType': mime_type,
                    'parents': [case_doc_folder_id],
                    'properties': {
                        'created_by_id': str(session.get('employee_id') or ''),
                        'created_by_name': session.get('employee_name') or '',
                        'task_id': task_id,
                        'task_type': 'case'
                    }
                }
                created = service.files().create(
                    body=file_metadata,
                    fields='id, name, webViewLink'
                ).execute()
                file_id = created.get('id')
                # Make file directly accessible via link to avoid access-request prompts.
                try:
                    service.permissions().create(
                        fileId=file_id,
                        body={'type': 'anyone', 'role': 'writer'},
                        fields='id'
                    ).execute()
                except Exception as perm_err:
                    print(f"Warning setting case file public permission: {perm_err}")
                docs_message = None
                docs_content_added = False
                DOCS_SCOPE = 'https://www.googleapis.com/auth/documents'
                # For Google Docs: insert company header, footer, letterhead, and logo from company_settings
                if file_type == 'doc' and company_settings:
                    creds_dict = company_creds_dict
                    stored_scopes = (creds_dict or {}).get('scopes') or []
                    has_docs_scope = DOCS_SCOPE in stored_scopes
                    if creds_dict and not has_docs_scope:
                        docs_message = 'Reconnect Google Drive in Documents Settings to add letterhead, footer and logo to new documents.'
                    elif creds_dict:
                        try:
                            credentials = Credentials(
                                token=creds_dict.get('token'),
                                refresh_token=creds_dict.get('refresh_token'),
                                token_uri=creds_dict.get('token_uri'),
                                client_id=creds_dict.get('client_id'),
                                client_secret=creds_dict.get('client_secret'),
                                scopes=creds_dict.get('scopes')
                            )
                            docs_service = build('docs', 'v1', credentials=credentials)
                            doc = docs_service.documents().get(documentId=file_id).execute()
                            body = doc.get('body', {})
                            content = body.get('content', [])
                            insert_index = 1
                            if content:
                                for el in content:
                                    if el.get('startIndex') is not None:
                                        insert_index = el['startIndex']
                                        break
                            # 1) Header text first (company name, prepared by, date) – always run
                            date_str = datetime.now().strftime('%Y-%m-%d')
                            header_lines = [
                                company_name,
                                f"Prepared by: {employee_name or 'N/A'}",
                                f"Date: {date_str}",
                                ""
                            ]
                            header_text = "\n".join(header_lines)
                            docs_service.documents().batchUpdate(
                                documentId=file_id,
                                body={'requests': [{'insertText': {'location': {'index': insert_index}, 'text': header_text}}]}
                            ).execute()
                            docs_content_added = True
                            # 2) Footer at end of document
                            footer_text = (company_settings.get('document_footer_text') or '').strip()
                            if footer_text:
                                try:
                                    doc = docs_service.documents().get(documentId=file_id).execute()
                                    content = doc.get('body', {}).get('content', [])
                                    end_index = 1
                                    for el in content:
                                        if el.get('endIndex') is not None:
                                            end_index = max(end_index, el['endIndex'])
                                    footer_index = max(1, end_index - 1)
                                    docs_service.documents().batchUpdate(
                                        documentId=file_id,
                                        body={'requests': [{'insertText': {'location': {'index': footer_index}, 'text': '\n\n' + footer_text}}]}
                                    ).execute()
                                except Exception as footer_err:
                                    print(f"Docs API footer insert failed: {footer_err}")
                            # 3) Letterhead image at top (index 1) – best effort
                            letterhead_url = (company_settings.get('default_letterhead') or '').strip()
                            if letterhead_url and (letterhead_url.startswith('http://') or letterhead_url.startswith('https://')):
                                try:
                                    docs_service.documents().batchUpdate(
                                        documentId=file_id,
                                        body={'requests': [{
                                            'insertInlineImage': {
                                                'uri': letterhead_url,
                                                'objectId': 'letterhead_' + str(uuid.uuid4()).replace('-', '')[:16],
                                                'location': {'index': 1}
                                            }
                                        }]}
                                    ).execute()
                                except Exception as letter_err:
                                    print(f"Docs API letterhead insert failed (use a public image URL): {letter_err}")
                            # 4) Company logo after letterhead – best effort
                            logo_url = (company_settings.get('company_logo') or '').strip()
                            if logo_url and (logo_url.startswith('http://') or logo_url.startswith('https://')):
                                try:
                                    docs_service.documents().batchUpdate(
                                        documentId=file_id,
                                        body={'requests': [{
                                            'insertInlineImage': {
                                                'uri': logo_url,
                                                'objectId': 'logo_' + str(uuid.uuid4()).replace('-', '')[:16],
                                                'location': {'index': 1}
                                            }
                                        }]}
                                    ).execute()
                                except Exception as logo_err:
                                    print(f"Docs API logo insert failed (use a public image URL): {logo_err}")
                        except HttpError as docs_err:
                            err_msg = str(docs_err).lower()
                            if '403' in err_msg or 'insufficient' in err_msg or 'scope' in err_msg or 'permission' in err_msg:
                                docs_message = 'Reconnect Google Drive in Documents Settings to add letterhead, footer and logo to new documents.'
                            print(f"Docs API insert failed: {docs_err}")
                        except Exception as docs_err:
                            print(f"Docs API insert failed: {docs_err}")
                edit_urls = {
                    'doc': f'https://docs.google.com/document/d/{file_id}/edit',
                    'sheet': f'https://docs.google.com/spreadsheets/d/{file_id}/edit',
                    'slides': f'https://docs.google.com/presentation/d/{file_id}/edit',
                    'notebook': f'https://docs.google.com/document/d/{file_id}/edit',
                }
                payload = {
                    'success': True,
                    'file_id': file_id,
                    'name': created.get('name', name),
                    'webViewLink': created.get('webViewLink') or f'https://drive.google.com/file/d/{file_id}/view',
                    'editLink': edit_urls.get(file_type)
                }
                if docs_message:
                    payload['docs_message'] = docs_message
                    payload['docs_settings_url'] = url_for('documents_settings')
                if file_type == 'doc':
                    payload['docs_content_added'] = docs_content_added
                return jsonify(payload)
        except Exception as e:
            err_str = str(e).lower()
            if 'invalid_grant' in err_str or 'expired' in err_str or 'revoked' in err_str:
                return jsonify({
                    'success': False,
                    'error': 'Google Drive session expired or revoked. Please reconnect in Documents Settings.',
                    'settings_url': url_for('documents_settings')
                }), 400
            raise
        finally:
            connection.close()
    except Exception as e:
        print(f"Create case google file error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/matter/<int:matter_id>/create-google-file', methods=['POST'])
def create_matter_google_file(matter_id):
    """Create a Google Doc/Sheet/Slides/Notebook in the matter's Drive folder."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        data = request.get_json() or {}
        file_type = (data.get('type') or '').strip().lower()
        file_name = (data.get('name') or '').strip()
        task_id = str(data.get('task_id') or '').strip()
        if file_type not in ('doc', 'sheet', 'slides', 'notebook'):
            return jsonify({'success': False, 'error': 'Invalid type. Use doc, sheet, slides, or notebook.'}), 400

        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                           google_drive_scopes, google_drive_main_folder_id
                    FROM company_settings ORDER BY id DESC LIMIT 1
                """)
                drive_settings = cursor.fetchone()
                if not drive_settings or not drive_settings.get('google_drive_token') or not drive_settings.get('google_drive_refresh_token'):
                    return jsonify({'success': False, 'error': 'Google Drive not connected. Connect in Documents Settings.'}), 400

                drive_scopes = json.loads(drive_settings['google_drive_scopes']) if drive_settings.get('google_drive_scopes') else []
                credentials = Credentials(
                    token=drive_settings.get('google_drive_token'),
                    refresh_token=drive_settings.get('google_drive_refresh_token'),
                    token_uri=drive_settings.get('google_drive_token_uri'),
                    client_id=GOOGLE_CLIENT_ID,
                    client_secret=GOOGLE_CLIENT_SECRET,
                    scopes=drive_scopes
                )
                if credentials.expired and credentials.refresh_token:
                    from google.auth.transport.requests import Request
                    credentials.refresh(Request())
                    cursor.execute("""
                        UPDATE company_settings
                        SET google_drive_token = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = (SELECT id FROM (SELECT id FROM company_settings ORDER BY id DESC LIMIT 1) AS sub)
                    """, (credentials.token,))
                    connection.commit()

                cursor.execute("""
                    SELECT
                        m.id, m.matter_reference_number, m.matter_title, m.client_id, m.client_name, m.client_phone, m.assigned_employee_id,
                        cl.full_name AS client_full_name, cl.phone_number AS client_phone_number
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.id = %s
                """, (matter_id,))
                matter_data = cursor.fetchone()
                if not matter_data:
                    return jsonify({'success': False, 'error': 'Matter not found'}), 404

                current_employee_id = session.get('employee_id')
                if str(matter_data.get('assigned_employee_id') or '') != str(current_employee_id):
                    user_role = session.get('employee_role')
                    original_role = session.get('original_role')
                    if user_role != 'IT Support' and original_role != 'IT Support':
                        return jsonify({'success': False, 'error': 'You do not have access to this matter'}), 403

                main_folder_id = drive_settings.get('google_drive_main_folder_id')
                if not main_folder_id:
                    return jsonify({'success': False, 'error': 'Google Drive main folder not configured'}), 400

                service = build('drive', 'v3', credentials=credentials)
                client_phone = matter_data.get('client_phone_number') or matter_data.get('client_phone')
                client_name = matter_data.get('client_full_name') or matter_data.get('client_name') or ''
                if client_name or client_phone:
                    client_folder_name = get_user_folder_name(client_phone, client_name, 'client')
                    client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                else:
                    client_folder_id = get_or_create_folder(service, main_folder_id, 'Other Matters')

                ref = (matter_data.get('matter_reference_number') or '').strip() or f'Matter-{matter_id}'
                ref_safe = re.sub(r'[\\/:*?"<>|]', '_', ref)
                matter_folder_name = f"Matter {ref_safe}"
                matter_doc_folder_id = get_or_create_folder(service, client_folder_id, matter_folder_name)

                employee_name = (session.get('employee_name') or '').strip() or 'Document'
                auto_title = f"{employee_name} - {ref_safe}"
                mime_and_default = {
                    'doc': ('application/vnd.google-apps.document', auto_title),
                    'sheet': ('application/vnd.google-apps.spreadsheet', auto_title),
                    'slides': ('application/vnd.google-apps.presentation', auto_title),
                    'notebook': ('application/vnd.google-apps.document', f"{auto_title} - NOTEBOOK"),
                }
                mime_type, default_name = mime_and_default[file_type]
                name = file_name if file_name else default_name

                created = service.files().create(
                    body={
                        'name': name,
                        'mimeType': mime_type,
                        'parents': [matter_doc_folder_id],
                        'properties': {
                            'created_by_id': str(session.get('employee_id') or ''),
                            'created_by_name': session.get('employee_name') or '',
                            'task_id': task_id,
                            'task_type': 'matter'
                        }
                    },
                    fields='id, name, webViewLink'
                ).execute()
                file_id = created.get('id')
                # Make file directly accessible via link to avoid access-request prompts.
                try:
                    service.permissions().create(
                        fileId=file_id,
                        body={'type': 'anyone', 'role': 'writer'},
                        fields='id'
                    ).execute()
                except Exception as perm_err:
                    print(f"Warning setting matter file public permission: {perm_err}")
                edit_urls = {
                    'doc': f'https://docs.google.com/document/d/{file_id}/edit',
                    'sheet': f'https://docs.google.com/spreadsheets/d/{file_id}/edit',
                    'slides': f'https://docs.google.com/presentation/d/{file_id}/edit',
                    'notebook': f'https://docs.google.com/document/d/{file_id}/edit',
                }
                return jsonify({
                    'success': True,
                    'file_id': file_id,
                    'name': created.get('name', name),
                    'webViewLink': created.get('webViewLink') or f'https://drive.google.com/file/d/{file_id}/view',
                    'editLink': edit_urls.get(file_type)
                })
        finally:
            connection.close()
    except Exception as e:
        print(f"Create matter google file error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/case_management/<int:case_id>/document/<file_id>/download')
def download_case_document(case_id, file_id):
    """Stream a case document from Google Drive as a download (attachment)."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    if (user_role not in allowed_roles) and (original_role != 'IT Support'):
        flash('You do not have permission to download this document.', 'error')
        return redirect(url_for('case_management'))
    connection = get_db_connection()
    if not connection:
        return redirect(url_for('case_documents', case_id=case_id))
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, filled_by_id FROM cases WHERE id = %s", (case_id,))
            case_row = cursor.fetchone()
            if not case_row:
                flash('Case not found.', 'error')
                return redirect(url_for('case_management'))
            employee_id = session.get('employee_id')
            is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
            is_case_owner = str(case_row.get('filled_by_id') or '') == str(employee_id)
            task_id = (request.args.get('task_id') or '').strip()
            if not is_it_support and not is_case_owner:
                ensure_task_management_table(cursor, connection)
                if not has_active_case_task_access(cursor, case_id, employee_id, task_id or None, permission_key='download'):
                    flash('You can only download while your allocated task is active.', 'error')
                    return redirect(url_for('my_tasks'))
    finally:
        connection.close()
    service = get_google_drive_service()
    if not service:
        flash('Google Drive is not connected.', 'error')
        return redirect(url_for('case_documents', case_id=case_id))
    try:
        meta = service.files().get(fileId=file_id, fields='name,mimeType').execute()
        name = meta.get('name', 'document')
        mime = meta.get('mimeType', '')
        if mime and mime.startswith('application/vnd.google-apps.'):
            export_map = {
                'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
                'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
                'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
            }
            exp_mime, ext = export_map.get(mime, ('application/pdf', '.pdf'))
            content = service.files().export(fileId=file_id, mimeType=exp_mime).execute()
            base_name = name.rsplit('.', 1)[0] if '.' in name else name
            download_name = base_name + ext
            return send_file(BytesIO(content), as_attachment=True, download_name=download_name, mimetype=exp_mime)
        request_dl = service.files().get_media(fileId=file_id)
        buf = BytesIO()
        downloader = MediaIoBaseDownload(buf, request_dl)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name=name, mimetype=mime or 'application/octet-stream')
    except HttpError as e:
        print(f"Drive download error: {e}")
        flash('Could not download file from Google Drive.', 'error')
        return redirect(url_for('case_documents', case_id=case_id))
    except Exception as e:
        print(f"Download error: {e}")
        flash('Download failed.', 'error')
        return redirect(url_for('case_documents', case_id=case_id))

@app.route('/api/case/<int:case_id>/document/<file_id>/delete', methods=['POST', 'DELETE'])
def delete_case_document_api(case_id, file_id):
    """Delete a case document from Google Drive. File must be in the case's folder."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    if (user_role not in allowed_roles) and (original_role != 'IT Support'):
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT c.id, c.client_id, c.tracking_number, c.filled_by_id, cl.id as client_table_id,
                       cl.full_name as client_full_name, cl.phone_number as client_phone
                FROM cases c
                LEFT JOIN clients cl ON c.client_id = cl.id
                WHERE c.id = %s
            """, (case_id,))
            case_data = cursor.fetchone()
            employee_id = session.get('employee_id')
            is_it_support = (user_role == 'IT Support') or (original_role == 'IT Support')
            is_case_owner = str((case_data or {}).get('filled_by_id') or '') == str(employee_id)
            task_id = (request.args.get('task_id') or '').strip()
            if case_data and not is_it_support and not is_case_owner:
                ensure_task_management_table(cursor, connection)
                if not has_active_case_task_access(cursor, case_id, employee_id, task_id or None, permission_key='view'):
                    return jsonify({'success': False, 'error': 'You can only delete while your allocated task is active.'}), 403
        if not case_data:
            return jsonify({'success': False, 'error': 'Case not found'}), 404
        service = get_google_drive_service()
        if not service:
            return jsonify({'success': False, 'error': 'Google Drive not connected'}), 503
        main_folder_id = session.get('google_drive_main_folder_id')
        if not main_folder_id:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT google_drive_main_folder_id FROM company_settings ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                main_folder_id = row.get('google_drive_main_folder_id') if row else None
        if not main_folder_id or not case_data.get('client_table_id'):
            return jsonify({'success': False, 'error': 'Case folder not available'}), 400
        client_folder_name = get_user_folder_name(
            case_data.get('client_phone'),
            case_data.get('client_full_name'),
            'client'
        )
        client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
        case_folder_name = get_case_drive_folder_name(case_data, case_id)
        case_doc_folder_id = get_or_create_folder(service, client_folder_id, case_folder_name)
        file_meta = service.files().get(fileId=file_id, fields='parents').execute()
        parents = file_meta.get('parents') or []
        if case_doc_folder_id not in parents:
            return jsonify({'success': False, 'error': 'File is not in this case folder'}), 403
        service.files().delete(fileId=file_id).execute()
        return jsonify({'success': True})
    except HttpError as e:
        print(f"Drive delete error: {e}")
        return jsonify({'success': False, 'error': 'Could not delete file from Drive'}), 500
    except Exception as e:
        print(f"Delete error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/matter/<int:matter_id>/upload-document', methods=['POST'])
def upload_matter_document(matter_id):
    """Upload a document for a specific matter to Google Drive (Main -> Client -> Matter {ref})."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        service = get_google_drive_service()
        if not service:
            connection = get_db_connection()
            if connection:
                try:
                    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                        cursor.execute("""
                            SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri, google_drive_scopes
                            FROM company_settings ORDER BY id DESC LIMIT 1
                        """)
                        settings = cursor.fetchone()
                        if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                            scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                            session['google_drive_credentials'] = {
                                'token': settings['google_drive_token'],
                                'refresh_token': settings['google_drive_refresh_token'],
                                'token_uri': settings.get('google_drive_token_uri'),
                                'client_id': GOOGLE_CLIENT_ID,
                                'client_secret': GOOGLE_CLIENT_SECRET,
                                'scopes': scopes
                            }
                            service = get_google_drive_service()
                except Exception as e:
                    print(f"Error loading credentials: {e}")
                finally:
                    connection.close()
        if not service:
            return jsonify({'success': False, 'error': 'Google Drive not connected. Please connect Google Drive in Documents Settings.'}), 400
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500

        # Fine-grained permission: upload/manage matter documents
        deny = enforce_permission(connection, 'matter_documents')
        if deny:
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT m.id, m.matter_reference_number, m.client_id, m.client_name, m.client_phone,
                           cl.id as client_table_id, cl.full_name as client_full_name, cl.phone_number as client_phone_number
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.id = %s
                """, (matter_id,))
                matter_data = cursor.fetchone()
                if not matter_data:
                    return jsonify({'success': False, 'error': 'Matter not found'}), 404
                current_employee_id = session.get('employee_id')
                cursor.execute("SELECT assigned_employee_id FROM matters WHERE id = %s", (matter_id,))
                row = cursor.fetchone()
                assigned_id = row.get('assigned_employee_id') if row else None
                if assigned_id is None or str(assigned_id) != str(current_employee_id):
                    return jsonify({'success': False, 'error': 'You do not have access to this matter'}), 403
                main_folder_id = session.get('google_drive_main_folder_id')
                if not main_folder_id:
                    cursor.execute("SELECT google_drive_main_folder_id FROM company_settings ORDER BY id DESC LIMIT 1")
                    settings = cursor.fetchone()
                    if settings and settings.get('google_drive_main_folder_id'):
                        main_folder_id = settings['google_drive_main_folder_id']
                        session['google_drive_main_folder_id'] = main_folder_id
                if not main_folder_id:
                    err_str = 'Main folder not configured'
                    return jsonify({'success': False, 'error': err_str}), 400
                client_phone = matter_data.get('client_phone_number') or matter_data.get('client_phone')
                client_name = matter_data.get('client_full_name') or matter_data.get('client_name') or ''
                if client_name or client_phone:
                    client_folder_name = get_user_folder_name(client_phone, client_name, 'client')
                    client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                else:
                    client_folder_id = get_or_create_folder(service, main_folder_id, 'Other Matters')
                ref = (matter_data.get('matter_reference_number') or '').strip() or f'Matter-{matter_id}'
                ref = re.sub(r'[\\/:*?"<>|]', '_', ref)
                matter_folder_name = f"Matter {ref}"
                matter_doc_folder_id = get_or_create_folder(service, client_folder_id, matter_folder_name)
                if 'document_file' not in request.files:
                    return jsonify({'success': False, 'error': 'No file provided'}), 400
                file = request.files['document_file']
                if not file or file.filename == '':
                    return jsonify({'success': False, 'error': 'No file selected'}), 400
                if not allowed_document_file(file.filename):
                    return jsonify({'success': False, 'error': 'Invalid file type. Allowed: PDF, DOC, DOCX, JPG, JPEG, PNG'}), 400
                file_content = file.read()
                file_name = secure_filename(file.filename)
                description = request.form.get('description', '').strip()
                file_ext = file_name.rsplit('.', 1)[1].lower() if '.' in file_name else ''
                mime_types = {
                    'pdf': 'application/pdf',
                    'doc': 'application/msword',
                    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'
                }
                mime_type = mime_types.get(file_ext, 'application/octet-stream')
                file_metadata = {'name': file_name, 'parents': [matter_doc_folder_id]}
                if description:
                    file_metadata['description'] = description
                media = MediaIoBaseUpload(BytesIO(file_content), mimetype=mime_type, resumable=True)
                uploaded_file = service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name, webViewLink'
                ).execute()
                return jsonify({
                    'success': True,
                    'message': 'Document uploaded successfully',
                    'file_id': uploaded_file.get('id'),
                    'file_name': uploaded_file.get('name')
                })
        except Exception as e:
            err_str = str(e).lower()
            if 'invalid_grant' in err_str or 'expired' in err_str or 'revoked' in err_str:
                return jsonify({
                    'success': False,
                    'error': 'Google Drive session expired or revoked. Please reconnect Google Drive in Documents Settings.',
                    'settings_url': url_for('documents_settings')
                }), 400
            raise
        finally:
            connection.close()
    except Exception as e:
        print(f"Upload matter document error: {e}")
        return jsonify({'success': False, 'error': f'Upload failed: {str(e)}'}), 500

@app.route('/other_matters/<int:matter_id>/document/<file_id>/download')
def download_matter_document(matter_id, file_id):
    """Stream a matter document from Google Drive as a download."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    connection = get_db_connection()
    if not connection:
        return redirect(url_for('matter_documents', matter_id=matter_id))
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, assigned_employee_id FROM matters WHERE id = %s", (matter_id,))
            row = cursor.fetchone()
            if not row:
                flash('Matter not found.', 'error')
                return redirect(url_for('other_matters'))
            current_employee_id = session.get('employee_id')
            assigned_id = row.get('assigned_employee_id')
            if assigned_id is None or str(assigned_id) != str(current_employee_id):
                flash('You do not have permission to download this document.', 'error')
                return redirect(url_for('other_matters'))
    finally:
        connection.close()
    service = get_google_drive_service()
    if not service:
        flash('Google Drive is not connected.', 'error')
        return redirect(url_for('matter_documents', matter_id=matter_id))
    try:
        meta = service.files().get(fileId=file_id, fields='name,mimeType').execute()
        name = meta.get('name', 'document')
        mime = meta.get('mimeType', '')
        if mime and mime.startswith('application/vnd.google-apps.'):
            export_map = {
                'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
                'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
                'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
            }
            exp_mime, ext = export_map.get(mime, ('application/pdf', '.pdf'))
            content = service.files().export(fileId=file_id, mimeType=exp_mime).execute()
            base_name = name.rsplit('.', 1)[0] if '.' in name else name
            download_name = base_name + ext
            return send_file(BytesIO(content), as_attachment=True, download_name=download_name, mimetype=exp_mime)
        request_dl = service.files().get_media(fileId=file_id)
        buf = BytesIO()
        downloader = MediaIoBaseDownload(buf, request_dl)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name=name, mimetype=mime or 'application/octet-stream')
    except HttpError as e:
        print(f"Drive download error: {e}")
        flash('Could not download file from Google Drive.', 'error')
        return redirect(url_for('matter_documents', matter_id=matter_id))
    except Exception as e:
        print(f"Download error: {e}")
        flash('Download failed.', 'error')
        return redirect(url_for('matter_documents', matter_id=matter_id))

@app.route('/api/matter/<int:matter_id>/document/<file_id>/delete', methods=['POST', 'DELETE'])
def delete_matter_document_api(matter_id, file_id):
    """Delete a matter document from Google Drive. File must be in the matter's folder."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT m.id, m.matter_reference_number, m.assigned_employee_id,
                       m.client_id, cl.full_name as client_full_name, cl.phone_number as client_phone_number,
                       m.client_name as matter_client_name, m.client_phone as matter_client_phone
                FROM matters m
                LEFT JOIN clients cl ON m.client_id = cl.id
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
        if not matter_data:
            return jsonify({'success': False, 'error': 'Matter not found'}), 404
        current_employee_id = session.get('employee_id')
        assigned_id = matter_data.get('assigned_employee_id')
        if assigned_id is None or str(assigned_id) != str(current_employee_id):
            return jsonify({'success': False, 'error': 'You do not have access to this matter'}), 403
        service = get_google_drive_service()
        if not service:
            return jsonify({'success': False, 'error': 'Google Drive not connected'}), 503
        main_folder_id = session.get('google_drive_main_folder_id')
        if not main_folder_id:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT google_drive_main_folder_id FROM company_settings ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                main_folder_id = row.get('google_drive_main_folder_id') if row else None
        if not main_folder_id:
            return jsonify({'success': False, 'error': 'Matter folder not available'}), 400
        client_phone = matter_data.get('client_phone_number') or matter_data.get('matter_client_phone')
        client_name = matter_data.get('client_full_name') or matter_data.get('matter_client_name') or ''
        if client_name or client_phone:
            client_folder_name = get_user_folder_name(client_phone, client_name, 'client')
            client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
        else:
            client_folder_id = get_or_create_folder(service, main_folder_id, 'Other Matters')
        ref = (matter_data.get('matter_reference_number') or '').strip() or f'Matter-{matter_id}'
        ref = re.sub(r'[\\/:*?"<>|]', '_', ref)
        matter_folder_name = f"Matter {ref}"
        matter_doc_folder_id = get_or_create_folder(service, client_folder_id, matter_folder_name)
        file_meta = service.files().get(fileId=file_id, fields='parents').execute()
        parents = file_meta.get('parents') or []
        if matter_doc_folder_id not in parents:
            return jsonify({'success': False, 'error': 'File is not in this matter folder'}), 403
        service.files().delete(fileId=file_id).execute()
        return jsonify({'success': True})
    except HttpError as e:
        print(f"Drive delete error: {e}")
        return jsonify({'success': False, 'error': 'Could not delete file from Drive'}), 500
    except Exception as e:
        print(f"Delete matter document error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if connection:
            connection.close()

@app.route('/documents_settings')
def documents_settings():
    """Documents Settings page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Load Google Drive credentials from database into session if not already loaded
    if 'google_drive_credentials' not in session:
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri,
                               google_drive_scopes, google_drive_account_email, google_drive_account_name,
                               google_drive_account_picture, google_drive_main_folder_id
                        FROM company_settings 
                        ORDER BY id DESC LIMIT 1
                    """)
                    settings = cursor.fetchone()
                    
                    if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                        scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                        session['google_drive_credentials'] = {
                            'token': settings['google_drive_token'],
                            'refresh_token': settings['google_drive_refresh_token'],
                            'token_uri': settings.get('google_drive_token_uri'),
                            'client_id': GOOGLE_CLIENT_ID,
                            'client_secret': GOOGLE_CLIENT_SECRET,
                            'scopes': scopes
                        }
                        session['google_drive_account'] = {
                            'email': settings.get('google_drive_account_email'),
                            'name': settings.get('google_drive_account_name'),
                            'picture': settings.get('google_drive_account_picture')
                        }
                        if settings.get('google_drive_main_folder_id'):
                            session['google_drive_main_folder_id'] = settings['google_drive_main_folder_id']
            except Exception as e:
                print(f"Error loading Google Drive credentials: {e}")
            finally:
                connection.close()
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    # Fine-grained permission: manage document settings
    connection = get_db_connection()
    if connection:
        deny = enforce_permission(connection, 'system_manage_document_settings')
        connection.close()
        if deny:
            return deny
    
    return render_template('documents_settings.html', company_settings=company_settings)

@app.route('/view_client_documents/<int:client_id>')
def view_client_documents(client_id):
    """View documents for a specific client"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('document_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (client_id,))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('document_management'))
            
            # Convert date objects to strings
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('view_client_documents.html',
                                 client=client,
                                 client_id=client_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client documents: {e}")
        flash('An error occurred while fetching client information.', 'error')
        return redirect(url_for('document_management'))
    finally:
        connection.close()

@app.route('/view_client_documents/<int:client_id>/<document_type>')
def view_client_document_type(client_id, document_type):
    """View documents for a specific client by document type"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Validate document type
    valid_types = ['CLIENT_PERSONAL_DOCUMENT', 'CLIENT_CASE_DOCUMENT', 'CLIENT_OTHER_MATTERS']
    if document_type not in valid_types:
        flash('Invalid document type', 'error')
        return redirect(url_for('view_client_documents', client_id=client_id))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('document_management'))
    
    # Handle CLIENT_OTHER_MATTERS differently - show matters instead of documents
    if document_type == 'CLIENT_OTHER_MATTERS':
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Fetch client details
                cursor.execute("""
                    SELECT 
                        id,
                        google_id,
                        full_name,
                        email,
                        phone_number,
                        profile_picture,
                        client_type,
                        status,
                        created_at
                    FROM clients
                    WHERE id = %s
                """, (client_id,))
                client = cursor.fetchone()
                
                if not client:
                    flash('Client not found', 'error')
                    return redirect(url_for('document_management'))
                
                # Fetch all matters for this client
                cursor.execute("""
                    SELECT 
                        m.id,
                        m.matter_reference_number,
                        m.matter_title,
                        m.matter_category,
                        m.client_instructions,
                        m.assigned_employee_name,
                        m.date_opened,
                        m.status,
                        m.created_at,
                        m.updated_at
                    FROM matters m
                    WHERE m.client_id = %s
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """, (client_id,))
                matters = cursor.fetchall()
                
                # Convert date objects to strings
                if client.get('created_at'):
                    client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                for matter in matters:
                    if matter.get('date_opened'):
                        matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d') if hasattr(matter['date_opened'], 'strftime') else str(matter['date_opened'])
                    if matter.get('created_at'):
                        matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['created_at'], 'strftime') else str(matter['created_at'])
                    if matter.get('updated_at'):
                        matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['updated_at'], 'strftime') else str(matter['updated_at'])
                
                company_settings = get_company_settings()
                if not company_settings:
                    company_settings = {'company_name': 'BAUNI LAW GROUP'}
                
                return render_template('view_client_other_matters.html',
                                     client=client,
                                     client_id=client_id,
                                     document_type=document_type,
                                     document_type_name='Other Matters',
                                     matters=matters,
                                     company_settings=company_settings)
        except Exception as e:
            print(f"Error fetching client matters: {e}")
            flash('An error occurred while fetching client matters.', 'error')
            return redirect(url_for('view_client_documents', client_id=client_id))
        finally:
            connection.close()
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch client details
            cursor.execute("""
                SELECT 
                    id,
                    google_id,
                    full_name,
                    email,
                    phone_number,
                    profile_picture,
                    client_type,
                    status,
                    created_at
                FROM clients
                WHERE id = %s
            """, (client_id,))
            client = cursor.fetchone()
            
            if not client:
                flash('Client not found', 'error')
                return redirect(url_for('document_management'))
            
            # Convert date objects to strings
            if client.get('created_at'):
                client['created_at'] = client['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Map document type to display name
            document_type_names = {
                'CLIENT_PERSONAL_DOCUMENT': 'Personal Documents',
                'CLIENT_CASE_DOCUMENT': 'Case Documents'
            }
            document_type_name = document_type_names.get(document_type, document_type)
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('view_client_document_type.html',
                                 client=client,
                                 client_id=client_id,
                                 document_type=document_type,
                                 document_type_name=document_type_name,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client documents: {e}")
        flash('An error occurred while fetching client information.', 'error')
        return redirect(url_for('document_management'))
    finally:
        connection.close()

@app.route('/view_employee_documents/<int:employee_id>')
def view_employee_documents(employee_id):
    """View documents for a specific employee"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('document_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch employee details
            cursor.execute("""
                SELECT 
                    id,
                    full_name,
                    phone_number,
                    work_email,
                    employee_code,
                    profile_picture,
                    role,
                    status,
                    id_front,
                    id_back,
                    employment_contract,
                    created_at
                FROM employees
                WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                flash('Employee not found', 'error')
                return redirect(url_for('document_management'))
            
            # Convert date objects to strings
            if employee.get('created_at'):
                employee['created_at'] = employee['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('view_employee_documents.html',
                                 employee=employee,
                                 employee_id=employee_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching employee documents: {e}")
        flash('An error occurred while fetching employee information.', 'error')
        return redirect(url_for('document_management'))
    finally:
        connection.close()

@app.route('/registration_documents')
def registration_documents():
    """Registration Documents page - displays onboarding documents"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch employees with onboarding documents
    employees_with_docs = []
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT id, full_name, employee_code, work_email, 
                           id_front, id_back, employment_contract,
                           onboarding_completed
                    FROM employees
                    WHERE onboarding_completed = TRUE
                    AND (id_front IS NOT NULL OR id_back IS NOT NULL OR employment_contract IS NOT NULL)
                    ORDER BY full_name ASC
                """)
                employees_with_docs = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching employee documents: {e}")
        finally:
            connection.close()
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('registration_documents.html', 
                         company_settings=company_settings,
                         employees=employees_with_docs)

@app.route('/download_document/<document_type>/<filename>')
def download_document(document_type, filename):
    """Download employee document"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if os.path.exists(filepath):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    else:
        flash('Document not found', 'error')
        return redirect(url_for('document_management'))

@app.route('/download_employee_contract')
def download_employee_contract():
    """Allow employees to download their own employment contract"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    employee_id = session['employee_id']
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('onboarding'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT employment_contract FROM employees WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                flash('Employee not found', 'error')
                return redirect(url_for('onboarding'))
            
            if not employee.get('employment_contract'):
                flash('No contract file found. Please upload your contract first.', 'error')
                return redirect(url_for('onboarding'))
            
            filename = employee['employment_contract']
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            if os.path.exists(filepath):
                return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
            else:
                flash('Contract file not found on server', 'error')
                return redirect(url_for('onboarding'))
    except Exception as e:
        print(f"Error downloading contract: {e}")
        flash('An error occurred while downloading the contract.', 'error')
        return redirect(url_for('onboarding'))
    finally:
        connection.close()

@app.route('/calendar')
def calendar():
    """Calendar page - displays all upcoming court dates (cases) and matter dates"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))

    deny = enforce_permission(connection, 'calendar_shared')
    if deny:
        return deny
    
    try:
        from datetime import date
        today = date.today()
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # ── Case proceedings (upcoming court dates) ──────────────────────
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.next_court_date,
                    p.next_attendance,
                    p.virtual_link,
                    p.outcome_orders,
                    c.tracking_number,
                    c.client_name,
                    c.id as case_table_id
                FROM case_proceedings p
                JOIN cases c ON p.case_id = c.id
                WHERE p.next_court_date IS NOT NULL AND p.next_court_date >= %s
                ORDER BY p.next_court_date ASC
            """, (today,))
            all_upcoming_proceedings = list(cursor.fetchall() or [])

            for proceeding in all_upcoming_proceedings:
                proceeding['source'] = 'case'
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    proceeding['days_until'] = (next_date - today).days
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')

            # ── Matters ──────────────────────────────────────────────────────
            cursor.execute("""
                SELECT
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_name,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status
                FROM matters m
                WHERE m.status NOT IN ('Closed', 'Completed')
                ORDER BY m.date_opened ASC
            """)
            all_matters = list(cursor.fetchall() or [])

            matter_events = []
            for matter in all_matters:
                matter['source'] = 'matter'
                if matter.get('date_opened'):
                    d = matter['date_opened']
                    matter['date_opened'] = d.strftime('%Y-%m-%d')
                    matter['days_until'] = (d - today).days
                matter_events.append(matter)

            # ── Build combined calendar_events dict keyed by date ────────────
            calendar_events = {}
            for proceeding in all_upcoming_proceedings:
                key = proceeding.get('next_court_date')
                if key:
                    calendar_events.setdefault(key, []).append(proceeding)
            for matter in matter_events:
                key = matter.get('date_opened')
                if key:
                    calendar_events.setdefault(key, []).append(matter)

            # Combined agenda list sorted by date
            all_agenda = sorted(
                all_upcoming_proceedings + matter_events,
                key=lambda x: x.get('next_court_date') or x.get('date_opened') or ''
            )

            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('calendar.html', 
                                 company_settings=company_settings,
                                 all_upcoming_proceedings=all_agenda,
                                 calendar_events=calendar_events,
                                 matter_events=matter_events)
    except Exception as e:
        print(f"Error fetching calendar: {e}")
        flash('An error occurred while fetching calendar.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/reminders')
def reminders():
    """Reminders page - displays all case reminders and all active matters"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        from datetime import date
        today = date.today()
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # ── Case proceedings with materials ───────────────────────────────
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.next_court_date,
                    p.next_attendance,
                    p.virtual_link,
                    p.outcome_orders,
                    c.tracking_number,
                    c.client_name,
                    c.id as case_table_id
                FROM case_proceedings p
                JOIN cases c ON p.case_id = c.id
                WHERE p.next_court_date IS NOT NULL AND p.next_court_date >= %s
                ORDER BY p.next_court_date ASC
            """, (today,))
            all_upcoming_proceedings = list(cursor.fetchall() or [])

            proceedings_with_materials = []
            all_reminders = []
            for proceeding in all_upcoming_proceedings:
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    proceeding['days_until'] = (next_date - today).days
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute("""
                    SELECT m.id, m.proceeding_id, m.material_description,
                           m.reminder_frequency, m.allocated_to_id, m.allocated_to_name,
                           m.created_at, m.updated_at
                    FROM case_proceeding_materials m
                    WHERE m.proceeding_id = %s
                    ORDER BY m.created_at ASC
                """, (proceeding['id'],))
                materials = cursor.fetchall()
                for material in materials:
                    if material.get('created_at'):
                        material['created_at'] = material['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if material.get('updated_at'):
                        material['updated_at'] = material['updated_at'].strftime('%Y-%m-%d %H:%M:%S')

                proceeding['materials'] = materials
                if materials:
                    proceedings_with_materials.append(proceeding)
                    all_reminders.extend(materials)

            # ── All active matters ────────────────────────────────────────────
            cursor.execute("""
                SELECT
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_name,
                    m.client_phone,
                    m.assigned_employee_id,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status,
                    m.created_at
                FROM matters m
                WHERE m.status NOT IN ('Closed', 'Completed')
                ORDER BY
                    CASE WHEN m.status = 'Pending Approval' THEN 0 ELSE 1 END ASC,
                    m.date_opened ASC
            """)
            all_matters = cursor.fetchall()
            for matter in all_matters:
                if matter.get('date_opened'):
                    d = matter['date_opened']
                    matter['date_opened'] = d.strftime('%Y-%m-%d')
                    matter['days_since_opened'] = (today - d).days
                if matter.get('created_at'):
                    matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S')

            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}

            return render_template('reminders.html',
                                 company_settings=company_settings,
                                 proceedings_with_materials=proceedings_with_materials,
                                 all_reminders=all_reminders,
                                 all_matters=all_matters)
    except Exception as e:
        print(f"Error fetching reminders: {e}")
        flash('An error occurred while fetching reminders.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/calendar_reminders')
def calendar_reminders():
    """Calendar & Reminders page - currently reuses calendar view with same permission."""
    return redirect(url_for('calendar'))
    try:
        from datetime import date
        today = date.today()
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch all upcoming court dates across all cases
            cursor.execute("""
                SELECT 
                    p.id,
                    p.case_id,
                    p.court_activity_type,
                    p.court_room,
                    p.judicial_officer,
                    p.date_of_court_appeared,
                    p.next_court_date,
                    p.next_attendance,
                    p.virtual_link,
                    p.outcome_orders,
                    c.tracking_number,
                    c.client_name,
                    c.id as case_table_id
                FROM case_proceedings p
                JOIN cases c ON p.case_id = c.id
                WHERE p.next_court_date IS NOT NULL AND p.next_court_date >= %s
                ORDER BY p.next_court_date ASC
            """, (today,))
            all_upcoming_proceedings = list(cursor.fetchall() or [])
            
            # Convert dates and calculate days until
            for proceeding in all_upcoming_proceedings:
                if proceeding.get('date_of_court_appeared'):
                    proceeding['date_of_court_appeared'] = proceeding['date_of_court_appeared'].strftime('%Y-%m-%d')
                if proceeding.get('next_court_date'):
                    next_date = proceeding['next_court_date']
                    proceeding['next_court_date'] = next_date.strftime('%Y-%m-%d')
                    days_until = (next_date - today).days
                    proceeding['days_until'] = days_until
                if proceeding.get('created_at'):
                    proceeding['created_at'] = proceeding['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Fetch materials for each proceeding and attach them
            proceedings_with_materials = []
            all_reminders = []
            for proceeding in all_upcoming_proceedings:
                cursor.execute("""
                    SELECT 
                        m.id,
                        m.proceeding_id,
                        m.material_description,
                        m.reminder_frequency,
                        m.allocated_to_id,
                        m.allocated_to_name,
                        m.created_at,
                        m.updated_at
                    FROM case_proceeding_materials m
                    WHERE m.proceeding_id = %s
                    ORDER BY m.created_at ASC
                """, (proceeding['id'],))
                materials = cursor.fetchall()
                
                # Convert dates to strings
                for material in materials:
                    if material.get('created_at'):
                        material['created_at'] = material['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                    if material.get('updated_at'):
                        material['updated_at'] = material['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Attach materials to proceeding
                proceeding['materials'] = materials
                if materials:
                    proceedings_with_materials.append(proceeding)
                    all_reminders.extend(materials)
            
            
            # Organize calendar events by date
            calendar_events = {}
            for proceeding in all_upcoming_proceedings:
                if proceeding.get('next_court_date'):
                    date_key = proceeding['next_court_date']
                    if date_key not in calendar_events:
                        calendar_events[date_key] = []
                    calendar_events[date_key].append(proceeding)
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('calendar_reminders.html', 
                                 company_settings=company_settings,
                                 all_upcoming_proceedings=all_upcoming_proceedings,
                                 proceedings_with_materials=proceedings_with_materials,
                                 all_reminders=all_reminders,
                                 calendar_events=calendar_events)
    except Exception as e:
        print(f"Error fetching calendar and reminders: {e}")
        flash('An error occurred while fetching calendar and reminders.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/communication_messaging')
def communication_messaging():
    """Communication & Messaging page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    communication_type = request.args.get('type', 'email')
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    # Initialize variables
    email_accounts = []
    employees = []
    all_emails = []
    client_messages = []
    
    # Fetch data based on communication type
    if communication_type == 'email':
        # Fetch all employees
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT id, full_name, phone_number, work_email, employee_code, role, status, profile_picture
                        FROM employees 
                        WHERE status = 'Active'
                        ORDER BY full_name ASC
                    """)
                    employees = cursor.fetchall()
            except Exception as e:
                print(f"Error fetching employees: {e}")
            finally:
                connection.close()
        
        # Fetch all email accounts from database and cPanel
        email_accounts = get_email_accounts_from_db()
        email_settings = get_email_settings()
        
        # Also fetch from cPanel if settings are configured
        cpanel_emails = []
        if email_settings:
            try:
                result = list_email_accounts(
                    email_settings['cpanel_api_token'],
                    email_settings['cpanel_domain'],
                    email_settings['cpanel_user'],
                    email_settings['cpanel_api_port']
                )
                if result.get('status') == 1 and 'data' in result:
                    for account in result['data']:
                        email_addr = account.get('email', '')
                        if email_addr:
                            # Check if already in email_accounts
                            if not any(ea.get('email_address') == email_addr for ea in email_accounts):
                                cpanel_emails.append({
                                    'email_address': email_addr,
                                    'is_cpanel': True,
                                    'disk_used': account.get('humandiskused', '0 MB'),
                                    'disk_quota': account.get('humandiskquota', '250 MB')
                                })
            except Exception as e:
                print(f"Error fetching cPanel emails: {e}")
        
        # Combine all emails
        all_email_accounts = email_accounts + cpanel_emails
        
        # Fetch all emails from all email accounts (with timeout protection)
        email_fetch_error = None
        if email_settings:
            import signal, threading

            def _fetch_account_emails(email_address, password, imap_host, imap_port, imap_ssl, results_list):
                try:
                    emails = fetch_emails_from_imap(email_address, password, imap_host, imap_port, imap_ssl, limit=200)
                    for email in emails:
                        email['account_email'] = email_address
                    results_list.extend(emails)
                except Exception as e:
                    print(f"Error fetching emails for {email_address}: {e}")

            for email_account in all_email_accounts:
                email_address = email_account.get('email_address') or email_account.get('email', '')
                if not email_address:
                    continue
                password = email_settings.get('main_email_password', '')
                if email_account.get('email_password'):
                    password = email_account['email_password']
                if not password:
                    continue

                thread_results = []
                t = threading.Thread(target=_fetch_account_emails, args=(
                    email_address, password,
                    email_settings.get('imap_host', 'mail.baunilawgroup.com'),
                    int(email_settings.get('imap_port', 993)),
                    bool(email_settings.get('imap_use_ssl', True)),
                    thread_results
                ))
                t.start()
                t.join(timeout=15)
                if t.is_alive():
                    email_fetch_error = f"Timeout fetching emails for {email_address}"
                    print(f"[WARNING] {email_fetch_error}")
                else:
                    all_emails.extend(thread_results)

        all_emails.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        return render_template('communication_messaging.html', 
                             company_settings=company_settings,
                             communication_type=communication_type,
                             email_accounts=all_email_accounts,
                             employees=employees,
                             all_emails=all_emails,
                             email_fetch_error=email_fetch_error)
    elif communication_type == 'webapp':
        # Fetch client messages
        connection = get_db_connection()
        if connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT 
                            cm.*,
                            c.full_name as client_name,
                            c.email as client_email,
                            e.full_name as employee_full_name,
                            e.profile_picture as employee_profile_picture
                        FROM client_messages cm
                        LEFT JOIN clients c ON cm.client_id = c.id
                        LEFT JOIN employees e ON cm.employee_id = e.id
                        ORDER BY cm.created_at DESC
                        LIMIT 100
                    """)
                    client_messages = cursor.fetchall()
                    
                    # Convert dates to strings
                    for msg in client_messages:
                        if msg.get('created_at'):
                            msg['created_at'] = msg['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(msg['created_at'], 'strftime') else str(msg['created_at'])
            except Exception as e:
                print(f"Error fetching client messages: {e}")
            finally:
                connection.close()
        
        return render_template('communication_messaging.html', 
                             company_settings=company_settings,
                             communication_type=communication_type,
                             client_messages=client_messages)
    else:
        # Default: show email accounts
        return render_template('communication_messaging.html', 
                             company_settings=company_settings,
                             communication_type=communication_type)

@app.route('/employee_communication_settings')
def employee_communication_settings():
    """Employee Communication Settings page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch all employees
    connection = get_db_connection()
    employees = []
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT id, full_name, phone_number, work_email, employee_code, role, status, profile_picture
                    FROM employees 
                    ORDER BY full_name ASC
                """)
                employees = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching employees: {e}")
        finally:
            connection.close()
    
    # Fetch all email accounts from cPanel and database
    email_accounts = get_email_accounts_from_db()
    email_settings = get_email_settings()
    
    # Also fetch from cPanel if settings are configured
    cpanel_emails = []
    if email_settings:
        try:
            result = list_email_accounts(
                email_settings['cpanel_api_token'],
                email_settings['cpanel_domain'],
                email_settings['cpanel_user'],
                email_settings['cpanel_api_port']
            )
            if result.get('status') == 1 and 'data' in result:
                for account in result['data']:
                    email_addr = account.get('email', '')
                    if email_addr:
                        # Check if already in email_accounts
                        if not any(ea.get('email_address') == email_addr for ea in email_accounts):
                            cpanel_emails.append({
                                'email_address': email_addr,
                                'is_cpanel': True,
                                'disk_used': account.get('humandiskused', '0 MB'),
                                'disk_quota': account.get('humandiskquota', '250 MB')
                            })
        except Exception as e:
            print(f"Error fetching cPanel emails: {e}")
    
    # Combine all emails
    all_emails = email_accounts + cpanel_emails
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    # Fine-grained permission: manage communication channels / employee mappings
    connection = get_db_connection()
    if connection:
        deny = enforce_permission(connection, 'system_manage_channels')
        connection.close()
        if deny:
            return deny
    
    return render_template('employee_communication_settings.html', 
                         company_settings=company_settings,
                         employees=employees,
                         email_accounts=all_emails)

# ==================== EMAIL MANAGEMENT FUNCTIONS ====================

def get_email_settings():
    """Get email settings from database"""
    try:
        connection = get_db_connection()
        if not connection:
            return None
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM email_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            return settings
    except Exception as e:
        print(f"Error getting email settings: {e}")
        return None
    finally:
        if connection:
            connection.close()

def save_email_settings(cpanel_user, cpanel_domain, cpanel_api_token, cpanel_api_port, 
                        main_email, main_email_password, smtp_host, smtp_port, smtp_use_tls,
                        imap_host, imap_port, imap_use_ssl, sender_name):
    """Save or update email settings"""
    try:
        connection = get_db_connection()
        if not connection:
            print("Failed to get database connection")
            return False
        
        # Convert boolean values properly
        smtp_use_tls = bool(smtp_use_tls) if smtp_use_tls is not None else True
        imap_use_ssl = bool(imap_use_ssl) if imap_use_ssl is not None else True
        
        with connection.cursor() as cursor:
            # Check if settings exist
            cursor.execute("SELECT id FROM email_settings LIMIT 1")
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute("""
                    UPDATE email_settings SET
                        cpanel_user = %s, cpanel_domain = %s, cpanel_api_token = %s,
                        cpanel_api_port = %s, main_email = %s, main_email_password = %s,
                        smtp_host = %s, smtp_port = %s, smtp_use_tls = %s,
                        imap_host = %s, imap_port = %s, imap_use_ssl = %s, sender_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                """, (cpanel_user, cpanel_domain, cpanel_api_token, cpanel_api_port,
                      main_email, main_email_password, smtp_host, smtp_port, smtp_use_tls,
                      imap_host, imap_port, imap_use_ssl, sender_name))
                print("Updated existing email settings")
            else:
                cursor.execute("""
                    INSERT INTO email_settings 
                    (cpanel_user, cpanel_domain, cpanel_api_token, cpanel_api_port,
                     main_email, main_email_password, smtp_host, smtp_port, smtp_use_tls,
                     imap_host, imap_port, imap_use_ssl, sender_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (cpanel_user, cpanel_domain, cpanel_api_token, cpanel_api_port,
                      main_email, main_email_password, smtp_host, smtp_port, smtp_use_tls,
                      imap_host, imap_port, imap_use_ssl, sender_name))
                print("Inserted new email settings")
            connection.commit()
            return True
    except Exception as e:
        import traceback
        print(f"Error saving email settings: {e}")
        print(traceback.format_exc())
        return False
    finally:
        if connection:
            connection.close()

# Connection pool for persistent connections
_cpanel_sessions = {}
_email_connections = {}

def get_cpanel_session(api_token, domain, user, api_port):
    """Get or create a persistent cPanel API session"""
    session_key = f"{user}@{domain}:{api_port}"
    
    if session_key not in _cpanel_sessions:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        session = requests.Session()
        session.headers.update({
            'Authorization': f'cpanel {user}:{api_token}'
        })
        session.verify = False
        _cpanel_sessions[session_key] = {
            'session': session,
            'last_used': datetime.now(),
            'api_token': api_token
        }
    
    # Update last used time
    _cpanel_sessions[session_key]['last_used'] = datetime.now()
    return _cpanel_sessions[session_key]['session']

def close_cpanel_session(api_token, domain, user, api_port):
    """Close a cPanel API session"""
    session_key = f"{user}@{domain}:{api_port}"
    if session_key in _cpanel_sessions:
        _cpanel_sessions[session_key]['session'].close()
        del _cpanel_sessions[session_key]

def get_email_connection(email_address, password, smtp_host, smtp_port, use_tls, connection_type='smtp'):
    """Get or create a persistent email connection (SMTP or IMAP)"""
    # Ensure all parameters are the correct type
    email_address = str(email_address) if email_address else ''
    password = str(password) if password else ''
    smtp_host = str(smtp_host) if smtp_host else ''
    smtp_port = int(smtp_port) if smtp_port else (587 if connection_type == 'smtp' else 993)
    use_tls = bool(use_tls) if use_tls is not None else True
    
    conn_key = f"{connection_type}:{email_address}@{smtp_host}:{smtp_port}"
    
    if conn_key not in _email_connections:
        if connection_type == 'smtp':
            if use_tls:
                server = smtplib.SMTP(smtp_host, smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port)
            server.login(email_address, password)
            _email_connections[conn_key] = {
                'connection': server,
                'type': 'smtp',
                'last_used': datetime.now()
            }
        elif connection_type == 'imap':
            if use_tls:
                mail = imaplib.IMAP4_SSL(smtp_host, smtp_port)
            else:
                mail = imaplib.IMAP4(smtp_host, smtp_port)
            mail.login(email_address, password)
            _email_connections[conn_key] = {
                'connection': mail,
                'type': 'imap',
                'last_used': datetime.now()
            }
    else:
        # Update last used time
        _email_connections[conn_key]['last_used'] = datetime.now()
        # For IMAP connections, verify the connection is still alive
        if connection_type == 'imap':
            conn = _email_connections[conn_key]['connection']
            try:
                # Test connection with NOOP command
                conn.noop()
            except Exception:
                # Connection is dead, recreate it
                try:
                    try:
                        conn.close()
                    except:
                        pass
                    try:
                        conn.logout()
                    except:
                        pass
                except:
                    pass
                # Remove dead connection and create new one
                del _email_connections[conn_key]
                if use_tls:
                    mail = imaplib.IMAP4_SSL(smtp_host, smtp_port)
                else:
                    mail = imaplib.IMAP4(smtp_host, smtp_port)
                mail.login(email_address, password)
                _email_connections[conn_key] = {
                    'connection': mail,
                    'type': 'imap',
                    'last_used': datetime.now()
                }
    
    return _email_connections[conn_key]['connection']

def close_email_connection(email_address, smtp_host, smtp_port, connection_type='smtp'):
    """Close an email connection"""
    conn_key = f"{connection_type}:{email_address}@{smtp_host}:{smtp_port}"
    if conn_key in _email_connections:
        conn = _email_connections[conn_key]['connection']
        conn_type = _email_connections[conn_key]['type']
        
        try:
            if conn_type == 'smtp':
                conn.quit()
            elif conn_type == 'imap':
                try:
                    # Try to close any selected mailbox first
                    conn.close()
                except:
                    pass  # Ignore errors when closing mailbox
                try:
                    conn.logout()
                except:
                    pass  # Ignore errors when logging out
        except Exception as e:
            # Log but don't raise - connection cleanup should be best effort
            print(f"Error closing {connection_type} connection: {e}")
        
        del _email_connections[conn_key]

def cleanup_idle_connections(max_idle_minutes=30):
    """Clean up idle connections"""
    from datetime import timedelta
    now = datetime.now()
    idle_threshold = timedelta(minutes=max_idle_minutes)
    
    # Clean cPanel sessions
    to_remove = []
    for key, session_data in _cpanel_sessions.items():
        if now - session_data['last_used'] > idle_threshold:
            to_remove.append(key)
    
    for key in to_remove:
        _cpanel_sessions[key]['session'].close()
        del _cpanel_sessions[key]
    
    # Clean email connections
    to_remove = []
    for key, conn_data in _email_connections.items():
        if now - conn_data['last_used'] > idle_threshold:
            to_remove.append(key)
    
    for key in to_remove:
        conn = _email_connections[key]['connection']
        conn_type = _email_connections[key]['type']
        try:
            if conn_type == 'smtp':
                conn.quit()
            elif conn_type == 'imap':
                conn.close()
                conn.logout()
        except:
            pass
        del _email_connections[key]

def cpanel_api_call(api_token, domain, user, api_port, api_module, api_function, **kwargs):
    """Make a cPanel API call using persistent connection"""
    try:
        session = get_cpanel_session(api_token, domain, user, api_port)
        url = f"https://{domain}:{api_port}/execute/{api_module}/{api_function}"
        response = session.get(url, params=kwargs, timeout=30)
        return response.json()
    except Exception as e:
        print(f"cPanel API error: {e}")
        # Try to recreate session on error
        close_cpanel_session(api_token, domain, user, api_port)
        return {'error': str(e), 'status': 0}

def create_sub_email(api_token, domain, user, api_port, email_address, password, quota=250):
    """Create a sub-email account via cPanel API"""
    try:
        result = cpanel_api_call(
            api_token, domain, user, api_port,
            'Email', 'add_pop',
            email=email_address,
            password=password,
            quota=quota
        )
        return result
    except Exception as e:
        return {'error': str(e), 'status': 0}

def list_email_accounts(api_token, domain, user, api_port):
    """List all email accounts via cPanel API"""
    try:
        result = cpanel_api_call(
            api_token, domain, user, api_port,
            'Email', 'list_pops'
        )
        return result
    except Exception as e:
        return {'error': str(e), 'status': 0}

def delete_email_account(api_token, domain, user, api_port, email_address):
    """Delete an email account via cPanel API"""
    try:
        result = cpanel_api_call(
            api_token, domain, user, api_port,
            'Email', 'delete_pop',
            email=email_address
        )
        return result
    except Exception as e:
        return {'error': str(e), 'status': 0}

def get_email_accounts_from_db():
    """Get all email accounts from database"""
    try:
        connection = get_db_connection()
        if not connection:
            return []
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT ea.*, e.full_name as created_by_name
                FROM email_accounts ea
                LEFT JOIN employees e ON ea.created_by_id = e.id
                ORDER BY ea.is_main DESC, ea.created_at DESC
            """)
            accounts = cursor.fetchall()
            return accounts
    except Exception as e:
        print(f"Error getting email accounts: {e}")
        return []
    finally:
        if connection:
            connection.close()

def save_email_account_to_db(email_address, email_password, display_name, is_main, created_by_id):
    """Save email account to database"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
        with connection.cursor() as cursor:
            # If this is main email, unset other main emails
            if is_main:
                cursor.execute("UPDATE email_accounts SET is_main = FALSE")
            
            cursor.execute("""
                INSERT INTO email_accounts (email_address, email_password, display_name, is_main, created_by_id)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    email_password = VALUES(email_password),
                    display_name = VALUES(display_name),
                    is_main = VALUES(is_main),
                    updated_at = CURRENT_TIMESTAMP
            """, (email_address, email_password, display_name, is_main, created_by_id))
            connection.commit()
            return True
    except Exception as e:
        print(f"Error saving email account: {e}")
        return False
    finally:
        if connection:
            connection.close()

def send_email_via_smtp(from_email, from_password, to_email, subject, body, 
                        smtp_host, smtp_port, use_tls, html_body=None, sender_name=None):
    """Send email via SMTP using persistent connection"""
    try:
        # Ensure all string parameters are actually strings (not ints) to prevent encode errors
        from_email = str(from_email) if from_email else ''
        from_password = str(from_password) if from_password else ''
        to_email = str(to_email) if to_email else ''
        subject = str(subject) if subject else ''
        body = str(body) if body else ''
        smtp_host = str(smtp_host) if smtp_host else ''
        smtp_port = int(smtp_port) if smtp_port else 587
        use_tls = bool(use_tls) if use_tls is not None else True
        html_body = str(html_body) if html_body else None
        sender_name = str(sender_name) if sender_name else None
        
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{sender_name} <{from_email}>" if sender_name else from_email
        msg['To'] = to_email
        msg['Subject'] = subject
        
        # Add plain text and HTML parts
        if html_body:
            part1 = MIMEText(body, 'plain')
            part2 = MIMEText(html_body, 'html')
            msg.attach(part1)
            msg.attach(part2)
        else:
            msg.attach(MIMEText(body, 'plain'))
        
        # Use persistent connection
        server = get_email_connection(from_email, from_password, smtp_host, smtp_port, use_tls, 'smtp')
        server.send_message(msg)
        # Don't quit - keep connection alive
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        import traceback
        print(traceback.format_exc())
        # Close connection on error and try to recreate
        close_email_connection(from_email, smtp_host, smtp_port, 'smtp')
        return False

def fetch_emails_from_imap(email_address, password, imap_host, imap_port, use_ssl, limit=50):
    """Fetch emails from IMAP server using persistent connection (not stored in DB, fetched on trigger)"""
    mail = None
    try:
        # Try to use persistent connection, but recreate if there's an issue
        try:
            mail = get_email_connection(email_address, password, imap_host, imap_port, use_ssl, 'imap')
            # Test the connection by selecting INBOX
            try:
                # Try to check connection state first
                try:
                    mail.noop()  # Send a NOOP command to check if connection is alive
                except:
                    pass  # If noop fails, connection is likely dead, will be caught below
                
                status, data = mail.select('INBOX')
                if status != 'OK':
                    raise Exception("Failed to select INBOX")
            except (imaplib.IMAP4.error, Exception) as e:
                # Connection might be bad, close it and recreate
                error_msg = str(e)
                # Clean up error message if it contains email data
                if 'Return-Path' in error_msg or 'unexpected response' in error_msg.lower():
                    error_msg = "Connection in bad state (received unexpected data)"
                print(f"Connection test failed, recreating: {error_msg}")
                close_email_connection(email_address, imap_host, imap_port, 'imap')
                # Create fresh connection
                if use_ssl:
                    mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                else:
                    mail = imaplib.IMAP4(imap_host, imap_port)
                mail.login(email_address, password)
                status, data = mail.select('INBOX')
                if status != 'OK':
                    raise Exception("Failed to select INBOX after reconnect")
        except Exception as e:
            # If persistent connection fails, create a new one
            error_msg = str(e)
            if 'Return-Path' in error_msg or 'unexpected response' in error_msg.lower():
                error_msg = "Connection in bad state (received unexpected data)"
            print(f"Using persistent connection failed, creating new: {error_msg}")
            close_email_connection(email_address, imap_host, imap_port, 'imap')
            try:
                if use_ssl:
                    mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                else:
                    mail = imaplib.IMAP4(imap_host, imap_port)
                mail.login(email_address, password)
                status, data = mail.select('INBOX')
                if status != 'OK':
                    raise Exception("Failed to select INBOX")
            except (imaplib.IMAP4.error, ConnectionResetError, OSError) as auth_error:
                error_msg = str(auth_error)
                if 'AUTHENTICATIONFAILED' in error_msg or 'Authentication failed' in error_msg:
                    print(f"Authentication failed: {error_msg}")
                    raise Exception(f"Email authentication failed. Please check email credentials.")
                elif 'forcibly closed' in error_msg or 'ConnectionResetError' in str(type(auth_error)):
                    print(f"Connection reset by server: {error_msg}")
                    raise Exception(f"Connection to email server was reset. Please try again later.")
                else:
                    print(f"Connection error: {error_msg}")
                    raise Exception(f"Failed to connect to email server: {error_msg}")
        
        # Search for all emails - use a more robust approach
        try:
            status, messages = mail.search(None, 'ALL')
            if status != 'OK':
                raise Exception(f"Search failed with status: {status}")
            
            if not messages or not messages[0]:
                return []  # No emails found
            
            email_ids = messages[0].split()
        except Exception as e:
            # If search fails, try using a different approach
            print(f"Search with 'ALL' failed: {e}, trying alternative method")
            try:
                # Try searching for recent emails
                status, messages = mail.search(None, 'RECENT')
                if status != 'OK' or not messages or not messages[0]:
                    # If that fails, try to get emails by sequence number
                    status, data = mail.status('INBOX', '(MESSAGES)')
                    if status == 'OK' and data:
                        # Get message count
                        msg_count = int(data[0].split()[2].strip(b')').decode())
                        if msg_count == 0:
                            return []
                        # Fetch emails by sequence number
                        email_ids = [str(i).encode() for i in range(1, min(msg_count + 1, limit + 1))]
                    else:
                        return []
                else:
                    email_ids = messages[0].split()
            except Exception as e2:
                print(f"Alternative search also failed: {e2}")
                return []
        
        # Get the most recent emails (limit)
        if not email_ids:
            return []
        
        email_ids = email_ids[-limit:] if len(email_ids) > limit else email_ids
        
        emails = []
        connection_recreated = False
        for email_id in reversed(email_ids):
            # Check connection state before fetching
            if connection_recreated:
                # Verify connection is still good
                try:
                    mail.noop()
                except:
                    # Connection is bad again, try to reconnect
                    try:
                        close_email_connection(email_address, imap_host, imap_port, 'imap')
                        if use_ssl:
                            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                        else:
                            mail = imaplib.IMAP4(imap_host, imap_port)
                        mail.login(email_address, password)
                        status, data = mail.select('INBOX')
                        if status != 'OK':
                            print("Failed to maintain connection, stopping fetch")
                            break
                    except Exception as reconnect_err:
                        print(f"Failed to maintain connection: {reconnect_err}")
                        break
            
            try:
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status == 'OK':
                    email_body = msg_data[0][1]
                    email_message = email.message_from_bytes(email_body)
                    
                    # Decode subject
                    subject_header = email_message.get('Subject', '')
                    if subject_header:
                        decoded_subject = decode_header(subject_header)
                        if decoded_subject and decoded_subject[0][0]:
                            subject = decoded_subject[0][0]
                            if isinstance(subject, bytes):
                                subject = subject.decode(decoded_subject[0][1] or 'utf-8')
                        else:
                            subject = ''
                    else:
                        subject = '(No Subject)'
                    
                    # Get sender
                    sender = email_message.get('From', 'Unknown')
                    
                    # Get recipient
                    recipient = email_message.get('To', '')
                    
                    # Get date
                    date_str = email_message.get('Date', '')
                    
                    # Get body
                    body = ""
                    if email_message.is_multipart():
                        for part in email_message.walk():
                            content_type = part.get_content_type()
                            if content_type == "text/plain":
                                try:
                                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                except:
                                    body = str(part.get_payload())
                                break
                    else:
                        try:
                            body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            body = str(email_message.get_payload())
                    
                    # Get full body (not truncated)
                    full_body = body if body else ''
                    
                    emails.append({
                        'id': email_id.decode() if isinstance(email_id, bytes) else str(email_id),
                        'subject': subject,
                        'from': sender,
                        'to': recipient,
                        'date': date_str,
                        'body': full_body  # Full body for conversation view
                    })
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, ConnectionResetError, OSError) as e:
                # Connection error during fetch - try to recreate connection once
                error_msg = str(e)
                # Check for various connection state errors
                is_connection_error = (
                    'Logging out' in error_msg or 
                    'socket error: EOF' in error_msg or 
                    'forcibly closed' in error_msg or
                    'illegal in state LOGOUT' in error_msg or
                    'illegal in state' in error_msg or
                    'LOGOUT' in error_msg and 'state' in error_msg
                )
                
                if is_connection_error:
                    if not connection_recreated:
                        print(f"Connection lost during fetch (state error), attempting to reconnect...")
                        try:
                            # Close the bad connection
                            close_email_connection(email_address, imap_host, imap_port, 'imap')
                            # Recreate connection
                            if use_ssl:
                                mail = imaplib.IMAP4_SSL(imap_host, imap_port)
                            else:
                                mail = imaplib.IMAP4(imap_host, imap_port)
                            mail.login(email_address, password)
                            status, data = mail.select('INBOX')
                            if status == 'OK':
                                connection_recreated = True
                                print("Connection reestablished, continuing to fetch emails...")
                                # Retry the fetch for this email
                                try:
                                    status, msg_data = mail.fetch(email_id, '(RFC822)')
                                    if status == 'OK':
                                        email_body = msg_data[0][1]
                                        email_message = email.message_from_bytes(email_body)
                                        
                                        # Decode subject
                                        subject_header = email_message.get('Subject', '')
                                        if subject_header:
                                            decoded_subject = decode_header(subject_header)
                                            if decoded_subject and decoded_subject[0][0]:
                                                subject = decoded_subject[0][0]
                                                if isinstance(subject, bytes):
                                                    subject = subject.decode(decoded_subject[0][1] or 'utf-8')
                                            else:
                                                subject = ''
                                        else:
                                            subject = '(No Subject)'
                                        
                                        sender = email_message.get('From', 'Unknown')
                                        recipient = email_message.get('To', '')
                                        date_str = email_message.get('Date', '')
                                        
                                        body = ""
                                        if email_message.is_multipart():
                                            for part in email_message.walk():
                                                content_type = part.get_content_type()
                                                if content_type == "text/plain":
                                                    try:
                                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                                    except:
                                                        body = str(part.get_payload())
                                                    break
                                        else:
                                            try:
                                                body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
                                            except:
                                                body = str(email_message.get_payload())
                                        
                                        full_body = body if body else ''
                                        
                                        emails.append({
                                            'id': email_id.decode() if isinstance(email_id, bytes) else str(email_id),
                                            'subject': subject,
                                            'from': sender,
                                            'to': recipient,
                                            'date': date_str,
                                            'body': full_body
                                        })
                                        continue  # Successfully fetched, continue to next email
                                except Exception as retry_error:
                                    # Skip this email and continue with next
                                    continue
                            else:
                                print("Failed to select INBOX after reconnect")
                                # Return what we have so far
                                break
                        except Exception as reconnect_error:
                            error_msg = str(reconnect_error)
                            if 'AUTHENTICATIONFAILED' in error_msg or 'Authentication failed' in error_msg:
                                print(f"Authentication failed when reconnecting: {error_msg}")
                            else:
                                print(f"Failed to reconnect: {error_msg}")
                            # Return what we have so far
                            break
                    else:
                        # Already recreated once, connection is still bad - stop trying
                        print(f"Connection still in bad state after reconnect, stopping fetch operation")
                        break
                else:
                    # Other error, skip this email silently
                    continue
            except Exception as e:
                # Other unexpected error, skip this email
                print(f"Unexpected error fetching email {email_id}: {e}")
                continue
        
        # Don't close - keep connection alive (only if using persistent connection)
        # If we created a new temporary connection, close it
        conn_key = f"imap:{email_address}@{imap_host}:{imap_port}"
        if conn_key not in _email_connections:
            try:
                mail.close()
                mail.logout()
            except:
                pass
        
        return emails
    except Exception as e:
        print(f"Error fetching emails: {e}")
        import traceback
        print(traceback.format_exc())
        # Close connection on error
        try:
            close_email_connection(email_address, imap_host, imap_port, 'imap')
        except:
            pass
        return []

@app.route('/communication_settings')
def communication_settings():
    """Communication Settings page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    email_settings = get_email_settings()
    email_accounts = get_email_accounts_from_db()
    whatsapp_settings = get_whatsapp_settings()
    sms_settings = get_sms_settings()
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    # Fine-grained permission: manage communication channels (email, SMS, WhatsApp)
    connection = get_db_connection()
    if connection:
        deny = enforce_permission(connection, 'system_manage_channels')
        connection.close()
        if deny:
            return deny
    
    return render_template('communication_settings.html', 
                         company_settings=company_settings,
                         email_settings=email_settings,
                         email_accounts=email_accounts,
                         whatsapp_settings=whatsapp_settings,
                         sms_settings=sms_settings)

# ==================== EMAIL API ROUTES ====================

@app.route('/api/email/settings/save', methods=['POST'])
def api_save_email_settings():
    """Save email settings"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    connection = get_db_connection()
    if connection:
        # Fine-grained permission: manage communication channels (email settings)
        deny = enforce_permission(connection, 'system_manage_channels')
        connection.close()
        if deny:
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
    
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['cpanel_user', 'cpanel_domain', 'cpanel_api_token', 'main_email', 'main_email_password']
        missing_fields = [field for field in required_fields if not data.get(field)]
        
        if missing_fields:
            return jsonify({'success': False, 'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400
        
        # Convert boolean values properly
        smtp_use_tls = data.get('smtp_use_tls', True)
        if isinstance(smtp_use_tls, str):
            smtp_use_tls = smtp_use_tls.lower() == 'true'
        
        imap_use_ssl = data.get('imap_use_ssl', True)
        if isinstance(imap_use_ssl, str):
            imap_use_ssl = imap_use_ssl.lower() == 'true'
        
        success = save_email_settings(
            cpanel_user=data.get('cpanel_user'),
            cpanel_domain=data.get('cpanel_domain'),
            cpanel_api_token=data.get('cpanel_api_token'),
            cpanel_api_port=int(data.get('cpanel_api_port', 2083)),
            main_email=data.get('main_email'),
            main_email_password=data.get('main_email_password'),
            smtp_host=data.get('smtp_host', 'mail.baunilawgroup.com'),
            smtp_port=int(data.get('smtp_port', 587)),
            smtp_use_tls=smtp_use_tls,
            imap_host=data.get('imap_host', 'mail.baunilawgroup.com'),
            imap_port=int(data.get('imap_port', 993)),
            imap_use_ssl=imap_use_ssl,
            sender_name=data.get('sender_name', 'BAUNI LAW GROUP')
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Email settings saved successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to save settings. Check server logs for details.'}), 500
    except Exception as e:
        import traceback
        print(f"Error in api_save_email_settings: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/test-connection', methods=['POST'])
def api_test_email_connection():
    """Test email connection"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        email_settings = get_email_settings()
        
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        # Test SMTP connection
        try:
            # Handle boolean values (may be 0/1 from database)
            use_tls = bool(email_settings.get('smtp_use_tls', True))
            smtp_host = email_settings.get('smtp_host', 'mail.baunilawgroup.com')
            smtp_port = int(email_settings.get('smtp_port', 587))
            main_email = email_settings.get('main_email', '')
            main_password = email_settings.get('main_email_password', '')
            
            if not main_email or not main_password:
                return jsonify({'success': False, 'error': 'Main email and password must be configured'}), 400
            
            if use_tls:
                server = smtplib.SMTP(smtp_host, smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port)
            
            server.login(main_email, main_password)
            server.quit()
            return jsonify({'success': True, 'message': 'SMTP connection successful'})
        except Exception as e:
            return jsonify({'success': False, 'error': f'SMTP connection failed: {str(e)}'}), 400
    except Exception as e:
        import traceback
        print(f"Error in test-connection: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/sub-email/create', methods=['POST'])
def api_create_sub_email():
    """Create a sub-email account"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        email_settings = get_email_settings()
        
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        email_address = data.get('email_address')
        password = data.get('password')
        display_name = data.get('display_name', '')
        is_main = data.get('is_main', False)
        
        if not email_address or not password:
            return jsonify({'success': False, 'error': 'Email address and password are required'}), 400
        
        # Create email via cPanel API
        result = create_sub_email(
            email_settings['cpanel_api_token'],
            email_settings['cpanel_domain'],
            email_settings['cpanel_user'],
            email_settings['cpanel_api_port'],
            email_address,
            password
        )
        
        if result.get('status') == 1:
            # Save to database
            save_email_account_to_db(
                email_address, password, display_name, is_main, session.get('employee_id')
            )
            return jsonify({'success': True, 'message': 'Sub-email created successfully'})
        else:
            error_msg = result.get('errors', [{}])[0].get('message', 'Unknown error') if result.get('errors') else 'Failed to create email'
            return jsonify({'success': False, 'error': error_msg}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/sub-email/list', methods=['GET'])
def api_list_sub_emails():
    """List all sub-email accounts from database and cPanel"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        email_settings = get_email_settings()
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        # Fetch from database
        db_accounts = get_email_accounts_from_db()
        
        # Fetch from cPanel API
        cpanel_accounts = []
        try:
            result = list_email_accounts(
                email_settings['cpanel_api_token'],
                email_settings['cpanel_domain'],
                email_settings['cpanel_user'],
                email_settings['cpanel_api_port']
            )
            
            if result.get('status') == 1 and 'data' in result:
                cpanel_accounts = result['data']
        except Exception as e:
            print(f"Error fetching from cPanel: {e}")
        
        # Merge and sync accounts
        db_emails = {acc['email_address']: acc for acc in db_accounts}
        cpanel_emails = {}
        
        for account in cpanel_accounts:
            email_addr = account.get('email', '')
            if email_addr:
                cpanel_emails[email_addr] = {
                    'email_address': email_addr,
                    'domain': account.get('domain', ''),
                    'disk_used': account.get('humandiskused', '0 MB'),
                    'disk_quota': account.get('humandiskquota', '250 MB'),
                    'is_cpanel': True
                }
                
                # If not in DB, add it
                if email_addr not in db_emails:
                    # Save to database without password (we don't have it from cPanel)
                    save_email_account_to_db(
                        email_addr, '', '', False, session.get('employee_id')
                    )
        
        # Combine results
        all_accounts = []
        for email_addr, account in db_emails.items():
            account_dict = dict(account)
            if email_addr in cpanel_emails:
                account_dict.update(cpanel_emails[email_addr])
            all_accounts.append(account_dict)
        
        # Add cPanel-only accounts
        for email_addr, account in cpanel_emails.items():
            if email_addr not in db_emails:
                all_accounts.append(account)
        
        return jsonify({'success': True, 'accounts': all_accounts})
    except Exception as e:
        import traceback
        print(f"Error listing emails: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/sync-cpanel', methods=['POST'])
def api_sync_cpanel_emails():
    """Sync email accounts from cPanel to database"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        email_settings = get_email_settings()
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        # Fetch from cPanel
        result = list_email_accounts(
            email_settings['cpanel_api_token'],
            email_settings['cpanel_domain'],
            email_settings['cpanel_user'],
            email_settings['cpanel_api_port']
        )
        
        if result.get('status') != 1:
            error_msg = result.get('errors', [{}])[0].get('message', 'Unknown error') if result.get('errors') else 'Failed to fetch from cPanel'
            return jsonify({'success': False, 'error': error_msg}), 400
        
        synced_count = 0
        if 'data' in result:
            for account in result['data']:
                email_addr = account.get('email', '')
                if email_addr:
                    # Check if exists in DB
                    connection = get_db_connection()
                    if connection:
                        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                            cursor.execute("SELECT id FROM email_accounts WHERE email_address = %s", (email_addr,))
                            exists = cursor.fetchone()
                            
                            if not exists:
                                # Add to database
                                save_email_account_to_db(
                                    email_addr, '', account.get('domain', ''), False, session.get('employee_id')
                                )
                                synced_count += 1
                        connection.close()
        
        return jsonify({'success': True, 'message': f'Synced {synced_count} email accounts from cPanel', 'synced_count': synced_count})
    except Exception as e:
        import traceback
        print(f"Error syncing cPanel emails: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/sub-email/delete', methods=['POST'])
def api_delete_sub_email():
    """Delete a sub-email account"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        email_address = data.get('email_address')
        
        if not email_address:
            return jsonify({'success': False, 'error': 'Email address is required'}), 400
        
        email_settings = get_email_settings()
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        # Delete via cPanel API
        result = delete_email_account(
            email_settings['cpanel_api_token'],
            email_settings['cpanel_domain'],
            email_settings['cpanel_user'],
            email_settings['cpanel_api_port'],
            email_address
        )
        
        if result.get('status') == 1:
            # Delete from database
            connection = get_db_connection()
            if connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM email_accounts WHERE email_address = %s", (email_address,))
                    connection.commit()
                connection.close()
            return jsonify({'success': True, 'message': 'Email account deleted successfully'})
        else:
            error_msg = result.get('errors', [{}])[0].get('message', 'Unknown error') if result.get('errors') else 'Failed to delete email'
            return jsonify({'success': False, 'error': error_msg}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/send', methods=['POST'])
def api_send_email():
    """Send email through web app"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        email_settings = get_email_settings()
        
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        from_email = data.get('from_email', email_settings['main_email'])
        to_email = data.get('to_email')
        subject = data.get('subject')
        body = data.get('body')
        html_body = data.get('html_body')
        
        if not to_email or not subject or not body:
            return jsonify({'success': False, 'error': 'To, subject, and body are required'}), 400
        
        # Get password for the from_email
        connection = get_db_connection()
        password = email_settings['main_email_password']
        if connection:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT email_password FROM email_accounts WHERE email_address = %s", (from_email,))
                account = cursor.fetchone()
                if account and account['email_password']:
                    password = account['email_password']
            connection.close()
        
        # Ensure port is an integer
        smtp_port = int(email_settings['smtp_port']) if email_settings.get('smtp_port') else 587
        smtp_use_tls = bool(email_settings.get('smtp_use_tls', True))
        sender_name = email_settings.get('sender_name')
        
        success = send_email_via_smtp(
            from_email, password, to_email, subject, body,
            email_settings['smtp_host'], smtp_port,
            smtp_use_tls, html_body, sender_name
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Email sent successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to send email'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/email/fetch', methods=['POST'])
def api_fetch_emails():
    """Fetch emails from server (not stored in DB)"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        email_settings = get_email_settings()
        
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        email_address = data.get('email_address', email_settings['main_email'])
        limit = int(data.get('limit', 50))
        
        # Get password for the email
        connection = get_db_connection()
        password = email_settings['main_email_password']
        if connection:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT email_password FROM email_accounts WHERE email_address = %s", (email_address,))
                account = cursor.fetchone()
                if account and account['email_password']:
                    password = account['email_password']
            connection.close()
        
        emails = fetch_emails_from_imap(
            email_address, password,
            email_settings['imap_host'], email_settings['imap_port'],
            email_settings['imap_use_ssl'], limit
        )
        
        return jsonify({'success': True, 'emails': emails})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/employee/update-email', methods=['POST'])
def api_update_employee_email():
    """Update employee work email"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    try:
        data = request.get_json()
        employee_id = data.get('employee_id')
        work_email = data.get('work_email', '').strip()
        
        if not employee_id:
            return jsonify({'success': False, 'error': 'Employee ID is required'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        
        try:
            with connection.cursor() as cursor:
                # Update employee work_email
                cursor.execute("""
                    UPDATE employees 
                    SET work_email = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (work_email if work_email else None, employee_id))
                connection.commit()
                
                return jsonify({'success': True, 'message': 'Employee email updated successfully'})
        except Exception as e:
            connection.rollback()
            print(f"Error updating employee email: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
        finally:
            connection.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/employee/communications', methods=['GET'])
def api_get_employee_communications():
    """Get employee communications (people they've been communicating with)"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    employee_id = request.args.get('employee_id')
    if not employee_id:
        return jsonify({'success': False, 'error': 'Employee ID is required'}), 400
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT work_email FROM employees WHERE id = %s", (employee_id,))
            employee = cursor.fetchone()
            
            if not employee or not employee.get('work_email'):
                return jsonify({'success': False, 'error': 'Employee not found or no work email'}), 404
            
            # Get password for the email
            email_settings = get_email_settings()
            if not email_settings:
                return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
            
            password = email_settings['main_email_password']
            cursor.execute("SELECT email_password FROM email_accounts WHERE email_address = %s", (employee['work_email'],))
            account = cursor.fetchone()
            if account and account.get('email_password'):
                password = account['email_password']
            
            connection.close()
            
            # Fetch emails
            emails = fetch_emails_from_imap(
                employee['work_email'], password,
                email_settings['imap_host'], email_settings['imap_port'],
                email_settings['imap_use_ssl'], 100
            )
            
            # Group by contact
            contacts = {}
            for email in emails:
                from_addr = email.get('from', 'Unknown')
                contact_key = from_addr.lower()
                
                if contact_key not in contacts:
                    contacts[contact_key] = {
                        'email': from_addr,
                        'count': 0,
                        'last_date': email.get('date', ''),
                        'last_subject': email.get('subject', 'No Subject')
                    }
                contacts[contact_key]['count'] += 1
                if email.get('date', '') > contacts[contact_key]['last_date']:
                    contacts[contact_key]['last_date'] = email.get('date', '')
                    contacts[contact_key]['last_subject'] = email.get('subject', 'No Subject')
            
            return jsonify({
                'success': True,
                'contacts': list(contacts.values()),
                'total_emails': len(emails)
            })
    except Exception as e:
        import traceback
        print(f"Error getting employee communications: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/employee/create-work-email', methods=['POST'])
def api_create_work_email():
    """Create work email in cPanel and link to employee"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    
    try:
        data = request.get_json()
        employee_id = data.get('employee_id')
        email_address = data.get('email_address', '').strip()
        password = data.get('password', '')
        personal_email = data.get('personal_email', '').strip()
        
        if not employee_id or not email_address or not password:
            return jsonify({'success': False, 'error': 'Employee ID, email address, and password are required'}), 400
        
        email_settings = get_email_settings()
        if not email_settings:
            return jsonify({'success': False, 'error': 'Email settings not configured'}), 400
        
        # Get employee details
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT full_name FROM employees WHERE id = %s", (employee_id,))
                employee = cursor.fetchone()
                
                if not employee:
                    return jsonify({'success': False, 'error': 'Employee not found'}), 404
            
            # Create email in cPanel
            result = create_sub_email(
                email_settings['cpanel_api_token'],
                email_settings['cpanel_domain'],
                email_settings['cpanel_user'],
                email_settings['cpanel_api_port'],
                email_address,
                password
            )
            
            if result.get('status') == 1:
                # Save to database
                save_email_account_to_db(
                    email_address, password, employee['full_name'], False, session.get('employee_id')
                )
                
                # Update employee work_email
                with connection.cursor() as cursor:
                    cursor.execute("""
                        UPDATE employees 
                        SET work_email = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (email_address, employee_id))
                    connection.commit()
                
                # TODO: Set up email forwarding to personal email if provided
                # This would require additional cPanel API calls to set up forwarding
                if personal_email:
                    print(f"Note: Email forwarding to {personal_email} should be configured in cPanel")
                
                connection.close()
                return jsonify({'success': True, 'message': 'Work email created and linked successfully'})
            else:
                error_msg = result.get('errors', [{}])[0].get('message', 'Unknown error') if result.get('errors') else 'Failed to create email'
                connection.close()
                return jsonify({'success': False, 'error': error_msg}), 400
        except Exception as e:
            if connection:
                connection.rollback()
                connection.close()
            print(f"Error creating work email: {e}")
            import traceback
            print(traceback.format_exc())
            return jsonify({'success': False, 'error': str(e)}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/compliance_audit')
def compliance_audit():
    """Compliance & Audit page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('compliance_audit.html', company_settings=company_settings)

@app.route('/system_reports_analytics')
def system_reports_analytics():
    """System Reports & Analytics page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('system_reports_analytics.html', company_settings=company_settings)

@app.route('/data_backup_recovery')
def data_backup_recovery():
    """Data Backup & Recovery page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('data_backup_recovery.html', company_settings=company_settings)

@app.route('/access_control_security')
def access_control_security():
    """Access Control & Security page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('access_control_security.html', company_settings=company_settings)

@app.route('/system_health_module')
def system_health_module():
    """System Health Module page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('system_health_module.html', company_settings=company_settings)

@app.route('/system_settings')
def system_settings():
    """System Settings page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    # Fine-grained permission: view system settings
    connection = get_db_connection()
    if connection:
        deny = enforce_permission(connection, 'system_manage_settings')
        connection.close()
        if deny:
            return deny
    
    return render_template('system_settings.html', company_settings=company_settings)

@app.route('/system_settings/update', methods=['POST'])
def update_company_settings():
    """Update company settings from the system settings form"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
    if user_role not in allowed_roles and original_role != 'IT Support':
        flash('You do not have permission to update company settings', 'error')
        return redirect(url_for('dashboard'))
    connection = get_db_connection()
    if not connection:
        flash('Database error.', 'error')
        return redirect(url_for('system_settings'))

    # Fine-grained permission: update system settings
    deny = enforce_permission(connection, 'system_manage_settings', redirect_endpoint='system_settings')
    if deny:
        return deny
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            def _normalize_hex_color(value, fallback):
                """Normalize hex colors to #RRGGBB."""
                if value is None:
                    return fallback
                text = str(value).strip()
                if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
                    return text
                short = re.fullmatch(r"#([0-9a-fA-F]{3})", text)
                if short:
                    return "#" + "".join(ch * 2 for ch in short.group(1))
                return fallback

            cursor.execute("SELECT id FROM company_settings ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if not row:
                flash('No company settings record found.', 'error')
                return redirect(url_for('system_settings'))
            pk = row['id']
            upload_folder = app.config['UPLOAD_FOLDER']
            def save_upload(file_key, prefix):
                f = request.files.get(file_key)
                if not f or not f.filename:
                    return None
                filename = secure_filename(f.filename)
                if not filename:
                    return None
                ext = os.path.splitext(filename)[1] or '.bin'
                unique = f"{prefix}_{pk}_{secrets.token_hex(4)}{ext}"
                path = os.path.join(upload_folder, unique)
                f.save(path)
                return unique
            updates = {}
            text_fields = [
                'company_name', 'company_tagline', 'registration_number', 'tax_pin_vat_number', 'year_established',
                'email', 'contact_number', 'whatsapp_number', 'alternative_phone', 'customer_support_email',
                'country', 'county_state', 'city_town', 'street_building', 'office_number_floor', 'postal_address', 'postal_code',
                'opening_time', 'closing_time', 'public_holiday_status', 'public_holiday_open_time', 'public_holiday_close_time',
                'website_url', 'fb_link', 'linkedin_link', 'twitter_link', 'instagram_link',
                'law_society_reg_number', 'practicing_certificate_number', 'lead_advocate_name', 'bar_association_membership',
                'document_footer_text', 'currency', 'invoice_prefix', 'payment_terms', 'bank_account_details', 'mobile_payment_mpesa',
                'primary_brand_color', 'secondary_color'
            ]
            for key in text_fields:
                val = request.form.get(key)
                if val is not None:
                    updates[key] = val.strip() if isinstance(val, str) else val
            # Validate and normalize brand colors
            updates['primary_brand_color'] = _normalize_hex_color(
                updates.get('primary_brand_color'),
                '#1E1A4E'
            )
            updates['secondary_color'] = _normalize_hex_color(
                updates.get('secondary_color'),
                '#6C5CE7'
            )
            # Working days: multiple checkboxes stored as comma-separated
            wd = request.form.getlist('working_days')
            if wd is not None:
                updates['working_days'] = ','.join(wd) if wd else ''
            for key in ['send_email_notifications', 'send_sms_notifications', 'whatsapp_notifications', 'court_date_reminders']:
                updates[key] = 1 if request.form.get(key) == 'on' else 0
            for file_key, col, prefix in [
                ('company_logo', 'company_logo', 'company_logo'),
                ('stamp_seal_upload', 'stamp_seal_upload', 'stamp_seal'),
                ('default_signature_documents', 'default_signature_documents', 'signature'),
                ('favicon', 'favicon', 'favicon'),
                ('login_page_background', 'login_page_background', 'login_bg'),
                ('default_letterhead', 'default_letterhead', 'letterhead')
            ]:
                saved = save_upload(file_key, prefix)
                if saved:
                    updates[col] = saved
            if not updates:
                flash('No changes to save.', 'info')
                return redirect(url_for('system_settings'))
            set_clause = ', '.join([f"`{k}` = %s" for k in updates])
            sql = f"UPDATE company_settings SET {set_clause} WHERE id = %s"
            cursor.execute(sql, list(updates.values()) + [pk])
            connection.commit()
            flash('Company settings updated successfully.', 'success')
    except Exception as e:
        print(f"Error updating company settings: {e}")
        flash('An error occurred while saving.', 'error')
    finally:
        connection.close()
    return redirect(url_for('system_settings'))

@app.route('/other_matters')
def other_matters():
    """Other Matters page - shows only matters allocated to the logged-in user"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has permission (IT Support, Firm Administrator, Managing Partner, Clerk, or Associate Advocate)
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')
    
    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    can_approve_matters = (
        user_role in ['Firm Administrator', 'Managing Partner', 'IT Support']
    ) or (original_role == 'IT Support')

    see_all_matters = (user_role == 'IT Support') or (original_role == 'IT Support')

    return render_template('other_matters.html', company_settings=company_settings,
                           user_role=user_role, current_employee_id=session.get('employee_id'),
                           can_approve_matters=can_approve_matters,
                           see_all_matters=see_all_matters)

@app.route('/other_matters/tasks', methods=['GET', 'POST'])
def matter_task_management():
    """Matter task management page."""
    if 'employee_id' not in session:
        return redirect(url_for('login'))

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner', 'Clerk', 'Associate Advocate']
    has_permission = (user_role in allowed_roles) or (original_role == 'IT Support')

    if not has_permission:
        flash('You do not have permission to access this page', 'error')
        return redirect(url_for('dashboard'))

    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))

    current_employee_id = session.get('employee_id')
    matters = []
    matter_tasks = []
    reminder_options = [
        ('10m', '10 minutes before'),
        ('30m', '30 minutes before'),
        ('1h', '1 hour before'),
        ('6h', '6 hours before'),
        ('12h', '12 hours before'),
        ('1d', '1 day before'),
        ('2d', '2 days before'),
        ('7d', '1 week before'),
    ]

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            ensure_task_management_table(cursor, connection)

            if user_role == 'IT Support' or original_role == 'IT Support':
                cursor.execute("""
                    SELECT id, matter_reference_number, matter_title, assigned_employee_name, status
                    FROM matters
                    ORDER BY updated_at DESC
                """)
            else:
                cursor.execute("""
                    SELECT id, matter_reference_number, matter_title, assigned_employee_name, status
                    FROM matters
                    WHERE assigned_employee_id = %s
                    ORDER BY updated_at DESC
                """, (current_employee_id,))
            matters = cursor.fetchall()
            matter_ids = {str(m['id']) for m in matters}

            if request.method == 'POST':
                linked_matter_id = (request.form.get('linked_matter_id') or '').strip()
                task_title = (request.form.get('task_title') or '').strip()
                task_description = (request.form.get('task_description') or '').strip()
                due_at = (request.form.get('due_at') or '').strip()
                reminder_intervals = request.form.getlist('reminder_intervals')

                errors = []
                if not linked_matter_id:
                    errors.append('Please select a matter.')
                elif linked_matter_id not in matter_ids:
                    errors.append('Selected matter is not available for your account.')
                if not task_title:
                    errors.append('Task title is required.')
                if not due_at:
                    errors.append('Task timeline is required.')
                if not reminder_intervals:
                    errors.append('Select at least one reminder interval.')

                if errors:
                    for err in errors:
                        flash(err, 'error')
                else:
                    cursor.execute("""
                        INSERT INTO task_management
                        (task_type, linked_id, task_title, task_description, due_at, reminder_intervals, created_by_id, created_by_name)
                        VALUES ('matter', %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        int(linked_matter_id),
                        task_title,
                        task_description,
                        due_at.replace('T', ' '),
                        ','.join(reminder_intervals),
                        current_employee_id,
                        session.get('employee_name') or 'Unknown'
                    ))
                    connection.commit()
                    flash('Matter task created successfully.', 'success')
                    return redirect(url_for('matter_task_management'))

            if matter_ids:
                cursor.execute("""
                    SELECT
                        t.id,
                        t.task_title,
                        t.task_description,
                        t.due_at,
                        t.reminder_intervals,
                        t.task_status,
                        t.created_by_name,
                        m.id AS matter_id,
                        m.matter_reference_number,
                        m.matter_title
                    FROM task_management t
                    INNER JOIN matters m ON m.id = t.linked_id
                    WHERE t.task_type = 'matter'
                    ORDER BY t.created_at DESC
                    LIMIT 100
                """)
                task_rows = cursor.fetchall()
                matter_tasks = [r for r in task_rows if str(r.get('matter_id')) in matter_ids]
            else:
                matter_tasks = []
    except Exception as e:
        print(f"Matter task management error: {e}")
        flash('An error occurred while loading matter tasks.', 'error')
    finally:
        connection.close()

    return render_template(
        'matter_task_management.html',
        company_settings=company_settings,
        user_role=user_role,
        current_employee_id=current_employee_id,
        matters=matters,
        matter_tasks=matter_tasks,
        reminder_options=reminder_options
    )

@app.route('/approve_matters')
def approve_matters():
    """Approve Matters page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matters with status 'Pending Approval'
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_id,
                    m.client_name,
                    m.client_phone,
                    m.client_instructions,
                    m.assigned_employee_id,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status,
                    m.created_by_id,
                    m.created_by_name,
                    m.created_at,
                    m.updated_at,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone_number,
                    cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type as client_type,
                    cl.status as client_status
                FROM matters m
                LEFT JOIN clients cl ON m.client_id = cl.id
                WHERE m.status = 'Pending Approval'
                ORDER BY m.created_at DESC
            """)
            matters = cursor.fetchall()
            
            # Convert date objects to strings for JSON serialization
            for matter in matters:
                if matter.get('date_opened'):
                    matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d') if hasattr(matter['date_opened'], 'strftime') else str(matter['date_opened'])
                if matter.get('created_at'):
                    matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['created_at'], 'strftime') else str(matter['created_at'])
                if matter.get('updated_at'):
                    matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['updated_at'], 'strftime') else str(matter['updated_at'])
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('approve_matters.html', matters=matters, company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching pending matters: {e}")
        flash('Error loading pending matters.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()


def _other_matters_api_scope():
    """Scope for Other Matters listing APIs: (current_employee_id, is_mp, see_all_matters).
    IT Support (including role-switch) sees all matters firm-wide; Managing Partner only own active;
    others see matters assigned to them."""
    current_employee_id = session['employee_id']
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    is_mp = (user_role == 'Managing Partner')
    see_all_matters = (user_role == 'IT Support') or (original_role == 'IT Support')
    return current_employee_id, is_mp, see_all_matters


@app.route('/api/matters/search', methods=['GET'])
def api_matters_search():
    """API endpoint to search/return matters. Managing Partners see only their active allocated matters."""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    current_employee_id, is_mp, see_all_matters = _other_matters_api_scope()

    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            matter_cols = """
                    m.id, m.matter_reference_number, m.matter_title, m.matter_category,
                    m.client_id, m.client_name, m.client_phone, m.client_instructions,
                    m.assigned_employee_id, m.assigned_employee_name,
                    m.date_opened, m.status, m.created_by_id, m.created_by_name,
                    m.created_at, m.updated_at,
                    cl.id as client_table_id, cl.full_name as client_full_name,
                    cl.phone_number as client_phone_number, cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type as client_type, cl.status as client_status
            """
            if see_all_matters:
                cursor.execute("""
                    SELECT """ + matter_cols + """
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """)
                msg_label = 'all'
            elif is_mp:
                cursor.execute("""
                    SELECT """ + matter_cols + """
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.assigned_employee_id = %s
                      AND m.status NOT IN ('Pending Approval', 'Closed', 'Completed')
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """, (current_employee_id,))
                msg_label = 'your active'
            else:
                cursor.execute("""
                    SELECT """ + matter_cols + """
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """)
                msg_label = 'all'
            matters = cursor.fetchall()

            for matter in matters:
                if matter.get('date_opened'):
                    matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d') if hasattr(matter['date_opened'], 'strftime') else str(matter['date_opened'])
                if matter.get('created_at'):
                    matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['created_at'], 'strftime') else str(matter['created_at'])
                if matter.get('updated_at'):
                    matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['updated_at'], 'strftime') else str(matter['updated_at'])

            return jsonify({'matters': matters, 'message': f'Displaying {msg_label} {len(matters)} matter(s)'})
    except Exception as e:
        print(f"Error searching matters: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/clients', methods=['GET'])
def api_matters_clients():
    """API endpoint to get clients with their matter counts (allocated to current user, active-only for Managing Partner)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    current_employee_id, is_mp, see_all_matters = _other_matters_api_scope()
    search_query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if see_all_matters:
                if search_query:
                    cursor.execute("""
                        SELECT cl.id, cl.full_name, cl.phone_number, cl.email,
                               cl.profile_picture, cl.client_type, cl.status as client_status,
                               COUNT(m.id) as matter_count
                        FROM clients cl
                        INNER JOIN matters m ON cl.id = m.client_id
                        WHERE cl.status = 'Active'
                        AND (cl.full_name LIKE %s OR cl.phone_number LIKE %s)
                        GROUP BY cl.id, cl.full_name, cl.phone_number, cl.email, cl.profile_picture, cl.client_type, cl.status
                        ORDER BY matter_count DESC, cl.full_name ASC
                    """, (f'%{search_query}%', f'%{search_query}%'))
                else:
                    cursor.execute("""
                        SELECT cl.id, cl.full_name, cl.phone_number, cl.email,
                               cl.profile_picture, cl.client_type, cl.status as client_status,
                               COUNT(m.id) as matter_count
                        FROM clients cl
                        INNER JOIN matters m ON cl.id = m.client_id
                        WHERE cl.status = 'Active'
                        GROUP BY cl.id, cl.full_name, cl.phone_number, cl.email, cl.profile_picture, cl.client_type, cl.status
                        ORDER BY matter_count DESC, cl.full_name ASC
                    """)
            else:
                active_clause = "AND m.status NOT IN ('Pending Approval', 'Closed', 'Completed')" if is_mp else ""
                if search_query:
                    cursor.execute("""
                        SELECT cl.id, cl.full_name, cl.phone_number, cl.email,
                               cl.profile_picture, cl.client_type, cl.status as client_status,
                               COUNT(m.id) as matter_count
                        FROM clients cl
                        INNER JOIN matters m ON cl.id = m.client_id AND m.assigned_employee_id = %s """ + active_clause + """
                        WHERE cl.status = 'Active'
                        AND (cl.full_name LIKE %s OR cl.phone_number LIKE %s)
                        GROUP BY cl.id, cl.full_name, cl.phone_number, cl.email, cl.profile_picture, cl.client_type, cl.status
                        ORDER BY matter_count DESC, cl.full_name ASC
                    """, (current_employee_id, f'%{search_query}%', f'%{search_query}%'))
                else:
                    cursor.execute("""
                        SELECT cl.id, cl.full_name, cl.phone_number, cl.email,
                               cl.profile_picture, cl.client_type, cl.status as client_status,
                               COUNT(m.id) as matter_count
                        FROM clients cl
                        INNER JOIN matters m ON cl.id = m.client_id AND m.assigned_employee_id = %s """ + active_clause + """
                        WHERE cl.status = 'Active'
                        GROUP BY cl.id, cl.full_name, cl.phone_number, cl.email, cl.profile_picture, cl.client_type, cl.status
                        ORDER BY matter_count DESC, cl.full_name ASC
                    """, (current_employee_id,))
            clients = cursor.fetchall()
            return jsonify({'clients': clients, 'message': f'Found {len(clients)} client(s) with matters'})
    except Exception as e:
        print(f"Error fetching clients with matters: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/client/<int:client_id>', methods=['GET'])
def api_matters_by_client(client_id):
    """API endpoint to get matters for a specific client (allocated to current user; active-only for Managing Partner)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    current_employee_id, is_mp, see_all_matters = _other_matters_api_scope()
    active_clause = "AND m.status NOT IN ('Pending Approval', 'Closed', 'Completed')" if is_mp else ""
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if see_all_matters:
                cursor.execute("""
                    SELECT
                        m.id, m.matter_reference_number, m.matter_title, m.matter_category,
                        m.client_id, m.client_name, m.client_phone, m.client_instructions,
                        m.assigned_employee_id, m.assigned_employee_name,
                        m.date_opened, m.status, m.created_by_id, m.created_by_name,
                        m.created_at, m.updated_at,
                        cl.id as client_table_id, cl.full_name as client_full_name,
                        cl.phone_number as client_phone_number, cl.email as client_email,
                        cl.profile_picture as client_profile_picture,
                        cl.client_type as client_type, cl.status as client_status
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.client_id = %s
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """, (client_id,))
            else:
                cursor.execute("""
                    SELECT
                        m.id, m.matter_reference_number, m.matter_title, m.matter_category,
                        m.client_id, m.client_name, m.client_phone, m.client_instructions,
                        m.assigned_employee_id, m.assigned_employee_name,
                        m.date_opened, m.status, m.created_by_id, m.created_by_name,
                        m.created_at, m.updated_at,
                        cl.id as client_table_id, cl.full_name as client_full_name,
                        cl.phone_number as client_phone_number, cl.email as client_email,
                        cl.profile_picture as client_profile_picture,
                        cl.client_type as client_type, cl.status as client_status
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.client_id = %s AND m.assigned_employee_id = %s """ + active_clause + """
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """, (client_id, current_employee_id))
            matters = cursor.fetchall()
            
            # Convert date objects to strings for JSON serialization
            for matter in matters:
                if matter.get('date_opened'):
                    matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d') if hasattr(matter['date_opened'], 'strftime') else str(matter['date_opened'])
                if matter.get('created_at'):
                    matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['created_at'], 'strftime') else str(matter['created_at'])
                if matter.get('updated_at'):
                    matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['updated_at'], 'strftime') else str(matter['updated_at'])
            
            return jsonify({
                'matters': matters,
                'message': f'Found {len(matters)} matter(s) for this client'
            })
    except Exception as e:
        print(f"Error fetching matters for client: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/categories', methods=['GET'])
def api_matters_categories():
    """API endpoint to get categories with matter counts (allocated to current user; active-only for Managing Partner)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    current_employee_id, is_mp, see_all_matters = _other_matters_api_scope()
    active_clause = "AND m.status NOT IN ('Pending Approval', 'Closed', 'Completed')" if is_mp else ""
    search_query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if see_all_matters:
                if search_query:
                    cursor.execute("""
                        SELECT m.matter_category as category_name, COUNT(m.id) as matter_count
                        FROM matters m
                        WHERE m.matter_category LIKE %s
                        GROUP BY m.matter_category
                        ORDER BY matter_count DESC, m.matter_category ASC
                    """, (f'%{search_query}%',))
                else:
                    cursor.execute("""
                        SELECT m.matter_category as category_name, COUNT(m.id) as matter_count
                        FROM matters m
                        GROUP BY m.matter_category
                        ORDER BY matter_count DESC, m.matter_category ASC
                    """)
            else:
                if search_query:
                    cursor.execute("""
                        SELECT m.matter_category as category_name, COUNT(m.id) as matter_count
                        FROM matters m
                        WHERE m.assigned_employee_id = %s """ + active_clause + """
                          AND m.matter_category LIKE %s
                        GROUP BY m.matter_category
                        ORDER BY matter_count DESC, m.matter_category ASC
                    """, (current_employee_id, f'%{search_query}%'))
                else:
                    cursor.execute("""
                        SELECT m.matter_category as category_name, COUNT(m.id) as matter_count
                        FROM matters m
                        WHERE m.assigned_employee_id = %s """ + active_clause + """
                        GROUP BY m.matter_category
                        ORDER BY matter_count DESC, m.matter_category ASC
                    """, (current_employee_id,))
            categories = cursor.fetchall()
            return jsonify({'categories': categories, 'message': f'Found {len(categories)} category(ies) with matters'})
    except Exception as e:
        print(f"Error fetching categories with matters: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/<int:matter_id>', methods=['GET'])
def api_matter_by_id(matter_id):
    """API endpoint to get a single matter by ID"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matter details
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_id,
                    m.client_name,
                    m.client_phone,
                    m.client_instructions,
                    m.assigned_employee_id,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status,
                    m.created_by_id,
                    m.created_by_name,
                    m.created_at,
                    m.updated_at,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone_number,
                    cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type as client_type,
                    cl.status as client_status
                FROM matters m
                LEFT JOIN clients cl ON m.client_id = cl.id
                WHERE m.id = %s
            """, (matter_id,))
            matter = cursor.fetchone()
            
            if not matter:
                return jsonify({'error': 'Matter not found'}), 404
            
            # Convert date objects to strings for JSON serialization
            if matter.get('date_opened'):
                matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d') if hasattr(matter['date_opened'], 'strftime') else str(matter['date_opened'])
            if matter.get('created_at'):
                matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['created_at'], 'strftime') else str(matter['created_at'])
            if matter.get('updated_at'):
                matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['updated_at'], 'strftime') else str(matter['updated_at'])
            
            return jsonify({
                'matter': matter,
                'message': 'Matter retrieved successfully'
            })
    except Exception as e:
        print(f"Error fetching matter: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matter/<int:matter_id>', methods=['GET'])
def api_matter_singular(matter_id):
    """API endpoint to get a single matter by ID (singular alias)"""
    # Reuse the existing function
    return api_matter_by_id(matter_id)

@app.route('/api/approve_matter/<int:matter_id>', methods=['POST'])
def api_approve_matter(matter_id):
    """Approve a pending matter, allocate it, and save allocation details."""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    _matter_approve_roles = ['Firm Administrator', 'Managing Partner', 'IT Support']
    if user_role not in _matter_approve_roles and original_role != 'IT Support':
        return jsonify({'error': 'Only Firm Administrators, Managing Partners, or IT Support can approve matters'}), 403

    data = request.get_json() or {}
    allocated_employee_id = data.get('allocated_employee_id')
    allocation_description = (data.get('allocation_description') or '').strip()
    allocation_timeline = (data.get('allocation_timeline') or '').strip()

    if not allocated_employee_id:
        return jsonify({'error': 'Allocated employee is required'}), 400
    if not allocation_description:
        return jsonify({'error': 'Brief description is required'}), 400
    if not allocation_timeline:
        return jsonify({'error': 'Timeline is required'}), 400

    try:
        allocated_employee_id = int(allocated_employee_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid allocated employee'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    # Fine-grained permission: allocate/approve matter
    deny = enforce_permission(connection, 'matter_allocate')
    if deny:
        return jsonify({'error': 'Forbidden'}), 403

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # First, verify the matter exists and has status 'Pending Approval'
            cursor.execute("""
                SELECT id, status FROM matters WHERE id = %s
            """, (matter_id,))
            matter = cursor.fetchone()
            
            if not matter:
                return jsonify({'error': 'Matter not found'}), 404
            
            if matter['status'] != 'Pending Approval':
                return jsonify({'error': f'Matter is not pending approval. Current status: {matter["status"]}'}), 400

            # Ensure allocation metadata columns exist
            if not column_exists('matters', 'allocation_description'):
                try:
                    cursor.execute("ALTER TABLE matters ADD COLUMN allocation_description TEXT NULL")
                    connection.commit()
                except Exception:
                    pass
            if not column_exists('matters', 'allocation_timeline'):
                try:
                    cursor.execute("ALTER TABLE matters ADD COLUMN allocation_timeline VARCHAR(500) NULL")
                    connection.commit()
                except Exception:
                    pass

            # Fetch and validate allowed assignee (Firm Administrator or Managing Partner)
            cursor.execute("""
                SELECT id, full_name, role
                FROM employees
                WHERE id = %s AND status = 'Active'
            """, (allocated_employee_id,))
            assignee = cursor.fetchone()
            if not assignee:
                return jsonify({'error': 'Allocated employee not found'}), 404
            if assignee.get('role') not in ['Firm Administrator', 'Managing Partner']:
                return jsonify({'error': 'Allocated employee must be a Firm Administrator or Managing Partner'}), 400

            # Update allocation and approve the matter
            cursor.execute("""
                UPDATE matters 
                SET assigned_employee_id = %s,
                    assigned_employee_name = %s,
                    allocation_description = %s,
                    allocation_timeline = %s,
                    status = 'Open',
                    updated_at = NOW()
                WHERE id = %s
            """, (
                assignee['id'],
                assignee['full_name'],
                allocation_description,
                allocation_timeline,
                matter_id
            ))
            connection.commit()

            return jsonify({
                'success': True,
                'message': f"Matter approved and allocated to {assignee['full_name']} successfully"
            })
    except Exception as e:
        print(f"Error approving matter: {e}")
        connection.rollback()
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/update_matter_status/<int:matter_id>', methods=['POST'])
def api_update_matter_status(matter_id):
    """API endpoint to update matter status"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'status' not in data:
        return jsonify({'success': False, 'error': 'Status is required'}), 400
    
    new_status = data['status']
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    # Fine-grained permission: change matter status
    deny = enforce_permission(connection, 'matter_change_status')
    if deny:
        return jsonify({'success': False, 'error': 'Forbidden'}), 403
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if matter exists
            cursor.execute("SELECT id, status FROM matters WHERE id = %s", (matter_id,))
            matter = cursor.fetchone()
            
            if not matter:
                return jsonify({'success': False, 'error': 'Matter not found'}), 404
            
            # Update the matter status
            cursor.execute("""
                UPDATE matters 
                SET status = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_status, matter_id))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Matter status updated to {new_status} successfully'
            })
    except Exception as e:
        print(f"Error updating matter status: {e}")
        connection.rollback()
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/allocate_matter/<int:matter_id>', methods=['POST'])
def api_allocate_matter(matter_id):
    """API endpoint to allocate a matter to an employee"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'employee_id' not in data:
        return jsonify({'success': False, 'error': 'Employee ID is required'}), 400
    
    employee_id = data['employee_id']
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500

    # Fine-grained permission: allocate matter
    deny = enforce_permission(connection, 'matter_allocate')
    if deny:
        return jsonify({'success': False, 'error': 'Forbidden'}), 403
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if matter exists
            cursor.execute("SELECT id FROM matters WHERE id = %s", (matter_id,))
            matter = cursor.fetchone()
            
            if not matter:
                return jsonify({'success': False, 'error': 'Matter not found'}), 404
            
            # Get employee name
            cursor.execute("SELECT full_name FROM employees WHERE id = %s", (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return jsonify({'success': False, 'error': 'Employee not found'}), 400
            
            employee_name = employee['full_name']
            
            # Update the matter allocation
            cursor.execute("""
                UPDATE matters 
                SET assigned_employee_id = %s, 
                    assigned_employee_name = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (employee_id, employee_name, matter_id))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Matter allocated to {employee_name} successfully'
            })
    except Exception as e:
        print(f"Error allocating matter: {e}")
        connection.rollback()
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/category/<path:category_name>', methods=['GET'])
def api_matters_by_category(category_name):
    """API endpoint to get matters for a specific category (allocated to current user; active-only for Managing Partner)"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    current_employee_id, is_mp, see_all_matters = _other_matters_api_scope()
    active_clause = "AND m.status NOT IN ('Pending Approval', 'Closed', 'Completed')" if is_mp else ""
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if see_all_matters:
                cursor.execute("""
                    SELECT
                        m.id, m.matter_reference_number, m.matter_title, m.matter_category,
                        m.client_id, m.client_name, m.client_phone, m.client_instructions,
                        m.assigned_employee_id, m.assigned_employee_name,
                        m.date_opened, m.status, m.created_by_id, m.created_by_name,
                        m.created_at, m.updated_at,
                        cl.id as client_table_id, cl.full_name as client_full_name,
                        cl.phone_number as client_phone_number, cl.email as client_email,
                        cl.profile_picture as client_profile_picture,
                        cl.client_type as client_type, cl.status as client_status
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.matter_category = %s
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """, (category_name,))
            else:
                cursor.execute("""
                    SELECT
                        m.id, m.matter_reference_number, m.matter_title, m.matter_category,
                        m.client_id, m.client_name, m.client_phone, m.client_instructions,
                        m.assigned_employee_id, m.assigned_employee_name,
                        m.date_opened, m.status, m.created_by_id, m.created_by_name,
                        m.created_at, m.updated_at,
                        cl.id as client_table_id, cl.full_name as client_full_name,
                        cl.phone_number as client_phone_number, cl.email as client_email,
                        cl.profile_picture as client_profile_picture,
                        cl.client_type as client_type, cl.status as client_status
                    FROM matters m
                    LEFT JOIN clients cl ON m.client_id = cl.id
                    WHERE m.matter_category = %s AND m.assigned_employee_id = %s """ + active_clause + """
                    ORDER BY m.date_opened DESC, m.created_at DESC
                """, (category_name, current_employee_id))
            matters = cursor.fetchall()
            
            # Convert date objects to strings for JSON serialization
            for matter in matters:
                if matter.get('date_opened'):
                    matter['date_opened'] = matter['date_opened'].strftime('%Y-%m-%d') if hasattr(matter['date_opened'], 'strftime') else str(matter['date_opened'])
                if matter.get('created_at'):
                    matter['created_at'] = matter['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['created_at'], 'strftime') else str(matter['created_at'])
                if matter.get('updated_at'):
                    matter['updated_at'] = matter['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter['updated_at'], 'strftime') else str(matter['updated_at'])
            
            return jsonify({
                'matters': matters,
                'message': f'Found {len(matters)} matter(s) for this category'
            })
    except Exception as e:
        print(f"Error fetching matters for category: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/<int:matter_id>/accept', methods=['POST'])
def api_matter_accept(matter_id):
    """Allocated user accepts the matter (sets status to Open so they can view it)."""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    current_employee_id = session['employee_id']
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT id, status, assigned_employee_id FROM matters WHERE id = %s",
                (matter_id,)
            )
            matter = cursor.fetchone()
            if not matter:
                return jsonify({'success': False, 'error': 'Matter not found'}), 404
            if str(matter.get('assigned_employee_id')) != str(current_employee_id):
                return jsonify({'success': False, 'error': 'Only the allocated person can accept this matter'}), 403
            if (matter.get('status') or '') != 'Pending Approval':
                return jsonify({'success': False, 'error': 'Matter is not pending acceptance'}), 400
            cursor.execute(
                "UPDATE matters SET status = 'Open', updated_at = NOW() WHERE id = %s",
                (matter_id,)
            )
            connection.commit()
            return jsonify({'success': True, 'message': 'Matter accepted. You can now view it.'})
    except Exception as e:
        if connection:
            connection.rollback()
        print(f"Error accepting matter: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/other_matters/register')
def register_matter():
    """Register New Matter page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}

    connection = get_db_connection()
    if connection:
        # Fine-grained permission: register other matters
        deny = enforce_permission(connection, 'matter_register_other')
        connection.close()
        if deny:
            return deny
    
    return render_template('register_matter.html', company_settings=company_settings)

@app.route('/other_matters/<int:matter_id>')
def matter_details(matter_id):
    """Matter Details page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matter details with client and employee information
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_id,
                    m.client_name,
                    m.client_phone,
                    m.client_instructions,
                    m.assigned_employee_id,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status,
                    m.created_by_id,
                    m.created_by_name,
                    m.created_at,
                    m.updated_at,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone_number,
                    cl.email as client_email,
                    cl.profile_picture as client_profile_picture,
                    cl.client_type as client_type,
                    cl.status as client_status,
                    e_assigned.id as assigned_employee_table_id,
                    e_assigned.full_name as assigned_employee_full_name,
                    e_assigned.employee_code as assigned_employee_code,
                    e_assigned.work_email as assigned_employee_email,
                    e_assigned.role as assigned_employee_role,
                    e_created.id as created_by_employee_id,
                    e_created.full_name as created_by_full_name,
                    e_created.employee_code as created_by_code,
                    e_created.work_email as created_by_email,
                    e_created.role as created_by_role
                FROM matters m
                LEFT JOIN clients cl ON m.client_id = cl.id
                LEFT JOIN employees e_assigned ON m.assigned_employee_id = e_assigned.id
                LEFT JOIN employees e_created ON m.created_by_id = e_created.id
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
            
            if not matter_data:
                flash('Matter not found', 'error')
                return redirect(url_for('other_matters'))
            
            # Allocated user can view; IT Support (incl. role-switch) can view any matter
            _, _, see_all_matters = _other_matters_api_scope()
            current_employee_id = session.get('employee_id')
            assigned_id = matter_data.get('assigned_employee_id')
            is_assigned = (assigned_id is not None and str(assigned_id) == str(current_employee_id))
            if not is_assigned and not see_all_matters:
                flash('You do not have access to this matter.', 'error')
                return redirect(url_for('other_matters'))
            
            status = matter_data.get('status') or ''
            pending_accept = (status == 'Pending Approval')
            
            # Convert date objects to strings
            if matter_data.get('date_opened'):
                matter_data['date_opened'] = matter_data['date_opened'].strftime('%Y-%m-%d')
            if matter_data.get('created_at'):
                matter_data['created_at'] = matter_data['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if matter_data.get('updated_at'):
                matter_data['updated_at'] = matter_data['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            # Documents and Google Drive (only when not pending accept)
            google_drive_connected = False
            documents = []
            suggested_doc_title = ''
            if not pending_accept:
                if 'google_drive_credentials' in session:
                    google_drive_connected = True
                else:
                    cursor.execute("""
                        SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri, google_drive_scopes
                        FROM company_settings ORDER BY id DESC LIMIT 1
                    """)
                    settings = cursor.fetchone()
                    if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                        google_drive_connected = True
                        scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                        session['google_drive_credentials'] = {
                            'token': settings['google_drive_token'],
                            'refresh_token': settings['google_drive_refresh_token'],
                            'token_uri': settings.get('google_drive_token_uri'),
                            'client_id': GOOGLE_CLIENT_ID,
                            'client_secret': GOOGLE_CLIENT_SECRET,
                            'scopes': scopes
                        }
                emp_name = (session.get('employee_name') or matter_data.get('assigned_employee_name') or matter_data.get('assigned_employee_full_name') or '').strip()
                if not emp_name and session.get('employee_id'):
                    cursor.execute("SELECT full_name FROM employees WHERE id = %s", (session['employee_id'],))
                    emp_row = cursor.fetchone()
                    emp_name = (emp_row.get('full_name') or '').strip() if emp_row else ''
                ref = (matter_data.get('matter_reference_number') or '').strip() or f'Matter-{matter_id}'
                suggested_doc_title = f"{emp_name or 'Document'} - {ref}"
                if google_drive_connected:
                    try:
                        service = get_google_drive_service()
                        if service:
                            main_folder_id = session.get('google_drive_main_folder_id')
                            if not main_folder_id:
                                cursor.execute("SELECT google_drive_main_folder_id FROM company_settings ORDER BY id DESC LIMIT 1")
                                row = cursor.fetchone()
                                if row and row.get('google_drive_main_folder_id'):
                                    main_folder_id = row['google_drive_main_folder_id']
                                    session['google_drive_main_folder_id'] = main_folder_id
                            if main_folder_id:
                                client_phone = matter_data.get('client_phone_number') or matter_data.get('client_phone')
                                client_name = matter_data.get('client_full_name') or matter_data.get('client_name') or ''
                                if client_name or client_phone:
                                    client_folder_name = get_user_folder_name(client_phone, client_name, 'client')
                                    client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                                else:
                                    client_folder_id = get_or_create_folder(service, main_folder_id, 'Other Matters')
                                ref_safe = re.sub(r'[\\/:*?"<>|]', '_', ref)
                                matter_folder_name = f"Matter {ref_safe}"
                                matter_doc_folder_id = get_or_create_folder(service, client_folder_id, matter_folder_name)
                                if matter_doc_folder_id:
                                    query = f"'{matter_doc_folder_id}' in parents and trashed=false"
                                    all_files = []
                                    page_token = None
                                    while True:
                                        results = service.files().list(
                                            q=query,
                                            spaces='drive',
                                            fields='nextPageToken, files(id, name, createdTime, modifiedTime, webViewLink, size, mimeType)',
                                            orderBy='modifiedTime desc',
                                            pageSize=100,
                                            pageToken=page_token
                                        ).execute()
                                        all_files.extend(results.get('files', []))
                                        page_token = results.get('nextPageToken')
                                        if not page_token:
                                            break
                                    for file in all_files:
                                        if file.get('mimeType') == 'application/vnd.google-apps.folder':
                                            continue
                                        created_time = file.get('createdTime', '')
                                        modified_time = file.get('modifiedTime', '')
                                        if created_time:
                                            try:
                                                dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                                                created_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                            except Exception:
                                                pass
                                        if modified_time:
                                            try:
                                                dt = datetime.fromisoformat(modified_time.replace('Z', '+00:00'))
                                                modified_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                            except Exception:
                                                pass
                                        size = file.get('size', '0')
                                        try:
                                            size_int = int(size) if size else 0
                                            size_str = f"{size_int} B" if size_int < 1024 else (f"{size_int / 1024:.2f} KB" if size_int < 1024 * 1024 else f"{size_int / (1024 * 1024):.2f} MB")
                                        except Exception:
                                            size_str = "Unknown"
                                        documents.append({
                                            'id': file.get('id'),
                                            'name': file.get('name', 'Unknown'),
                                            'created_time': created_time,
                                            'modified_time': modified_time,
                                            'url': file.get('webViewLink', ''),
                                            'size': size_str,
                                            'mime_type': file.get('mimeType', '')
                                        })
                    except Exception as e:
                        print(f"Error fetching matter documents for matter details: {e}")
            
            return render_template('matter_details.html', 
                                 matter_data=matter_data, 
                                 matter_id=matter_id,
                                 pending_accept=pending_accept,
                                 company_settings=company_settings,
                                 google_drive_connected=google_drive_connected,
                                 documents=documents,
                                 suggested_doc_title=suggested_doc_title)
    except Exception as e:
        print(f"Error fetching matter details: {e}")
        flash('An error occurred while fetching matter details.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()

@app.route('/other_matters/<int:matter_id>/documents')
def matter_documents(matter_id):
    """Matter Documents page - upload and list documents for a specific matter in Google Drive"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))
    
    documents = []
    google_drive_connected = False
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.client_id,
                    m.client_name,
                    m.client_phone,
                    m.status,
                    m.assigned_employee_id,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone_number,
                    cl.email as client_email
                FROM matters m
                LEFT JOIN clients cl ON m.client_id = cl.id
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
            
            if not matter_data:
                flash('Matter not found', 'error')
                return redirect(url_for('other_matters'))
            
            _, _, see_all_matters = _other_matters_api_scope()
            current_employee_id = session.get('employee_id')
            assigned_id = matter_data.get('assigned_employee_id')
            is_assigned = (assigned_id is not None and str(assigned_id) == str(current_employee_id))
            if not is_assigned and not see_all_matters:
                flash('You do not have access to this matter.', 'error')
                return redirect(url_for('other_matters'))
            
            # Check Google Drive and load credentials
            if 'google_drive_credentials' in session:
                google_drive_connected = True
            else:
                cursor.execute("""
                    SELECT google_drive_token, google_drive_refresh_token, google_drive_token_uri, google_drive_scopes
                    FROM company_settings ORDER BY id DESC LIMIT 1
                """)
                settings = cursor.fetchone()
                if settings and settings.get('google_drive_token') and settings.get('google_drive_refresh_token'):
                    google_drive_connected = True
                    scopes = json.loads(settings['google_drive_scopes']) if settings.get('google_drive_scopes') else []
                    session['google_drive_credentials'] = {
                        'token': settings['google_drive_token'],
                        'refresh_token': settings['google_drive_refresh_token'],
                        'token_uri': settings.get('google_drive_token_uri'),
                        'client_id': GOOGLE_CLIENT_ID,
                        'client_secret': GOOGLE_CLIENT_SECRET,
                        'scopes': scopes
                    }
            
            if google_drive_connected:
                try:
                    service = get_google_drive_service()
                    if service:
                        main_folder_id = session.get('google_drive_main_folder_id')
                        if not main_folder_id:
                            cursor.execute("SELECT google_drive_main_folder_id FROM company_settings ORDER BY id DESC LIMIT 1")
                            settings = cursor.fetchone()
                            if settings and settings.get('google_drive_main_folder_id'):
                                main_folder_id = settings['google_drive_main_folder_id']
                                session['google_drive_main_folder_id'] = main_folder_id
                        
                        if main_folder_id:
                            client_phone = matter_data.get('client_phone_number') or matter_data.get('client_phone')
                            client_name = matter_data.get('client_full_name') or matter_data.get('client_name') or ''
                            if client_name or client_phone:
                                client_folder_name = get_user_folder_name(client_phone, client_name, 'client')
                                client_folder_id = get_or_create_folder(service, main_folder_id, client_folder_name)
                            else:
                                client_folder_id = get_or_create_folder(service, main_folder_id, 'Other Matters')
                            
                            ref = (matter_data.get('matter_reference_number') or '').strip() or f'Matter-{matter_id}'
                            ref = re.sub(r'[\\/:*?"<>|]', '_', ref)
                            matter_folder_name = f"Matter {ref}"
                            matter_doc_folder_id = get_or_create_folder(service, client_folder_id, matter_folder_name)
                            
                            if matter_doc_folder_id:
                                query = f"'{matter_doc_folder_id}' in parents and trashed=false"
                                files = []
                                page_token = None
                                while True:
                                    results = service.files().list(
                                        q=query,
                                        spaces='drive',
                                        fields='nextPageToken, files(id, name, createdTime, modifiedTime, webViewLink, size, mimeType)',
                                        orderBy='modifiedTime desc',
                                        pageSize=100,
                                        pageToken=page_token
                                    ).execute()
                                    files.extend(results.get('files', []))
                                    page_token = results.get('nextPageToken')
                                    if not page_token:
                                        break
                                for file in files:
                                    if file.get('mimeType') == 'application/vnd.google-apps.folder':
                                        continue
                                    created_time = file.get('createdTime', '')
                                    modified_time = file.get('modifiedTime', '')
                                    if created_time:
                                        try:
                                            from datetime import datetime
                                            dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                                            created_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except Exception:
                                            pass
                                    if modified_time:
                                        try:
                                            from datetime import datetime
                                            dt = datetime.fromisoformat(modified_time.replace('Z', '+00:00'))
                                            modified_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except Exception:
                                            pass
                                    size = file.get('size', '0')
                                    if size:
                                        try:
                                            size_int = int(size)
                                            size_str = f"{size_int} B" if size_int < 1024 else (f"{size_int / 1024:.2f} KB" if size_int < 1024 * 1024 else f"{size_int / (1024 * 1024):.2f} MB")
                                        except Exception:
                                            size_str = "Unknown"
                                    else:
                                        size_str = "Unknown"
                                    documents.append({
                                        'id': file.get('id'),
                                        'name': file.get('name', 'Unknown'),
                                        'created_time': created_time,
                                        'modified_time': modified_time,
                                        'url': file.get('webViewLink', ''),
                                        'size': size_str,
                                        'mime_type': file.get('mimeType', '')
                                    })
                except Exception as e:
                    print(f"Error fetching matter documents from Google Drive: {e}")
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('matter_documents.html',
                                 matter_data=matter_data,
                                 matter_id=matter_id,
                                 google_drive_connected=google_drive_connected,
                                 documents=documents,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error loading matter documents: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()

@app.route('/other_matters/<int:matter_id>/edit')
def matter_edit(matter_id):
    """Matter Edit page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))

    # Fine-grained permission: edit matter details
    deny = enforce_permission(connection, 'matter_edit', redirect_endpoint='other_matters')
    if deny:
        return deny
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matter details
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.client_id,
                    m.client_name,
                    m.client_phone,
                    m.client_instructions,
                    m.assigned_employee_id,
                    m.assigned_employee_name,
                    m.date_opened,
                    m.status,
                    m.created_by_id,
                    m.created_by_name,
                    m.created_at,
                    m.updated_at,
                    cl.id as client_table_id,
                    cl.full_name as client_full_name,
                    cl.phone_number as client_phone_number,
                    cl.email as client_email
                FROM matters m
                LEFT JOIN clients cl ON m.client_id = cl.id
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
            
            if not matter_data:
                flash('Matter not found', 'error')
                return redirect(url_for('other_matters'))
            
            # Convert date objects to strings
            if matter_data.get('date_opened'):
                matter_data['date_opened'] = matter_data['date_opened'].strftime('%Y-%m-%d')
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('matter_edit.html', 
                                 matter_data=matter_data, 
                                 matter_id=matter_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching matter for edit: {e}")
        flash('An error occurred while fetching matter details.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()

@app.route('/other_matters/<int:matter_id>/status')
def matter_status(matter_id):
    """Change Matter Status page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))

    # Fine-grained permission: change matter / case status
    deny = enforce_permission(connection, 'matter_change_status', redirect_endpoint='other_matters')
    if deny:
        return deny
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matter details
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.status
                FROM matters m
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
            
            if not matter_data:
                flash('Matter not found', 'error')
                return redirect(url_for('other_matters'))
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('matter_status.html', 
                                 matter_data=matter_data, 
                                 matter_id=matter_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching matter for status: {e}")
        flash('An error occurred while fetching matter details.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()

@app.route('/other_matters/<int:matter_id>/allocate')
def matter_allocate(matter_id):
    """Change Matter Allocation page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))

    # Fine-grained permission: allocate / re-allocate matters
    deny = enforce_permission(connection, 'matter_allocate', redirect_endpoint='other_matters')
    if deny:
        return deny
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matter details
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.assigned_employee_id,
                    m.assigned_employee_name
                FROM matters m
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
            
            if not matter_data:
                flash('Matter not found', 'error')
                return redirect(url_for('other_matters'))
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('matter_allocate.html', 
                                 matter_data=matter_data, 
                                 matter_id=matter_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching matter for allocation: {e}")
        flash('An error occurred while fetching matter details.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()

@app.route('/other_matters/<int:matter_id>/audit')
def matter_audit_progress(matter_id):
    """Matter Audit Progress page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('other_matters'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch matter details
            cursor.execute("""
                SELECT 
                    m.id,
                    m.matter_reference_number,
                    m.matter_title,
                    m.matter_category,
                    m.assigned_employee_id,
                    m.assigned_employee_name,
                    m.created_by_id,
                    m.created_by_name,
                    m.date_opened,
                    m.status,
                    m.created_at,
                    m.updated_at
                FROM matters m
                WHERE m.id = %s
            """, (matter_id,))
            matter_data = cursor.fetchone()
            
            if not matter_data:
                flash('Matter not found', 'error')
                return redirect(url_for('other_matters'))
            
            # Build audit trail from matter creation, updates, and status changes
            audit_items = []
            
            # Matter creation
            if matter_data.get('created_at'):
                created_at = matter_data['created_at']
                if hasattr(created_at, 'strftime'):
                    created_at_str = created_at.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    created_at_str = str(created_at)
                
                audit_items.append({
                    'title': 'Matter Created',
                    'description': f'Matter "{matter_data.get("matter_title", "N/A")}" was created',
                    'timestamp': created_at_str,
                    'user': matter_data.get('created_by_name', 'Unknown'),
                    'color': 'bg-blue-500',
                    'icon': 'fa-plus-circle'
                })
            
            # Matter updates
            if matter_data.get('updated_at') and matter_data.get('created_at'):
                updated_at = matter_data['updated_at']
                created_at = matter_data['created_at']
                if hasattr(updated_at, 'strftime') and hasattr(created_at, 'strftime'):
                    if updated_at != created_at:
                        updated_at_str = updated_at.strftime('%Y-%m-%d %H:%M:%S')
                        audit_items.append({
                            'title': 'Matter Updated',
                            'description': f'Matter details were updated',
                            'timestamp': updated_at_str,
                            'user': 'System',
                            'color': 'bg-yellow-500',
                            'icon': 'fa-edit'
                        })
            
            # Sort by timestamp descending
            audit_items.sort(key=lambda x: x['timestamp'], reverse=True)
            
            # Convert date objects to strings
            if matter_data.get('date_opened'):
                matter_data['date_opened'] = matter_data['date_opened'].strftime('%Y-%m-%d') if hasattr(matter_data['date_opened'], 'strftime') else str(matter_data['date_opened'])
            if matter_data.get('created_at'):
                matter_data['created_at'] = matter_data['created_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter_data['created_at'], 'strftime') else str(matter_data['created_at'])
            if matter_data.get('updated_at'):
                matter_data['updated_at'] = matter_data['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if hasattr(matter_data['updated_at'], 'strftime') else str(matter_data['updated_at'])
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('matter_audit_progress.html', 
                                 matter_data=matter_data, 
                                 matter_id=matter_id,
                                 audit_items=audit_items,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching matter audit: {e}")
        flash('An error occurred while fetching matter audit.', 'error')
        return redirect(url_for('other_matters'))
    finally:
        connection.close()

@app.route('/api/matters/clients/search', methods=['GET'])
def api_matters_clients_search():
    """API endpoint to search clients by name or phone number for matters"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                like = f'%{query}%'
                cursor.execute("""
                    SELECT id, full_name, email, phone_number, client_type
                    FROM clients 
                    WHERE status = 'Active' 
                    AND (full_name LIKE %s OR COALESCE(phone_number, '') LIKE %s)
                    ORDER BY full_name ASC
                    LIMIT 20
                """, (like, like))
            else:
                cursor.execute("""
                    SELECT id, full_name, email, phone_number, client_type
                    FROM clients 
                    WHERE status = 'Active'
                    ORDER BY full_name ASC
                    LIMIT 50
                """)
            clients = cursor.fetchall()
            return jsonify({'clients': clients})
    except Exception as e:
        print(f"Error searching clients: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/employees/search', methods=['GET'])
def api_matters_employees_search():
    """API endpoint to search employees by name for matters"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                cursor.execute("""
                    SELECT id, full_name, employee_code, work_email, role
                    FROM employees 
                    WHERE status = 'Active' 
                    AND full_name LIKE %s
                    ORDER BY full_name ASC
                    LIMIT 20
                """, (f'%{query}%',))
            else:
                cursor.execute("""
                    SELECT id, full_name, employee_code, work_email, role
                    FROM employees 
                    WHERE status = 'Active'
                    ORDER BY full_name ASC
                    LIMIT 50
                """)
            employees = cursor.fetchall()
            return jsonify({'employees': employees})
    except Exception as e:
        print(f"Error searching employees: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/approvers/search', methods=['GET'])
def api_matters_approvers_search():
    """Search active employees eligible for matter approval allocation."""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            role_filter = "AND role IN ('Firm Administrator', 'Managing Partner')"
            if query:
                like = f'%{query}%'
                cursor.execute(f"""
                    SELECT id, full_name, employee_code, work_email, role
                    FROM employees
                    WHERE status = 'Active'
                    {role_filter}
                    AND (full_name LIKE %s OR COALESCE(employee_code, '') LIKE %s)
                    ORDER BY
                        CASE WHEN role = 'Managing Partner' THEN 0 ELSE 1 END,
                        full_name ASC
                    LIMIT 20
                """, (like, like))
            else:
                cursor.execute(f"""
                    SELECT id, full_name, employee_code, work_email, role
                    FROM employees
                    WHERE status = 'Active'
                    {role_filter}
                    ORDER BY
                        CASE WHEN role = 'Managing Partner' THEN 0 ELSE 1 END,
                        full_name ASC
                    LIMIT 50
                """)
            employees = cursor.fetchall()
            return jsonify({'employees': employees})
    except Exception as e:
        print(f"Error searching approver employees: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/categories/search', methods=['GET'])
def api_matters_categories_search():
    """API endpoint to search matter categories from existing matters"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip().upper()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            if query:
                # Search for distinct matter categories that match the query
                cursor.execute("""
                    SELECT DISTINCT matter_category as category_name
                    FROM matters 
                    WHERE matter_category LIKE %s
                    ORDER BY matter_category ASC
                    LIMIT 20
                """, (f'%{query}%',))
            else:
                # Get all distinct matter categories
                cursor.execute("""
                    SELECT DISTINCT matter_category as category_name
                    FROM matters 
                    ORDER BY matter_category ASC
                    LIMIT 50
                """)
            categories = cursor.fetchall()
            return jsonify({'categories': categories})
    except Exception as e:
        print(f"Error searching matter categories: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/register', methods=['POST'])
def api_register_matter():
    """API endpoint to register a new matter"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        
        # Validate required fields (assignee defaults to current user — no picker on register form)
        required_fields = ['matter_title', 'matter_category', 'client_id', 'date_opened', 'status']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'{field} is required'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500

        # Fine-grained permission: register matter
        deny = enforce_permission(connection, 'matter_register_other')
        if deny:
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Get client info
                cursor.execute("SELECT id, full_name, phone_number FROM clients WHERE id = %s", (data['client_id'],))
                client = cursor.fetchone()
                if not client:
                    return jsonify({'success': False, 'error': 'Client not found'}), 404
                
                # Assigned employee: optional in JSON; otherwise current user (form no longer picks assignee)
                assignee_id = data.get('assigned_employee_id')
                if assignee_id is not None and assignee_id != '':
                    try:
                        assignee_id = int(assignee_id)
                    except (TypeError, ValueError):
                        return jsonify({'success': False, 'error': 'Invalid assigned_employee_id'}), 400
                else:
                    assignee_id = int(session['employee_id'])
                
                cursor.execute("SELECT id, full_name FROM employees WHERE id = %s", (assignee_id,))
                employee = cursor.fetchone()
                if not employee:
                    return jsonify({'success': False, 'error': 'Employee not found'}), 404
                
                # Get current user info
                cursor.execute("SELECT id, full_name FROM employees WHERE id = %s", (session['employee_id'],))
                creator = cursor.fetchone()
                if not creator:
                    return jsonify({'success': False, 'error': 'Creator not found'}), 404
                
                # Generate matter reference number
                import datetime
                year = datetime.datetime.now().year
                cursor.execute("""
                    SELECT COUNT(*) as count FROM matters 
                    WHERE YEAR(created_at) = %s
                """, (year,))
                count_result = cursor.fetchone()
                count = count_result['count'] + 1 if count_result else 1
                matter_ref = f"MAT-{year}-{str(count).zfill(5)}"
                
                # Insert matter (status is always 'Pending Approval' for new matters)
                cursor.execute("""
                    INSERT INTO matters (
                        matter_reference_number, matter_title, matter_category,
                        client_id, client_name, client_phone, client_instructions,
                        assigned_employee_id, assigned_employee_name,
                        date_opened, status, created_by_id, created_by_name
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    matter_ref,
                    data['matter_title'].upper(),
                    data['matter_category'].upper(),
                    client['id'],
                    client['full_name'],
                    client.get('phone_number', ''),
                    data.get('client_instructions', ''),
                    employee['id'],
                    employee['full_name'],
                    data['date_opened'],
                    'Pending Approval',  # Always set to Pending Approval for new matters
                    creator['id'],
                    creator['full_name']
                ))
                
                connection.commit()
                matter_id = cursor.lastrowid
                
                return jsonify({
                    'success': True,
                    'message': 'Matter registered successfully',
                    'matter_id': matter_id,
                    'matter_reference_number': matter_ref
                })
        except Exception as e:
            connection.rollback()
            print(f"Error registering matter: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
        finally:
            connection.close()
    except Exception as e:
        print(f"Error in register matter API: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/api/matters/update/<int:matter_id>', methods=['PUT'])
def api_matters_update(matter_id):
    """API endpoint to update an existing matter"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    # Validate required fields
    required_fields = ['matter_title', 'matter_category', 'date_opened', 'status']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'success': False, 'error': f'{field} is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if matter exists and get current assignee
            cursor.execute("SELECT id, assigned_employee_id FROM matters WHERE id = %s", (matter_id,))
            matter_row = cursor.fetchone()
            if not matter_row:
                return jsonify({'success': False, 'error': 'Matter not found'}), 404
            
            # Keep current assignee if no new one is provided
            assignee_id = data.get('assigned_employee_id')
            if assignee_id is not None and assignee_id != '':
                try:
                    assignee_id = int(assignee_id)
                except (TypeError, ValueError):
                    return jsonify({'success': False, 'error': 'Invalid assigned_employee_id'}), 400
            else:
                assignee_id = matter_row['assigned_employee_id']

            # Get employee name
            cursor.execute("SELECT full_name FROM employees WHERE id = %s", (assignee_id,))
            employee = cursor.fetchone()
            if not employee:
                return jsonify({'success': False, 'error': 'Assigned employee not found'}), 400
            
            assigned_employee_name = employee['full_name']
            
            # Update matter
            cursor.execute("""
                UPDATE matters SET
                    matter_title = %s,
                    matter_category = %s,
                    assigned_employee_id = %s,
                    assigned_employee_name = %s,
                    date_opened = %s,
                    status = %s,
                    client_instructions = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                data['matter_title'].upper().strip(),
                data['matter_category'].upper().strip(),
                assignee_id,
                assigned_employee_name,
                data['date_opened'],
                data['status'],
                data.get('client_instructions', '').strip(),
                matter_id
            ))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Matter updated successfully'
            })
    except Exception as e:
        connection.rollback()
        print(f"Error updating matter: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()

@app.context_processor
def inject_my_task_badge():
    """Provide task and notification badge counts for nav UI."""
    try:
        employee_id = session.get('employee_id')
        if not employee_id:
            return {'my_task_badge_count': 0, 'notification_badge_count': 0}

        connection = get_db_connection()
        if not connection:
            return {'my_task_badge_count': 0, 'notification_badge_count': 0}

        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                ensure_task_management_table(cursor, connection)

                # Match "My Tasks" visibility, but only count active work items.
                cursor.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM task_management t
                    LEFT JOIN cases c
                        ON t.task_type = 'case' AND t.linked_id = c.id
                    LEFT JOIN matters m
                        ON t.task_type = 'matter' AND t.linked_id = m.id
                    WHERE
                        t.task_status IN ('Pending', 'In Progress')
                        AND (
                            (t.task_type = 'case' AND ((t.assigned_to_id IS NOT NULL AND t.assigned_to_id = %s) OR (t.assigned_to_id IS NULL AND c.filled_by_id = %s)))
                            OR
                            (t.task_type = 'matter' AND m.assigned_employee_id = %s)
                        )
                """, (employee_id, employee_id, employee_id))
                base_cnt_row = cursor.fetchone() or {}
                base_cnt = int(base_cnt_row.get('cnt') or 0)

                # Session allocations (court-session materials) are always actionable in My Tasks.
                session_cnt = 0
                try:
                    cursor.execute("""
                        SELECT COUNT(*) AS cnt
                        FROM case_proceeding_materials m
                        WHERE m.allocated_to_id = %s
                    """, (employee_id,))
                    session_cnt_row = cursor.fetchone() or {}
                    session_cnt = int(session_cnt_row.get('cnt') or 0)
                except Exception:
                    session_cnt = 0

                calendar_cnt = 0
                try:
                    from datetime import date, timedelta
                    start_date = date.today()
                    end_date = start_date + timedelta(days=14)
                    cursor.execute("""
                        SELECT COUNT(*) AS cnt
                        FROM case_proceedings p
                        INNER JOIN cases c ON c.id = p.case_id
                        WHERE p.next_court_date IS NOT NULL
                          AND p.next_court_date >= %s
                          AND p.next_court_date <= %s
                          AND c.filled_by_id = %s
                    """, (start_date, end_date, employee_id))
                    calendar_cnt_row = cursor.fetchone() or {}
                    calendar_cnt = int(calendar_cnt_row.get('cnt') or 0)
                except Exception:
                    calendar_cnt = 0

                my_task_badge_count = base_cnt + session_cnt
                return {
                    'my_task_badge_count': my_task_badge_count,
                    'notification_badge_count': my_task_badge_count + calendar_cnt
                }
        finally:
            connection.close()
    except Exception:
        return {'my_task_badge_count': 0, 'notification_badge_count': 0}

@app.before_request
def cleanup_idle_connections_before_request():
    """Clean up idle connections before each request"""
    try:
        cleanup_idle_connections(max_idle_minutes=30)
    except:
        pass  # Don't fail requests if cleanup fails

# Initialize database when app is loaded (runs for both 'python app.py' and WSGI/Passenger)
# This ensures tables and migrations are applied on the hosted side too
try:
    init_database()
except Exception as e:
    print(f"[WARNING] Database initialization failed (may be first run or DB not configured): {e}")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

