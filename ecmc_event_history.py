"""Bounded, session-local transition history; initial observations are not events."""
from collections import deque
from datetime import datetime,timezone

class EventHistory:
    def __init__(self,capacity=5000):
        self.events=deque(maxlen=capacity)
        self.previous={}
        self.revision=0
    def observe(self,scope,values):
        for key,value in values.items():
            token=(scope,key)
            if value in (None,""):
                self.previous.pop(token,None)
                continue
            old=self.previous.get(token)
            self.previous[token]=value
            if old is not None and old!=value:
                self.events.append((datetime.now(timezone.utc).isoformat(timespec="milliseconds"),scope,key,str(old),str(value)))
                self.revision+=1
    def reset_scope(self,scope):
        self.previous={k:v for k,v in self.previous.items() if k[0]!=scope}
