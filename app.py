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
from google.auth.transport import requests
import json

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
    print("⚠ WARNING: OAuth insecure transport enabled (development mode only)")

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
    db_password = os.environ.get('DB_PASSWORD')
    db_name = os.environ.get('DB_NAME')
    
    # If all DB environment variables are set, use them
    if db_user and db_password and db_name:
        print(f"✓ Using database configuration from environment variables")
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
        print("✓ Using cPanel database configuration (from DB_ENV)")
        return {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'user': os.environ.get('DB_USER', ''),
            'password': os.environ.get('DB_PASSWORD', ''),
            'database': os.environ.get('DB_NAME', ''),
            'charset': 'utf8mb4'
        }
    
    if os.environ.get('DB_ENV') == 'local':
        print("✓ Using local database configuration (from DB_ENV)")
        return {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'user': os.environ.get('DB_USER', 'root'),
            'password': os.environ.get('DB_PASSWORD', ''),
            'database': os.environ.get('DB_NAME', 'casely_db'),
            'charset': 'utf8mb4'
        }
    
    # Method 3: Try to detect by testing local connection (development only)
    # This is a fallback for local development when env vars are not set
    if os.environ.get('FLASK_ENV') == 'development':
        try:
            test_connection = pymysql.connect(
                host='localhost',
                user='root',
                password='',
                database='casely_db',
                charset='utf8mb4'
            )
            test_connection.close()
            print("✓ Using local database configuration (auto-detected for development)")
            return {
                'host': 'localhost',
                'user': 'root',
                'password': '',
                'database': 'casely_db',
                'charset': 'utf8mb4'
            }
        except:
            pass
    
    # If no configuration found, raise an error
    raise ValueError(
        "Database configuration not found. Please set DB_HOST, DB_USER, DB_PASSWORD, and DB_NAME "
        "environment variables, or set DB_ENV=local/cpanel with appropriate DB_* variables."
    )

# Initialize DB_CONFIG
DB_CONFIG = get_db_config()

# Schema version for migrations
SCHEMA_VERSION = 3

