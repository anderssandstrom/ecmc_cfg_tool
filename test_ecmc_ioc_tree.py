import unittest

from ecmc_ioc_tree import demo_ioc, discover_ioc, linked_ids, object_id


class FakeClient:
    def __init__(self, values):
        self.values = values

    def get(self, pv, as_string=False):
        if pv not in self.values:
            raise RuntimeError("missing")
        return self.values[pv]


class IocTreeTests(unittest.TestCase):
    def test_demo_contains_every_navigator_category(self):
        demo = demo_ioc()
        for key in ("hardware", "axes", "plcs", "plugins", "data_storages", "cpp_logic", "safety_plugins"):
            self.assertTrue(demo[key], key)

    def test_object_id_normalizes_epics_numeric_text(self):
        self.assertEqual(object_id("3.000"), "3")
        self.assertEqual(object_id(-1), "-1")
        self.assertEqual(object_id("bad"), "")

    def test_linked_ids_stops_on_cycle(self):
        client = FakeClient({"first": "1", "next1": "2", "next2": "1"})
        self.assertEqual(linked_ids(client, "first", lambda value: f"next{value}"), ["1", "2"])

    def test_discovers_all_primary_categories(self):
        p = "IOC:ECMC"
        values = {
            f"{p}:MCU-Cfg-EC-Mst": "0",
            f"{p}:MCU-Cfg-Host": "c6025a",
            f"{p}:MCU-Cfg-AX-FrstObjId": "3",
            f"{p}:MCU-Cfg-AX3-NxtObjId": "-1",
            f"{p}:MCU-Cfg-AX3-Pfx": "DEV:",
            f"{p}:MCU-Cfg-AX3-Nam": "M1",
            "DEV:M1-Type": "REAL",
            f"{p}:MCU-Cfg-EC-FrstObjId": "7",
            f"{p}:m0s007-NxtObjId": "-1",
            f"{p}:m0s007-HWType": "EL7041",
            f"{p}:m0s007-PnlTyp": "EL70x1",
            f"{p}:MCU-Cfg-PLC-FrstObjId": "2",
            f"{p}:MCU-Cfg-PLC2-NxtObjId": "-1",
            f"{p}:PLC02-Desc": "Motion PLC",
            f"{p}:MCU-Cfg-PLG-FrstObjId": "4",
            f"{p}:MCU-Cfg-PLG4-NxtObjId": "-1",
            f"{p}:MCU-Cfg-DS-FrstObjId": "5",
            f"{p}:MCU-Cfg-DS5-NxtObjId": "-1",
            f"{p}:DS05-Desc": "Position capture",
            f"{p}:CppLogic0-RateMsAct": "1.0",
            f"{p}:SS1-Loaded": "1",
            f"{p}:SS1-GrpCnt": "2",
        }
        result = discover_ioc(FakeClient(values), p, "MCU-Cfg-Host")
        self.assertEqual(result["ssh_host"], "c6025a")
        self.assertEqual(result["axes"][0]["motor"], "DEV:M1")
        self.assertEqual(result["hardware"][0]["panel"], "EL70x1")
        self.assertEqual(result["plcs"][0]["name"], "Motion PLC")
        self.assertEqual(result["plugins"][0]["id"], "4")
        self.assertEqual(result["data_storages"][0]["name"], "Position capture")
        self.assertEqual(result["cpp_logic"][0]["rate_ms"], "1.0")
        self.assertEqual(result["safety_plugins"][0]["group_count"], "2")


if __name__ == "__main__":
    unittest.main()
