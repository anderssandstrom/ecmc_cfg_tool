"""IOC tree health interpretation and object PV prefixes."""
import math

def object_prefix(prefix,data):
    kind=data.get("kind")
    if kind=="axis":
        return data.get("motor","")
    if kind=="hardware":
        return prefix.rstrip(":")+":"+data["pv_base"] if data.get("pv_base") else ""
    try:
        index=int(data.get("id"))
    except (TypeError,ValueError):
        return ""
    base={"plc":f"PLC{index:02d}","state_machine":f"SM{index:02d}",
          "cpp_logic":f"CPP{index:02d}","data_storage":f"DS{index:02d}",
          "plugin":f"MCU-Cfg-PLG{index}","axis_group":f"MCU-Cfg-AXGRP{index}"}.get(kind)
    return prefix.rstrip(":")+":"+base if base else ""

def health_pvs(prefix,data):
    base=object_prefix(prefix,data)
    if not base:
        return ()
    suffixes={"axis":("ErrId","EnaAct"),"hardware":("Online","Operational"),
              "plc":("Err","EnaCmd"),"state_machine":("StatTimeout","EnaCmd"),
              "cpp_logic":("StatWord","RateMsAct"),"data_storage":("Full","DataIdAct")}.get(data.get("kind"),())
    return tuple(base+"-"+s for s in suffixes)

def health_status(kind,a,b):
    def valid(v):
        try:
            return math.isfinite(float(v))
        except (ValueError,TypeError,OverflowError):
            return False
    if not valid(a) or not valid(b):
        return "Unknown"
    if kind=="hardware":
        return "Offline" if a==0 else "Not OP" if b==0 else "OP" if a==b==1 else "Unknown"
    if kind=="state_machine":
        return "Timeout" if a==1 else "Enabled" if a==0 and b==1 else "Disabled" if a==b==0 else "Unknown"
    if kind=="cpp_logic":
        return "Connected" if b>0 else "Unknown"
    if kind=="data_storage":
        return "Full" if a==1 else "Ready" if a==0 else "Unknown"
    return f"Error {int(a)}" if a!=0 else "Enabled" if b==1 else "Disabled" if b==0 else "Unknown"

def is_problem(status):
    return status.startswith("Error") or status in ("Offline","Not OP","Unknown","Timeout")
