# PyInstaller spec - builds a portable folder: dist\PieGuy\PieGuy.exe  (run build.bat)
import sys
from PyInstaller.utils.hooks import collect_all

datas = [("ui", "ui"), ("assets", "assets")]
binaries, hiddenimports = [], ["PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel"]
if sys.platform == "win32":
    for pkg in ("uiautomation", "comtypes"):   # uiautomation ships its own UIA DLLs in bin/
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h

a = Analysis(
    ["PieGuy.pyw"],
    pathex=["."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "unittest", "pydoc", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtQuick3D", "PySide6.QtBluetooth",
              "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtPdf", "PySide6.QtDesigner",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtGraphs", "PySide6.QtSpatialAudio"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PieGuy",
    icon="assets/pieguy.ico",
    console=False,
    upx=False,
    version="version_info.txt" if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PieGuy", upx=False)
