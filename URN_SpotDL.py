#!/usr/bin/env python3
# SpotDL GUI – Windows-friendly build (no admin, no console).
# Rewritten to avoid triple-quoted strings that can cause unterminated literal errors.

import os
import sys
import subprocess
import threading
import queue
import platform
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ----- Platform helpers -----
IS_WINDOWS = platform.system() == 'Windows'
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception as e:  # Tk not available
    raise SystemExit("Tkinter is not installed. Please install Python from python.org (not the MS Store).")

# Simple Windows message box for very-early errors
if IS_WINDOWS:
    try:
        import ctypes
        def _win_message_box(text, title='SpotDL GUI'):
            try:
                ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x10)
            except Exception:
                pass
    except Exception:
        def _win_message_box(text, title='SpotDL GUI'):
            pass
else:
    def _win_message_box(text, title='SpotDL GUI'):
        pass

# ----- Paths & logging -----
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
_handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding='utf-8')
_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
logger.addHandler(_handler)
logger.info('=== App start ===')
logger.info('Logging to: %s', LOG_FILE)

# ----- Venv helpers -----

def _venv_python():
    return VENV_DIR / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')


def _venv_pip():
    return VENV_DIR / ('Scripts/pip.exe' if IS_WINDOWS else 'bin/pip')


def _venv_spotdl_exe():
    return VENV_DIR / ('Scripts/spotdl.exe' if IS_WINDOWS else 'bin/spotdl')


class SpotDLGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SpotDL – Easy Downloader')
        self.geometry('760x560')
        self.minsize(680, 480)
        self._build_ui()
        self.log_queue = queue.Queue()
        self.after(120, self._drain_log_queue)

    # UI
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
        ttk.Button(out_row, text='Browse…', command=self._choose_output_dir).pack(side=tk.LEFT, padx=(pad, 0))

        opt_row = ttk.Frame(main)
        opt_row.pack(fill=tk.X, pady=(pad, 0))
        self.threads_var = tk.IntVar(value=3)
        ttk.Label(opt_row, text='Threads:').pack(side=tk.LEFT)
        ttk.Spinbox(opt_row, from_=1, to=max(32, self.threads_var.get()), textvariable=self.threads_var, width=5).pack(side=tk.LEFT)
        self.overwrite_mode = tk.StringVar(value='skip')
        ttk.Label(opt_row, text='  Overwrite:').pack(side=tk.LEFT)
        ttk.Combobox(opt_row, values=['skip', 'force', 'prompt'], textvariable=self.overwrite_mode, state='readonly', width=8).pack(side=tk.LEFT)

        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=(pad, 0))
        self.download_btn = ttk.Button(btn_row, text='Download', command=self._on_download)
        self.download_btn.pack(side=tk.LEFT)
        self.diag_btn = ttk.Button(btn_row, text='Open log', command=self._open_last_log)
        self.diag_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_btn = ttk.Button(btn_row, text='Stop', command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(pad, 0))
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

    # Logging bridge to UI
    def log(self, msg):
        logger.info(msg)
        self.log_queue.put(msg)

    def _drain_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + '
