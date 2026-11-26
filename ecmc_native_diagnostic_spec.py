"""Verified ECMCCFG native diagnostic record contracts."""
PLC = (
    ("Description","Desc","text"), ("Enabled","EnaCmd","switch"),
    ("Scan time","ScanTime","seconds"), ("Error","Err","error"),
    ("Debug flag","DbgCmd","switch"),
)
SM = (
    ("Name","Nam","text"),("Slave group","SlvGrpNam","text"),("Master group","MstGrpNam","text"),
    ("Enabled","EnaCmd","switch"),("Requested state","StateCmd","enum"),
    ("Current phase","StatTxt","text"),("Previous state","StatPrvStateTxt","text"),
    ("Transition reason","StatTrRsnTxt","text"),("Historical fault","StatHistFault","alarm"),
    ("Timeout","StatTimeout","alarm"),("Status word","Stat","hex"),
    ("Slave active","StatSlvAct","bool"),("Preparing master","StatPrepMst","bool"),
    ("Waiting for slave external","StatWaitSlvExt","bool"),("Master moving","StatMstMov","bool"),
    ("Waiting for master target","StatWaitMstAt","bool"),("Waiting for master disable","StatWaitMstDis","bool"),
    ("Auto disable masters","MstsAutoDsbleCmd","switch"),("Auto disable slaves","SlvsAutoDsbleCmd","switch"),
    ("Debug printouts","DbgPrntEna","switch"),
)
THREAD = (
    ("RT priority OK","ThdRTPrioOK","health"),("Memory locked","ThdMemLocked","health"),
    ("Average frequency","ThdFrqAvg","hz"),("Period minimum","ThdPrdMin","ns"),
    ("Period maximum","ThdPrdMax","ns"),("Latency minimum","ThdLatMin","ns"),
    ("Latency maximum","ThdLatMax","ns"),("Execution minimum","ThdExeMin","ns"),
    ("Execution maximum","ThdExeMax","ns"),("Send minimum","ThdSndMin","ns"),
    ("Send maximum","ThdSndMax","ns"),
)

def diagnostic_rows(prefix,kind,object_id=None):
    prefix = str(prefix).strip().rstrip(":")
    if not prefix:
        raise ValueError("IOC prefix required")
    if kind == "thread":
        base,rows = prefix+":MCU-",THREAD
    elif kind in ("plc","state_machine"):
        index=int(object_id)
        if index<0:
            raise ValueError("Invalid object ID")
        base = prefix+(":PLC" if kind=="plc" else ":SM")+f"{index:02d}-"
        rows = PLC if kind=="plc" else SM
    else:
        raise ValueError("Unknown diagnostic kind")
    return tuple((label,base+suffix,mode) for label,suffix,mode in rows)
