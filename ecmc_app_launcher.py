"""Shared launcher for ECMC tools. Qt is imported only by embedded factories."""
from pathlib import Path
import subprocess

APP_DIR = Path(__file__).resolve().parent
SCRIPTS = {
    "command": "start.sh", "axis": "start_axis.sh",
    "controller": "start_cntrl.sh", "motion": "start_mtn.sh",
    "iso230": "start_iso230.sh", "daq": "start_daq.sh",
    "fft": "start_fft.sh", "rtlog": "start_rtlog.sh",
    "navigator": "start_ioc.sh", "sdo": "start_sdo.sh",
    "iso230_standalone": "start_iso230_standalone.sh",
}


def script_command(script, *args, app_dir=APP_DIR):
    """Build an argv list for a known repository launcher (no shell expansion)."""
    name = Path(script).name
    if name not in SCRIPTS.values():
        raise ValueError("Unknown ECMC launcher: " + name)
    path = Path(app_dir).resolve() / name
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return ["bash", str(path), *(str(arg) for arg in args)]


def launch_script(script, *args, app_dir=APP_DIR):
    command = script_command(script, *args, app_dir=app_dir)
    return subprocess.Popen(command, cwd=str(Path(app_dir).resolve()),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_tool(tool, *args, app_dir=APP_DIR):
    return launch_script(SCRIPTS[tool], *args, app_dir=app_dir)


def open_embedded(factory, add_tab, title, **kwargs):
    """Construct a widget using its existing constructor and hand it to a tab host."""
    widget = factory(**kwargs)
    add_tab(widget, title)
    return widget
