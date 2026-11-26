"""Axis diagnostic interface mappings from ecmcAxis.ui and ecmcAxisExpert.ui.

Native coordinates and motor record user coordinates are deliberately separate.
"""
NATIVE = (
    ("Motion", (("Actual position","-PosAct"), ("Target position","-TgtPosCmd"), ("Trajectory position","-PosSet"), ("Following error","-PosErr"), ("Actual velocity","-VelAct"), ("Target velocity","-TgtVelCmd"), ("Acceleration command","-AccCmd"), ("Deceleration command","-DecCmd"), ("Position units","-TgtPosCmd.EGU"), ("Tweak distance","-TwkDstCmd"))),
    ("State", (("Enable command","-EnaCmd"), ("Enable command RB","-EnaCmd-RB"), ("Auto execute","-AutoExecuteOnTargetPos-RB"), ("Enabled","-EnaAct"), ("Busy","-Busy"), ("Moving","-Moving"), ("Homed","-Homed"), ("At target","-AtTarget"), ("Sequence state","-SeqState"), ("Motion command","-MtnCmd"), ("Blocked","-Blocked"), ("In realtime","-InRT"))),
    ("Homing", (("Procedure","-movHomCmd"),)),
    ("Interlocks", (("Forward limit","-LimFwd"), ("Backward limit","-LimBwd"), ("Home switch","-HomeSwitch"), ("Forward interlock","-SumIlockFwd"), ("Backward interlock","-SumIlockBwd"), ("Interlock text","-IlockTxt"))),
    ("ECMC soft limits", (("Lower limit (dial)","-CfgDLLM"), ("Upper limit (dial)","-CfgDHLM"), ("Forward enabled","-SftLimFwdEna-RB"), ("Backward enabled","-SftLimBwdEna-RB"), ("Forward interlock","-SftLimFwdIlock"), ("Backward interlock","-SftLimBwdIlock"), ("Forward enable command","-SftLimFwdEna"), ("Backward enable command","-SftLimBwdEna"))),
    ("Controller and diagnostics", (("Error ID","-ErrId"), ("Warning ID","-WrnId"), ("Message","-MsgTxt"), ("Encoder count","-EncCnt"), ("Selected encoder RB","-SetEncIdCmd-RB"), ("Encoder source","-EncSrcTyp-RB"), ("Trajectory source","-TrjSrcTyp-RB"), ("Trajectory type","-TrajType"), ("Drive type","-DrvType"), ("Drive mode","-DrvMode"), ("Kp","-Ctrl-Kp"), ("Ki","-Ctrl-Ki"), ("Kd","-Ctrl-Kd"))),
)
MOTOR = (
    ("Motion (motor record user coordinates)", (("Actual position",".RBV"), ("Requested position",".VAL"), ("Velocity setting",".VELO"), ("Acceleration time",".ACCL"), ("Units",".EGU"), ("Tweak distance",".TWV"))),
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

NATIVE_EDIT = {"-CfgDLLM","-CfgDHLM","-TgtPosCmd","-TgtVelCmd","-AccCmd","-DecCmd","-TwkDstCmd","-Ctrl-Kp","-Ctrl-Ki","-Ctrl-Kd"}
MOTOR_EDIT = {".VAL",".VELO",".ACCL",".TWV",".HLM",".LLM"}
NATIVE_ACTIONS = (("Enable","-EnaCmd",(1,)),("Disable","-EnaCmd",(0,)),
                  ("Execute","-ExeCmd",(0,1)),("Tweak −","-TwkBwdCmd",(1,)),
                  ("Tweak +","-TwkFwdCmd",(1,)),("Reset error","-ErrRst",(1,0)))
MOTOR_ACTIONS = (("Enable",".CNEN",(1,)),("Disable",".CNEN",(0,)),
                 ("Home forward",".HOMF",(1,)),("Home reverse",".HOMR",(1,)),
                 ("Tweak −",".TWR",(1,)),("Tweak +",".TWF",(1,)))

BOOL_COMMANDS = {"-EnaCmd",".CNEN","-SftLimFwdEna","-SftLimBwdEna"}
BOOL_STATUS = {"-SftLimFwdEna-RB","-SftLimBwdEna-RB","-SftLimFwdIlock","-SftLimBwdIlock","-EnaCmd-RB","-AutoExecuteOnTargetPos-RB","-EnaAct","-Busy","-Moving",
               "-Homed","-AtTarget","-Blocked","-InRT","-LimFwd","-LimBwd",
               "-HomeSwitch","-SumIlockFwd","-SumIlockBwd",".DMOV",".LVIO",".HLS",".LLS"}
MOTION_ENUMS = {"-MtnCmd","-movHomCmd"}

def format_axis_value(value, precision=8):
    if value is None:
        return "—"
    if isinstance(value,float):
        return f"{value:.{precision}g}"
    return str(value)

# Native hard-limit inputs are active-low, unlike motor record HLS/LLS.
ACTIVE_LOW_STATUS = {"-LimFwd","-LimBwd"}

def axis_status_value(suffix, value):
    if suffix in ACTIVE_LOW_STATUS and value in (0,1):
        return 1-int(value)
    return value

def native_soft_bounds(low, high, low_enabled, high_enabled):
    """Only enabled limits constrain a target; unknown state fails closed."""
    import math
    if low_enabled not in (0,1) or high_enabled not in (0,1):
        raise ValueError("Native soft-limit enable status unavailable")
    if low_enabled and (low is None or not math.isfinite(low)):
        raise ValueError("Native lower soft limit unavailable")
    if high_enabled and (high is None or not math.isfinite(high)):
        raise ValueError("Native upper soft limit unavailable")
    lower = low if low_enabled else -math.inf
    upper = high if high_enabled else math.inf
    if lower > upper:
        raise ValueError("Native soft limits reversed")
    return lower, upper

# EPICS motorApp/MotorSrc/motor.h, little-endian msta_field.Bits.
MSTA_BITS = ("Direction", "Done", "Plus limit", "Home switch",
             "Slip enabled", "Position maintenance", "Slip/stall",
             "Encoder home", "Encoder present", "Problem", "Moving",
             "Gain support", "Communication error", "Minus limit", "Homed")

def decode_msta(value):
    try:
        word = int(value)
        if not 0 <= word <= 0xffffffff or float(value) != word:
            return {}
    except (TypeError, ValueError, OverflowError):
        return {}
    return {label: (word >> bit) & 1 for bit,label in enumerate(MSTA_BITS)}
