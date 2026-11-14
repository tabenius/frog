import io
import unittest

from ragbaz_frog.vfs_s3 import S3VFS


class TrackingBody(io.BytesIO):
    """S3-like response body that records whether it was closed."""

    def __init__(self, data=b""):
        super().__init__(data)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


class FakeS3:
    def __init__(self, body: TrackingBody):
        self.body = body
        self.uploads = []

    def get_object(self, *, Bucket, Key):
        return {"Body": self.body}

    def put_object(self, *, Bucket, Key, Body):
        self.uploads.append((Bucket, Key, Body))


def make_vfs(body=b"Synthetic original"):
    """Build S3VFS without boto3 or network access."""
    vfs = object.__new__(S3VFS)
    vfs._prefix = "workspace"
    vfs._root = "/workspace"
    vfs._cwd = "/workspace"
    vfs._bucket = "synthetic-bucket"
    fake = FakeS3(TrackingBody(body))
    vfs._s3 = fake
    return vfs, fake


class S3VfsOpenLifecycle(unittest.TestCase):
    def test_text_read_is_correct_and_closes_the_body(self):
        vfs, fake = make_vfs()
        with vfs.open("/notes.txt") as file:
            self.assertEqual(file.read(), "Synthetic original")
        self.assertTrue(fake.body.was_closed)

    def test_binary_read_is_correct_and_closes_the_body(self):
        vfs, fake = make_vfs()
        with vfs.open("/blob", "rb") as file:
            self.assertEqual(file.read(), b"Synthetic original")
        self.assertTrue(fake.body.was_closed)

    def test_exception_inside_a_read_still_closes_the_body(self):
        vfs, fake = make_vfs()
        with self.assertRaises(RuntimeError):
            with vfs.open("/notes.txt") as file:
                self.assertEqual(file.read(), "Synthetic original")
                raise RuntimeError("synthetic failure")
        self.assertTrue(fake.body.was_closed)

    def test_binary_write_uploads_the_written_bytes(self):
        vfs, fake = make_vfs()
        with vfs.open("/blob", "wb") as file:
            file.write(b"Synthetic binary")
        self.assertEqual(
            fake.uploads, [("synthetic-bucket", "workspace/blob", b"Synthetic binary")]
        )

    def test_text_write_uploads_the_written_text(self):
        vfs, fake = make_vfs()
        with vfs.open("/notes.txt", "w") as file:
            file.write("Synthetic new text")
        self.assertEqual(
            fake.uploads,
            [("synthetic-bucket", "workspace/notes.txt", b"Synthetic new text")],
        )


if __name__ == "__main__":
    unittest.main()
