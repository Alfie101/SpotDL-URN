import os
import sys
import subprocess
import threading
import queue
import shutil
import platform
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
VENV_DIR = APP_DIR / ".venv"
IS_WINDOWS = platform.system() == "Windows"

# Helpers to get paths inside the venv

def _venv_python():
if IS_WINDOWS:
return VENV_DIR / "Scripts" / "python.exe"
else:
return VENV_DIR / "bin" / "python"


def _venv_pip():
if IS_WINDOWS:
return VENV_DIR / "Scripts" / "pip.exe"
else:
return VENV_DIR / "bin" / "pip"


def _venv_spotdl_exe():
# Prefer the entry-point script inside the venv
if IS_WINDOWS:
return VENV_DIR / "Scripts" / "spotdl.exe"
else:
return VENV_DIR / "bin" / "spotdl"


class SpotDLGUI(tk.Tk):
def __init__(self):
super().__init__()
self.title("SpotDL – Easy Downloader")
self.geometry("740x520")
self.minsize(680, 480)

self._build_ui()
self.log_queue = queue.Queue()
self.after(100, self._drain_log_queue)

# --------------------------- UI ----------------------------
def _build_ui(self):
pad = 10
main = ttk.Frame(self)
main.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

# Spotify URL
url_lbl = ttk.Label(main, text="Spotify link (track / album / playlist / artist):")
url_lbl.pack(anchor=tk.W)
self.url_var = tk.StringVar()
main()
