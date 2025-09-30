# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
SpotDL GUI — clean single-file version with masked env logging
- Creates a local venv (./.venv), installs spotdl + imageio-ffmpeg
- Reads Spotify API creds from ./spotdl.env (or uses system env)
- Forces those creds into the child env and logs a masked confirmation
- Launches spotdl with your options
Tested on Windows; also works on macOS/Linux.
"""

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
except Exception as e:
    print("Tkinter is required: ", e, file=sys.stderr)
    raise

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

# ---------- GET API CREDS ----------

def _load_spotify_env_from_file():
    """
    Read key=value lines from APP_DIR/spotdl.env (or .env) and return a dict.
    - Ignores blank lines and comments (# ...)
    - Trims whitespace and surrounding single/double quotes
    """
    def _strip_quotes(s: str) -> str:
        s = s.strip()
        if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
            return s[1:-1]
        return s

    # Look for spotdl.env first, then .env as a fallback
    candidates = [APP_DIR / 'spotdl.env', APP_DIR / '.env']

    out = {}
    for env_path in candidates:
        try:
            if env_path.exists():
                with open(env_path, 'r', encoding='utf-8-sig') as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        k, v = line.split('=', 1)
                        k = _strip_quotes(k)
                        v = _strip_quotes(v)
                        out[k] = v
                # stop at the first file found
                return out
        except Exception:
            # do not crash on malformed lines
            pass
    return out


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

        row = ttk.Frame(root)
        row.pack(fill=tk.X)
        ttk.Label(row, text='Spotify URL / Search:').pack(side=tk.LEFT)
        self.url_var = tk.StringVar(value='')
        ttk.Entry(row, textvariable=self.url_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(pad, 0))

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
        ttk.Combobox(opt, values=['skip', 'prompt', 'force'], textvariable=self.overwrite_var, width=8, state='readonly').pack(side=tk.LEFT)

        btns = ttk.Frame(root)
        btns.pack(fill=tk.X, pady=(pad, 0))
        self.run_btn = ttk.Button(btns, text='Run', command=self._run)
        self.run_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btns, text='Cancel', command=self._cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(pad, 0))

        self.status = ttk.Label(root, text='Idle')
        self.status.pack(anchor='w', pady=(pad, 0))

        self.pb = ttk.Progressbar(root, mode='indeterminate')
        self.pb.pack(fill=tk.X)

        self.log_text = tk.Text(root, height=18, wrap='word')
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(pad, 0))
        self.log_text.configure(state=tk.DISABLED)

    # -------------- UI Helpers --------------

    def _pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(APP_DIR))
        if d:
            self.out_var.set(d)

    def log(self, s: str):
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, s + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        except Exception:
            pass
        try:
            with open(LAST_RUN, 'a', encoding='utf-8') as f:
                f.write(s + "\n")
        except Exception:
            pass

    def _mask(self, v: str) -> str:
        """Mask sensitive values for logging (keeps a tiny prefix/suffix)."""
        if not v:
            return '<missing>'
        v = str(v)
        if len(v) <= 6:
            return v[0] + '***' + v[-1]
        return v[:3] + '...' + v[-3:]

    def _pump(self):
        try:
            while True:
                line = self.q.get_nowait()
                self.log(line)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _run(self):
        url = (self.url_var.get() or '').strip()
        if not url:
            messagebox.showerror('Missing input', 'Enter a Spotify URL or search query.')
            return
        out_dir = Path(self.out_var.get()).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        # clear previous log file
        try:
            LAST_RUN.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass

        self.pb.start(12)
        self.status.configure(text='Setting up...')
        self.stop_flag.clear()
        t = threading.Thread(target=self._worker, args=(url, out_dir), daemon=True)
        t.start()
        self.run_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)

    def _cancel(self):
        self.stop_flag.set()
        try:
            if hasattr(self, 'proc') and self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        self._done(error=True)

    def _done(self, error=False):
        self.pb.stop()
        self.cancel_btn.configure(state=tk.DISABLED)
        self.run_btn.configure(state=tk.NORMAL)
        self.status.configure(text='Idle' if not error else 'Error')

    # -------------- Worker --------------

    def _worker(self, url, out_dir: Path):
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
                '--preload',
                '--log-level', 'INFO',
            ]

            env = os.environ.copy()
            env['PATH'] = os.pathsep.join([ffmpeg_dir, str(exe.parent), env.get('PATH', '')])

            # inject Spotify creds (force from spotdl.env if present)
            spot_env = _load_spotify_env_from_file()
            for k in ('SPOTIPY_CLIENT_ID', 'SPOTIPY_CLIENT_SECRET', 'SPOTIPY_REDIRECT_URI'):
                if k in spot_env:
                    env[k] = spot_env[k]

            # --- Masked logging for verification ---
            self.log('Running: ' + ' '.join(cmd))
            try:
                client_id = env.get('SPOTIPY_CLIENT_ID')
                client_secret = env.get('SPOTIPY_CLIENT_SECRET')
                redirect = env.get('SPOTIPY_REDIRECT_URI')
                self.log('SPOTIPY_CLIENT_ID = ' + self._mask(client_id))
                self.log('SPOTIPY_CLIENT_SECRET = ' + ('present' if client_secret else '<missing>'))
                self.log('SPOTIPY_REDIRECT_URI = ' + (redirect or '<missing>'))
            except Exception as e:
                self.log('Env-log failed: ' + str(e))

            self.status.configure(text='Downloading...')

            creationflags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW on Windows
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(APP_DIR),
                text=True,
                creationflags=creationflags
            )

            # stream output
            for line in self.proc.stdout:  # type: ignore[union-attr]
                if not line:
                    break
                self.q.put(line.rstrip())
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
                try:
                    messagebox.showinfo('Complete', 'Finished. Files are in: \n' + str(out_dir))
                except Exception:
                    pass
            else:
                # show last ~50 lines for context
                try:
                    tail = ''
                    with open(LAST_RUN, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        tail = ''.join(lines[-50:])
                except Exception:
                    pass
                self._done(error=True)
                try:
                    messagebox.showerror('Failed', 'spotDL failed. Last output:\n' + tail)
                except Exception:
                    pass
        except Exception as e:
            self._done(error=True)
            self.log(f'Error: {e}')

    # -------------- Env setup --------------

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
            return out
        except Exception as e:
            self.log(f'FFmpeg probe failed: {e}')
            return ''

# ---------- main ----------

def main():
    app = App()
    app.log('Only download content you have rights to.')
    app.mainloop()

if __name__ == '__main__':
    main()