def get_db_connection(use_database=True):
    """Create and return database connection"""
    try:
        config = DB_CONFIG.copy()
        if not use_database:
            config.pop('database', None)
        connection = pymysql.connect(**config)
        return connection
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
            print(f"✓ Database '{DB_CONFIG['database']}' checked/created")
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
            print("✓ Schema version table checked/created")
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
                print("✓ Company settings table created")
                
                # Insert default company settings
                cursor.execute("""
                    INSERT INTO company_settings 
                    (company_name, email, contact_number, whatsapp_number, location_name)
                    VALUES ('BAUNI LAW GROUP', NULL, NULL, NULL, NULL)
                """)
                connection.commit()
                print("✓ Default company settings inserted")
            else:
                print("✓ Company settings table already exists")
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
                    ('created_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
                    ('updated_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')
                ]
                
                for column_name, column_def in columns_to_check:
                    if not column_exists('company_settings', column_name):
                        try:
                            cursor.execute(f"ALTER TABLE company_settings ADD COLUMN {column_name} {column_def}")
                            connection.commit()
                            print(f"✓ Added column '{column_name}' to company_settings table")
                        except Exception as e:
                            print(f"⚠ Could not add column '{column_name}': {e}")
            
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
                print("✓ Employees table created")
            else:
                print("✓ Employees table already exists")
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
                            print(f"⚠ Could not add column '{column_name}': {e}")
                
                # Add onboarding columns if they don't exist
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
                            print(f"✓ Added onboarding column '{column_name}' to employees table")
                        except Exception as e:
                            print(f"⚠ Could not add column '{column_name}': {e}")
            
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
                print("✓ Clients table created")
            else:
                print("✓ Clients table already exists")
                # Check and add phone_number column if it doesn't exist
                if not column_exists('clients', 'phone_number'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN phone_number VARCHAR(20)")
                        connection.commit()
                        print("✓ Added phone_number column to clients table")
                    except Exception as e:
                        print(f"⚠ Could not add phone_number column: {e}")
                
                # Update client_type ENUM to include 'Pending' if needed
                try:
                    cursor.execute("""
                        ALTER TABLE clients 
                        MODIFY COLUMN client_type ENUM('Pending', 'Individual', 'Corporate') DEFAULT 'Pending'
                    """)
                    connection.commit()
                    print("✓ Updated client_type ENUM to include 'Pending'")
                except Exception as e:
                    # If error, try to check if 'Pending' already exists
                    if 'Duplicate' not in str(e) and 'already exists' not in str(e).lower():
                        print(f"⚠ Could not update client_type ENUM: {e}")
                
                # Add columns for Individual client requirements (ID front and back)
                if not column_exists('clients', 'id_front'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN id_front VARCHAR(500)")
                        connection.commit()
                        print("✓ Added id_front column to clients table")
                    except Exception as e:
                        print(f"⚠ Could not add id_front column: {e}")
                
                if not column_exists('clients', 'id_back'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN id_back VARCHAR(500)")
                        connection.commit()
                        print("✓ Added id_back column to clients table")
                    except Exception as e:
                        print(f"⚠ Could not add id_back column: {e}")
                
                # Add columns for Corporate client requirements (CR-12 and post office address)
                if not column_exists('clients', 'cr12_certificate'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN cr12_certificate VARCHAR(500)")
                        connection.commit()
                        print("✓ Added cr12_certificate column to clients table")
                    except Exception as e:
                        print(f"⚠ Could not add cr12_certificate column: {e}")
                
                if not column_exists('clients', 'post_office_address'):
                    try:
                        cursor.execute("ALTER TABLE clients ADD COLUMN post_office_address TEXT")
                        connection.commit()
                        print("✓ Added post_office_address column to clients table")
                    except Exception as e:
                        print(f"⚠ Could not add post_office_address column: {e}")
            return True
    except Exception as e:
        print(f"Error creating/updating clients table: {e}")
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
                    print("✓ Created company_settings table")
                
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
                    print(f"✓ Inserted default company settings with name: {company_name}")
                
                # Remove company_name column from employees table if it exists
                if column_exists('employees', 'company_name'):
                    try:
                        cursor.execute("ALTER TABLE employees DROP COLUMN company_name")
                        connection.commit()
                        print("✓ Removed company_name column from employees table")
                    except Exception as e:
                        print(f"⚠ Could not remove company_name column: {e}")
                
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
                            print(f"⚠ Could not add column '{column_name}': {e}")
                
                migrations_applied = True
            
            # Migration 1: Ensure all required columns exist (for older versions)
            if current_version < 1:
                print("Applying migration 1: Schema updates...")
                migrations_applied = True
            
            if migrations_applied:
                connection.commit()
                update_schema_version(SCHEMA_VERSION)
                print(f"✓ Migrations applied. Schema version updated to {SCHEMA_VERSION}")
        
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
    print("CASELY Database Initialization")
    print("="*50)
    
    # Step 1: Check and create database
    if not database_exists():
        print(f"Database '{DB_CONFIG['database']}' not found. Creating...")
        if not create_database():
            print("✗ Failed to create database")
            return False
    else:
        print(f"✓ Database '{DB_CONFIG['database']}' exists")
    
    # Step 2: Create schema version table
    if not create_schema_version_table():
        print("✗ Failed to create schema_version table")
        return False
    
    # Step 3: Create company_settings table
    if not create_company_settings_table():
        print("✗ Failed to create/update company_settings table")
        return False
    
    # Step 4: Create employees table
    if not create_employees_table():
        print("✗ Failed to create/update employees table")
        return False
    
    # Step 5: Create clients table
    if not create_clients_table():
        print("✗ Failed to create/update clients table")
        return False
    
    # Step 6: Check schema version and apply migrations
    current_version = get_schema_version()
    print(f"Current schema version: {current_version}")
    print(f"Target schema version: {SCHEMA_VERSION}")
    
    if current_version < SCHEMA_VERSION:
        print("Schema updates detected. Applying migrations...")
        if not apply_migrations(current_version):
            print("✗ Failed to apply migrations")
            return False
    elif current_version == SCHEMA_VERSION:
        print("✓ Database schema is up to date")
    else:
        print(f"⚠ Warning: Database schema version ({current_version}) is newer than application version ({SCHEMA_VERSION})")
    
    print("="*50)
    print("✓ Database initialization completed successfully")
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
                        flash('Your account is pending approval. Please wait for administrator approval.', 'warning')
                        return render_template('login.html')
                    else:
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
            errors.append('Work email is required')
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
                flash('Work email already registered', 'error')
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
            
            # Check if onboarding is completed, redirect if not
            if employee.get('status') == 'Active' and not employee.get('onboarding_completed'):
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
                SELECT id, full_name, phone_number, work_email, employee_code, role, status, created_at
                FROM employees 
                WHERE status = 'Pending Approval'
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
            
            return {'success': True, 'employee': employee}
    except Exception as e:
        print(f"Error fetching employee: {e}")
        return {'error': str(e)}, 500
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
        return {'error': 'Employee ID and status required'}, 400
    
    if new_status not in ['Active', 'Suspended', 'Pending Approval']:
        return {'error': 'Invalid status'}, 400
    
    connection = get_db_connection()
    if not connection:
        return {'error': 'Database error'}, 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE employees 
                SET status = %s 
                WHERE id = %s
            """, (new_status, employee_id))
            connection.commit()
            
            return {'success': True, 'message': 'Status updated successfully'}
    except Exception as e:
        print(f"Error updating employee status: {e}")
        return {'error': str(e)}, 500
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
            scopes=SCOPES,
            state=session['state']
        )
        flow.redirect_uri = url_for('google_callback', _external=True)
        
        authorization_response = request.url
        flow.fetch_token(authorization_response=authorization_response)
        
        credentials = flow.credentials
        request_session = requests.Request()
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
    
    # Check if client has completed registration
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT phone_number, client_type, id_front, id_back, cr12_certificate, post_office_address FROM clients WHERE id = %s", (session['client_id'],))
                client = cursor.fetchone()
                if client:
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
        except Exception as e:
            print(f"Error checking client registration: {e}")
        finally:
            connection.close()
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    # Set company name in session for header display
    session['company_name'] = company_settings.get('company_name', 'BAUNI LAW GROUP')
    
    return render_template('client_dashboard.html', company_settings=company_settings)

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

@app.route('/employee_directory')
def employee_directory():
    """Employee Directory page"""
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
    
    return render_template('employee_directory.html', company_settings=company_settings)

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
                flash('You have already completed onboarding.', 'info')
                return redirect(url_for('dashboard'))
            
            # Check if employee is Active (approved)
            if employee.get('status') != 'Active':
                flash('Your account must be approved before completing onboarding.', 'error')
                return redirect(url_for('dashboard'))
            
            company_settings = get_company_settings()
            if not company_settings:
                company_settings = {'company_name': 'BAUNI LAW GROUP'}
            
            return render_template('onboarding.html', employee=employee, company_settings=company_settings)
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
    account_number = request.form.get('account_number', '').strip().upper()
    account_name = request.form.get('account_name', '').strip().upper()
    tax_pin = request.form.get('tax_pin', '').strip().upper()
    
    # Checkboxes
    nda_accepted = request.form.get('nda_accepted') == 'on'
    code_of_conduct_accepted = request.form.get('code_of_conduct_accepted') == 'on'
    health_safety_accepted = request.form.get('health_safety_accepted') == 'on'
    
    # Validation
    errors = []
    if not account_number:
        errors.append('Account number is required')
    if not account_name:
        errors.append('Account name is required')
    if not tax_pin:
        errors.append('Tax PIN is required')
    if not nda_accepted:
        errors.append('NDA acceptance is required')
    if not code_of_conduct_accepted:
        errors.append('Code of Conduct acceptance is required')
    if not health_safety_accepted:
        errors.append('Health & Safety Declaration acceptance is required')
    
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
                    employment_contract = %s,
                    id_front = %s,
                    id_back = %s,
                    signature = %s,
                    signature_hash = %s,
                    stamp = %s,
                    stamp_hash = %s,
                    nda_accepted = %s,
                    code_of_conduct_accepted = %s,
                    health_safety_accepted = %s,
                    onboarding_completed = TRUE
                WHERE id = %s
            """, (account_number, account_name, tax_pin, employment_contract, 
                  id_front, id_back, signature, signature_hash, stamp, stamp_hash, 
                  nda_accepted, code_of_conduct_accepted, health_safety_accepted, employee_id))
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
    
    return render_template('case_management.html', company_settings=company_settings)

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
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('document_management.html', company_settings=company_settings)

@app.route('/individual_client_documents')
def individual_client_documents():
    """Individual Client Documents page"""
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
    
    return render_template('individual_client_documents.html', company_settings=company_settings)

@app.route('/corporate_client_documents')
def corporate_client_documents():
    """Corporate Client Documents page"""
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
    
    return render_template('corporate_client_documents.html', company_settings=company_settings)

@app.route('/employee_documents')
def employee_documents():
    """Employee Documents page"""
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
    
    return render_template('employee_documents.html', company_settings=company_settings)

@app.route('/personal_documents')
def personal_documents():
    """Personal Documents page for employees"""
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
    
    return render_template('personal_documents.html', company_settings=company_settings)

@app.route('/work_documents')
def work_documents():
    """Work Documents page for employees"""
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
    
    return render_template('work_documents.html', company_settings=company_settings)

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
        return redirect(url_for('employee_documents'))

@app.route('/calendar_reminders')
def calendar_reminders():
    """Calendar & Reminders page"""
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
    
    return render_template('calendar_reminders.html', company_settings=company_settings)

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
    
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    
    return render_template('communication_messaging.html', company_settings=company_settings)

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

@app.route('/contract/nda')
def contract_nda():
    """Non-Disclosure Agreement (NDA) contract page"""
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    return render_template('contracts/nda.html', company_settings=company_settings)

@app.route('/contract/code_of_conduct')
def contract_code_of_conduct():
    """Code of Conduct contract page"""
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    return render_template('contracts/code_of_conduct.html', company_settings=company_settings)

@app.route('/contract/health_safety')
def contract_health_safety():
    """Health & Safety Declaration contract page"""
    company_settings = get_company_settings()
    if not company_settings:
        company_settings = {'company_name': 'BAUNI LAW GROUP'}
    return render_template('contracts/health_safety.html', company_settings=company_settings)

if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    app.run(debug=True, host='0.0.0.0', port=5000)

