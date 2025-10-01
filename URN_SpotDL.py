# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
SpotDL GUI — clean rewrite with robust env loading + masked logging

What this does
- Creates/uses a local virtualenv (./.venv)
- Ensures spotdl + imageio-ffmpeg are installed inside that venv
- Loads Spotify Web API creds from ./spotdl.env (or ./.env) and forces them into the child env
- Logs the exact command being run + masked confirmation of creds + which env file was used
- Streams spotdl output into a GUI log and supports cancellation safely

Tested on Windows; should work on macOS/Linux as well.
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

# -------------------- Globals & Paths --------------------
IS_WINDOWS = platform.system() == 'Windows'
APP_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
VENV_DIR = APP_DIR / '.venv'
LAST_RUN = APP_DIR / 'last_run.log'
DEFAULT_DOWNLOADS = APP_DIR / 'downloads'

# -------------------- Venv Helpers --------------------

def venv_python() -> Path:
    return VENV_DIR / ('Scripts/python.exe' if IS_WINDOWS else 'bin/python')

def venv_pip() -> Path:
    return VENV_DIR / ('Scripts/pip.exe' if IS_WINDOWS else 'bin/pip')

def venv_spotdl() -> Path:
    return VENV_DIR / ('Scripts/spotdl.exe' if IS_WINDOWS else 'bin/spotdl')

# -------------------- Env File Loader --------------------

