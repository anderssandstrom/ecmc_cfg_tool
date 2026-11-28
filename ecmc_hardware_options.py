"""Resolve optional hardware signals from startup metadata, not PV probes."""
import re

STEPPER = {'EX7062_OPT','EL7062_OPT','ED7062_OPT','MO7062-0100_OPT'}
SERVO = {'EX72XX_OPT':1,'EX72XX_OPT_2ch':2,'EL7201_OPT':1,
         'EL7201-0010_OPT':1,'EL7211-9014_OPT':1,'EL7411_OPT':1,
         'EP7211-0034_OPT':1,'ELM7222-0010_OPT':2,'ELM7231-0010_OPT':1}
STEP_FIELDS = ('Mode','TouchProbe','Inputs','Encoder2','Diagnostics','TpDc','AutoTune','FollowingError')
SERVO_FIELDS = ('Mode','Info2','TouchProbe','Diagnostics','TorqueOffset','FollowingError','EncoderStatus')

def variant(candidates):
    keys = [str(x or '').removeprefix('ecmc').removesuffix('.ui') for x in candidates]
    for key in sorted(keys, key=lambda x:x.startswith('EX')):
        if key in STEPPER:
            return key,2
        if key in SERVO:
            return key,SERVO[key]
    return None,0

def legacy_names(key, channels):
    names = {'Supply':'CfgSupplyDiagnostics'} if key in STEPPER else {}
    for i in range(1,channels+1):
        for field in STEP_FIELDS if key in STEPPER else SERVO_FIELDS:
            names[f'{i:02d}.{field}'] = 'Cfg'+field+('02' if i==2 else '')
    return names

def parse_options(raw, key, channels):
    if raw is None:
        return {}
    if not isinstance(raw,str):
        raw = bytes(raw).split(b'\0',1)[0].decode('ascii')
    fields = dict(part.split('=',1) for part in raw.strip('\0').split(';') if part)
    if fields.get('v')!='1' or fields.get('Channels')!=str(channels):
        return {}
    names = legacy_names(key,channels)
    if not names.keys() <= fields.keys():
        return {}
    return {k:int(fields[k]) for k in names}

def read_options(base,key,channels,timeout,epics_module=None):
    if epics_module is None:
        import epics as epics_module
    try:
        raw = epics_module.caget(base+'HWOptions$',as_string=False,timeout=timeout)
        opts = parse_options(raw,key,channels)
        if opts:
            return opts,'HWOptions'
    except Exception:
        pass
    names = legacy_names(key,channels)
    try:
        values = epics_module.caget_many([base+n for n in names.values()],
                      as_string=False,timeout=timeout,connection_timeout=timeout)
        opts = {k:int(v) for k,v in zip(names,values) if v is not None}
        return opts,'legacy configuration' if opts else 'configuration unavailable'
    except Exception:
        return {},'configuration unavailable'

def allowed_signal(s,key,channels,opts):
    step = key in STEPPER
    if s.startswith('Cfg'):
        return s in {name for k,name in legacy_names(key,channels).items() if k in opts} or (s=='CfgChannels' and not step and bool(opts))
    m = re.fullmatch(r'(Drv|Enc|Tp)(\d\d)-(.+)',s)
    if not m:
        return False
    group,ch,field = m.groups()
    if not 1<=int(ch)<=channels:
        return False
    def enabled(name):
        return opts.get(ch+'.'+name)==1
    mode = opts.get(ch+'.Mode')
    if group=='Enc':
        if field=='PosAct':
            return step or mode in (0,1,2) or (mode==3 and key=='EL7411_OPT')
        if field=='PosAct02':
            return step and enabled('Encoder2')
        return not step and enabled('EncoderStatus') and field in ('Stat','NotValid')
    if group=='Tp':
        if not enabled('TouchProbe'):
            return False
        return step and enabled('TpDc') if 'Timestamp' in field else True
    if field in ('Cmd','Cmd-RB','Stat','WrnAlrm','ErrAlrm','TrgDS402Ena','TrgDS402Dis','DS402State','DS402StateCode'):
        return True
    if field in ('Spd','Spd-RB'):
        return mode==0
    if field in ('PosCmd','PosCmd-RB'):
        return mode==1
    if field in ('Trq','Trq-RB'):
        return mode in (2,3)
    if field.startswith('Tune'):
        return step and enabled('AutoTune')
    if field in ('VelAct','TrqAct','VolAct'):
        return enabled('Diagnostics')
    if field=='PosDiff':
        return enabled('FollowingError')
    if step:
        if field in ('VltAct','TmpAct'):
            return ch=='01' and opts.get('Supply')==1
        return field.startswith('BI') and enabled('Inputs')
    if field in ('TrqOff','TrqOff-RB'):
        return enabled('TorqueOffset')
    if field in ('ComAngle','ComAngle-RB'):
        return mode==3
    info = opts.get(ch+'.Info2')
    if field=='Errors':
        return info==2
    if field=='TmpAct':
        return info==1
    if field in ('InfoData2','BI01','BI02','STO01'):
        return info==0
    return False


