"""Delete the reserved-name file 'nul' using Win32 DeleteFileW with extended path."""
import ctypes
import os
import sys

# bgf_auto path
project = r'C:\Users\kanur\OneDrive\바탕 화면\bbb\bgf_auto'
abs_path = os.path.join(project, 'nul')
extended = '\\\\?\\' + abs_path

print('target  :', abs_path)
print('extended:', extended)

DeleteFileW = ctypes.windll.kernel32.DeleteFileW
DeleteFileW.argtypes = [ctypes.c_wchar_p]
DeleteFileW.restype = ctypes.c_bool
GetLastError = ctypes.windll.kernel32.GetLastError

ok = DeleteFileW(extended)
print('DeleteFileW result:', ok)
if not ok:
    print('LastError:', GetLastError())
    sys.exit(1)
print('OK deleted')
