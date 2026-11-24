"""Offline comparison of verified EtherCAT SDO readback snapshots."""
import re
from decimal import Decimal, InvalidOperation

def canonical_value(value,data_type):
    text=str(value).strip()
    typ=str(data_type).lower()
    if any(x in typ for x in ('string','octet','domain')):
        return text
    if re.fullmatch(r'[-+]?0[xX][0-9a-fA-F]+',text):
        return int(text,16)
    if re.fullmatch(r'[-+]?\d+',text):
        return int(text,10)
    try:
        v=Decimal(text)
        return v if v.is_finite() else text
    except InvalidOperation:
        return text

def compare_snapshots(old,new):
    def rows(snapshot):
        result={}
        for row in snapshot['entries']:
            key=(str(row['index']).lower(),str(row['subindex']).lower())
            if key in result:
                raise ValueError('Duplicate SDO '+':'.join(key))
            result[key]=row
        return result
    a,b=rows(old),rows(new)
    result=[]
    for key in sorted(a.keys()|b.keys()):
        x,y=a.get(key),b.get(key)
        if x is None: state='Not in baseline'
        elif y is None: state='Not read in current'
        elif (x.get('type'),x.get('bits'))!=(y.get('type'),y.get('bits')): state='Type mismatch'
        elif canonical_value(x['value'],x.get('type'))!=canonical_value(y['value'],y.get('type')): state='Changed'
        else: continue
        result.append(dict(key=key,old=x,new=y,state=state))
    return result

def decode_word(index,subindex,value,bits,data_type):
    if not isinstance(bits,int) or not 1<=bits<=64:
        return []
    if not re.fullmatch(r'(?:u?int\d+|boolean|bool|bit\d+)',str(data_type).lower()):
        return []
    v=canonical_value(value,data_type)
    if not isinstance(v,int):
        return []
    v &= (1<<bits)-1
    result=[f'Hex: 0x{v:0{(bits+3)//4}X}',f'Binary: {v:0{bits}b}',
            'Set bits: '+(', '.join(str(i) for i in range(bits) if v&(1<<i)) or 'none')]
    if index.lower()=='0x6041' and subindex.lower()=='0x00' and bits==16:
        names={0:'Ready to switch on',1:'Switched on',2:'Operation enabled',3:'Fault',
               4:'Voltage enabled',5:'Quick stop (1 = inactive)',6:'Switch on disabled',
               7:'Warning',9:'Remote',10:'Target reached',11:'Internal limit active'}
        result.extend(f'Bit {i}: {name}: {int(bool(v&(1<<i)))}' for i,name in names.items())
    return result
