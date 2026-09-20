import unittest

from ecmc_sdo import (
    SdoEntry,
    build_ssh_command,
    download_arguments,
    ecmc_add_sdo_line,
    normalized_upload_value,
    parse_sdos,
    upload_arguments,
)


SAMPLE = '''SDO 0x6040, "Control word"
  0x6040:00, rwrwrw, uint16, 16 bit, "Control word"
SDO 0x6041, "Status word"
  0x6041:00, r-r-r-, uint16, 16 bit, "Status word"
'''


class SdoTests(unittest.TestCase):
    def test_parse_tree_and_permissions(self):
        objects = parse_sdos(SAMPLE)
        self.assertEqual([obj.index for obj in objects], ["0x6040", "0x6041"])
        self.assertEqual(objects[0].entries[0].subindex, "0x00")
        self.assertTrue(objects[0].entries[0].writable)
        self.assertFalse(objects[1].entries[0].writable)

    def test_remote_command_is_quoted(self):
        command = build_ssh_command(
            "c6025a", ["download", "-p", "1", "0x2000", "0", "string", "hello world"]
        )
        self.assertEqual(command[0], "ssh")
        self.assertIn("ControlMaster=auto", command)
        self.assertIn("ControlPersist=600", command)
        self.assertEqual(command[-2:], ["c6025a", "ethercat download -p 1 0x2000 0 string 'hello world'"])

    def test_upload_and_download_syntax(self):
        entry = SdoEntry("0x6040", "0x00", "rwrwrw", "uint16", "16 bit", "Control word")
        self.assertEqual(
            upload_arguments("2", "7", entry),
            ["upload", "-m", "2", "-p", "7", "0x6040", "0x00", "--type", "uint16"],
        )
        self.assertEqual(
            download_arguments("2", "7", entry, "6"),
            ["download", "-m", "2", "-p", "7", "0x6040", "0x00", "--type", "uint16", "6"],
        )

    def test_unknown_type_is_left_for_ethercat_to_infer(self):
        entry = SdoEntry("0x7002", "0x02", "r-r-r-", "type 0000", "7 bit", "Reserved")
        self.assertEqual(
            upload_arguments("0", "1", entry),
            ["upload", "-m", "0", "-p", "1", "0x7002", "0x02"],
        )

    def test_ecmc_snippet_uses_normalized_value_and_byte_size(self):
        entry = SdoEntry("0x6040", "0x00", "rwrwrw", "uint16", "16 bit", "Control word")
        self.assertEqual(normalized_upload_value("0x0006 6\n"), "6")
        self.assertEqual(
            ecmc_add_sdo_line("3", entry, "0x0006 6"),
            'ecmcConfigOrDie "Cfg.EcAddSdo(${ECMC_EC_SLAVE_NUM=3},0x6040,0x00,6,2)"',
        )


if __name__ == "__main__":
    unittest.main()
