"""Axis diagnostic interface mappings from ecmcAxis.ui and ecmcAxisExpert.ui.

Native coordinates and motor record user coordinates are deliberately separate.
"""
NATIVE = (
    ("Motion", (("Actual position","-PosAct"), ("Target position","-TgtPosCmd"), ("Trajectory position","-PosSet"), ("Following error","-PosErr"), ("Actual velocity","-VelAct"), ("Target velocity","-TgtVelCmd"), ("Acceleration command","-AccCmd"), ("Deceleration command","-DecCmd"), ("Position units","-TgtPosCmd.EGU"))),
    ("State", (("Enable command RB","-EnaCmd-RB"), ("Enabled","-EnaAct"), ("Busy","-Busy"), ("Moving","-Moving"), ("Homed","-Homed"), ("At target","-AtTarget"), ("Sequence state","-SeqState"), ("Motion command","-MtnCmd"), ("Blocked","-Blocked"), ("In realtime","-InRT"))),
    ("Interlocks", (("Forward limit","-LimFwd"), ("Backward limit","-LimBwd"), ("Home switch","-HomeSwitch"), ("Forward interlock","-SumIlockFwd"), ("Backward interlock","-SumIlockBwd"), ("Interlock text","-IlockTxt"))),
    ("Controller and diagnostics", (("Error ID","-ErrId"), ("Warning ID","-WrnId"), ("Message","-MsgTxt"), ("Encoder count","-EncCnt"), ("Selected encoder RB","-SetEncIdCmd-RB"), ("Encoder source","-EncSrcTyp-RB"), ("Trajectory source","-TrjSrcTyp-RB"), ("Trajectory type","-TrajType"), ("Drive type","-DrvType"), ("Drive mode","-DrvMode"), ("Kp","-Ctrl-Kp"), ("Ki","-Ctrl-Ki"), ("Kd","-Ctrl-Kd"))),
)
MOTOR = (
    ("Motion (motor record user coordinates)", (("Actual position",".RBV"), ("Requested position",".VAL"), ("Velocity setting",".VELO"), ("Units",".EGU"), ("Tweak distance",".TWV"))),
    ("State", (("Done moving",".DMOV"), ("Enable command",".CNEN"), ("Motor status word",".MSTA"), ("Limit violation",".LVIO"))),
    ("Limits", (("High limit switch",".HLS"), ("Low limit switch",".LLS"), ("High software limit",".HLM"), ("Low software limit",".LLM"))),
)
def axis_signals(base, interface):
    if interface not in ("native","motor"):
        raise ValueError("Unknown axis interface")
    if not base:
        return ()
    return tuple((group, tuple((label,base+suffix) for label,suffix in rows))
                 for group,rows in (NATIVE if interface == "native" else MOTOR))
