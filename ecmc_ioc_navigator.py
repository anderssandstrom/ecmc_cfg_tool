#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from qt_compat import QtCore, QtWidgets
from ecmc_stream_qt import EpicsClient, MainWindow as StreamWindow, _join_prefix_pv
from ecmc_axis_cfg import AxisYamlConfigWindow
from ecmc_mtn_qt import MotionWindow
from ecmc_cntrl_qt import CntrlWindow
from ecmc_iso230_qt import Iso230Window
from ecmc_daq_qt import DaqWindow
from ecmc_rtlog_qt import RtLogWindow
from ecmc_ioc_tree import demo_ioc, discover_ioc
from ecmc_sdo_qt import SdoBrowserWindow


class DiscoverySignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(object, str) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(object, str)


class DiscoveryTask(QtCore.QRunnable):
    def __init__(self, prefix, timeout, ssh_host_pv):
        super().__init__()
        self.prefix = prefix
        self.timeout = timeout
        self.ssh_host_pv = ssh_host_pv
        self.signals = DiscoverySignals()

    def run(self):
        try:
            client = EpicsClient(timeout=self.timeout)
            self.signals.finished.emit(discover_ioc(client, self.prefix, self.ssh_host_pv), "")
        except Exception as ex:
            self.signals.finished.emit({}, str(ex))


