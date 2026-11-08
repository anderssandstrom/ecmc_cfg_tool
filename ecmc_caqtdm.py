"""Launch caQtDM with installed or repository-local display files."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


REPOSITORY_PANELS = Path(__file__).resolve().parent.parent / "ecmccfg" / "qt"


def caqtdm_executable():
    override = os.environ.get("CAQTDM_EXECUTABLE")
    if override:
        candidate = str(Path(override).expanduser())
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        raise FileNotFoundError(f"CAQTDM_EXECUTABLE is not executable: {candidate}")
    for name in ("caqtdm", "caQtDM"):
        executable = shutil.which(name)
        if executable:
            return executable
    if sys.platform == "darwin":
        for app in (Path('/Applications'), Path.home() / 'Applications'):
            executable = app / 'caQtDM.app/Contents/MacOS/caQtDM'
            if executable.is_file() and os.access(executable, os.X_OK):
                return str(executable)
    raise FileNotFoundError("caQtDM was not found. Install it or set CAQTDM_EXECUTABLE to its executable path.")


def panel_directories(panel_dir=None):
    directories = []
    for value in ([panel_dir, os.environ.get("ECMC_CAQTDM_DIR")]
                  + os.environ.get("CAQTDM_DISPLAY_PATH", "").split(os.pathsep)
                  + os.environ.get("EPICS_DISPLAY_PATH", "").split(os.pathsep)
                  + [REPOSITORY_PANELS]):
        if value:
            path = Path(os.path.expandvars(str(value))).expanduser().resolve()
            if path not in directories:
                directories.append(path)
    return directories


def launch_caqtdm(panel, macro, panel_dir=None):
    executable = caqtdm_executable()
    directories = panel_directories(panel_dir)
    panel = Path(panel).expanduser()
    candidates = [panel] if panel.is_absolute() else [directory / panel for directory in directories]
    display = next((path.resolve() for path in candidates if path.is_file()), None)
    if display is None:
        raise FileNotFoundError(f"Panel {panel} not found. Searched: " + ", ".join(map(str, directories)))
    environment = os.environ.copy()
    # Also resolve embedded displays and related panels, preserving installed paths.
    environment['EPICS_DISPLAY_PATH'] = os.pathsep.join(map(str, [display.parent] + directories))
    environment['CAQTDM_DISPLAY_PATH'] = environment['EPICS_DISPLAY_PATH']
    return subprocess.Popen([executable, '-macro', macro, str(display)],
                            cwd=str(display.parent), env=environment)
