import io
import unittest

import _util
from ragbaz_frog.vfs_ssh import SSHVFS


class TrackingStream(io.BytesIO):
    """SFTP-like binary stream that records whether it was closed."""

    def __init__(self, data=b""):
        super().__init__(data)
        self.was_closed = False
        self.persisted = None

    def close(self):
        if not self.was_closed:
            self.persisted = self.getvalue()
            self.was_closed = True
        super().close()


class FakeSFTP:
    def __init__(self, data=b""):
        self.stream = TrackingStream(data)
        self.opened = []

    def open(self, path, mode):
        self.opened.append((path, mode))
        if "w" in mode:
            self.stream = TrackingStream(b"")
        return self.stream


class SshVfsOpenLifecycle(unittest.TestCase):
    def setUp(self):
        self.vfs = SSHVFS(
            "ssh://synthetic-host/src",
            host="synthetic-host",
            username="synthetic-user",
            root="/src",
        )
        self.sftp = FakeSFTP(b"Synthetic original")
        self.vfs._ensure_connected = lambda: self.sftp

    def test_text_read_is_correct_and_closes_the_stream(self):
        with self.vfs.open("/notes.txt") as file:
            self.assertEqual(file.read(), "Synthetic original")
        self.assertTrue(self.sftp.stream.was_closed)

    def test_text_write_is_flushed_and_closes_the_stream(self):
        with self.vfs.open("/notes.txt", "w") as file:
            file.write("Synthetic new text")
        self.assertTrue(self.sftp.stream.was_closed)
        self.assertEqual(self.sftp.stream.persisted, b"Synthetic new text")

    def test_binary_read_is_correct_and_closes_the_stream(self):
        with self.vfs.open("/blob", "rb") as file:
            self.assertEqual(file.read(), b"Synthetic original")
        self.assertTrue(self.sftp.stream.was_closed)

    def test_binary_write_is_flushed_and_closes_the_stream(self):
        with self.vfs.open("/blob", "wb") as file:
            file.write(b"Synthetic binary")
        self.assertTrue(self.sftp.stream.was_closed)
        self.assertEqual(self.sftp.stream.persisted, b"Synthetic binary")

    def test_exception_inside_the_block_still_closes_the_stream(self):
        with self.assertRaises(RuntimeError):
            with self.vfs.open("/notes.txt", "w") as file:
                file.write("Synthetic partial")
                raise RuntimeError("synthetic failure")
        self.assertTrue(self.sftp.stream.was_closed)


if __name__ == "__main__":
    unittest.main()
