import sys
import os

# root 디렉터리를 sys.path에 추가하여 app.py 및 tools.py를 안전하게 참조
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app
