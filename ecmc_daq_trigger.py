"""Bounded CA event capture with edge triggering (timestamps are IOC timestamps)."""
from collections import deque
import math


class TriggerCapture:
    def __init__(self,pvs,trigger_pv,edge,threshold,pre_s,post_s,max_samples):
        self.trigger_pv=trigger_pv
        self.edge=edge
        self.threshold=abs(threshold) if edge==2 else threshold
        self.pre_s=pre_s
        self.post_s=post_s
        self.buffers={p:deque(maxlen=max_samples) for p in pvs}
        self.timestamp=None
        self.previous=None

    @property
    def fired(self):
        return self.timestamp is not None

    def add(self,pv,ts,value):
        if not math.isfinite(ts) or not math.isfinite(value):
            return False
        fired=False
        if pv==self.trigger_pv and not self.fired:
            v=abs(value) if self.edge==2 else value
            old=self.previous
            self.previous=v
            if old is not None and ((old>self.threshold>=v) if self.edge==1 else (old<self.threshold<=v)):
                self.timestamp=ts
                fired=True
                for q in self.buffers.values():
                    kept=[p for p in q if self.timestamp-self.pre_s<=p[0]<=self.timestamp+self.post_s]
                    q.clear()
                    q.extend(kept)
        q=self.buffers.get(pv)
        if q is not None:
            if not self.fired or self.timestamp-self.pre_s<=ts<=self.timestamp+self.post_s:
                q.append((ts,value))
                if not self.fired:
                    while q and q[0][0]<ts-self.pre_s:
                        q.popleft()
        return fired
