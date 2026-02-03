import sys
import os

Single_mutex = None

def check_single_instance(MUTEX_NAME = "Global\\SnippingTool_SingleInstance_v1.0"):
    """Check if another instance is running and prevent multiple instances."""
    global Single_mutex

    if sys.platform == 'win32':
        # Win32 dependencies for mutex/single instance check
        import win32api, winerror, win32event
        try:
            Single_mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                print("Another instance is already running.")
                sys.exit(1)
        except Exception as ae:
            print("Failed to check single instance:", ae)
            sys.exit(1)
    else:
        # Linux / Unix implementation using file locking
        import fcntl
        import tempfile
        
        # Create a valid filename from the mutex name
        lock_filename = MUTEX_NAME.replace('\\', '_').replace('/', '_') + '.lock'
        lock_file_path = os.path.join(tempfile.gettempdir(), lock_filename)
        
        try:
            # Open file for writing; create if not exists
            Single_mutex = open(lock_file_path, 'w')
            # Try to acquire an exclusive lock without blocking
            fcntl.lockf(Single_mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            print("Another instance is already running.")
            sys.exit(1)
        except Exception as e:
            print(f"Failed to check single instance: {e}")
            sys.exit(1)