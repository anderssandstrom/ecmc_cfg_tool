"""Domain counter and snapshot comparison helpers."""
def domain_signals(prefix,master,count):
    try: n=int(count)
    except (ValueError,TypeError): return ()
    if not 0<=n<=256: return ()
    return tuple((i,prefix.rstrip(":")+f":m{int(master)}-Dom{i}-"+suffix)
                 for i in range(n) for suffix in ("WC","WC-Complete","WC-Incomplete","WC-Zero","FailCntrTot","RedunActive"))

def counter_change(old,new):
    if old is None or new is None: return None
    try:
        a,b=int(old),int(new)
        if a!=float(old) or b!=float(new) or min(a,b)<0 or b<a: return None
        return b-a
    except (TypeError,ValueError,OverflowError): return None

def compare_snapshots(before,after):
    """Compare discovered objects by stable group and ID, and named configuration PVs."""
    def flatten(snapshot):
        result={}
        discovery=snapshot.get("discovery",snapshot)
        for group in discovery.get("ecmc",[]):
            if group.get("name") not in ("General","Versions","Object Counts"): continue
            for row in group.get("items",[]):
                result[(group["name"],row.get("label",""),"value")]=row.get("value")
        for kind in ("hardware","axes","axis_groups","state_machines","plcs","plugins","data_storages","cpp_logic","safety_plugins"):
            for obj in discovery.get(kind,[]):
                key=str(obj.get("id",""))
                if kind=="hardware": key=str(obj.get("master",discovery.get("master","0")))+":"+key
                for field,value in obj.items():
                    result[(kind,key,field)]=value
        return result
    a,b=flatten(before),flatten(after)
    return [(k,a.get(k),b.get(k),"Added" if k not in a else "Removed" if k not in b else "Changed")
            for k in sorted(a.keys()|b.keys()) if k not in a or k not in b or a[k]!=b[k]]
