# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# SpotDL GUI - ultra-robust, compact build to avoid copy/paste syntax issues.
# Keep it under ~220 lines, ASCII-only, balanced parentheses, no fancy quotes.

import os
import sys
import subprocess
import threading
import queue
import platform
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    raise SystemExit('Tkinter missing. Install Python from python.org (not MS Store).')

IS_WINDOWS = platform.system() == 'Windows'
APP_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
VENV_DIR = APP_DIR / '.venv'
LAST_RUN = APP_DIR / 'last_run.log'

# ---------- VENV HELPERS ----------

def venv_python():
    return VENV_DIR / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')

def venv_pip():
    return VENV_DIR / ('Scripts/pip.exe' if IS_WINDOWS else 'bin/pip')

def venv_spotdl():
    return VENV_DIR / ('Scripts/spotdl.exe' if IS_WINDOWS else 'bin/spotdl')

# ---------- GUI APP ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SpotDL GUI')
        self.geometry('740x520')
        self.minsize(660, 460)
        self._build()
        self.q = queue.Queue()
        self.after(100, self._pump)
        self.stop_flag = threading.Event()

    def _build(self):
        pad = 10
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        ttk.Label(root, text='Spotify link (track/album/playlist/artist):').pack(anchor=tk.W)
        self.url_var = tk.StringVar()
        ttk.Entry(root, textvariable=self.url_var).pack(fill=tk.X, expand=True)

        row = ttk.Frame(root)
        row.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(row, text='Output folder:').pack(side=tk.LEFT)
        self.out_var = tk.StringVar(value=str(APP_DIR / 'downloads'))
        ttk.Entry(row, textvariable=self.out_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(pad, 0))
        ttk.Button(row, text='Browse...', command=self._pick_out).pack(side=tk.LEFT, padx=(pad, 0))

        opt = ttk.Frame(root)
        opt.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(opt, text='Threads:').pack(side=tk.LEFT)
        self.threads_var = tk.IntVar(value=2)
        ttk.Spinbox(opt, from_=1, to=8, textvariable=self.threads_var, width=5).pack(side=tk.LEFT)
        ttk.Label(opt, text='  Overwrite:').pack(side=tk.LEFT)
        self.overwrite_var = tk.StringVar(value='skip')
        ttk.Combobox(opt, values=['skip', 'force', 'prompt'], textvariable=self.overwrite_var, state='readonly', width=8).pack(side=tk.LEFT)

        btns = ttk.Frame(root)
        btns.pack(fill=tk.X, pady=(pad, 0))
        self.btn_go = ttk.Button(btns, text='Download', command=self._start)
        self.btn_go.pack(side=tk.LEFT)
        self.btn_stop = ttk.Button(btns, text='Stop', command=self._stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(pad, 0))
        ttk.Button(btns, text='Open log', command=self._open_log).pack(side=tk.LEFT, padx=(8, 0))
        self.status = ttk.Label(btns, text='Idle')
        self.status.pack(side=tk.RIGHT)

        self.pb = ttk.Progressbar(root, mode='indeterminate')
        self.pb.pack(fill=tk.X, pady=(pad, 0))

        frame = ttk.LabelFrame(root, text='Log')
        frame.pack(fill=tk.BOTH, expand=True, pady=(pad, 0))
        self.txt = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED)
        self.txt.pack(fill=tk.BOTH, expand=True)

    def _pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    def log(self, s):
        self.q.put(s)

    def _pump(self):
        try:
            while True:
                s = self.q.get_nowait()
                self.txt.configure(state=tk.NORMAL)
                self.txt.insert(tk.END, s + "\n")
                self.txt.see(tk.END)
                self.txt.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _start(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning('Missing link', 'Please paste a Spotify link.')
            return
        out_dir = Path(self.out_var.get()).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.btn_go.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.pb.start(12)
        self.status.configure(text='Setting up...')
        self.stop_flag.clear()
        t = threading.Thread(target=self._worker, args=(url, out_dir), daemon=True)
        t.start()

    def _stop(self):
        self.stop_flag.set()
        try:
            if getattr(self, 'proc', None) and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        self.status.configure(text='Stopped')
        self.pb.stop()
        self.btn_go.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)

    def _worker(self, url, out_dir):
        try:
            self.status.configure(text='Preparing environment...')
            self._ensure_env()
            ffmpeg_dir = self._ffmpeg_dir()
            if not ffmpeg_dir or not Path(ffmpeg_dir).exists():
                self.log('FFmpeg not found via imageio-ffmpeg')
                messagebox.showerror('FFmpeg not found', 'Install FFmpeg or add it to PATH and retry.')
                self._done(error=True)
                return

            exe = venv_spotdl()
            cmd = [str(exe)] if exe.exists() else [str(venv_python()), '-m', 'spotdl']
            cmd += [
                url,
                '--output', str(out_dir),
                '--threads', str(self.threads_var.get()),
                '--overwrite', self.overwrite_var.get(),
                '--preload'
            ]

            env = os.environ.copy()
            env['PATH'] = os.pathsep.join([ffmpeg_dir, str(exe.parent), env.get('PATH', '')])
            self.status.configure(text='Downloading...')
            self.log('Running: ' + ' '.join(cmd))

            creationflags = 0x08000000 if IS_WINDOWS else 0
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(APP_DIR),
                text=True,
                creationflags=creationflags
            )

            with open(LAST_RUN, 'w', encoding='utf-8', errors='ignore') as raw:
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    raw.write(line)
                    self.log(line.rstrip())
                    if self.stop_flag.is_set():
                        break

            rc = self.proc.wait()
            if self.stop_flag.is_set():
                self.log('Cancelled by user.')
                self._done(error=True)
                return
            if rc == 0:
                self.log('Done. Files at: ' + str(out_dir))
                self.status.configure(text='Done')
                messagebox.showinfo('Complete', 'Finished. Files are in: \n' + str(out_dir))
            else:
                self.log('spotDL exited with code ' + str(rc))
                self.status.configure(text='Failed')
                self._show_tail()
        except Exception as e:
            self.log('Error: ' + str(e))
            try:
                messagebox.showerror('Error', str(e))
            except Exception:
                pass
        finally:
            self._done()

    def _done(self, error=False):
        try:
            self.pb.stop()
        except Exception:
            pass
        self.btn_go.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        if not error and self.status['text'] not in ('Done', 'Failed'):
            self.status.configure(text='Idle')

    def _ensure_env(self):
        import venv
        if not VENV_DIR.exists():
            self.log('Creating virtual environment...')
            venv.create(str(VENV_DIR), with_pip=True)
        self.log('Upgrading pip...')
        subprocess.check_call([str(venv_python()), '-m', 'pip', 'install', '--upgrade', 'pip', 'wheel', 'setuptools'])
        self.log('Installing spotdl and imageio-ffmpeg...')
        subprocess.check_call([str(venv_pip()), 'install', '--upgrade', 'spotdl', 'imageio-ffmpeg'])

    def _ffmpeg_dir(self):
        code = 'import os, imageio_ffmpeg; p=imageio_ffmpeg.get_ffmpeg_exe(); print(os.path.dirname(p))'
        try:
            out = subprocess.check_output([str(venv_python()), '-c', code], text=True).strip()
            if out:
                self.log('FFmpeg at: ' + out)
            return out
        except Exception as e:
            self.log('FFmpeg detect failed: ' + str(e))
            return ''

    def _open_log(self):
        try:
            if IS_WINDOWS:
                os.startfile(str(LAST_RUN))
            else:
                subprocess.Popen(['notepad', str(LAST_RUN)])
        except Exception:
            messagebox.showinfo('Open log', 'Open this file: ' + str(LAST_RUN))

    def _show_tail(self):
        tail = ''
        try:
            with open(LAST_RUN, 'r', encoding='utf-8', errors='ignore') as f:
                tail = ''.join(f.readlines()[-60:])
        except Exception:
            tail = '(no last_run.log)'
        try:
            messagebox.showerror('Failed', 'spotDL failed. Last output:\n' + tail)
        except Exception:
            pass


def main():
    app = App()
    app.log('Only download content you have rights to.')
    app.mainloop()


if __name__ == '__main__':
    main()
