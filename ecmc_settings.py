"""Local UI-only preferences; no IOC targets or motion state are persisted."""
from qt_compat import QtCore


class UiSettings:
    def __init__(self, tool, settings=None):
        self.tool = str(tool)
        self.settings = settings if settings is not None else QtCore.QSettings(
            "ecmc", "ecmc_cfg_tool")

    def _key(self, key):
        return f"ui/v1/{self.tool}/{key}"

    def boolean(self, key, default=False):
        value = self.settings.value(self._key(key), default)
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1")

    def set_boolean(self, key, value):
        self.settings.setValue(self._key(key), bool(value))

    def restore_geometry(self, window):
        value = self.settings.value(self._key("geometry"))
        if isinstance(value, QtCore.QByteArray) and not value.isEmpty():
            return window.restoreGeometry(value)
        return False

    def save_geometry(self, window):
        self.settings.setValue(self._key("geometry"), window.saveGeometry())

    def restore_layout(self, key, widget):
        value = self.settings.value(self._key(key))
        if isinstance(value, QtCore.QByteArray) and not value.isEmpty():
            return widget.restoreState(value)
        return False

    def save_layout(self, key, widget):
        self.settings.setValue(self._key(key), widget.saveState())

    def sync(self):
        self.settings.sync()
