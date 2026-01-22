from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
import pymysql
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import hashlib
from PIL import Image, ImageEnhance, ImageFilter
import base64
from io import BytesIO
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as google_requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
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

app = Flask(__name__)

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, will use environment variables directly

# Secret key from environment variable or generate one
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# Configuration
UPLOAD_FOLDER = 'static/uploads/profile_pictures'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_DOCUMENT_EXTENSIONS = {'pdf', 'doc', 'docx'}
ALLOWED_ID_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload folder if it doesn't exist
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
    """Detect if running locally or on cPanel and return appropriate DB config"""
    # Method 1: Use environment variables (most secure and reliable)
    db_host = os.environ.get('DB_HOST', 'localhost')
    db_user = os.environ.get('DB_USER')
    db_password = os.environ.get('DB_PASSWORD', '')  # Default to empty string
    db_name = os.environ.get('DB_NAME')
    
    # If all DB environment variables are set, use them
    if db_user and db_name:
        print("[OK] Using database configuration from environment variables")
        # Debug output (mask password for security)
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
    
    # Method 2: Check DB_ENV environment variable (fallback for quick switching)
    # Note: For security, prefer using individual DB_* environment variables
    if os.environ.get('DB_ENV') == 'cpanel':
        # Use environment variables for cPanel credentials
        print("[OK] Using cPanel database configuration (from DB_ENV)")
        return {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'user': os.environ.get('DB_USER', ''),
            'password': os.environ.get('DB_PASSWORD', ''),
            'database': os.environ.get('DB_NAME', ''),
            'charset': 'utf8mb4'
        }
    
    if os.environ.get('DB_ENV') == 'local':
        print("[OK] Using local database configuration (from DB_ENV)")
        return {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'user': os.environ.get('DB_USER', 'root'),
            'password': os.environ.get('DB_PASSWORD', ''),
            'database': os.environ.get('DB_NAME', 'sheria_centric_db'),
            'charset': 'utf8mb4'
        }
    
    # Method 3: Try to detect by testing local connection (development only)
    # This is a fallback for local development when env vars are not set
    # Only use this if FLASK_ENV is development or not set (assumes local dev)
    flask_env = os.environ.get('FLASK_ENV', '').lower()
    if flask_env in ('development', '') or flask_env == '':
        try:
            test_connection = pymysql.connect(
                host='localhost',
                user='root',
                password='',
                database='sheria_centric_db',
                charset='utf8mb4'
            )
            test_connection.close()
            print("[OK] Using local database configuration (auto-detected for development)")
            return {
                'host': 'localhost',
                'user': 'root',
                'password': '',
                'database': 'sheria_centric_db',
                'charset': 'utf8mb4'
            }
        except Exception as e:
            # If local connection fails, check if we're in production
            if flask_env == 'production':
                # In production, we must have environment variables
                raise ValueError(
                    "Database configuration not found. Please set DB_HOST, DB_USER, DB_PASSWORD, and DB_NAME "
                    "environment variables, or set DB_ENV=local/cpanel with appropriate DB_* variables."
                )
            # For development, still try local config even if connection test fails
            print("[WARNING] Local database connection test failed, but using local config for development")
            print(f"   Error: {e}")
            return {
                'host': 'localhost',
                'user': 'root',
                'password': '',
                'database': 'sheria_centric_db',
                'charset': 'utf8mb4'
            }
    
    # If no configuration found and not in development, raise an error
    raise ValueError(
        "Database configuration not found. Please set DB_HOST, DB_USER, DB_PASSWORD, and DB_NAME "
        "environment variables, or set DB_ENV=local/cpanel with appropriate DB_* variables."
    )

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
SCHEMA_VERSION = 14

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
                    ('email', 'VARCHAR(255)'),
                    ('contact_number', 'VARCHAR(20)'),
                    ('whatsapp_number', 'VARCHAR(20)'),
                    ('tiktok_link', 'VARCHAR(500)'),
                    ('instagram_link', 'VARCHAR(500)'),
                    ('fb_link', 'VARCHAR(500)'),
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
                            print(f"✓ Added column '{column_name}' to employees table")
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
                
                # Add columns for Corporate client requirements (CR-12 and post office address)
                if not column_exists('clients', 'cr12_certificate'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN cr12_certificate VARCHAR(500)")
                        connection.commit()
                        print("[OK] Added cr12_certificate column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add cr12_certificate column: {e}")
                
                if not column_exists('clients', 'post_office_address'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN post_office_address TEXT")
                        connection.commit()
                        print("✓ Added post_office_address column to clients table")
                    except Exception as e:
                        print(f"[WARNING] Could not add post_office_address column: {e}")
            return True
    except Exception as e:
        print(f"Error creating/updating clients table: {e}")
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
                print("✓ Email accounts table already exists")
        
        return True
    except Exception as e:
        print(f"Error creating email tables: {e}")
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
                            print(f"✓ Added column '{column_name}' to employees table")
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
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                            INDEX idx_case_id (case_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """)
                    connection.commit()
                    print("✓ Created case_parties table")
                
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
                    print("✓ outcome_details column already exists")
                
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
                    print("✓ Added virtual_link column to case_proceedings table")
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
                    print("✓ Added previous_proceeding_id column to case_proceedings table")
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

def init_database():
    """Initialize database system: check, create, and update as needed"""
    print("\n" + "="*50)
    print("SHERIA CENTRIC Database Initialization")
    print("="*50)
    
    # Step 1: Check and create database
    if not database_exists():
        print(f"Database '{DB_CONFIG['database']}' not found. Creating...")
        if not create_database():
            print("[ERROR] Failed to create database")
            return False
    else:
        print(f"[OK] Database '{DB_CONFIG['database']}' exists")
    
    # Step 2: Create schema version table
    if not create_schema_version_table():
        print("✗ Failed to create schema_version table")
        return False
    
    # Step 3: Create company_settings table
    if not create_company_settings_table():
        print("[ERROR] Failed to create/update company_settings table")
        return False
    
    # Step 4: Create employees table
    if not create_employees_table():
        print("[ERROR] Failed to create/update employees table")
        return False
    
    # Step 5: Create clients table
    if not create_clients_table():
        print("[ERROR] Failed to create/update clients table")
        return False
    
    # Step 6: Create case management tables
    if not create_case_tables():
        print("✗ Failed to create/update case management tables")
        return False
    
    # Step 7: Create matters table
    if not create_matters_table():
        print("[ERROR] Failed to create/update matters table")
        return False
    
    # Step 8: Create email tables
    if not create_email_tables():
        print("[ERROR] Failed to create/update email tables")
        return False
    
    # Step 9: Check schema version and apply migrations
    current_version = get_schema_version()
    print(f"Current schema version: {current_version}")
    print(f"Target schema version: {SCHEMA_VERSION}")
    
    if current_version < SCHEMA_VERSION:
        print("Schema updates detected. Applying migrations...")
        if not apply_migrations(current_version):
            print("✗ Failed to apply migrations")
            return False
    elif current_version == SCHEMA_VERSION:
        print("[OK] Database schema is up to date")
    else:
        print(f"[WARNING] Database schema version ({current_version}) is newer than application version ({SCHEMA_VERSION})")
    
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

@app.route('/')
def index():
    """Home page - redirects to login if not authenticated"""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Employee login page"""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    
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
    """Employee signup page"""
    if 'employee_id' in session:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip().upper()
        phone_number = request.form.get('phone_number', '').strip().upper()
        work_email = request.form.get('work_email', '').strip().lower()
        employee_code = request.form.get('employee_code', '').strip().upper()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        terms_accepted = request.form.get('terms_accepted')
        
        # Validation
        errors = []
        if not full_name:
            errors.append('Full name is required')
        if not phone_number:
            errors.append('Phone number is required')
        if not work_email:
            errors.append('Personal email is required')
        if not employee_code or len(employee_code) != 6:
            errors.append('Employee code must be 6 digits')
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
                flash('Employee code already exists', 'error')
            elif 'work_email' in str(e):
                flash('Personal email already registered', 'error')
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
    """Check if employee code is already in use"""
    try:
        data = request.get_json()
        employee_code = data.get('employee_code', '').strip().upper()
        
        if not employee_code or len(employee_code) != 6:
            return jsonify({'available': False, 'message': 'Employee code must be 6 digits'})
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'available': False, 'message': 'Database connection error'})
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT id FROM employees WHERE employee_code = %s", (employee_code,))
                result = cursor.fetchone()
                
                if result:
                    return jsonify({'available': False, 'message': 'Employee code already in use'})
                else:
                    return jsonify({'available': True, 'message': 'Employee code is available'})
        except Exception as e:
            print(f"Error checking employee code: {e}")
            return jsonify({'available': False, 'message': 'Error checking employee code'})
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
            
            return render_template('dashboard.html', employee=employee, company_settings=company_settings)
    except Exception as e:
        print(f"Dashboard error: {e}")
        flash('An error occurred.', 'error')
        return redirect(url_for('login'))
    finally:
        connection.close()

@app.route('/user_management')
def user_management():
    """User Management page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    # Check if user has permission (IT Support, Firm Administrator, or Managing Partner)
    user_role = session.get('employee_role')
    original_role = session.get('original_role')
    
    # Allow IT Support, Firm Administrator, Managing Partner, or if IT Support is role-switched
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
        # Get company settings
        company_settings = get_company_settings()
        if not company_settings:
            company_settings = {'company_name': 'BAUNI LAW GROUP'}
        
        return render_template('user_management.html', company_settings=company_settings)
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
    
    # Check permission
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
                SELECT id, full_name, phone_number, work_email, employee_code, role, status, created_at, onboarding_completed
                FROM employees 
                WHERE status = 'Pending Approval' AND onboarding_completed = TRUE
                ORDER BY created_at DESC
            """)
            employees = cursor.fetchall()
            
            # Convert datetime to string for JSON
            for emp in employees:
                if emp.get('created_at'):
                    emp['created_at'] = emp['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            return {'success': True, 'employees': employees, 'count': len(employees)}
    except Exception as e:
        print(f"Error fetching pending approvals: {e}")
        return {'error': str(e)}, 500
    finally:
        connection.close()

@app.route('/api/get_all_employees')
def get_all_employees():
    """Get all employees"""
    if 'employee_id' not in session:
        return {'error': 'Unauthorized'}, 401
    
    # Check permission
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
            
            # Convert datetime to string for JSON
            for emp in employees:
                if emp.get('created_at'):
                    emp['created_at'] = emp['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            return {'success': True, 'employees': employees}
    except Exception as e:
        print(f"Error fetching employees: {e}")
        return {'error': str(e)}, 500
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
            cursor.execute("""
                SELECT 
                    id, full_name, phone_number, work_email, employee_code, role, status,
                    account_number, account_name, salary, salary_components, tax_pin, pay_frequency,
                    employment_contract, id_front, id_back, signature, stamp,
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

@app.route('/logout')
def logout():
    """Logout user"""
    # Clear all session data including role switch
    session.clear()
    flash('You have been logged out successfully', 'success')
    return redirect(url_for('login'))

@app.route('/client_login')
def client_login():
    """Client login page with Google OAuth"""
    if 'client_id' in session:
        return redirect(url_for('client_dashboard'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('client_login.html', company_settings=company_settings)

@app.route('/google_login')
def google_login():
    """Initiate Google OAuth flow"""
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [request.url_root + "callback"]
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = url_for('google_callback', _external=True)
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
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
                    "redirect_uris": [request.url_root + "callback"]
                }
            },
            scopes=scopes_to_use,
            state=session['state']
        )
        flow.redirect_uri = url_for('google_callback', _external=True)
        
        authorization_response = request.url
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
                            "redirect_uris": [request.url_root + "callback"]
                        }
                    },
                    scopes=normalized_scopes  # Use exact normalized scopes from Google
                )
                flow.redirect_uri = url_for('google_callback', _external=True)
                flow.fetch_token(authorization_response=authorization_response)
            else:
                raise
        
        credentials = flow.credentials
        request_session = google_requests.Request()
        id_info = id_token.verify_oauth2_token(
            credentials.id_token, request_session, GOOGLE_CLIENT_ID
        )
        
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
                        flash('Please complete your registration', 'info')
                        return redirect(url_for('client_registration'))
                    
                    # Check Individual client requirements
                    if client_type == 'Individual':
                        if not client.get('phone_number') or not client.get('id_front') or not client.get('id_back'):
                            flash('Please complete your registration by providing phone number and ID documents', 'info')
                            return redirect(url_for('client_registration'))
                    
                    # Check Corporate client requirements
                    elif client_type == 'Corporate':
                        if not client.get('phone_number') or not client.get('cr12_certificate') or not client.get('post_office_address'):
                            flash('Please complete your registration by providing phone number, CR-12 certificate, and post office address', 'info')
                            return redirect(url_for('client_registration'))
                    
                    flash('Successfully logged in!', 'success')
                    return redirect(url_for('client_dashboard'))
            except Exception as e:
                print(f"Error processing client login: {e}")
                flash('An error occurred during login. Please try again.', 'error')
            finally:
                connection.close()
        
        return redirect(url_for('client_login'))
    except Exception as e:
        print(f"OAuth callback error: {e}")
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('client_login'))

@app.route('/client_dashboard')
def client_dashboard():
    """Client dashboard page"""
    if 'client_id' not in session:
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
                    post_office_address,
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
            
            # Check Individual client requirements
            if client_type == 'Individual':
                if not client.get('phone_number') or not client.get('id_front') or not client.get('id_back'):
                    return redirect(url_for('client_registration'))
            
            # Check Corporate client requirements
            elif client_type == 'Corporate':
                if not client.get('phone_number') or not client.get('cr12_certificate') or not client.get('post_office_address'):
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
            
            return render_template('client_dashboard.html', 
                                 client=client,
                                 cases=cases,
                                 matters=matters,
                                 company_settings=company_settings)
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
    """View documents for a specific client by document type (client access)"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    # Validate document type
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
            
            # Map document type to display name
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
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client documents: {e}")
        flash('An error occurred while fetching client information.', 'error')
        return redirect(url_for('client_dashboard'))
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
                ORDER BY date_of_court_appeared DESC, created_at DESC
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
            all_upcoming_proceedings = cursor.fetchall()
            
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
            all_upcoming_proceedings = cursor.fetchall()
            
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
    """Client messages page - displays messages between client and support team"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    connection = get_db_connection()
    if not connection:
        flash('Database connection error.', 'error')
        return redirect(url_for('client_dashboard'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Try to fetch messages from webapp_messages table if it exists
            # For now, we'll use an empty list if the table doesn't exist
            messages = []
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
                        m.created_at,
                        e.full_name as employee_name,
                        e.full_name as employee_full_name,
                        e.profile_picture as employee_profile_picture
                    FROM webapp_messages m
                    LEFT JOIN employees e ON m.employee_id = e.id
                    WHERE m.client_id = %s
                    ORDER BY m.created_at ASC
                """, (session['client_id'],))
                messages = cursor.fetchall()
                
                # Convert date objects to strings
                for msg in messages:
                    if msg.get('created_at'):
                        msg['created_at'] = msg['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                # Table doesn't exist or error fetching messages - use empty list
                print(f"Messages table may not exist or error fetching: {e}")
                messages = []
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('client_messages.html',
                                 messages=messages,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching client messages: {e}")
        flash('An error occurred while loading messages.', 'error')
        return redirect(url_for('client_dashboard'))
    finally:
        connection.close()

@app.route('/client_registration')
def client_registration():
    """Client registration page - complete profile with phone number"""
    if 'client_id' not in session:
        return redirect(url_for('client_login'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('client_registration.html', company_settings=company_settings)

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
    
    # Handle Individual client requirements (ID front and back)
    id_front = None
    id_back = None
    if client_type == 'Individual':
        # Handle ID front upload
        if 'id_front' in request.files:
            file = request.files['id_front']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"id_front_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                id_front = unique_filename
        
        if not id_front:
            flash('ID/Passport front image is required for Individual clients', 'error')
            return redirect(url_for('client_registration'))
        
        # Handle ID back upload
        if 'id_back' in request.files:
            file = request.files['id_back']
            if file and file.filename and allowed_id_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"id_back_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                id_back = unique_filename
        
        if not id_back:
            flash('ID/Passport back image is required for Individual clients', 'error')
            return redirect(url_for('client_registration'))
    
    # Handle Corporate client requirements (CR-12 certificate and post office address)
    cr12_certificate = None
    post_office_address = None
    if client_type == 'Corporate':
        # Handle CR-12 certificate upload
        if 'cr12_certificate' in request.files:
            file = request.files['cr12_certificate']
            if file and file.filename and allowed_document_file(file.filename):
                filename = secure_filename(file.filename)
                file_ext = filename.rsplit('.', 1)[1].lower()
                unique_filename = f"cr12_{session['client_id']}_{secrets.token_hex(8)}.{file_ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                cr12_certificate = unique_filename
        
        if not cr12_certificate:
            flash('CR-12 certificate is required for Corporate clients', 'error')
            return redirect(url_for('client_registration'))
        
        # Get post office address
        post_office_address = request.form.get('post_office_address', '').strip()
        if not post_office_address:
            flash('Post office address is required for Corporate clients', 'error')
            return redirect(url_for('client_registration'))
    
    # Update client in database
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor() as cursor:
                # Build update query based on client type and provided data
                update_fields = ['phone_number = %s', 'client_type = %s']
                update_values = [phone_number, client_type]
                
                if profile_picture:
                    update_fields.append('profile_picture = %s')
                    update_values.append(profile_picture)
                    session['client_profile_picture'] = profile_picture
                
                if client_type == 'Individual':
                    update_fields.append('id_front = %s')
                    update_fields.append('id_back = %s')
                    update_values.extend([id_front, id_back])
                elif client_type == 'Corporate':
                    update_fields.append('cr12_certificate = %s')
                    update_fields.append('post_office_address = %s')
                    update_values.extend([cr12_certificate, post_office_address])
                
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
        client_type = request.form.get('client_type', 'Individual')
        
        # Validation
        errors = []
        if not full_name:
            errors.append('Full name is required')
        if not phone_number:
            errors.append('Phone number is required')
        if not client_type or client_type not in ['Individual', 'Corporate']:
            errors.append('Client type is required')
        
        # Validate phone number format (Kenyan format: starts with 07)
        if phone_number and not (phone_number.startswith('07') or phone_number.startswith('+254')):
            errors.append('Please enter a valid Kenyan phone number (starting with 07)')
        
        # Validate client_type
        if client_type and client_type not in ['Individual', 'Corporate']:
            errors.append('Please select a valid client type')
        
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
            if profile_picture:
                cursor.execute("""
                    UPDATE clients 
                    SET full_name = %s, phone_number = %s, client_type = %s, profile_picture = %s
                    WHERE id = %s
                """, (full_name, phone_number, client_type, profile_picture, session['client_id']))
                # Update session
                session['client_profile_picture'] = profile_picture
            else:
                cursor.execute("""
                    UPDATE clients 
                    SET full_name = %s, phone_number = %s, client_type = %s
                    WHERE id = %s
                """, (full_name, phone_number, client_type, session['client_id']))
                # Keep existing profile picture in session
                if old_profile_picture:
                    if old_profile_picture.startswith('http'):
                        session['client_profile_picture'] = old_profile_picture
                    else:
                        session['client_profile_picture'] = old_profile_picture
            
            connection.commit()
            
            # Update session
            session['client_name'] = full_name
            session['client_type'] = client_type
            
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
    return redirect(url_for('client_login'))

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
    
    # Get form data
    tax_pin = request.form.get('tax_pin', '').strip().upper()
    payment_method = request.form.get('payment_method', '').strip()
    account_number = request.form.get('account_number', '').strip().upper()
    account_name = request.form.get('account_name', '').strip().upper()
    bank_name = request.form.get('bank_name', '').strip()
    mobile_money_company = request.form.get('mobile_money_company', '').strip()
    
    # Validation
    errors = []
    if not tax_pin:
        errors.append('Tax PIN is required')
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
            unique_filename = f"id_front_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            id_front = unique_filename
    
    if not id_front:
        flash('ID/Passport front upload is required', 'error')
        return redirect(url_for('onboarding'))
    
    # Handle ID back file upload
    id_back = None
    if 'id_back' in request.files:
        file = request.files['id_back']
        if file and file.filename and allowed_id_file(file.filename):
            filename = secure_filename(file.filename)
            # Create unique filename
            file_ext = filename.rsplit('.', 1)[1].lower()
            unique_filename = f"id_back_{employee_id}_{secrets.token_hex(8)}.{file_ext}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            id_back = unique_filename
    
    if not id_back:
        flash('ID/Passport back upload is required', 'error')
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
                  employment_contract, id_front, id_back, signature, signature_hash, 
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
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('hr_roles_permissions.html', company_settings=company_settings)

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

@app.route('/case_allocation')
def case_allocation():
    """Case Allocation & Coverage page"""
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
    
    return render_template('case_allocation.html', company_settings=company_settings)

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
    
    return render_template('finance_billing.html', company_settings=company_settings)

@app.route('/case_management')
def case_management():
    """Case Management page"""
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
    
    return render_template('case_management.html', company_settings=company_settings, employee_name=employee_name)

@app.route('/case_management/<int:case_id>')
def case_details(case_id):
    """Case Details page"""
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
        return redirect(url_for('case_management'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch case details with client and employee information
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
            
            # Fetch parties for this case
            cursor.execute("""
                SELECT 
                    id,
                    party_name,
                    party_type,
                    party_category,
                    firm_agent,
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
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('case_details.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
                                 parties=parties,
                                 company_settings=company_settings)
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
                    c.client_id,
                    c.client_name,
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
            
            # Fetch parties for this case
            cursor.execute("""
                SELECT 
                    id,
                    party_name,
                    party_type,
                    party_category,
                    firm_agent
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
                                 employee_name=employee_name)
    except Exception as e:
        print(f"Error fetching case for edit: {e}")
        flash('An error occurred while fetching case details.', 'error')
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
                ORDER BY date_of_court_appeared DESC, created_at DESC
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
                    c.filled_by_id,
                    c.filled_by_name
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
            
            return render_template('case_allocate.html', 
                                 case_data=case_data, 
                                 case_id=case_id,
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
    """API endpoint to add a new case proceeding"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
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
            
            # Insert proceeding
            cursor.execute("""
                INSERT INTO case_proceedings (
                    case_id, court_activity_type, court_room, judicial_officer,
                    date_of_court_appeared, outcome_orders, outcome_details, next_court_date, attendance, next_attendance, virtual_link, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data['case_id'],
                None,  # court_activity_type set to NULL
                None,  # court_room set to NULL
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
            
            message = 'Proceeding added successfully'
            if materials_added > 0:
                message += f' with {materials_added} material(s)'
            
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
    """API endpoint to update an existing case proceeding"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    
    # Validate required fields
    if not data.get('date_of_court_appeared'):
        return jsonify({'error': 'Date of Court Appeared is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Verify proceeding exists and get case_id
            cursor.execute("SELECT id, case_id FROM case_proceedings WHERE id = %s", (proceeding_id,))
            old_proceeding = cursor.fetchone()
            if not old_proceeding:
                return jsonify({'error': 'Proceeding not found'}), 404
            
            case_id = old_proceeding['case_id']
            
            # Create new proceeding record with previous_proceeding_id pointing to the old one
            cursor.execute("""
                INSERT INTO case_proceedings (
                    case_id, previous_proceeding_id, court_activity_type, court_room, judicial_officer,
                    date_of_court_appeared, outcome_orders, outcome_details, next_court_date, 
                    attendance, next_attendance, virtual_link, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                case_id,
                proceeding_id,  # previous_proceeding_id points to the old record
                None,  # court_activity_type set to NULL
                None,  # court_room set to NULL
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
            new_proceeding_id = cursor.lastrowid
            
            # Insert materials if provided
            if data.get('materials') and isinstance(data['materials'], list):
                for material in data['materials']:
                    if material.get('material_description'):
                        cursor.execute("""
                            INSERT INTO case_proceeding_materials (
                                proceeding_id, material_description, reminder_frequency,
                                allocated_to_id, allocated_to_name
                            ) VALUES (%s, %s, %s, %s, %s)
                        """, (
                            new_proceeding_id,
                            material['material_description'],
                            material.get('reminder_frequency') if material.get('reminder_frequency') else None,
                            material.get('allocated_to_id') if material.get('allocated_to_id') else None,
                            material.get('allocated_to_name') if material.get('allocated_to_name') else None
                        ))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Proceeding updated successfully. Previous record kept.',
                'proceeding_id': new_proceeding_id
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
    """API endpoint to search cases by client phone number or return all cases"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    phone_number = request.args.get('phone', '').strip()
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            client = None
            
            # If phone number is provided, find client with all details
            if phone_number:
                cursor.execute("""
                    SELECT 
                        id, 
                        google_id,
                        full_name, 
                        phone_number, 
                        email, 
                        profile_picture,
                        client_type,
                        status,
                        created_at,
                        updated_at
                    FROM clients 
                    WHERE phone_number LIKE %s AND status = 'Active'
                    LIMIT 1
                """, (f'%{phone_number}%',))
                client = cursor.fetchone()
            
            # Fetch cases based on whether client was found or not
            if phone_number and client:
                # Fetch cases for specific client with full client details
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
                    WHERE c.client_id = %s
                    ORDER BY c.filing_date DESC, c.created_at DESC
                """, (client['id'],))
                cases = cursor.fetchall()
                message = f'Found {len(cases)} case(s) for {client["full_name"]}'
            elif phone_number and not client:
                # Phone number provided but no client found
                return jsonify({
                    'cases': [],
                    'client': None,
                    'message': 'No client found with this phone number'
                })
            else:
                # No phone number provided, fetch all cases with client details
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
                    ORDER BY c.filing_date DESC, c.created_at DESC
                """)
                cases = cursor.fetchall()
                message = f'Displaying all {len(cases)} case(s)'
            
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

@app.route('/case_management/register')
def register_case():
    """Case Registration page"""
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
    
    return render_template('register_case.html', company_settings=company_settings, employee_name=employee_name)

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
    required_fields = ['client_id', 'client_name', 'case_type', 'filing_date', 'case_category', 'station', 'filled_by_id', 'filled_by_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    
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
                    tracking_number, court_case_number, client_id, client_name, case_type, filing_date, case_category, 
                    station, filled_by_id, filled_by_name, created_by_id, created_by_name, description, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                tracking_number,
                data.get('court_case_number', '').upper() if data.get('court_case_number') else None,
                data['client_id'],
                data['client_name'].upper(),
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
                            INSERT INTO case_parties (case_id, party_name, party_type, party_category, firm_agent)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (
                            case_id,
                            party['party_name'],
                            party['party_type'],
                            party.get('party_category') if party.get('party_category') else None,
                            party.get('firm_agent') if party.get('firm_agent') else None
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
    required_fields = ['client_id', 'client_name', 'case_type', 'filing_date', 'case_category', 'station', 'filled_by_id', 'filled_by_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
    
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
                            INSERT INTO case_parties (case_id, party_name, party_type, party_category, firm_agent)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (
                            case_id,
                            party['party_name'].upper() if isinstance(party['party_name'], str) else party['party_name'],
                            party['party_type'].upper() if isinstance(party['party_type'], str) else party['party_type'],
                            party.get('party_category').upper() if party.get('party_category') and isinstance(party.get('party_category'), str) else (party.get('party_category') if party.get('party_category') else None),
                            party.get('firm_agent').upper() if party.get('firm_agent') and isinstance(party.get('firm_agent'), str) else (party.get('firm_agent') if party.get('firm_agent') else None)
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
    """API endpoint to allocate a case to an employee"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'employee_id' not in data:
        return jsonify({'success': False, 'error': 'Employee ID is required'}), 400
    
    employee_id = data['employee_id']
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if case exists
            cursor.execute("SELECT id FROM cases WHERE id = %s", (case_id,))
            case = cursor.fetchone()
            
            if not case:
                return jsonify({'success': False, 'error': 'Case not found'}), 404
            
            # Get employee name
            cursor.execute("SELECT full_name FROM employees WHERE id = %s", (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return jsonify({'success': False, 'error': 'Employee not found'}), 400
            
            employee_name = employee['full_name']
            
            # Update the case allocation
            cursor.execute("""
                UPDATE cases 
                SET filled_by_id = %s, 
                    filled_by_name = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (employee_id, employee_name, case_id))
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

@app.route('/api/auth/google-drive/authorize')
def google_drive_authorize():
    """Initiate Google Drive OAuth flow with account selection"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        # Google Drive API scopes (openid is automatically added by Google)
        drive_scopes = [
            'openid',
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile'
        ]
        
        # Get the redirect URI using url_for to ensure it matches exactly
        redirect_uri = url_for('google_drive_callback', _external=True)
        
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
            scopes=drive_scopes
        )
        flow.redirect_uri = redirect_uri
        
        # Generate authorization URL with account selection
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='select_account consent'  # Force account selection and consent
        )
        # Store state in session for security
        session['google_drive_oauth_state'] = state
        
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
    if 'employee_id' not in session:
        return '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: "Unauthorized"}, "*"); window.close();</script>', 401
    
    try:
        state = session.get('google_drive_oauth_state')
        if not state:
            return '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: "Invalid state"}, "*"); window.close();</script>', 400
        
        # Validate state from request
        request_state = request.args.get('state')
        if request_state != state:
            return '<script>window.opener.postMessage({type: "GOOGLE_DRIVE_ERROR", error: "State mismatch"}, "*"); window.close();</script>', 400
        
        # Google Drive API scopes (must match authorize function, including openid)
        drive_scopes = [
            'openid',
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile'
        ]
        
        # Get the redirect URI using url_for to ensure it matches exactly
        redirect_uri = url_for('google_drive_callback', _external=True)
        
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
            scopes=scopes_to_use
        )
        flow.redirect_uri = redirect_uri
        
        authorization_response = request.url
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
                    scopes=normalized_scopes  # Use exact normalized scopes from Google
                )
                flow.redirect_uri = redirect_uri
                flow.fetch_token(authorization_response=authorization_response)
            else:
                raise
        
        credentials = flow.credentials
        
        # Get user info
        request_session = google_requests.Request()
        id_info = id_token.verify_oauth2_token(
            credentials.id_token, request_session, GOOGLE_CLIENT_ID
        )
        
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
    
    return render_template('documents_settings.html', company_settings=company_settings)

@app.route('/view_client_documents/<int:client_id>')
def view_client_documents(client_id):
    """View documents for a specific client"""
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
    allowed_roles = ['IT Support', 'Firm Administrator', 'Managing Partner']
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
    """Calendar page - displays all upcoming court dates across all cases"""
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
            all_upcoming_proceedings = cursor.fetchall()
            
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
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('calendar.html', 
                                 company_settings=company_settings,
                                 all_upcoming_proceedings=all_upcoming_proceedings,
                                 calendar_events=calendar_events)
    except Exception as e:
        print(f"Error fetching calendar: {e}")
        flash('An error occurred while fetching calendar.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/reminders')
def reminders():
    """Reminders page - displays all materials/reminders across all cases"""
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
            all_upcoming_proceedings = cursor.fetchall()
            
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
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('reminders.html', 
                                 company_settings=company_settings,
                                 proceedings_with_materials=proceedings_with_materials,
                                 all_reminders=all_reminders)
    except Exception as e:
        print(f"Error fetching reminders: {e}")
        flash('An error occurred while fetching reminders.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/calendar_reminders')
def calendar_reminders():
    """Calendar & Reminders page - redirects to calendar"""
    return redirect(url_for('calendar'))
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
            all_upcoming_proceedings = cursor.fetchall()
            
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
        
        # Fetch all emails from all email accounts
        if email_settings:
            for email_account in all_email_accounts:
                email_address = email_account.get('email_address') or email_account.get('email', '')
                if not email_address:
                    continue
                
                # Get password for this email account
                password = email_settings.get('main_email_password', '')
                if email_account.get('email_password'):
                    password = email_account['email_password']
                
                if password:
                    try:
                        # Fetch emails from this account
                        emails = fetch_emails_from_imap(
                            email_address,
                            password,
                            email_settings.get('imap_host', 'mail.baunilawgroup.com'),
                            int(email_settings.get('imap_port', 993)),
                            bool(email_settings.get('imap_use_ssl', True)),
                            limit=200  # Fetch more emails per account
                        )
                        
                        # Add email address to each email for identification
                        for email in emails:
                            email['account_email'] = email_address
                        
                        all_emails.extend(emails)
                    except Exception as e:
                        print(f"Error fetching emails for {email_address}: {e}")
                        import traceback
                        print(traceback.format_exc())
        
        # Sort all emails by date (newest first)
        all_emails.sort(key=lambda x: x.get('date', ''), reverse=True)
        
        return render_template('communication_messaging.html', 
                             company_settings=company_settings,
                             communication_type=communication_type,
                             email_accounts=all_email_accounts,
                             employees=employees,
                             all_emails=all_emails)
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
                conn.close()
                conn.logout()
        except:
            pass
        
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
                status, data = mail.select('INBOX')
                if status != 'OK':
                    raise Exception("Failed to select INBOX")
            except Exception as e:
                # Connection might be bad, close it and recreate
                print(f"Connection test failed, recreating: {e}")
                close_email_connection(email_address, imap_host, imap_port, 'imap')
                mail = get_email_connection(email_address, password, imap_host, imap_port, use_ssl, 'imap')
                status, data = mail.select('INBOX')
                if status != 'OK':
                    raise Exception("Failed to select INBOX after reconnect")
        except Exception as e:
            # If persistent connection fails, create a new one
            print(f"Using persistent connection failed, creating new: {e}")
            close_email_connection(email_address, imap_host, imap_port, 'imap')
            if use_ssl:
                mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            else:
                mail = imaplib.IMAP4(imap_host, imap_port)
            mail.login(email_address, password)
            status, data = mail.select('INBOX')
            if status != 'OK':
                raise Exception("Failed to select INBOX")
        
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
        for email_id in reversed(email_ids):
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
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('communication_settings.html', 
                         company_settings=company_settings,
                         email_settings=email_settings,
                         email_accounts=email_accounts)

# ==================== EMAIL API ROUTES ====================

@app.route('/api/email/settings/save', methods=['POST'])
def api_save_email_settings():
    """Save email settings"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
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

@app.route('/other_matters')
def other_matters():
    """Other Matters page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('other_matters.html', company_settings=company_settings)

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

@app.route('/api/matters/search', methods=['GET'])
def api_matters_search():
    """API endpoint to search matters or return all matters"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch all matters with client details
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
                ORDER BY m.date_opened DESC, m.created_at DESC
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
            
            return jsonify({
                'matters': matters,
                'message': f'Displaying all {len(matters)} matter(s)'
            })
    except Exception as e:
        print(f"Error searching matters: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/clients', methods=['GET'])
def api_matters_clients():
    """API endpoint to get clients with their matter counts, with optional search"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    search_query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get clients with their matter counts, with optional search
            if search_query:
                # Search by name or phone number
                cursor.execute("""
                    SELECT 
                        cl.id,
                        cl.full_name,
                        cl.phone_number,
                        cl.email,
                        cl.profile_picture,
                        cl.client_type,
                        cl.status as client_status,
                        COUNT(m.id) as matter_count
                    FROM clients cl
                    LEFT JOIN matters m ON cl.id = m.client_id
                    WHERE cl.status = 'Active'
                    AND (cl.full_name LIKE %s OR cl.phone_number LIKE %s)
                    GROUP BY cl.id, cl.full_name, cl.phone_number, cl.email, cl.profile_picture, cl.client_type, cl.status
                    HAVING COUNT(m.id) > 0
                    ORDER BY matter_count DESC, cl.full_name ASC
                """, (f'%{search_query}%', f'%{search_query}%'))
            else:
                # Get all clients with matters
                cursor.execute("""
                    SELECT 
                        cl.id,
                        cl.full_name,
                        cl.phone_number,
                        cl.email,
                        cl.profile_picture,
                        cl.client_type,
                        cl.status as client_status,
                        COUNT(m.id) as matter_count
                    FROM clients cl
                    LEFT JOIN matters m ON cl.id = m.client_id
                    WHERE cl.status = 'Active'
                    GROUP BY cl.id, cl.full_name, cl.phone_number, cl.email, cl.profile_picture, cl.client_type, cl.status
                    HAVING COUNT(m.id) > 0
                    ORDER BY matter_count DESC, cl.full_name ASC
                """)
            clients = cursor.fetchall()
            
            return jsonify({
                'clients': clients,
                'message': f'Found {len(clients)} client(s) with matters'
            })
    except Exception as e:
        print(f"Error fetching clients with matters: {e}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        connection.close()

@app.route('/api/matters/client/<int:client_id>', methods=['GET'])
def api_matters_by_client(client_id):
    """API endpoint to get all matters for a specific client"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch all matters for the client
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
                WHERE m.client_id = %s
                ORDER BY m.date_opened DESC, m.created_at DESC
            """, (client_id,))
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
    """API endpoint to get categories with their matter counts, with optional search"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    search_query = request.args.get('q', '').strip()
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get categories with their matter counts, with optional search
            if search_query:
                # Search by category name
                cursor.execute("""
                    SELECT 
                        m.matter_category as category_name,
                        COUNT(m.id) as matter_count
                    FROM matters m
                    WHERE m.matter_category LIKE %s
                    GROUP BY m.matter_category
                    ORDER BY matter_count DESC, m.matter_category ASC
                """, (f'%{search_query}%',))
            else:
                # Get all categories with matters
                cursor.execute("""
                    SELECT 
                        m.matter_category as category_name,
                        COUNT(m.id) as matter_count
                    FROM matters m
                    GROUP BY m.matter_category
                    ORDER BY matter_count DESC, m.matter_category ASC
                """)
            categories = cursor.fetchall()
            
            return jsonify({
                'categories': categories,
                'message': f'Found {len(categories)} category(ies) with matters'
            })
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
    """API endpoint to approve a matter (change status from 'Pending Approval' to 'Open')"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
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
            
            # Update the matter status to 'Open'
            cursor.execute("""
                UPDATE matters 
                SET status = 'Open', updated_at = NOW()
                WHERE id = %s
            """, (matter_id,))
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Matter approved successfully'
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
    """API endpoint to get all matters for a specific category"""
    if 'employee_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Fetch all matters for the category
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
                WHERE m.matter_category = %s
                ORDER BY m.date_opened DESC, m.created_at DESC
            """, (category_name,))
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

@app.route('/other_matters/register')
def register_matter():
    """Register New Matter page"""
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
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
            
            return render_template('matter_details.html', 
                                 matter_data=matter_data, 
                                 matter_id=matter_id,
                                 company_settings=company_settings)
    except Exception as e:
        print(f"Error fetching matter details: {e}")
        flash('An error occurred while fetching matter details.', 'error')
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
    """API endpoint to search clients by phone number for matters"""
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
                    AND phone_number LIKE %s
                    ORDER BY full_name ASC
                    LIMIT 20
                """, (f'%{query}%',))
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
        
        # Validate required fields
        required_fields = ['matter_title', 'matter_category', 'client_id', 'assigned_employee_id', 'date_opened', 'status']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'{field} is required'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection error'}), 500
        
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                # Get client info
                cursor.execute("SELECT id, full_name, phone_number FROM clients WHERE id = %s", (data['client_id'],))
                client = cursor.fetchone()
                if not client:
                    return jsonify({'success': False, 'error': 'Client not found'}), 404
                
                # Get assigned employee info
                cursor.execute("SELECT id, full_name FROM employees WHERE id = %s", (data['assigned_employee_id'],))
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
    required_fields = ['matter_title', 'matter_category', 'assigned_employee_id', 'date_opened', 'status']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'success': False, 'error': f'{field} is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if matter exists
            cursor.execute("SELECT id FROM matters WHERE id = %s", (matter_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'error': 'Matter not found'}), 404
            
            # Get employee name
            cursor.execute("SELECT full_name FROM employees WHERE id = %s", (data['assigned_employee_id'],))
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
                data['assigned_employee_id'],
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

@app.before_request
def cleanup_idle_connections_before_request():
    """Clean up idle connections before each request"""
    try:
        cleanup_idle_connections(max_idle_minutes=30)
    except:
        pass  # Don't fail requests if cleanup fails

if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    app.run(debug=True, host='0.0.0.0', port=5000)

