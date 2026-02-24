"""PythonAnywhere WSGI entry point.

PythonAnywhere WSGI 설정 파일에서 이 파일을 import합니다:

    import sys
    path = '/home/USERNAME/bgf-dashboard'
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application
"""

from src.web.app import create_app

application = create_app()
