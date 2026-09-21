import contextlib
import io
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ecmc_sdo_askpass import main


class AskpassTests(unittest.TestCase):
    def _exchange(self, response):
        with tempfile.TemporaryDirectory(prefix="ecmc_sdo_test_") as directory:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                try:
                    path = str(Path(directory) / "askpass.sock")
                    listener.bind(path)
                except PermissionError:
                    self.skipTest("Unix sockets are blocked by this sandbox")
                listener.listen(1)
                prompts = []

                def serve():
                    with listener.accept()[0] as connection:
                        prompts.append(connection.recv(4096))
                        connection.sendall(response)

                server = threading.Thread(target=serve)
                server.start()
                output = io.StringIO()
                with patch.dict("os.environ", {"ECMC_SDO_ASKPASS_SOCKET": path}):
                    with patch("sys.argv", ["askpass", "Test SSH password:"]):
                        with contextlib.redirect_stdout(output):
                            result = main()
                server.join(2)
                self.assertFalse(server.is_alive())
                self.assertEqual(prompts, [b"Test SSH password:"])
                return result, output.getvalue()

    def test_accepted_prompt(self):
        self.assertEqual(self._exchange(b"\x01sample"), (0, "sample\n"))

    def test_cancelled_prompt(self):
        self.assertEqual(self._exchange(b"\x00"), (1, ""))


if __name__ == "__main__":
    unittest.main()
