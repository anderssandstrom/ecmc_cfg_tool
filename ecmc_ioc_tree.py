#!/usr/bin/env python3
"""Build an ecmc IOC object tree from its configuration PVs."""

from __future__ import annotations

import re


def join_pv(prefix: str, suffix: str) -> str:
    return f"{str(prefix).rstrip(':')}:{str(suffix).lstrip(':')}"


def object_id(value) -> str:
    text = str(value if value is not None else "").strip().strip('"')
    if re.fullmatch(r"[-+]?\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return ""


def text_value(client, pv: str) -> str:
    try:
        return str(client.get(pv, as_string=True) or "").strip().strip('"')
    except Exception:
        return ""


def linked_ids(client, first_pv: str, next_pv, limit=1000) -> list[str]:
    current = object_id(text_value(client, first_pv))
    result = []
    visited = set()
    while current and current != "-1" and current not in visited and len(result) < limit:
        visited.add(current)
        result.append(current)
        current = object_id(text_value(client, next_pv(current)))
    return result


def discover_ioc(client, prefix: str, ssh_host_pv: str = "") -> dict:
    prefix = str(prefix).strip().rstrip(":")
    snapshot = {
        "prefix": prefix, "master": "0", "ssh_host": "", "axes": [], "hardware": [], "plcs": [],
        "plugins": [], "data_storages": [], "cpp_logic": [], "safety_plugins": []
    }
    if ssh_host_pv:
        pv = ssh_host_pv if ":" in ssh_host_pv else join_pv(prefix, ssh_host_pv)
        snapshot["ssh_host"] = text_value(client, pv)

    master = object_id(text_value(client, join_pv(prefix, "MCU-Cfg-EC-Mst")))
    snapshot["master"] = master if master and master != "-1" else "0"

    axis_ids = linked_ids(
        client,
        join_pv(prefix, "MCU-Cfg-AX-FrstObjId"),
        lambda item_id: join_pv(prefix, f"MCU-Cfg-AX{item_id}-NxtObjId"),
    )
    for item_id in axis_ids:
        axis_prefix = text_value(client, join_pv(prefix, f"MCU-Cfg-AX{item_id}-Pfx"))
        name = text_value(client, join_pv(prefix, f"MCU-Cfg-AX{item_id}-Nam"))
        motor = f"{axis_prefix.rstrip(':')}:{name}" if axis_prefix and name else (name or axis_prefix.rstrip(":"))
        axis_type = text_value(client, f"{motor}-Type") if motor else ""
        snapshot["axes"].append(
            {"id": item_id, "name": name or f"Axis {item_id}", "motor": motor, "axis_type": axis_type}
        )

    master_id = snapshot["master"]
    hardware_ids = linked_ids(
        client,
        join_pv(prefix, "MCU-Cfg-EC-FrstObjId"),
        lambda item_id: join_pv(prefix, f"m{master_id}s{int(item_id):03d}-NxtObjId"),
    )
    for item_id in hardware_ids:
        base = f"m{master_id}s{int(item_id):03d}"
        hw_type = text_value(client, join_pv(prefix, f"{base}-HWType"))
        panel = text_value(client, join_pv(prefix, f"{base}-PnlTyp")) or "GenericSlave"
        snapshot["hardware"].append(
            {"id": item_id, "name": hw_type or f"Slave {item_id}", "pv_base": base, "panel": panel}
        )

    plc_ids = linked_ids(
        client,
        join_pv(prefix, "MCU-Cfg-PLC-FrstObjId"),
        lambda item_id: join_pv(prefix, f"MCU-Cfg-PLC{item_id}-NxtObjId"),
    )
    for item_id in plc_ids:
        desc = text_value(client, join_pv(prefix, f"PLC{int(item_id):02d}-Desc"))
        snapshot["plcs"].append({"id": item_id, "name": desc or f"PLC {item_id}"})

    plugin_ids = linked_ids(
        client,
        join_pv(prefix, "MCU-Cfg-PLG-FrstObjId"),
        lambda item_id: join_pv(prefix, f"MCU-Cfg-PLG{item_id}-NxtObjId"),
    )
    snapshot["plugins"] = [{"id": item_id, "name": f"Plugin {item_id}"} for item_id in plugin_ids]

    storage_ids = linked_ids(
        client,
        join_pv(prefix, "MCU-Cfg-DS-FrstObjId"),
        lambda item_id: join_pv(prefix, f"MCU-Cfg-DS{item_id}-NxtObjId"),
    )
    for item_id in storage_ids:
        desc = text_value(client, join_pv(prefix, f"DS{int(item_id):02d}-Desc"))
        snapshot["data_storages"].append({"id": item_id, "name": desc or f"Data Storage {item_id}"})

    # CppLogic instances are allocated sequentially by loadCppLogic.cmd. There is
    # currently no configuration linked list, so stop at the first absent core PV.
    for item_id in range(32):
        rate = text_value(client, join_pv(prefix, f"CppLogic{item_id}-RateMsAct"))
        if not rate:
            break
        snapshot["cpp_logic"].append({"id": str(item_id), "name": f"CppLogic {item_id}", "rate_ms": rate})

    safety_loaded = text_value(client, join_pv(prefix, "SS1-Loaded"))
    if safety_loaded:
        group_count = text_value(client, join_pv(prefix, "SS1-GrpCnt"))
        snapshot["safety_plugins"].append(
            {"id": "0", "name": "SafetyPlugin", "loaded": safety_loaded, "group_count": group_count}
        )
    return snapshot


def demo_ioc(prefix="DEMO:ECMC") -> dict:
    return {
        "prefix": prefix,
        "master": "0",
        "ssh_host": "demo-host",
        "hardware": [
            {"id": "0", "name": "EK1100", "pv_base": "m0s000", "panel": "EK1100"},
            {"id": "1", "name": "EL7041", "pv_base": "m0s001", "panel": "EL70x1"},
        ],
        "axes": [
            {"id": "1", "name": "Axis1", "motor": "DEMO:Axis1", "axis_type": "REAL"},
            {"id": "2", "name": "VirtualAxis", "motor": "DEMO:VirtualAxis", "axis_type": "VIRTUAL"},
        ],
        "plcs": [{"id": "0", "name": "Main PLC"}],
        "plugins": [{"id": "0", "name": "Plugin 0"}],
        "data_storages": [{"id": "0", "name": "Position capture"}],
        "cpp_logic": [{"id": "0", "name": "CppLogic 0", "rate_ms": "1.0"}],
        "safety_plugins": [{"id": "0", "name": "SafetyPlugin", "loaded": "1", "group_count": "2"}],
    }
