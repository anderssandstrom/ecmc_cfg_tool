"""Pure, conservative axis diagnostic rules: unknown is never healthy."""
from ecmc_axis_diagnostics import decode_msta

def diagnose(samples,native=True):
    rows=[]
    def add(level,title,detail,keys):
        rows.append((level,title,detail,tuple(keys)))
    def check(key,expected,title,bad,level="Blocked"):
        value=samples.get(key)
        add("Unknown" if value not in (0,1) else "OK" if value==expected else level,
            title,"Readback unavailable" if value not in (0,1) else "Expected state" if value==expected else bad,(key,))
    if native:
        error=samples.get("-ErrId")
        add("Unknown" if error is None else "OK" if error==0 else "Error","Axis error",
            "Readback unavailable" if error is None else "No error reported" if error==0 else str(samples.get("-MsgTxt") or error),("-ErrId","-MsgTxt"))
        check("-InRT",1,"Realtime","Axis is not in realtime")
        check("-EnaCmd-RB",1,"Enable command","Enable not requested","Inactive")
        check("-EnaAct",1,"Drive enabled","Drive not enabled")
        check("-Blocked",0,"Motion permission","Motion commands blocked")
        check("-Busy",0,"Axis idle","Axis busy","Active")
        check("-Homed",1,"Homing","Not homed; relevance depends on configuration","Warning")
        for name,short in (("Forward","Fwd"),("Backward","Bwd")):
            check("-SumIlock"+short,0,name+" interlock",name+" motion inhibited")
            check("-Lim"+short,1,name+" hard limit",name+" limit input active","Warning")
            check("-SftLim"+short+"Ilock",0,name+" soft limit",name+" software limit active","Warning")
        for key,title in (("-TrjSrcTyp-RB","Trajectory source"),("-EncSrcTyp-RB","Encoder source")):
            value=samples.get(key)
            add("Unknown" if value not in (0,1) else "Info",title,{0:"Internal",1:"PLC"}.get(value,"Readback unavailable"),(key,))
    else:
        check(".CNEN",1,"Drive command","Drive enable not requested")
        check(".DMOV",1,"Done moving","Motion not done","Active")
        check(".LVIO",0,"Limit violation","Motor record limit violation")
        for key,title in ((".HLS","High limit"),(".LLS","Low limit")):
            check(key,0,title,"Limit active","Warning")
        bits=decode_msta(samples.get(".MSTA"))
        for key in ("Problem","Communication error","Slip/stall"):
            v=bits.get(key)
            add("Unknown" if v is None else "Error" if v else "OK",key,"Readback unavailable" if v is None else "Reported" if v else "Not reported",(".MSTA",))
    return rows
