"""Local UI-only preferences; no IOC targets or motion state are persisted."""
from qt_compat import QtCore


class _GeometryObserver(QtCore.QObject):
    def __init__(self, settings, window):
        super().__init__(window)
        self.settings = settings

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Close:
            self.settings.save_geometry(obj)
            self.settings.sync()
        return False



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

    def attach_window(self, window):
        """Restore a standalone window and save geometry on close."""
        self.restore_geometry(window)
        observer = _GeometryObserver(self, window)
        window._ui_geometry_observer = observer
        window.installEventFilter(observer)

    def sync(self):
        self.settings.sync()
