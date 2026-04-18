import os
import sys

# Setting PYTHONPATH
cwd = os.getcwd()
(setPath, fronEnd) = os.path.split(cwd)
sys.path.append(setPath)

# Dynamically resolve repo root so images always load correctly
_this_file = os.path.abspath(__file__)        # .../src/frontEnd/pathmagic.py
_frontEnd_dir = os.path.dirname(_this_file)   # .../src/frontEnd/
_src_dir = os.path.dirname(_frontEnd_dir)     # .../src/
_repo_root = os.path.dirname(_src_dir)        # .../esim--chatbot/

init_path = _repo_root + '/'
