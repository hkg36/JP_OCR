import sys
import os

Single_mutex = None

def check_single_instance(MUTEX_NAME = "Global\\SnippingTool_SingleInstance_v1.0"):
    """Check if another instance is running and prevent multiple instances."""
    global Single_mutex

    if sys.platform == 'win32':
        # Win32 dependencies for mutex/single instance check
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # 定义 CreateMutexW
        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = [
            wintypes.LPVOID,      # lpMutexAttributes
            wintypes.BOOL,        # bInitialOwner
            wintypes.LPCWSTR      # lpName
        ]
        CreateMutexW.restype = wintypes.HANDLE
        ERROR_ALREADY_EXISTS = 183
        def create_mutex(name: str, initial_owner: bool = False) -> wintypes.HANDLE:
            handle = CreateMutexW(None, initial_owner, name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            return handle
        try:
            Single_mutex = create_mutex(MUTEX_NAME, False)
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
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
if __name__ == "__main__":
    check_single_instance()