')
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        finally:
            self.after(150, self._drain_log_queue)

    # Download flow
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
        self.status_lbl.configure(text='Setting up…')
        self.log('Starting…')

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
            self.status_lbl.configure(text='Preparing environment…')
            self._ensure_env()

            ffmpeg_dir = self._get_ffmpeg_dir_from_venv()
            if not ffmpeg_dir or not Path(ffmpeg_dir).exists():
                self.log('FFmpeg not located automatically.')
                messagebox.showerror('FFmpeg not found', 'Could not auto-provision FFmpeg. Install FFmpeg or add it to PATH.')
                self._reset_ui_done(error=True)
                return

            spotdl_exe = _venv_spotdl_exe()
            if not spotdl_exe.exists():
                spotdl_cmd = [str(_venv_python()), '-m', 'spotdl']
            else:
                spotdl_cmd = [str(spotdl_exe)]

            cmd = spotdl_cmd + [
                url,
                '--output', str(out_dir),
                '--threads', str(self.threads_var.get()),
                '--overwrite', self.overwrite_mode.get(),
                '--preload',
                '--log-level', 'DEBUG'
            ]

            env = os.environ.copy()
            venv_bin = str(_venv_spotdl_exe().parent)
            env['PATH'] = os.pathsep.join([ffmpeg_dir, venv_bin, env.get('PATH', '')])

            self.status_lbl.configure(text='Downloading…')
            self.log('Running: ' + ' '.join(cmd))

            creationflags = 0x08000000 if IS_WINDOWS else 0
            # Write full raw output to a file for troubleshooting
            self.last_run = APP_DIR / 'last_run.log'
            last_run = self.last_run
            self.log('Full output will be saved to: ' + str(last_run))

            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(APP_DIR),
                text=True,
                creationflags=creationflags,
            )

            assert self.proc.stdout is not None
            with open(last_run, 'w', encoding='utf-8', errors='ignore') as lf:
                for line in self.proc.stdout:
                    line = line.rstrip('
')
                    lf.write(line + '
')
                    lf.flush()
                    self.log(line)
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
                messagebox.showinfo('Download complete', 'Finished. Files are in:
' + str(out_dir))
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
                    messagebox.showerror('Failed', 'spotDL exited with code ' + str(ret) + '

Last output (tail):
' + tail)
                except Exception:
                    _win_message_box('spotDL failed. See last_run.log for details.')
        except Exception as e:
            logger.exception('Worker crashed')
            self.log('Error: ' + str(e))
            self.status_lbl.configure(text='Error')
            try:
                messagebox.showerror('Error', str(e))
            except Exception:
                _win_message_box('SpotDL GUI error: ' + str(e))
        finally:
            self._reset_ui_done()

    def _open_last_log(self):
        target = getattr(self, 'last_run', APP_DIR / 'last_run.log')
        if IS_WINDOWS:
            try:
                os.startfile(str(target))  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        try:
            subprocess.Popen(['notepad', str(target)])
        except Exception:
            messagebox.showinfo('Open log', f'Open this file manually:
{target}')

    def _reset_ui_done(self, error=False):
        try:
            self.progress.stop()
        except Exception:
            pass
        self.download_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        if not error and self.status_lbl['text'] not in ('Done', 'Failed'):
            self.status_lbl.configure(text='Idle')

    # Env bootstrap
    def _ensure_env(self):
        import venv
        if not VENV_DIR.exists():
            self.log('Creating virtual environment…')
            venv.create(str(VENV_DIR), with_pip=True)
        self.log('Upgrading pip…')
        subprocess.check_call([str(_venv_python()), '-m', 'pip', 'install', '--upgrade', 'pip', 'wheel', 'setuptools'])
        self.log('Installing/Updating dependencies (spotdl, imageio-ffmpeg, yt-dlp)…')
        subprocess.check_call([str(_venv_pip()), 'install', '--upgrade', 'spotdl', 'imageio-ffmpeg', 'yt-dlp'])

    def _get_ffmpeg_dir_from_venv(self):
        code = 'import os, imageio_ffmpeg; p=imageio_ffmpeg.get_ffmpeg_exe(); print(os.path.dirname(p))'
        try:
            out = subprocess.check_output([str(_venv_python()), '-c', code], text=True).strip()
            if out:
                self.log('FFmpeg located at: ' + out)
            # verify ffmpeg runs
            test_env = os.environ.copy()
            test_env['PATH'] = os.pathsep.join([out, test_env.get('PATH', '')])
            try:
                subprocess.check_output(['ffmpeg', '-version'], env=test_env, text=True, stderr=subprocess.STDOUT)
            except Exception as e:
                self.log('FFmpeg sanity check failed: ' + str(e))
            return out
        except Exception as e:
            self.log('FFmpeg detection failed: ' + str(e))
            return ''


def main():
    app = SpotDLGUI()
    app.log('Reminder: Only download content you have the rights to.')
    app.log('If something fails, open last_run.log in the app folder for the full trace.')
    app.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.exception('Startup crash')
        try:
            messagebox.showerror('SpotDL GUI crashed', str(e))
        except Exception:
            _win_message_box('SpotDL GUI failed to start:
' + str(e))
        raise
