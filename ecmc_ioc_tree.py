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


def read_pv_group(client, prefix: str, rows: list[tuple[str, str]]) -> list[dict]:
    values = []
    for label, suffix in rows:
        pv = join_pv(prefix, suffix)
        value = text_value(client, pv)
        values.append({"label": label, "pv": pv, "value": value})
    return values


def update_object_counts(snapshot: dict) -> None:
    count_labels = {
        "hardware": "Hardware objects",
        "axes": "Motion axes",
        "axis_groups": "Axis groups",
        "state_machines": "State machines",
        "plcs": "PLCs",
        "plugins": "Plugins",
        "data_storages": "Data storages",
        "cpp_logic": "CppLogic",
        "safety_plugins": "SafetyPlugin",
    }
    for group in snapshot.get("ecmc", []):
        if group.get("name") != "Object Counts":
            continue
        items = group.setdefault("items", [])
        existing = {item.get("label") for item in items}
        for key, label in count_labels.items():
            if label in existing:
                continue
            items.append({"label": label, "pv": "", "value": str(len(snapshot.get(key, [])))})
        return


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
        "prefix": prefix, "master": "0", "ec_rows": "1", "ssh_host": "", "ecmc": [], "axes": [],
        "axis_groups": [], "state_machines": [], "hardware": [], "plcs": [], "plugins": [],
        "data_storages": [], "cpp_logic": [], "safety_plugins": []
    }
    if ssh_host_pv:
        pv = ssh_host_pv if ":" in ssh_host_pv else join_pv(prefix, ssh_host_pv)
        snapshot["ssh_host"] = text_value(client, pv)

    master = object_id(text_value(client, join_pv(prefix, "MCU-Cfg-EC-Mst")))
    snapshot["master"] = master if master and master != "-1" else "0"
    master_id = snapshot["master"]
    rows = object_id(text_value(client, join_pv(prefix, "MCU-Cfg-UI-EC-Rows")))
    snapshot["ec_rows"] = rows if rows and rows != "-1" else "1"

    snapshot["ecmc"] = [
        {
            "name": "General",
            "items": read_pv_group(client, prefix, [
                ("IOC prefix", f"m{master_id}-Prefix"),
                ("Config info", "MCU-Cfg-Info"),
                ("Naming", "MCU-Cfg-Naming"),
                ("Mode", "MCU-Cfg-Mode"),
                ("Engineering mode", "MCU-Cfg-Eng-Mode"),
                ("PV time", "MCU-Cfg-PV-Time"),
                ("Config time", "MCU-Cfg-Time"),
                ("Realtime rate", "MCU-Cfg-Rate"),
                ("EtherCAT master", "MCU-Cfg-EC-Mst"),
            ]),
        },
        {
            "name": "Versions",
            "items": read_pv_group(client, prefix, [
                ("EPICS version", f"m{master_id}-Epics-Ver"),
                ("ecmccfg version", f"m{master_id}-Ecmccfg-Ver"),
            ]),
        },
        {
            "name": "Thread",
            "items": read_pv_group(client, prefix, [
                ("Period min", "MCU-ThdPrdMin"),
                ("Period max", "MCU-ThdPrdMax"),
                ("Latency min", "MCU-ThdLatMin"),
                ("Latency max", "MCU-ThdLatMax"),
                ("Execute min", "MCU-ThdExeMin"),
                ("Execute max", "MCU-ThdExeMax"),
                ("Send min", "MCU-ThdSndMin"),
                ("Send max", "MCU-ThdSndMax"),
                ("Average frequency", "MCU-ThdFrqAvg"),
                ("RT priority OK", "MCU-ThdRTPrioOK"),
                ("Memory locked", "MCU-ThdMemLocked"),
            ]),
        },
        {
            "name": "Object Counts",
            "items": read_pv_group(client, prefix, [
                ("Axes", "MCU-Cfg-AX-Cnt"),
                ("Axis groups", "MCU-Cfg-AXGRP-Cnt"),
                ("Sequences", "MCU-Cfg-SEQ-Cnt"),
                ("PLCs", "MCU-Cfg-PLC-Cnt"),
                ("Plugins", "MCU-Cfg-PLG-Cnt"),
                ("Data storages", "MCU-Cfg-DS-Cnt"),
                ("EtherCAT slaves", "MCU-Cfg-EC-Slv-Cnt"),
                ("EtherCAT domains", "MCU-Cfg-EC-Dom-Cnt"),
            ]),
        },
        {
            "name": "Status",
            "items": read_pv_group(client, prefix, [
                ("Error id", "MCU-ErrId"),
                ("Error message", "MCU-ErrMsg"),
            ]),
        },
    ]

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

    group_count = object_id(text_value(client, join_pv(prefix, "MCU-Cfg-AXGRP-Cnt")))
    for item_id in range(int(group_count or "0")):
        name = text_value(client, join_pv(prefix, f"MCU-Cfg-AXGRP{item_id}-Nam"))
        axes = text_value(client, join_pv(prefix, f"MCU-Cfg-AXGRP{item_id}-Axes"))
        snapshot["axis_groups"].append(
            {"id": str(item_id), "name": name or f"Axis Group {item_id}", "axes": axes}
        )

    sm_ids = linked_ids(
        client,
        join_pv(prefix, "MCU-Cfg-SM-FrstObjId"),
        lambda item_id: join_pv(prefix, f"MCU-Cfg-SM{item_id}-NxtObjId"),
    )
    snapshot["state_machines"] = [
        {"id": item_id, "name": f"State Machine {item_id}"} for item_id in sm_ids
    ]

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
    update_object_counts(snapshot)
    return snapshot


