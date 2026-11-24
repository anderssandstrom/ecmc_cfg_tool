"""Offline configuration simulator; no network access and no motion simulation."""
import re

DEFAULTS = {
    'AxisCntrlKp': '10', 'AxisCntrlKi': '0.05', 'AxisCntrlKd': '50',
    'AxisCntrlInnerKp': '5', 'AxisCntrlInnerKi': '0.01', 'AxisCntrlInnerKd': '0',
    'AxisCntrlInnerTol': '0.01', 'AxisCntrlKff': '1',
    'AxisCntrlIPartLL': '-100', 'AxisCntrlIPartHL': '100',
    'AxisCntrlOutLL': '-100', 'AxisCntrlOutHL': '100',
    'AxisCntrlDeadband': '0.001', 'AxisCntrlDeadbandTime': '50',
    'AxisMonAtTargetTol': '0.01', 'AxisMonAtTargetTime': '100',
    'AxisMonEnableAtTargetMon': '1', 'AxisMonSlvCtrlDbTol': '0.005',
    'AxisMonSlvCtrlDbTime': '100', 'AxisDrvScaleNum': '1',
    'AxisDrvScaleDenom': '1', 'AxisEncScaleNum': '0.001',
    'AxisEncScaleDenom': '1',
    'AxisVel': '10', 'AxisAcc': '5', 'AxisDec': '5', 'AxisJogVel': '2',
}


class DemoClient:
    backend = 'offline demo'

    def __init__(self, timeout=2.0):
        self.timeout = timeout
        self.values = {}
        self.reply = '0'

    def get(self, pv, as_string=False, **kwargs):
        pv = str(pv)
        if pv.endswith('MCU-Cmd.AINP'):
            return self.reply
        if pv.endswith('MCU-Cfg-AX-FrstObjId'):
            return '1'
        if pv.endswith('-NxtObjId'):
            return '-1'
        if re.search(r'MCU-Cfg-AX\d+-Pfx$', pv):
            return 'DEMO:'
        if re.search(r'MCU-Cfg-AX\d+-Nam$', pv):
            return 'X'
        if pv.endswith('-DrvMode'):
            return 'CSV'
        if pv.endswith('-Type'):
            return 'REAL'
        if pv.endswith('MCU-Cfg-Rate'):
            return '1000'
        if pv.endswith('MCU-Cfg-Time'):
            return '1'
        return '0'

    def put(self, pv, value, **kwargs):
        if not str(pv).endswith('MCU-Cmd.AOUT'):
            return True
        match = re.fullmatch(r'(?:Cfg\.)?(Get|Set)(\w+)\(([^()]*)\)', str(value).strip())
        if not match:
            self.reply = '0'
            return True
        operation, name, args = match.groups()
        parts = [v.strip() for v in args.split(',')]
        key = (parts[0], name)
        if operation == 'Set' and len(parts) > 1:
            self.values[key] = parts[1]
            self.reply = '0'
        else:
            self.reply = self.values.get(key, DEFAULTS.get(name, '0'))
        return True