def demo_options(key, channels):
    """One realistic, deterministic process image per demo terminal."""
    opts = {name: 0 for name in legacy_names(key, channels)}
    if key in STEPPER:
        opts['Supply'] = 1
    for i in range(1, channels + 1):
        ch = f'{i:02d}.'
        opts[ch+'Mode'] = 0 if i == 1 else 1
        opts[ch+'TouchProbe'] = int(key not in ('EL7201_OPT', 'ELM7231-0010_OPT'))
        opts[ch+'Diagnostics'] = 1
        opts[ch+'FollowingError'] = int(i == 2)
        if key in STEPPER:
            opts[ch+'Encoder2'] = 1
            opts[ch+'Inputs'] = int(key != 'MO7062-0100_OPT')
            opts[ch+'AutoTune'] = 1
        else:
            opts[ch+'EncoderStatus'] = int(key != 'EL7411_OPT')
    return opts


def overview_signal(name):
    """Keep optional diagnostics and configuration in Expert/All PVs."""
    if name in ('HWType','PnlTyp','Online','Operational') or name.startswith('Alstate-'):
        return True
    m = re.fullmatch(r'(Drv|Enc|Tp)\d\d-(.+)', name)
    if not m:
        return False
    group, signal = m.groups()
    if group == 'Enc':
        return signal in ('PosAct','PosAct02','NotValid')
    if group == 'Tp':
        return False
    return signal in ('Cmd','Cmd-RB','Stat','ErrAlrm','WrnAlrm','Spd','Spd-RB','PosCmd','PosCmd-RB','Trq','Trq-RB','DS402State','TrgDS402Ena','TrgDS402Dis','VelAct','PosDiff')


def option_tab(name):
    """Classify already-filtered OPT records; never infer enabled options."""
    if name in ('HWType','PnlTyp','Online','Operational') or name.startswith('Alstate-'):
        return 'Common'
    if name.startswith('Cfg'):
        return 'Config'
    m=re.fullmatch(r'(Drv|Enc|Tp)\d\d-(.+)',name)
    if not m:
        return 'Diagnostics'
    group,signal=m.groups()
    if group=='Tp':
        return 'TP'
    if group=='Enc':
        return 'Drive / encoder' if signal in ('PosAct','PosAct02','NotValid') else 'Diagnostics'
    if signal.startswith('Tune'):
        return 'Tune'
    if signal.startswith('BI') or signal=='STO01':
        return 'Inputs'
    if signal in ('TrqOff','TrqOff-RB','ComAngle','ComAngle-RB'):
        return 'Options'
    return 'Drive / encoder' if overview_signal(name) else 'Diagnostics'

OPTION_TABS=('Drive / encoder','TP','Inputs','Diagnostics','Tune','Options','Config')

def option_pages(pairs):
    pages={name:[] for name in OPTION_TABS}
    for signal,pv in pairs:
        name=option_tab(signal)
        if name!='Common':
            pages[name].append((signal,pv))
    return [(name,rows) for name,rows in pages.items() if rows]