def _load_spotify_env_from_file():
    """
    Read key=value lines from APP_DIR/spotdl.env, falling back to APP_DIR/.env.
    Handles BOMs, quotes, blank lines, and comments. Returns (env_dict, path_used_or_None).
    """
    def _strip_quotes(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        return s

    candidates = [APP_DIR / 'spotdl.env', APP_DIR / '.env']
    out, used = {}, None
    for env_path in candidates:
        try:
            if env_path.exists():
                with open(env_path, 'r', encoding='utf-8-sig') as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        k, v = line.split('=', 1)
                        out[_strip_quotes(k)] = _strip_quotes(v)
                used = env_path
                break
        except Exception:
            # tolerate malformed lines/files silently; caller will see missing keys in logs
            pass
    return out, used

# -------------------- GUI Application --------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SpotDL GUI')
        self.geometry('780x560')
        self.minsize(680, 480)

        # runtime state
        self.q: "queue.Queue[str]" = queue.Queue()
        self.stop_flag = threading.Event()
        self.proc = None  # type: ignore[attr-defined]

        self._build_ui()
        self.after(100, self._pump)

    # -------------------- UI --------------------
    def _build_ui(self):
        pad = 10
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        # URL/Search
        row = ttk.Frame(root)
        row.pack(fill=tk.X)
        ttk.Label(row, text='Spotify URL / Search:').pack(side=tk.LEFT)
        self.url_var = tk.StringVar(value='')
        ttk.Entry(row, textvariable=self.url_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(pad, 0))

        # Output folder
        row = ttk.Frame(root)
        row.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(row, text='Output folder:').pack(side=tk.LEFT)
        self.out_var = tk.StringVar(value=str(DEFAULT_DOWNLOADS))
        ttk.Entry(row, textvariable=self.out_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(pad, 0))
        ttk.Button(row, text='Browse...', command=self._pick_out).pack(side=tk.LEFT, padx=(pad, 0))

        # Options
        opt = ttk.Frame(root)
        opt.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(opt, text='Threads:').pack(side=tk.LEFT)
        self.threads_var = tk.IntVar(value=2)
        ttk.Spinbox(opt, from_=1, to=8, textvariable=self.threads_var, width=5).pack(side=tk.LEFT)

        ttk.Label(opt, text='  Overwrite:').pack(side=tk.LEFT)
        self.overwrite_var = tk.StringVar(value='skip')
        ttk.Combobox(opt, values=['skip', 'prompt', 'force'], textvariable=self.overwrite_var, width=8, state='readonly').pack(side=tk.LEFT)

        # Buttons
        btns = ttk.Frame(root)
        btns.pack(fill=tk.X, pady=(pad, 0))
        self.run_btn = ttk.Button(btns, text='Run', command=self._run)
        self.run_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btns, text='Cancel', command=self._cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(pad, 0))
        self.test_btn = ttk.Button(btns, text='Test env', command=self._test_env)
        self.test_btn.pack(side=tk.LEFT, padx=(pad, 0))

        # Status + progress
        self.status = ttk.Label(root, text='Idle')
        self.status.pack(anchor='w', pady=(pad, 0))
        self.pb = ttk.Progressbar(root, mode='indeterminate')
        self.pb.pack(fill=tk.X)

        # Log
        self.log_text = tk.Text(root, height=18, wrap='word')
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(pad, 0))
        self.log_text.configure(state=tk.DISABLED)

    # -------------------- UI helpers --------------------
    def _pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(APP_DIR))
        if d:
            self.out_var.set(d)

    def _mask(self, v: str) -> str:
        if not v:
            return '<missing>'
        v = str(v)
        if len(v) <= 6:
            return v[0] + '***' + v[-1]
        return v[:3] + '...' + v[-3:]

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

    def _pump(self):
        try:
            while True:
                line = self.q.get_nowait()
                self.log(line)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    # -------------------- Actions --------------------
    def _run(self):
        url = (self.url_var.get() or '').strip()
        if not url:
            messagebox.showerror('Missing input', 'Enter a Spotify URL or search query.')
            return

        out_dir = Path(self.out_var.get()).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)

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
            if self.proc and getattr(self.proc, 'poll', None) and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        self._done(error=True)

    def _done(self, error=False):
        self.pb.stop()
        self.cancel_btn.configure(state=tk.DISABLED)
        self.run_btn.configure(state=tk.NORMAL)
        self.status.configure(text='Idle' if not error else 'Error')

    def _test_env(self):
        """Run a tiny child process that prints its SPOTIPY_CLIENT_ID to the log."""
        try:
            self.status.configure(text='Testing env...')
            self._ensure_env()

            exe = venv_spotdl()
            ffmpeg_dir = self._ffmpeg_dir()

            env = os.environ.copy()
            env['PATH'] = os.pathsep.join(filter(None, [ffmpeg_dir, str(exe.parent), env.get('PATH', '')]))

            spot_env, env_file = _load_spotify_env_from_file()
            for k in ('SPOTIPY_CLIENT_ID', 'SPOTIPY_CLIENT_SECRET', 'SPOTIPY_REDIRECT_URI'):
                if k in spot_env:
                    env[k] = spot_env[k]

            self.log('Loaded creds from: ' + (str(env_file) if env_file else '<none found>'))
            self.log('SPOTIPY_CLIENT_ID = ' + self._mask(env.get('SPOTIPY_CLIENT_ID')))
            self.log('SPOTIPY_CLIENT_SECRET = ' + ('present' if env.get('SPOTIPY_CLIENT_SECRET') else '<missing>'))
            self.log('SPOTIPY_REDIRECT_URI = ' + (env.get('SPOTIPY_REDIRECT_URI') or '<missing>'))

            # print from the child's perspective
            out = subprocess.check_output([str(venv_python()), '-c', 'import os;print(os.environ.get("SPOTIPY_CLIENT_ID"))'], text=True, env=env, cwd=str(APP_DIR)).strip()
            self.log('Child sees SPOTIPY_CLIENT_ID = ' + self._mask(out))
            self.status.configure(text='Env OK')
        except Exception as e:
            self.log('Env test failed: ' + str(e))
            self.status.configure(text='Env test failed')

    # -------------------- Worker --------------------
    def _worker(self, url: str, out_dir: Path):
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
            ]

            env = os.environ.copy()
            env['PATH'] = os.pathsep.join(filter(None, [ffmpeg_dir, str(exe.parent), env.get('PATH', '')]))

            # Load from env file(s) and force into child env
            spot_env, env_file = _load_spotify_env_from_file()
            for k in ('SPOTIPY_CLIENT_ID', 'SPOTIPY_CLIENT_SECRET', 'SPOTIPY_REDIRECT_URI'):
                if k in spot_env:
                    env[k] = spot_env[k]

            # Log command + masked creds
            self.log('Running: ' + ' '.join(cmd))
            self.log('Loaded creds from: ' + (str(env_file) if env_file else '<none found>'))
            self.log('SPOTIPY_CLIENT_ID = ' + self._mask(env.get('SPOTIPY_CLIENT_ID')))
            self.log('SPOTIPY_CLIENT_SECRET = ' + ('present' if env.get('SPOTIPY_CLIENT_SECRET') else '<missing>'))
            self.log('SPOTIPY_REDIRECT_URI = ' + (env.get('SPOTIPY_REDIRECT_URI') or '<missing>'))

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

            if not self.proc or not self.proc.stdout:
                self._done(error=True)
                return

            # Stream output
            for line in self.proc.stdout:
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
                tail = ''
                try:
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

    # -------------------- Bootstrap --------------------
    def _ensure_env(self):
        import venv
        if not VENV_DIR.exists():
            self.log('Creating virtual environment...')
            venv.create(str(VENV_DIR), with_pip=True)
        self.log('Upgrading pip...')
        subprocess.check_call([str(venv_python()), '-m', 'pip', 'install', '--upgrade', 'pip', 'wheel', 'setuptools'])
        self.log('Installing/Updating spotdl & imageio-ffmpeg...')
        subprocess.check_call([str(venv_pip()), 'install', '--upgrade', 'spotdl', 'imageio-ffmpeg'])

    def _ffmpeg_dir(self) -> str:
        code = 'import os, imageio_ffmpeg; p=imageio_ffmpeg.get_ffmpeg_exe(); print(os.path.dirname(p))'
        try:
            out = subprocess.check_output([str(venv_python()), '-c', code], text=True).strip()
            return out
        except Exception as e:
            self.log(f'FFmpeg probe failed: {e}')
            return ''

# -------------------- main --------------------

def main():
    app = App()
    app.log('Only download content you have rights to.')
    app.mainloop()

if __name__ == '__main__':
    main()
