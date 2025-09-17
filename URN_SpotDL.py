# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# SpotDL GUI - minimal, Windows-friendly, ASCII-only strings to avoid quote/encoding issues.
# Save as spotdl_gui.py and run with Python 3.10+.

import os
import sys
import subprocess
import threading
import queue
import platform
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Platform
IS_WINDOWS = platform.system() == 'Windows'
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    raise SystemExit('Tkinter is not installed. Install Python from python.org (not MS Store).')

# Early Windows message box for startup errors
if IS_WINDOWS:
    try:
        import ctypes
        def _win_box(msg, title='SpotDL GUI'):
            try:
                ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), 0x10)
            except Exception:
                pass
    except Exception:
        def _win_box(msg, title='SpotDL GUI'):
            pass
else:
    def _win_box(msg, title='SpotDL GUI'):
        pass

# Paths and logging
APP_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
VENV_DIR = APP_DIR / '.venv'
local_appdata = os.getenv('LOCALAPPDATA')
if local_appdata:
    LOG_DIR = Path(local_appdata) / 'SpotDL-GUI'
else:
    LOG_DIR = APP_DIR
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = LOG_DIR / 'spotdl_gui.log'
except Exception:
    LOG_FILE = APP_DIR / 'spotdl_gui.log'

logger = logging.getLogger('spotdl_gui')
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=512000, backupCount=2, encoding='utf-8')
_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
logger.addHandler(_handler)
logger.info('=== App start ===')
logger.info('Logging to: %s', LOG_FILE)

# Venv helpers

def _venv_python():
    return VENV_DIR / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')

def _venv_pip():
    return VENV_DIR / ('Scripts/pip.exe' if IS_WINDOWS else 'bin/pip')

def _venv_spotdl():
    return VENV_DIR / ('Scripts/spotdl.exe' if IS_WINDOWS else 'bin/spotdl')


class SpotDLGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SpotDL - Easy Downloader')
        self.geometry('760x560')
        self.minsize(680, 480)
        self._build_ui()
        self.log_queue = queue.Queue()
        self.after(120, self._drain_log_queue)
        self.last_run = APP_DIR / 'last_run.log'

    def _build_ui(self):
        pad = 10
        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        ttk.Label(main, text='Spotify link (track / album / playlist / artist):').pack(anchor=tk.W)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(main, textvariable=self.url_var)
        self.url_entry.pack(fill=tk.X, expand=True)

        out_row = ttk.Frame(main)
        out_row.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(out_row, text='Output folder:').pack(side=tk.LEFT)
        self.output_var = tk.StringVar(value=str(APP_DIR / 'downloads'))
        self.output_entry = ttk.Entry(out_row, textvariable=self.output_var)
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(pad, 0))
        ttk.Button(out_row, text='Browse...', command=self._choose_output_dir).pack(side=tk.LEFT, padx=(pad, 0))

        opt_row = ttk.Frame(main)
        opt_row.pack(fill=tk.X, pady=(pad, 0))
        self.threads_var = tk.IntVar(value=2)
        ttk.Label(opt_row, text='Threads:').pack(side=tk.LEFT)
        ttk.Spinbox(opt_row, from_=1, to=8, textvariable=self.threads_var, width=5).pack(side=tk.LEFT)
        self.overwrite_mode = tk.StringVar(value='skip')
        ttk.Label(opt_row, text='  Overwrite:').pack(side=tk.LEFT)
        ttk.Combobox(opt_row, values=['skip', 'force', 'prompt'], textvariable=self.overwrite_mode, state='readonly', width=8).pack(side=tk.LEFT)

        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=(pad, 0))
        self.download_btn = ttk.Button(btn_row, text='Download', command=self._on_download)
        self.download_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_row, text='Stop', command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(pad, 0))
        self.openlog_btn = ttk.Button(btn_row, text='Open log', command=self._open_last_log)
        self.openlog_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.status_lbl = ttk.Label(btn_row, text='Idle')
        self.status_lbl.pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(main, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(pad, 0))

        log_frame = ttk.LabelFrame(main, text='Log')
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(pad, 0))
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_var.get())
        if chosen:
            self.output_var.set(chosen)

    def log(self, msg):
        logger.info(msg)
        self.log_queue.put(msg)

    def _drain_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + '\n')
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        finally:
            self.after(150, self._drain_log_queue)

    def _on_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning('Missing link', 'Please paste a Spotify link.')
            return
        out_dir = Path(self.output_var.get()).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        self.download_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress.start(12)
        self.status_lbl.configure(text='Setting up...')
        self.log('Starting...')

        self.stop_flag = threading.Event()
        t = threading.Thread(target=self._worker_download, args=(url, out_dir), daemon=True)
        t.start()

    def _on_stop(self):
        if hasattr(self, 'proc') and self.proc and self.proc.poll() is None:
            self.stop_flag.set()
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.status_lbl.configure(text='Stopped')
        self.progress.stop()
        self.download_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _worker_download(self, url, out_dir):
        try:
            self.status_lbl.configure(text='Preparing environment...')
            self._ensure_env()

            ffmpeg_dir = self._get_ffmpeg_dir_from_venv()
            if not ffmpeg_dir or not Path(ffmpeg_dir).exists():
                self.log('FFmpeg not located automatically.')
                messagebox.showerror('FFmpeg not found', 'Could not auto-provision FFmpeg. Install FFmpeg or add it to PATH.')
                self._reset_ui_done(error=True)
                return

            spotdl_exe = _venv_spotdl()
            if not spotdl_exe.exists():
                spotdl_cmd = [str(_venv_python()), '-m', 'spotdl']
            else:
                spotdl_cmd = [str(spotdl_exe)]

            cmd = spotdl_cmd + [
                url,
                '--output', str(out_dir),
                '--threads', str(self.threads_var.get()),
                '--overwrite', self.overwrite_mode.get(),
                '--preload'
            ]

            env = os.environ.copy()
            venv_bin = str(_venv_spotdl().parent)
            env['PATH'] = os.pathsep.join([ffmpeg_dir, venv_bin, env.get('PATH', '')])

            self.status_lbl.configure(text='Downloading...')
            self.log('Running: ' + ' '.join(cmd))

            creationflags = 0x08000000 if IS_WINDOWS else 0
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(APP_DIR),
                text=True,
                creationflags=creationflags,
            )

            with open(self.last_run, 'w', encoding='utf-8', errors='ignore') as raw:
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    raw.write(line)
                    self.log(line.rstrip())
                    if self.stop_flag.is_set():
                        break

            ret = self.proc.wait()
            if self.stop_flag.is_set():
                self.log('Download cancelled by user.')
                self._reset_ui_done(error=True)
                return

            if ret == 0:
                self.log('Done! Files saved to: ' + str(out_dir))
                self.status_lbl.configure(text='Done')
                messagebox.showinfo('Download complete', 'Finished. Files are in:\n' + str(out_dir))
            else:
                self.log('spotDL exited with code ' + str(ret))
                self.status_lbl.configure(text='Failed')
                tail = ''
                try:
                    with open(self.last_run, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()[-60:]
                        tail = ''.join(lines)
                except Exception:
                    tail = '(could not read last_run.log)'
                try:
                    messagebox.showerror('Failed', 'spotDL exited with code ' + str(ret) + '\n\nLast output (tail):\n' + tail)
                except Exception:
                    _win_box('spotDL failed. See last_run.log for details.')
        except Exception as e:
            logger.exception('Worker crashed')
            self.log('Error: ' + str(e))
            self.status_lbl.configure(text='Error')
            try:
                messagebox.showerror('Error', str(e))
            except Exception:
                _win_box('SpotDL GUI error: ' + str(e))
        finally:
            self._reset_ui_done()

    def _open_last_log(self):
        target = self.last_run
        if IS_WINDOWS:
            try:
                os.startfile(str(target))
                return
            except Exception:
                pass
        try:
            subprocess.Popen(['notepad', str(
