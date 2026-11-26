"""Resolve axis hardware links and build local caQtDM overview displays."""

import re
from math import ceil
from pathlib import Path
from xml.etree import ElementTree as ET

from ecmc_caqtdm import panel_directories
from ecmc_ioc_tree import join_pv, object_id, text_value


AXIS_LINKS = (
    ("Drive", "CfgLnkDrvPosSet"),
    ("Forward limit", "CfgLnkMonLimFwd"),
    ("Backward limit", "CfgLnkMonLimBwd"),
    ("Home switch", "CfgLnkMonHme"),
)
EC_PATH = re.compile(r"^ec([0-9]+)\.s([0-9]+)\.")


def axis_hardware(client, prefix, motor, master):
    count = object_id(text_value(client, f"{motor}-EncCnt"))
    # Older IOCs may expose the primary encoder link without an encoder count.
    encoders = range(1, 1 + (int(count) if count else 1))
    links = [(f"Encoder {i}", f"Enc{i:02d}-CfgLnkPosAct") for i in encoders]
    slaves = {}
    for role, suffix in [*links, *AXIS_LINKS]:
        pv = f"{motor}-{suffix}"
        value = text_value(client, pv)
        # ecmcAxisLinks.db stores the slave position in VAL and the EC path in DESC.
        path = EC_PATH.match(value) or EC_PATH.match(text_value(client, pv + ".DESC"))
        if path:
            address = tuple(map(int, path.groups()))
        else:
            slave = object_id(value)
            if not slave or int(slave) < 0:
                continue
            address = int(master), int(slave)
        slaves.setdefault(address, []).append(role)

    hardware = []
    for (master_id, slave_id), roles in slaves.items():
        base = join_pv(prefix, f"m{master_id}s{slave_id:03d}")
        panel = text_value(client, base + "-PnlTyp")
        hw_desc = text_value(client, base + "-HwDesc") if not panel or panel == "GenericSlave" else ""
        hw_type = text_value(client, base + "-HWType")
        hardware.append({
            "master": master_id, "id": slave_id, "roles": roles,
            "name": hw_desc or hw_type, "panels": [panel, hw_desc, hw_type],
        })
    return hardware


def _display_panel(candidates, directories):
    panel_names = [name for name in candidates if name != "GenericSlave"] + ["GenericSlave"]
    for name in panel_names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.ui)?", name):
            continue
        stem = name.removesuffix(".ui")
        if not stem.startswith("ecmc"):
            stem = "ecmc" + stem
        for directory in directories:
            path = directory / (stem + ".ui")
            if path.is_file():
                return path
    raise FileNotFoundError("Could not find ecmcGenericSlave.ui in the caQtDM display path.")


def _property(widget, name, kind, value, **attributes):
    prop = ET.SubElement(widget, "property", name=name, **attributes)
    ET.SubElement(prop, kind).text = str(value)


def _geometry(widget, x, y, width, height):
    prop = ET.SubElement(widget, "property", name="geometry")
    rect = ET.SubElement(prop, "rect")
    for name, value in zip(("x", "y", "width", "height"), (x, y, width, height)):
        ET.SubElement(rect, name).text = str(value)


def _widget(parent, kind, name):
    return ET.SubElement(parent, "widget", {"class": kind, "name": name})


def _panel_size(panel):
    widget = ET.parse(panel).find("widget")
    geometry = widget.find("./property[@name='geometry']/rect")
    if geometry is None:
        return 160, 500
    width, height = (int(geometry.findtext(key)) for key in ("width", "height"))
    # Some terminal displays have a shared frame wider than their root widget.
    for rect in widget.findall("./widget/property[@name='geometry']/rect"):
        x, y, w, h = (int(rect.findtext(key)) for key in ("x", "y", "width", "height"))
        width, height = max(width, x + w), max(height, y + h)
    return width, height


def write_axis_hardware_display(path, prefix, motor, hardware, panel_dir=None):
    directories = panel_directories(panel_dir)
    panels = _hardware_panels(prefix, hardware, directories, show_roles=True)
    _write_overview(path, f"{motor} · EtherCAT Hardware", panels)


def write_hardware_overview(path, prefix, hardware, rows, panel_dir=None):
    panels = _hardware_panels(prefix, hardware, panel_directories(panel_dir))
    _write_overview(path, f"{prefix} · EtherCAT Hardware", panels, columns=ceil(len(panels) / max(1, rows)))


