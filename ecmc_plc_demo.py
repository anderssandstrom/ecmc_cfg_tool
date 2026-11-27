"""Offline PLC source and deterministic diagnostic snapshots."""
def demo_program():
    source = """// DEMO ONLY — illustrative parking sequence
// State 0: idle, 10: moving, 20: parked, 99: fault
if (global.parkRequest and state == 0) {
    state := 10;
    timeout := 0;
}
if (state == 10) {
    timeout := timeout + 1;
    if (global.interlock or timeout > 20000) {
        state := 99;
    } else if (abs(ax1.posact - park1) < tolerance and
               abs(ax2.posact - park2) < tolerance) {
        state := 20;
    }
}
counter := counter + 1;
// Additional diagnostic variables for multi-page testing:
"""
    names = [('Local','counter'),('Local','state'),('Local','timeout'),('Local','park1'),('Local','park2'),('Local','tolerance'),('Global','global.parkRequest'),('Global','global.interlock'),('Global','ax1.posact'),('Global','ax2.posact')]
    for i in range(1,33):
        name=f'diag{i:02d}'
        names.append(('Local',name))
        source+=f'{name} := counter + {i};\n'
    return source,names

def demo_snapshot(variables,page,elapsed):
    t=max(0,elapsed)
    counter=int(t*1000)
    fraction=min(t/8,1)
    values={'counter':counter,'state':10 if fraction<1 else 20,'timeout':min(counter,8000),'park1':60,'park2':5.3,'tolerance':.01,'global.parkRequest':1,'global.interlock':0,'ax1.posact':60*fraction,'ax2.posact':5.3*fraction}
    return {i:values.get(name,counter+int(name[4:]) if name.startswith('diag') else 0) for i,(_,name) in enumerate(variables) if page*32<=i<(page+1)*32}