class IocNavigator(QtWidgets.QMainWindow):
    DATA_ROLE = int(QtCore.Qt.UserRole)

    def __init__(
        self, prefix="IOC:ECMC", ssh_host="", ssh_host_pv="MCU-Cfg-Hostname", timeout=1.0, caqtdm_dir="", demo=False
    ):
        super().__init__()
        self.setWindowTitle("ecmc IOC Navigator")
        self.resize(980, 700)
        self.timeout = float(timeout)
        self.app_dir = Path(__file__).resolve().parent
        default_qt = self.app_dir.parent / "ecmccfg" / "qt"
        self.caqtdm_dir = Path(caqtdm_dir).expanduser() if caqtdm_dir else default_qt
        self.safety_qt_dir = self.app_dir.parent / "ecmc_plugin_safety" / "qt"
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self._tasks = set()
        self._snapshot = {}
        self._ssh_host = ssh_host
        self._embedded_tools = []
        self._build_ui(prefix, ssh_host, ssh_host_pv)
        if demo:
            self._snapshot = demo_ioc(prefix)
            self._ssh_host = ssh_host or self._snapshot["ssh_host"]
            self._populate(self._snapshot)
            self.statusBar().showMessage("Offline demo data loaded; launch actions are not simulated")
        else:
            QtCore.QTimer.singleShot(0, self.refresh)

    def _build_ui(self, prefix, ssh_host, ssh_host_pv):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        workspace = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(workspace, 1)

        navigator = QtWidgets.QWidget()
        navigator.setMinimumWidth(280)
        navigator.setMaximumWidth(430)
        nav_layout = QtWidgets.QVBoxLayout(navigator)
        workspace.addWidget(navigator)

        top = QtWidgets.QHBoxLayout()
        self.prefix_edit = QtWidgets.QLineEdit(prefix)
        self.prefix_edit.setPlaceholderText("IOC prefix")
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.embed_tools_check = QtWidgets.QCheckBox("Open Python tools in tabs")
        self.embed_tools_check.setChecked(True)
        top.addWidget(QtWidgets.QLabel("IOC"))
        top.addWidget(self.prefix_edit, 1)
        top.addWidget(self.embed_tools_check)
        top.addWidget(self.refresh_btn)
        nav_layout.addLayout(top)

        host_pv_row = QtWidgets.QHBoxLayout()
        self.host_pv_edit = QtWidgets.QLineEdit(ssh_host_pv)
        self.host_pv_edit.setPlaceholderText("Optional hostname PV or suffix")
        host_pv_row.addWidget(QtWidgets.QLabel("SSH host PV"))
        host_pv_row.addWidget(self.host_pv_edit, 1)
        nav_layout.addLayout(host_pv_row)

        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter objects...")
        self.filter_edit.textChanged.connect(self._filter_tree)
        nav_layout.addWidget(self.filter_edit)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Object", "ID", "Type / Panel", "PV / Motor"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._default_action)
        header = self.tree.header()
        for column in range(4):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 190)
        self.tree.setColumnWidth(1, 55)
        self.tree.setColumnWidth(2, 115)
        self.tree.setColumnWidth(3, 210)
        nav_layout.addWidget(self.tree, 1)

        hint = QtWidgets.QLabel("Double-click an object for its default panel, or right-click for all available actions.")
        nav_layout.addWidget(hint)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tool_tab)
        self.tool_scroll = QtWidgets.QScrollArea()
        self.tool_scroll.setWidgetResizable(True)
        self.tool_scroll.setWidget(self.tabs)
        right_layout.addWidget(self.tool_scroll, 1)
        self._add_workspace_placeholder()
        workspace.addWidget(right)
        workspace.setSizes([330, 650])
        self.statusBar().showMessage("Ready")

    def _add_workspace_placeholder(self):
        placeholder = QtWidgets.QLabel("Open a Python tool from the navigator to show it here.")
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setProperty("placeholder", True)
        self.tabs.addTab(placeholder, "Workspace")

    def _add_tool_tab(self, widget, title):
        if self.tabs.count() == 1 and self.tabs.widget(0).property("placeholder"):
            old = self.tabs.widget(0)
            self.tabs.removeTab(0)
            old.deleteLater()
        widget.setParent(self.tabs)
        self._embedded_tools.append(widget)
        index = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(index)
        self.statusBar().showMessage(f"Opened tab: {title}", 5000)

    def _add_error_tab(self, title, message):
        pane = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pane)
        label = QtWidgets.QLabel(str(message))
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        self._add_tool_tab(pane, title)

    def _close_tool_tab(self, index):
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget in self._embedded_tools:
            self._embedded_tools.remove(widget)
        widget.close()
        if self.tabs.count() == 0:
            self._add_workspace_placeholder()

    def refresh(self):
        prefix = self.prefix_edit.text().strip().rstrip(":")
        if not prefix:
            QtWidgets.QMessageBox.warning(self, "Missing IOC", "Enter an IOC prefix.")
            return
        self.refresh_btn.setEnabled(False)
        self.statusBar().showMessage(f"Discovering {prefix}...")
        task = DiscoveryTask(prefix, self.timeout, self.host_pv_edit.text().strip())
        self._tasks.add(task)

        def done(snapshot, error):
            self._tasks.discard(task)
            self.refresh_btn.setEnabled(True)
            if error:
                self.statusBar().showMessage("Discovery failed")
                QtWidgets.QMessageBox.critical(self, "IOC discovery failed", error)
                return
            self._snapshot = snapshot
            if snapshot.get("ssh_host") and not self._ssh_host:
                self._ssh_host = snapshot["ssh_host"]
            self._populate(snapshot)

        task.signals.finished.connect(done)
        self.thread_pool.start(task)

    def _populate(self, snapshot):
        self.tree.clear()
        self._add_ecmc_info(snapshot)
        specs = [
            ("Hardware", "hardware", "hardware"),
            ("Motion", "axes", "axis"),
            ("PLCs", "plcs", "plc"),
            ("Plugins", "plugins", "plugin"),
            ("Data Storage", "data_storages", "data_storage"),
            ("CppLogic", "cpp_logic", "cpp_logic"),
            ("SafetyPlugin", "safety_plugins", "safety_plugin"),
        ]
        total = 0
        for title, key, kind in specs:
            objects = snapshot.get(key, [])
            parent = QtWidgets.QTreeWidgetItem([f"{title} ({len(objects)})", "", "", ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setData(0, self.DATA_ROLE, {"kind": f"{kind}_group"})
            self.tree.addTopLevelItem(parent)
            for obj in objects:
                if kind == "hardware":
                    columns = [obj["name"], obj["id"], obj["panel"], obj["pv_base"]]
                elif kind == "axis":
                    columns = [obj["name"], obj["id"], obj.get("axis_type", ""), obj.get("motor", "")]
                elif kind == "cpp_logic":
                    columns = [obj["name"], obj["id"], "C++ Logic", f"{obj.get('rate_ms', '')} ms"]
                elif kind == "safety_plugin":
                    columns = [obj["name"], obj["id"], "Safety Plugin", f"{obj.get('group_count', '')} groups"]
                else:
                    columns = [obj["name"], obj["id"], kind.replace("_", " ").upper(), ""]
                item = QtWidgets.QTreeWidgetItem(columns)
                item.setData(0, self.DATA_ROLE, {"kind": kind, **obj})
                parent.addChild(item)
                total += 1
            parent.setExpanded(True)
        self._filter_tree()
        self.statusBar().showMessage(
            f"Discovered {total} objects on {snapshot.get('prefix', '')} (EtherCAT master {snapshot.get('master', '0')})"
        )

    def _add_ecmc_info(self, snapshot):
        groups = snapshot.get("ecmc", [])
        parent = QtWidgets.QTreeWidgetItem(["ecmc / IOC", "", "Main", snapshot.get("prefix", "")])
        font = parent.font(0)
        font.setBold(True)
        parent.setFont(0, font)
        parent.setData(0, self.DATA_ROLE, {"kind": "ecmc_group"})
        self.tree.addTopLevelItem(parent)
        for group in groups:
            group_item = QtWidgets.QTreeWidgetItem([group.get("name", "Info"), "", "", ""])
            group_item.setData(0, self.DATA_ROLE, {"kind": "ecmc_info_group"})
            parent.addChild(group_item)
            for info in group.get("items", []):
                label = info.get("label", "")
                value = info.get("value", "")
                pv = info.get("pv", "")
                item = QtWidgets.QTreeWidgetItem([label, "", value, pv])
                item.setData(0, self.DATA_ROLE, {"kind": "ecmc_info", **info})
                group_item.addChild(item)
        parent.setExpanded(True)

    def _filter_tree(self, _text=None):
        needle = self.filter_edit.text().strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            visible = self._filter_item(parent, needle)
            parent.setHidden(bool(needle) and not visible)

    def _filter_item(self, item, needle):
        own_match = not needle or needle in " ".join(item.text(c).lower() for c in range(4))
        child_match = False
        for i in range(item.childCount()):
            child = item.child(i)
            visible = self._filter_item(child, needle)
            child.setHidden(bool(needle) and not visible)
            child_match = child_match or visible
        if needle and child_match:
            item.setExpanded(True)
        return own_match or child_match

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        data = item.data(0, self.DATA_ROLE) if item else None
        if not data:
            return
        menu = QtWidgets.QMenu(self)
        kind = data.get("kind")
        if kind == "axis":
            self._menu_action(menu, "Open Motion App", lambda: self._open_motion(data))
            self._menu_action(menu, "Open Axis Config App", lambda: self._open_axis_config(data))
            self._menu_action(menu, "Open Controller App", lambda: self._open_controller(data))
            self._menu_action(menu, "Open ISO230 App", lambda: self._open_iso230(data))
            menu.addSeparator()
            self._menu_action(menu, "Open Axis Panel", lambda: self._open_axis_panel(data, "ecmcAxis.ui"))
            self._menu_action(menu, "Open Axis Expert Panel", lambda: self._open_axis_panel(data, "ecmcAxisExpert.ui"))
        elif kind == "hardware":
            self._menu_action(menu, "Open Remote SDO Browser", lambda: self._open_sdo(data))
            menu.addSeparator()
            self._menu_action(menu, "Open Hardware Panel", lambda: self._open_hardware_panel(data))
        elif kind == "ecmc_group":
            self._menu_action(menu, "Open Command Parser", self._open_command_parser)
            self._menu_action(menu, "Open DAQ / FFT App", self._open_daq)
            self._menu_action(menu, "Open RT Log App", self._open_rtlog)
            menu.addSeparator()
            self._menu_action(menu, "Open Main Panel", self._open_main_panel)
        elif kind == "plc":
            self._menu_action(menu, "Open PLC Panel", lambda: self._open_object_panel("ecmcPLCxx.ui", data))
        elif kind == "plugin":
            self._menu_action(menu, "Open Plugin Panel", lambda: self._open_object_panel("ecmcPLGxx.ui", data))
        elif kind == "data_storage":
            self._menu_action(menu, "Open Data Storage Panel", lambda: self._open_object_panel("ecmcDSxx.ui", data))
        elif kind == "cpp_logic":
            self._menu_action(menu, "Open CppLogic Overview", self._open_cpp_logic_overview)
            menu.addSeparator()
            self._menu_action(menu, "Open CppLogic Panel", lambda: self._open_cpp_logic(data))
        elif kind == "safety_plugin":
            self._menu_action(menu, "Open SafetyPlugin Panel", self._open_safety_plugin)
        elif kind == "hardware_group":
            self._menu_action(menu, "Open Hardware Overview", self._open_hardware_overview)
        if not menu.isEmpty():
            menu.exec_(self.tree.viewport().mapToGlobal(pos)) if hasattr(menu, "exec_") else menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _menu_action(self, menu, title, callback):
        action = menu.addAction(title)
        action.triggered.connect(callback)

    def _default_action(self, item, _column):
        data = item.data(0, self.DATA_ROLE) or {}
        kind = data.get("kind")
        if kind == "axis":
            self._open_motion(data)
        elif kind == "ecmc_group":
            self._open_main_panel()
        elif kind == "hardware":
            self._open_hardware_panel(data)
        elif kind == "hardware_group":
            self._open_hardware_overview()
        elif kind == "plc":
            self._open_object_panel("ecmcPLCxx.ui", data)
        elif kind == "plugin":
            self._open_object_panel("ecmcPLGxx.ui", data)
        elif kind == "data_storage":
            self._open_object_panel("ecmcDSxx.ui", data)
        elif kind == "cpp_logic":
            self._open_cpp_logic(data)
        elif kind == "safety_plugin":
            self._open_safety_plugin()

    def _prefix(self):
        return self.prefix_edit.text().strip().rstrip(":")

    def _spawn(self, command, cwd=None):
        try:
            subprocess.Popen(command, cwd=str(cwd or self.app_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.statusBar().showMessage("Started: " + " ".join(str(part) for part in command), 5000)
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "Launch failed", str(ex))

    def _open_script(self, script_name, object_id):
        script = self.app_dir / script_name
        self._spawn(["bash", str(script), self._prefix(), str(object_id)], self.app_dir)

    def _open_script_no_object(self, script_name):
        script = self.app_dir / script_name
        self._spawn(["bash", str(script), self._prefix()], self.app_dir)

    def _caqtdm(self, panel, macro, panel_dir=None):
        executable = shutil.which("caqtdm") or "caqtdm"
        self._spawn([executable, "-macro", macro, panel], panel_dir or self.caqtdm_dir)

    def _open_main_panel(self):
        self._caqtdm("ecmcMain.ui", f"IOC={self._prefix()}")

    def _ec_overview_command(self):
        command = shutil.which("start_ecmc_overview.py")
        if command:
            return command
        for candidate in (
            "/sls/controls/bin/start_ecmc_overview.py",
            "/sf/controls/bin/start_ecmc_overview.py",
            "/hipa/controls/bin/start_ecmc_overview.py",
            "/proscan/controls/bin/start_ecmc_overview.py",
        ):
            path = Path(candidate)
            if path.exists() and os.access(str(path), os.X_OK):
                return str(path)
        return ""

    def _open_hardware_overview(self):
        command = self._ec_overview_command()
        if not command:
            QtWidgets.QMessageBox.critical(
                self,
                "Hardware overview not found",
                "Could not find start_ecmc_overview.py in PATH or known controls bin directories.",
            )
            return
        master = str(self._snapshot.get("master", "0"))
        rows = str(self._snapshot.get("ec_rows", "1"))
        self._spawn([command, "--master", master, "--rows", rows, self._prefix()], self.caqtdm_dir)

    def _open_command_parser(self, separate=False):
        if self.embed_tools_check.isChecked() and not separate:
            prefix = self._prefix()
            window = StreamWindow(
                catalog_path=str(self.app_dir / "ecmc_commands.json"),
                blocklist_path=str(self.app_dir / "ecmc_commands_blocklist_all.json"),
                default_cmd_pv=_join_prefix_pv(prefix, "MCU-Cmd.AOUT"),
                default_qry_pv=_join_prefix_pv(prefix, "MCU-Cmd.AINP"),
                timeout=max(self.timeout, 2.0),
                error_db_path=str(self.app_dir / "ecmc_error_codes.json"),
            )
            self._add_tool_tab(window, f"Command {prefix}")
            return
        script = self.app_dir / "start.sh"
        self._spawn(["bash", str(script), self._prefix()], self.app_dir)

    def _open_motion(self, data, separate=False):
        if self.embed_tools_check.isChecked() and not separate:
            axis_id = str(data.get("id", ""))
            try:
                window = MotionWindow(self._prefix(), axis_id, max(self.timeout, 2.0), axis_id_was_provided=True)
            except Exception as ex:
                self._add_error_tab(f"Motion {axis_id}", f"Could not open Motion App inside the navigator.\n\n{ex}")
                return
            self._add_tool_tab(window, f"Motion {axis_id}")
            return
        self._open_script("start_mtn.sh", data["id"])

    def _open_axis_config(self, data, separate=False):
        if self.embed_tools_check.isChecked() and not separate:
            prefix = self._prefix()
            axis_id = str(data.get("id", ""))
            try:
                window = AxisYamlConfigWindow(
                    catalog_path=str(self.app_dir / "ecmc_commands.json"),
                    yaml_path=str(self.app_dir / "axis_template.yaml"),
                    mapping_path="",
                    default_cmd_pv=_join_prefix_pv(prefix, "MCU-Cmd.AOUT"),
                    default_qry_pv=_join_prefix_pv(prefix, "MCU-Cmd.AINP"),
                    timeout=max(self.timeout, 2.0),
                    axis_id=axis_id,
                    title_prefix=prefix,
                    error_db_path=str(self.app_dir / "ecmc_error_codes.json"),
                    axis_id_was_provided=True,
                )
            except Exception as ex:
                self._add_error_tab(
                    f"Axis {axis_id}",
                    f"Could not open Axis Config App inside the navigator.\n\n{ex}",
                )
                return
            self._add_tool_tab(window, f"Axis {axis_id}")
            return
        self._open_script("start_axis.sh", data["id"])

    def _open_controller(self, data, separate=False):
        if self.embed_tools_check.isChecked() and not separate:
            prefix = self._prefix()
            axis_id = str(data.get("id", ""))
            sketch_image = ""
            for name in ("original.png", "controller_sketch.png"):
                candidate = self.app_dir / name
                if candidate.exists():
                    sketch_image = str(candidate)
                    break
            try:
                window = CntrlWindow(
                    catalog_path=str(self.app_dir / "ecmc_commands_cntrl.json"),
                    default_cmd_pv=_join_prefix_pv(prefix, "MCU-Cmd.AOUT"),
                    default_qry_pv=_join_prefix_pv(prefix, "MCU-Cmd.AINP"),
                    timeout=max(self.timeout, 2.0),
                    default_axis_id=axis_id,
                    title_prefix=prefix,
                    sketch_image_path=sketch_image,
                    error_db_path=str(self.app_dir / "ecmc_error_codes.json"),
                    axis_id_was_provided=True,
                )
            except Exception as ex:
                self._add_error_tab(
                    f"Controller {axis_id}",
                    f"Could not open Controller App inside the navigator.\n\n{ex}",
                )
                return
            self._add_tool_tab(window, f"Controller {axis_id}")
            return
        self._open_script("start_cntrl.sh", data["id"])

    def _open_iso230(self, data, separate=False):
        if self.embed_tools_check.isChecked() and not separate:
            axis_id = str(data.get("id", ""))
            try:
                window = Iso230Window(self._prefix(), axis_id, max(self.timeout, 2.0), axis_id_was_provided=True)
            except Exception as ex:
                self._add_error_tab(f"ISO230 {axis_id}", f"Could not open ISO230 App inside the navigator.\n\n{ex}")
                return
            self._add_tool_tab(window, f"ISO230 {axis_id}")
            return
        self._open_script("start_iso230.sh", data["id"])

    def _open_daq(self, separate=False):
        if self.embed_tools_check.isChecked() and not separate:
            window = DaqWindow(default_prefix=self._prefix(), initial_pvs=[], timeout=max(self.timeout, 2.0))
            self._add_tool_tab(window, "DAQ / FFT")
            return
        self._open_script_no_object("start_daq.sh")

    def _open_rtlog(self, data=None, separate=False):
        axis_id = str((data or {}).get("id", "1"))
        if self.embed_tools_check.isChecked() and not separate:
            window = RtLogWindow(
                prefix=self._prefix(),
                timeout=max(self.timeout, 2.0),
                poll_ms=250,
                history_limit=200,
                launch_axis_id=axis_id,
            )
            self._add_tool_tab(window, "RT Log")
            return
        if data is None:
            self._open_script_no_object("start_rtlog.sh")
        else:
            self._open_script("start_rtlog.sh", axis_id)

    def _open_axis_panel(self, data, panel="ecmcAxis.ui"):
        motor = data.get("motor", "")
        motor_prefix, _, name = motor.rpartition(":")
        macro = f"DEV={motor_prefix},IOC={self._prefix()},Axis={name},AX_ID={data['id']}"
        self._caqtdm(panel, macro)

    def _open_hardware_panel(self, data):
        slave = f"{int(data['id']):03d}"
        macro = (
            f"SYS={self._prefix()},IOC={self._prefix()},MasterID={self._snapshot.get('master', '0')},"
            f"SlaveID={slave},PANEL={data.get('panel', 'GenericSlave')}"
        )
        self._caqtdm("ecmcGenericSlaveOverview.ui", macro)

    def _open_object_panel(self, panel, data):
        item_id = int(data["id"])
        macro = f"SYS={self._prefix()},IOC={self._prefix()},ID_1={item_id},ID_2={item_id:02d}"
        self._caqtdm(panel, macro)

    def _open_cpp_logic(self, data):
        self._caqtdm("ecmcCppLogic.ui", f"IOC={self._prefix()},CPP_ID={int(data['id'])}")

    def _open_cpp_logic_overview(self):
        self._caqtdm("ecmcCppLogicOverview.ui", f"IOC={self._prefix()}")

    def _open_safety_plugin(self):
        panel_dir = self.safety_qt_dir if self.safety_qt_dir.exists() else self.caqtdm_dir
        self._caqtdm("ecmc_plugin_safety_main.ui", f"IOC={self._prefix()}", panel_dir)

    def _open_sdo(self, data, separate=False):
        default_host = self._ssh_host or self._snapshot.get("ssh_host", "")
        host, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Open Remote SDO Browser",
            "SSH server / host:",
            QtWidgets.QLineEdit.Normal,
            default_host,
        )
        if not accepted:
            return
        host = host.strip()
        if not host:
            QtWidgets.QMessageBox.warning(self, "Missing SSH host", "Enter the SSH host before opening the SDO browser.")
            return
        self._ssh_host = host
        master = self._snapshot.get("master", "0")
        slave = str(data["id"])
        if self.embed_tools_check.isChecked() and not separate:
            window = SdoBrowserWindow(host, master, slave)
            hw_name = str(data.get("name") or data.get("panel") or "").strip()
            title = f"SDO {slave} {hw_name}".strip()
            self._add_tool_tab(window, title)
            return
        script = self.app_dir / "start_sdo.sh"
        self._spawn(["bash", str(script), host, master, slave], self.app_dir)


def main():
    parser = argparse.ArgumentParser(description="Browse an ecmc IOC and launch object-specific tools")
    parser.add_argument("prefix", nargs="?", default="IOC:ECMC", help="IOC PV prefix")
    parser.add_argument("--ssh-host", default="", help="SSH host used by the SDO browser")
    parser.add_argument(
        "--ssh-host-pv", default="MCU-Cfg-Hostname", help="optional hostname PV or suffix read during refresh"
    )
    parser.add_argument("--timeout", type=float, default=1.0, help="PV read timeout")
    parser.add_argument("--caqtdm-dir", default="", help="directory containing ecmc caQtDM panels")
    parser.add_argument("--demo", action="store_true", help="show an offline example IOC without reading PVs")
    args = parser.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    window = IocNavigator(args.prefix, args.ssh_host, args.ssh_host_pv, args.timeout, args.caqtdm_dir, args.demo)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