def _hardware_panels(prefix, hardware, directories, show_roles=False):
    panels = []
    for slave in hardware:
        panel = _display_panel(slave["panels"], directories)
        master, slave_id = slave["master"], slave["id"]
        macros = f"SYS={prefix},IOC={prefix},MasterID={master},SlaveID={slave_id:03d}"
        heading = f"m{master} s{slave_id:03d}  {slave['name']}".strip() if show_roles else ""
        roles = ", ".join(slave["roles"]) if show_roles else ""
        panels.append((panel, macros, heading, roles))
    return panels


def write_motion_overview(path, prefix, axes, panel_dir=None):
    directories = panel_directories(panel_dir)
    panel = next((directory / "ecmcAxisMini.ui" for directory in directories
                  if (directory / "ecmcAxisMini.ui").is_file()), None)
    if panel is None:
        raise FileNotFoundError("Could not find ecmcAxisMini.ui in the caQtDM display path.")
    panels = []
    for axis in axes:
        device, _, name = axis["motor"].rpartition(":")
        macros = f"IOC={prefix},DEV={device},Axis={name},AX_ID={int(axis['id'])}"
        panels.append((panel, macros, "", ""))
    _write_overview(path, f"{prefix} · Motion Axes", panels, columns=6)


def _write_overview(path, title, panels, columns=None):
    ui = ET.Element("ui", version="4.0")
    ET.SubElement(ui, "class").text = "Overview"
    main = _widget(ui, "QMainWindow", "Overview")
    _property(main, "windowTitle", "string", title)
    _property(main, "Title", "string", title, stdset="0")
    central = _widget(main, "QWidget", "centralwidget")
    layout = ET.SubElement(central, "layout", {"class": "QVBoxLayout", "name": "layout"})
    scroll = _widget(ET.SubElement(layout, "item"), "QScrollArea", "scroll")
    _property(scroll, "frameShape", "enum", "QFrame::NoFrame")
    _property(scroll, "widgetResizable", "bool", "false")
    content = _widget(scroll, "QWidget", "panels")
    columns = columns or max(1, len(panels))
    y, content_width = 0, 0
    sizes = {panel: _panel_size(panel) for panel in dict.fromkeys(entry[0] for entry in panels)}
    for start in range(0, len(panels), columns):
        x, row_height = 0, 0
        for index, (panel, macros, heading, roles) in enumerate(panels[start:start + columns], start):
            width, height = sizes[panel]
            panel_y = y
            column_width = width + 4
            if heading:
                column_width = max(column_width, 190)
                label = _widget(content, "QLabel", f"heading_{index}")
                _geometry(label, x + 4, y + 2, column_width - 8, 22)
                _property(label, "textFormat", "enum", "Qt::PlainText")
                _property(label, "text", "string", heading)
                _property(label, "toolTip", "string", heading)
                _property(label, "styleSheet", "string", "font-weight: bold;")
                label = _widget(content, "QLabel", f"roles_{index}")
                _geometry(label, x + 4, y + 26, column_width - 8, 56)
                _property(label, "alignment", "set", "Qt::AlignLeft|Qt::AlignTop")
                _property(label, "textFormat", "enum", "Qt::PlainText")
                _property(label, "text", "string", roles)
                _property(label, "toolTip", "string", roles)
                _property(label, "wordWrap", "bool", "true")
                panel_y += 84
            include = _widget(content, "caInclude", f"panel_{index}")
            _geometry(include, x, panel_y, width + 4, height + 4)
            _property(include, "filename", "string", str(panel), stdset="0")
            _property(include, "macro", "string", macros)
            _property(include, "frameShape", "enum", "caInclude::Panel")
            _property(include, "frameShadow", "enum", "QFrame::Plain")
            _property(include, "frameLineWidth", "number", 1)
            color = ET.SubElement(ET.SubElement(include, "property", name="frameColor"), "color")
            for component in ("red", "green", "blue"):
                ET.SubElement(color, component).text = "115"
            x += column_width + (12 if heading else 6)
            row_height = max(row_height, panel_y - y + height + 4)
        content_width = max(content_width, x)
        y += row_height + 6
    _geometry(content, 0, 0, content_width, y)
    _geometry(main, 0, 0, min(content_width + 35, 1100), min(y + 24, 770))
    classes = ET.SubElement(ui, "customwidgets")
    include_class = ET.SubElement(classes, "customwidget")
    for name, value in (("class", "caInclude"), ("extends", "QWidget"), ("header", "caInclude")):
        ET.SubElement(include_class, name).text = value
    ET.ElementTree(ui).write(Path(path), encoding="utf-8", xml_declaration=True)
