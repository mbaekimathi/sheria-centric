import sys
import os
import time

# Add the application directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

# Import the Flask application (can take 30–90s on first worker boot).
_started = time.monotonic()
from app import app

_elapsed = time.monotonic() - _started
print(f"[passenger] app import completed in {_elapsed:.1f}s", flush=True)

# Passenger requires 'application' variable
application = app
