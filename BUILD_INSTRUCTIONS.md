# GestureLink — Build Instructions
## Packaging into Windows Executables

---

## Prerequisites

Before building, ensure the following are installed:

| Tool | Install Command |
|------|----------------|
| PyInstaller | `venv\Scripts\pip install pyinstaller pillow` |
| Node.js + npm | Download from https://nodejs.org |
| Inno Setup 6 | Download from https://jrsoftware.org/isdl.php |

---

## Building (One Command)

```powershell
# Activate venv first
venv\Scripts\activate

# Build BOTH Hub and Agent executables
python build.py

# Build only the Hub
python build.py --hub

# Build only the Agent
python build.py --agent
```

The build script will:
1. Clean old builds
2. Run `npm run build` for the mobile UI
3. Convert `logo.png` → `logo.ico` automatically
4. Run PyInstaller for Hub and Agent
5. Copy the final `.exe` files to the `release/` folder
6. Generate Inno Setup installer (`GestureLink_Installer.exe`) in `release/`

---

## Output

```
release/
  GestureLink_Hub.exe     (~150-200 MB)
  GestureLink_Agent.exe   (~120-160 MB)
```

---

## Administrator Rights

Both executables are configured to **automatically request Administrator elevation** via UAC manifests (`GestureLink_Hub.manifest`, `GestureLink_Agent.manifest`).

When a user double-clicks the `.exe`, Windows will show the "Do you want to allow this app to make changes?" UAC prompt. They must click **Yes**.

This is required for:
- `pyautogui` to control mouse/keyboard across all applications (including protected ones like Task Manager)
- Opening network ports (8000, 8001) on Windows

---

## Antivirus Warning

Unsigned `.exe` files may be flagged by Windows Defender or third-party AV software. This is a **false positive**.

### How to Allow (Windows Defender):

**Method 1 — SmartScreen (first run):**
1. Double-click `GestureLink_Hub.exe`
2. Click **"More info"** on the blue SmartScreen dialog
3. Click **"Run anyway"**

**Method 2 — Windows Security:**
1. Open **Windows Security** → **Virus & threat protection**
2. Click **"Protection history"**
3. Find the quarantined/blocked item
4. Click **"Allow on device"** or **"Allow"**

**Method 3 — Unblock via Properties:**
1. Right-click `GestureLink_Hub.exe` → **Properties**
2. At the bottom, check the **"Unblock"** checkbox
3. Click **OK**

---

## Manual Build (if build.py fails)

### Step 1: Build mobile frontend
```powershell
cd src\web\mobile
npm install
npm run build
cd ..\..\..
```

### Step 2: Build Hub exe
```powershell
venv\Scripts\python.exe -m PyInstaller GestureLink_Hub.spec --noconfirm --clean
```

### Step 3: Build Agent exe
```powershell
venv\Scripts\python.exe -m PyInstaller GestureLink_Agent.spec --noconfirm --clean
```

---

## Troubleshooting

### "ModuleNotFoundError" after building
Add the missing module to the `hiddenimports` list in the relevant `.spec` file and rebuild.

### "FileNotFoundError" for HTML/model files
All asset paths use `resource_path()` from `src/core/utils.py`. This resolves to `sys._MEIPASS` when frozen. If a file is missing, ensure it is listed in the `datas` section of the `.spec` file.

### Large file size (~200 MB)
This is expected — it includes:
- Python 3.11 runtime (~30 MB)
- MediaPipe + TensorFlow Lite (~60 MB)
- OpenCV (~40 MB)
- All web assets and the AI model (~30 MB)

---

## File Size Estimate

| Component | Size |
|-----------|------|
| Python runtime | ~30 MB |
| MediaPipe + model | ~60 MB |
| OpenCV | ~40 MB |
| NumPy + SciPy | ~25 MB |
| Web assets | ~5 MB |
| AI model (.task) | ~26 MB |
| **Total** | **~186 MB** |

This is completely normal for AI-powered desktop applications.
