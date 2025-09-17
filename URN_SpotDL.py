#!/usr/bin/env python3
"""
SpotDL GUI – a user-friendly wrapper around spotDL that hides the command prompt.

This build adds Windows-friendly error reporting + file logging so if something
fails during startup you still get a message box and a log file.

Key features
- Paste any Spotify link and click Download.
- No admin rights: creates local .venv and installs spotdl + imageio-ffmpeg.
- Hides console; streams progress to the Log box.
- Windows-specific: catches early exceptions and shows a native MessageBox.
- Writes a rotating log to spotdl_gui.log for troubleshooting.
"""

import os
import sys
import subprocess
import threading
import queue
import shutil
import platform
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# --- Early Windows MessageBox helper (works even before Tk is up) ---
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import ctypes  # noqa
        def _win_message_box(text, title="SpotDL GUI"):
            try:
                ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x10)
            except Exception:
                pass
    except Exception:  # pragma: no cover
        def _win_message_box(text, title="SpotDL GUI"):
            pass
else:
    def _win_message_box(text, title="SpotDL GUI"):
        pass

APP_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
VENV_DIR = APP_DIR / ".venv"
# Prefer a writable per-user log folder on Windows
_local = os.getenv("LOCALAPPDATA")
if _local:
    LOG_DIR = Path(_local) / "SpotDL-GUI"
else:
    LOG_DIR = APP_DIR
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = LOG_DIR / "spotdl_gui.log"
except Exception:
    # Fallback to script folder if LocalAppData is blocked
    LOG_FILE = APP_DIR / "spotdl_gui.log"

# --- Logging (works before Tk) ---
logger = logging.getLogger("spotdl_gui")
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logger.addHandler(_handler)
logger.info("=== App start ===")
logger.info(f"Logging to: {LOG_FILE}")
# Let Windows users know where the log is, since double-clicking uses pythonw (no console)
try:
    if IS_WINDOWS:
        _win_message_box(f"SpotDL GUI started.
Log file:
{LOG_FILE}")
except Exception:
    pass

# Defer Tk imports until after we set up logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "spotdl.exe"
    else:
        return VENV_DIR / "bin" / "spotdl"


class SpotDLGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SpotDL – Easy Downloader")
        self.geometry("760x560")
        self.minsize(680, 480)
        self.iconify()  # Avoid flash while building UI
        self._build_ui()
        self.deiconify()

        self.log_queue = queue.Queue()
        self.after(100, self._drain_log_queue)

    # --------------------------- UI ----------------------------
    def _build_ui(self):
        pad = 10
        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        url_lbl = ttk.Label(main, text="Spotify link (track / album / playlist / artist):")
        url_lbl.pack(anchor=tk.W)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(main, textvariable=self.url_var)
        self.url_entry.pack(fill=tk.X, expand=True)

        out_row = ttk.Frame(main)
        out_row.pack(fill=tk.X, pady=(pad, 0))
        ttk.Label(out_row, text="Output folder:").pack(side=tk.LEFT)
        self.output_var = tk.StringVar(value=str(APP_DIR / "downloads"))
        self.output_entry = ttk.Entry(out_row, textvariable=self.output_var)
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(pad, 0))
        ttk.Button(out_row, text="Browse…", command=self._choose_output_dir).pack(side=tk.LEFT, padx=(pad, 0))

        opt_row = ttk.Frame(main)
        opt_row.pack(fill=tk.X, pady=(pad, 0))
        self.threads_var = tk.IntVar(value=max(2, os.cpu_count() or 2))
        ttk.Label(opt_row, text="Threads:").pack(side=tk.LEFT)
        ttk.Spinbox(opt_row, from_=1, to=max(32, self.threads_var.get()), textvariable=self.threads_var, width=5).pack(side=tk.LEFT)
        self.overwrite_mode = tk.StringVar(value="skip")
        ttk.Label(opt_row, text="  Overwrite:").pack(side=tk.LEFT)
        ttk.Combobox(opt_row, values=["skip", "force", "prompt"], textvariable=self.overwrite_mode, state="readonly", width=8).pack(side=tk.LEFT)

        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=(pad, 0))
        self.download_btn = ttk.Button(btn_row, text="Download", command=self._on_download)
        self.download_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(pad, 0))
        self.status_lbl = ttk.Label(btn_row, text="Idle")
        self.status_lbl.pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(pad, 0))

        log_frame = ttk.LabelFrame(main, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(pad, 0))
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_var.get())
        if chosen:
            self.output_var.set(chosen)

    # ----------------------- Logging ---------------------------
    def log(self, msg: str):
        logger.info(msg)
        self.log_queue.put(msg)

    def _drain_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + "
")
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        finally:
            self.after(120, self._drain_log_queue)

    # -------------------- Download flow ------------------------
    def _on_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing link", "Please paste a Spotify link.")
            return
        out_dir = Path(self.output_var.get()).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        self.download_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress.start(12)
        self.status_lbl.configure(text="Setting up…")
        self.log("Starting…")

        self.stop_flag = threading.Event()
        t = threading.Thread(target=self._worker_download, args=(url, out_dir), daemon=True)
        t.start()

    def _on_stop(self):
        if hasattr(self, "proc") and self.proc and self.proc.poll() is None:
            self.stop_flag.set()
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.status_lbl.configure(text="Stopped")
        self.progress.stop()
        self.download_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _worker_download(self, url: str, out_dir: Path):
        try:
            self.status_lbl.configure(text="Preparing environment…")
            self._ensure_env()

            ffmpeg_dir = self._get_ffmpeg_dir_from_venv()
            if not ffmpeg_dir or not Path(ffmpeg_dir).exists():
                self.log("Could not locate FFmpeg automatically. spotDL requires FFmpeg.")
                messagebox.showerror(
                    "FFmpeg not found",
                    "I couldn't auto-provision FFmpeg. Please install FFmpeg or add it to PATH, then try again.",
                )
                self._reset_ui_done(error=True)
                return

            spotdl_exe = _venv_spotdl_exe()
            if not spotdl_exe.exists():
                spotdl_cmd = [str(_venv_python()), "-m", "spotdl"]
            else:
                spotdl_cmd = [str(spotdl_exe)]

            cmd = (
                spotdl_cmd
                + [
                    url,
                    "--output",
                    str(out_dir),
                    "--threads",
                    str(self.threads_var.get()),
                    "--overwrite",
                    self.overwrite_mode.get(),
                    "--preload",
                ]
            )

            env = os.environ.copy()
            venv_bin = str(_venv_spotdl_exe().parent)
            env["PATH"] = os.pathsep.join([ffmpeg_dir, venv_bin, env.get("PATH", "")])

            self.status_lbl.configure(text="Downloading…")
            self.log("Running: " + " ".join(cmd))

            creationflags = 0
            if IS_WINDOWS:
                creationflags = 0x08000000  # CREATE_NO_WINDOW

            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(APP_DIR),
                text=True,
                creationflags=creationflags,
            )

            for line in self.proc.stdout:  # type: ignore
                self.log(line.rstrip())
                if self.stop_flag.is_set():
                    break

            ret = self.proc.wait()
            if self.stop_flag.is_set():
                self.log("Download cancelled by user.")
                self._reset_ui_done(error=True)
                return

            if ret == 0:
                self.log("Done! Files saved to: " + str(out_dir))
                self.status_lbl.configure(text="Done")
                messagebox.showinfo("Download complete", f"Finished. Files are in:
{out_dir}")
            else:
                self.log(f"spotDL exited with code {ret}")
                self.status_lbl.configure(text="Failed")
                messagebox.showerror("Failed", "spotDL failed. Check the log for details.")
        except Exception as e:
            logger.exception("Worker crashed")
            self.log(f"Error: {e}")
            self.status_lbl.configure(text="Error")
            try:
                messagebox.showerror("Error", str(e))
            except Exception:
                _win_message_box(f"SpotDL GUI error: {e}")
        finally:
            self._reset_ui_done()

    def _reset_ui_done(self, error: bool = False):
        try:
            self.progress.stop()
        except Exception:
            pass
        self.download_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        if not error and self.status_lbl["text"] not in ("Done", "Failed"):
            self.status_lbl.configure(text="Idle")

    # ------------------ Env bootstrap logic --------------------
    def _ensure_env(self):
        import venv
        if not VENV_DIR.exists():
            self.log("Creating virtual environment…")
            venv.create(str(VENV_DIR), with_pip=True)
        self.log("Upgrading pip…")
        subprocess.check_call([str(_venv_python()), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"])
        self.log("Installing/Updating dependencies (spotdl, imageio-ffmpeg)…")
        subprocess.check_call([str(_venv_pip()), "install", "--upgrade", "spotdl", "imageio-ffmpeg"])

    def _get_ffmpeg_dir_from_venv(self) -> str:
        code = (
            "import os, imageio_ffmpeg;"
            "p=imageio_ffmpeg.get_ffmpeg_exe();"
            "print(os.path.dirname(p))"
        )
        try:
            out = subprocess.check_output([str(_venv_python()), "-c", code], text=True).strip()
            if out:
                self.log(f"FFmpeg located at: {out}")
            return out
        except Exception as e:
            self.log(f"FFmpeg detection failed: {e}")
            return ""


def main():
    app = SpotDLGUI()
    app.log("Reminder: Only download content you have the rights to.")
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # Catch *startup* errors (before Tk is ready)
        logger.exception("Startup crash")
        try:
            messagebox.showerror("SpotDL GUI crashed", str(e))
        except Exception:
            _win_message_box(f"SpotDL GUI failed to start:
{e}")
        raise
