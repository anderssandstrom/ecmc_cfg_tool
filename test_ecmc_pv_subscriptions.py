import unittest
from ecmc_pv_subscriptions import SubscriptionPool

class FakePV:
    def __init__(self,name,callback,connection_callback,**kw):
        self.callback,self.connection=callback,connection_callback
        self.closed=False
        connection_callback(conn=True)
    def clear_callbacks(self):pass
    def disconnect(self):self.closed=True

class SubscriptionTests(unittest.TestCase):
    def test_shared_lifetime_and_disconnect(self):
        created=[]
        def factory(*a,**k):
            pv=FakePV(*a,**k);created.append(pv);return pv
        pool=SubscriptionPool(factory)
        events=[]
        a=pool.acquire('IOC:X',lambda **kw:events.append(kw['value']))
        b=pool.acquire('IOC:X')
        self.assertEqual(len(created),1)
        created[0].callback(value=2,severity=0,timestamp=5)
        self.assertEqual(events,[2]);self.assertEqual(b.snapshot()['value'],2)
        a.close();a.close();self.assertFalse(created[0].closed)
        created[0].connection(conn=False)
        self.assertIsNone(b.snapshot()['value'])
        b.close();self.assertTrue(created[0].closed);self.assertEqual(pool.entries,{})
        created[0].callback(value=99);self.assertEqual(events,[2])
    def test_late_join_and_isolation(self):
        pool=SubscriptionPool(FakePV)
        a=pool.acquire('IOC:X',lambda **kw:1/0)
        pool.entries['IOC:X']['pv'].callback(value=3,severity=0)
        events=[]
        b=pool.acquire('IOC:X',lambda **kw:events.append(kw['value']))
        self.assertEqual(events,[3])
        a.close();b.close()
    def test_factory_error_does_not_leave_entry(self):
        def fail(*a,**k):raise RuntimeError('unavailable')
        pool=SubscriptionPool(fail)
        with self.assertRaises(RuntimeError):pool.acquire('IOC:X')
        self.assertEqual(pool.entries,{})
if __name__=='__main__':unittest.main()
