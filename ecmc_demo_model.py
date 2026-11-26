"""Pure demo models and bounded waveform sampling."""
import math
def waveform_points(values,limit=20000):
    if limit<=0: raise ValueError("Positive limit required")
    step=max(1,(len(values)+limit-1)//limit)
    result=[]
    for i in range(0,len(values),step):
        try:
            v=float(values[i])
            if math.isfinite(v): result.append((i,v))
        except (TypeError,ValueError,OverflowError): pass
    return result
class DemoMotion:
    def __init__(self,position=10):
        self.position=float(position)
        self.target=self.position
        self.velocity=0.
        self.speed=0.
        self.continuous=False
        self.active=False
    def start(self,target,speed,continuous=False):
        if not math.isfinite(speed) or speed==0 or not math.isfinite(target):
            raise ValueError("Invalid demo command")
        self.target=float(target)
        self.speed=float(speed)
        self.continuous=continuous
        self.active=True
    def stop(self):
        self.velocity=0.
        self.active=False
        self.continuous=False
    def step(self,dt,low,high):
        if not self.active: return
        if not all(math.isfinite(v) for v in (dt,low,high)) or low>high:
            self.stop()
            return
        target=(high if self.speed>0 else low) if self.continuous else min(high,max(low,self.target))
        delta=target-self.position
        duration=max(0,min(dt,.5))
        distance=abs(self.speed)*duration
        if abs(delta)<=distance:
            self.position=target
            self.stop()
        else:
            self.velocity=math.copysign(abs(self.speed),delta) if duration else 0.
            self.position+=math.copysign(distance,delta)
