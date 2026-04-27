import tkinter as tk
from tkinter import ttk, messagebox
import threading
import subprocess
import sys
import os
from pathlib import Path

VENV_DIR_NAME = ".venv"

# Try to import PIL for the logo, fallback if not available
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

class GestureLinkInstaller(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GestureLink — Smart Installer")
        self.geometry("500x400")
        self.configure(bg="#03070c")
        self.resizable(False, False)

        # Style
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("TProgressbar", thickness=10, troughcolor="#03070c", background="#00ff95")
        
        self._build_ui()

    def _build_ui(self):
        # Logo placeholder or actual image
        logo_frame = tk.Frame(self, bg="#03070c", height=150)
        logo_frame.pack(fill="x", pady=20)
        
        if HAS_PIL:
            # Look for the generated logo
            logo_path = PROJECT_ROOT / "logo.png"
            if logo_path.exists():
                img = Image.open(logo_path).resize((100, 100))
                self.logo_img = ImageTk.PhotoImage(img)
                tk.Label(logo_frame, image=self.logo_img, bg="#03070c").pack()
            else:
                tk.Label(logo_frame, text="🖐", font=("Arial", 60), fg="#00ff95", bg="#03070c").pack()
        else:
            tk.Label(logo_frame, text="🖐", font=("Arial", 60), fg="#00ff95", bg="#03070c").pack()

        tk.Label(self, text="GESTURELINK", font=("Outfit", 24, "bold"), fg="#ffffff", bg="#03070c").pack()
        tk.Label(self, text="AI-Powered Gesture Controller", font=("Outfit", 10), fg="#94a3b8", bg="#03070c").pack(pady=5)

        self.status_label = tk.Label(self, text="Ready to install", font=("Outfit", 9), fg="#94a3b8", bg="#03070c")
        self.status_label.pack(side="bottom", pady=20)

        self.progress = ttk.Progressbar(self, orient="horizontal", length=400, mode="determinate")
        self.progress.pack(pady=20)

        self.install_btn = tk.Button(
            self, text="START INSTALLATION", font=("Outfit", 12, "bold"),
            bg="#00ff95", fg="#000000", activebackground="#00e686",
            padx=40, pady=10, border=0, command=self.start_install
        )
        self.install_btn.pack(pady=10)

    def update_status(self, text, value=None):
        self.status_label.config(text=text)
        if value is not None:
            self.progress["value"] = value
        self.update_idletasks()

    def start_install(self):
        self.install_btn.config(state="disabled", text="INSTALLING...")
        threading.Thread(target=self.run_installation, daemon=True).start()

    def run_installation(self):
        try:
            # Step 1: VENV
            self.update_status("Creating virtual environment...", 10)
            subprocess.run([sys.executable, "-m", "venv", VENV_DIR_NAME], cwd=PROJECT_ROOT, check=True)
            
            # Step 2: PIP
            self.update_status("Upgrading pip...", 30)
            pip_bin = PROJECT_ROOT / VENV_DIR_NAME / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
            subprocess.run([str(pip_bin), "install", "--upgrade", "pip"], cwd=PROJECT_ROOT, check=True)
            
            # Step 3: Requirements
            self.update_status("Installing dependencies (this takes a moment)...", 50)
            subprocess.run([str(pip_bin), "install", "-r", "requirements.txt"], cwd=PROJECT_ROOT, check=True)
            
            # Step 4: SSL
            self.update_status("Generating security certificates...", 80)
            py_bin = PROJECT_ROOT / VENV_DIR_NAME / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([str(py_bin), "generate_certs.py"], cwd=PROJECT_ROOT, check=True)
            
            self.update_status("Installation Complete!", 100)
            messagebox.showinfo("Success", "GestureLink has been installed successfully!\n\nYou can now run 'python gesturelink.py hub' to start.")
            self.install_btn.config(state="normal", text="FINISH", command=self.destroy)
            
        except Exception as e:
            messagebox.showerror("Error", f"Installation failed: {e}")
            self.install_btn.config(state="normal", text="RETRY")

if __name__ == "__main__":
    app = GestureLinkInstaller()
    app.mainloop()