def demo_ioc(prefix="DEMO:ECMC") -> dict:
    snapshot = {
        "prefix": prefix,
        "master": "0",
        "ec_rows": "8",
        "ssh_host": "demo-host",
        "ecmc": [
            {
                "name": "General",
                "items": [
                    {"label": "IOC prefix", "pv": f"{prefix}:m0-Prefix", "value": prefix},
                    {"label": "Realtime rate", "pv": f"{prefix}:MCU-Cfg-Rate", "value": "1000"},
                    {"label": "EtherCAT master", "pv": f"{prefix}:MCU-Cfg-EC-Mst", "value": "0"},
                ],
            },
            {
                "name": "Versions",
                "items": [
                    {"label": "EPICS version", "pv": f"{prefix}:m0-Epics-Ver", "value": "demo-epics"},
                    {"label": "ecmccfg version", "pv": f"{prefix}:m0-Ecmccfg-Ver", "value": "demo-ecmccfg"},
                ],
            },
            {
                "name": "Thread",
                "items": [
                    {"label": "Period min", "pv": f"{prefix}:MCU-ThdPrdMin", "value": "999000"},
                    {"label": "Period max", "pv": f"{prefix}:MCU-ThdPrdMax", "value": "1001000"},
                    {"label": "Average frequency", "pv": f"{prefix}:MCU-ThdFrqAvg", "value": "1000.000"},
                    {"label": "RT priority OK", "pv": f"{prefix}:MCU-ThdRTPrioOK", "value": "Yes"},
                ],
            },
            {
                "name": "Object Counts",
                "items": [
                    {"label": "Axes", "pv": f"{prefix}:MCU-Cfg-AX-Cnt", "value": "2"},
                    {"label": "EtherCAT slaves", "pv": f"{prefix}:MCU-Cfg-EC-Slv-Cnt", "value": "2"},
                ],
            },
            {
                "name": "Status",
                "items": [
                    {"label": "Error id", "pv": f"{prefix}:MCU-ErrId", "value": "0"},
                    {"label": "Error message", "pv": f"{prefix}:MCU-ErrMsg", "value": ""},
                ],
            },
        ],
        "hardware": [
            {"id": "0", "name": "EK1100", "pv_base": "m0s000", "panel": "EK1100"},
            {"id": "1", "name": "EL7041", "pv_base": "m0s001", "panel": "EL70x1"},
        ],
        "axes": [
            {"id": "1", "name": "Axis1", "motor": "DEMO:Axis1", "axis_type": "REAL"},
            {"id": "2", "name": "VirtualAxis", "motor": "DEMO:VirtualAxis", "axis_type": "VIRTUAL"},
        ],
        "axis_groups": [{"id": "0", "name": "Demo group", "axes": "1,2"}],
        "state_machines": [{"id": "0", "name": "State Machine 0"}],
        "plcs": [{"id": "0", "name": "Main PLC"}],
        "plugins": [{"id": "0", "name": "Plugin 0"}],
        "data_storages": [{"id": "0", "name": "Position capture"}],
        "cpp_logic": [{"id": "0", "name": "CppLogic 0", "rate_ms": "1.0"}],
        "safety_plugins": [{"id": "0", "name": "SafetyPlugin", "loaded": "1", "group_count": "2"}],
    }
    update_object_counts(snapshot)
    return snapshot
