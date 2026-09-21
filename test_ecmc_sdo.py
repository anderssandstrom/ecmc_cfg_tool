import unittest

from ecmc_sdo import (
    SdoEntry,
    build_ssh_command,
    build_remote_command,
    decode_command_output,
    display_upload_value,
    download_arguments,
    ecmc_add_sdo_line,
    entry_byte_size,
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
        self.assertIn("ConnectTimeout=10", command)
        self.assertEqual(command[-2:], [
            "c6025a", "/opt/etherlab/bin/ethercat download -p 1 0x2000 0 string 'hello world'"
        ])

    def test_remote_binary_can_be_overridden(self):
        self.assertEqual(build_ssh_command("host", ["sdos"], "/custom/ethercat")[-1], "/custom/ethercat sdos")

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

    def test_unknown_type_uses_unsigned_int_for_size(self):
        entry = SdoEntry("0x7002", "0x02", "r-r-r-", "type 0000", "7 bit", "Reserved")
        self.assertEqual(entry.effective_data_type, "uint8")
        self.assertEqual(
            upload_arguments("0", "1", entry),
            ["upload", "-m", "0", "-p", "1", "0x7002", "0x02", "--type", "uint8"],
        )
        self.assertEqual(
            download_arguments("0", "1", entry, "1"),
            ["download", "-m", "0", "-p", "1", "0x7002", "0x02", "--type", "uint8", "1"],
        )

    def test_unknown_type_uses_exact_byte_width(self):
        entry = SdoEntry("0x7002", "0x03", "r-r-r-", "type 0000", "17 bit", "Reserved")
        self.assertEqual(entry.effective_data_type, "uint24")
        self.assertEqual(
            upload_arguments("0", "1", entry),
            ["upload", "-m", "0", "-p", "1", "0x7002", "0x03", "--type", "uint24"],
        )

    def test_octet_string_values_keep_spaces_and_are_quoted(self):
        entry = SdoEntry("0x2000", "0x01", "rwrwrw", "octet_string", "32 bit", "Label")
        self.assertTrue(entry.is_text_like)
        self.assertEqual(display_upload_value(entry, "hello world with spaces\n"), "hello world with spaces")
        arguments = download_arguments("0", "1", entry, "hello world with spaces")
        self.assertEqual(
            arguments,
            [
                "download", "-m", "0", "-p", "1", "0x2000", "0x01",
                "--type", "octet_string", "hello world with spaces",
            ],
        )
        self.assertEqual(
            build_remote_command(arguments),
            "/opt/etherlab/bin/ethercat download -m 0 -p 1 0x2000 0x01 --type octet_string "
            "'hello world with spaces'",
        )

    def test_command_output_decoder_tolerates_octet_bytes(self):
        self.assertEqual(decode_command_output(b"\xe0 diagnostic message"), "� diagnostic message")

    def test_ecmc_snippet_uses_normalized_value_and_byte_size(self):
        entry = SdoEntry("0x6040", "0x00", "rwrwrw", "uint16", "16 bit", "Control word")
        self.assertEqual(normalized_upload_value("0x0006 6\n"), "6")
        self.assertEqual(
            ecmc_add_sdo_line("3", entry, "0x0006 6"),
            "# 0x6040:00 Control word | type=uint16 | bits=16 bit | access=rwrwrw | value=6\n"
            'ecmcConfigOrDie "Cfg.EcAddSdo(${ECMC_EC_SLAVE_NUM=3},0x6040,0x00,6,2)"',
        )

    def test_one_bit_entries_use_one_byte(self):
        entry = SdoEntry("0x2000", "0x01", "rwrwrw", "bool", "1 bit", "Enable")
        self.assertEqual(entry_byte_size(entry), 1)
        self.assertEqual(
            ecmc_add_sdo_line("4", entry, "1"),
            "# 0x2000:01 Enable | type=bool | bits=1 bit | access=rwrwrw | value=1\n"
            'ecmcConfigOrDie "Cfg.EcAddSdo(${ECMC_EC_SLAVE_NUM=4},0x2000,0x01,1,1)"',
        )

    def test_zero_bit_entries_are_not_operable(self):
        entry = SdoEntry("0x2000", "0x00", "rwrwrw", "uint8", "0 bit", "Number of entries")
        self.assertEqual(entry.bit_count, 0)
        self.assertFalse(entry.has_data)
        self.assertFalse(entry.readable)
        self.assertFalse(entry.writable)


if __name__ == "__main__":
    unittest.main()
