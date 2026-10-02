"""S3/R2 VFS implementation for Frog."""

from __future__ import annotations

import contextlib
import fnmatch
import posixpath
import stat
from datetime import datetime
from typing import BinaryIO, Iterator, Optional, TextIO, Union

from .vfs import VFS, VFSStat, register_vfs

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None


class S3VFS(VFS):
    """S3/R2 virtual filesystem using boto3."""

    scheme = "s3"

    def __init__(
        self,
        uri: str,
        bucket: str,
        prefix: str = "",
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
    ):
        if boto3 is None:
            raise RuntimeError("boto3 not installed: pip install boto3")

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._root = f"/{self._prefix}" if self._prefix else "/"

        config = BotoConfig(
            retries={"max_attempts": 3, "mode": "adaptive"},
            connect_timeout=30,
            read_timeout=30,
        )
        session = boto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
            region_name=region,
        )
        self._s3 = session.client("s3", endpoint_url=endpoint_url, config=config)
        self._cwd = self._root

    def _to_key(self, path: str) -> str:
        path = self.resolve(path)
        if path == "/":
            return self._prefix
        rel = path.lstrip("/")
        return posixpath.join(self._prefix, rel) if self._prefix else rel

    def _from_key(self, key: str) -> str:
        if not self._prefix:
            return "/" + key
        if not key.startswith(self._prefix):
            return "/" + key
        rel = key[len(self._prefix):].lstrip("/")
        return "/" + rel if rel else "/"

    def _stat_from_head(self, head: dict, key: str) -> VFSStat:
        size = head.get("ContentLength", 0)
        mtime = head.get("LastModified", datetime.now()).timestamp()
        is_dir = key.endswith("/")
        return VFSStat(
            size=size,
            mtime=mtime,
            mode=stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644,
            is_dir=is_dir,
            is_file=not is_dir,
        )

    def exists(self, path: str) -> bool:
        key = self._to_key(path)
        if key.endswith("/"):
            return self._dir_exists(key)
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return self._dir_exists(key + "/")
            raise

    def _dir_exists(self, prefix: str) -> bool:
        resp = self._s3.list_objects_v2(Bucket=self._bucket, Prefix=prefix, MaxKeys=1)
        return "Contents" in resp or "CommonPrefixes" in resp

    def is_file(self, path: str) -> bool:
        key = self._to_key(path)
        if key.endswith("/"):
            return False
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def is_dir(self, path: str) -> bool:
        return self._dir_exists(self._to_key(path) + "/")

    def stat(self, path: str) -> VFSStat:
        key = self._to_key(path)
        if key.endswith("/"):
            return VFSStat(0, datetime.now().timestamp(), stat.S_IFDIR | 0o755, True, False)
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
            return self._stat_from_head(head, key)
        except self._s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                if self._dir_exists(key + "/"):
                    return VFSStat(0, datetime.now().timestamp(), stat.S_IFDIR | 0o755, True, False)
            raise

    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        data = self.read_bytes(path)
        return data.decode(encoding, errors=errors)

    def read_bytes(self, path: str) -> bytes:
        key = self._to_key(path)
        resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def write_bytes(self, path: str, content: bytes) -> None:
        key = self._to_key(path)
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=content)

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        key = self._to_key(path)
        if not key.endswith("/"):
            key += "/"
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=b"")

    def unlink(self, path: str, missing_ok: bool = True) -> None:
        key = self._to_key(path)
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=key)
        except self._s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "404" or not missing_ok:
                raise

    def rename(self, src: str, dst: str) -> None:
        src_key = self._to_key(src)
        dst_key = self._to_key(dst)

        if src_key.endswith("/") and dst_key.endswith("/"):
            self._copy_dir(src_key, dst_key)
            self._delete_dir(src_key)
        else:
            self._s3.copy_object(Bucket=self._bucket, CopySource={"Bucket": self._bucket, "Key": src_key}, Key=dst_key)
            self._s3.delete_object(Bucket=self._bucket, Key=src_key)

    def _copy_dir(self, src_prefix: str, dst_prefix: str) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=src_prefix):
            for obj in page.get("Contents", []):
                src_key = obj["Key"]
                rel = src_key[len(src_prefix):]
                dst_key = posixpath.join(dst_prefix, rel)
                self._s3.copy_object(Bucket=self._bucket, CopySource={"Bucket": self._bucket, "Key": src_key}, Key=dst_key)

    def _delete_dir(self, prefix: str) -> None:
        to_delete = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                to_delete.append({"Key": obj["Key"]})
        if to_delete:
            for i in range(0, len(to_delete), 1000):
                self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": to_delete[i:i+1000]})

    def listdir(self, path: str) -> list[str]:
        prefix = self._to_key(path)
        if not prefix.endswith("/"):
            prefix += "/"

        entries = set()
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix, Delimiter="/"):
            for prefix_obj in page.get("CommonPrefixes", []):
                name = prefix_obj["Prefix"][len(prefix):].rstrip("/")
                if name:
                    entries.add(name)
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix):]
                if name and "/" not in name:
                    entries.add(name)
        return sorted(entries)

    def walk(self, path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        prefix = self._to_key(path)
        if not prefix.endswith("/"):
            prefix += "/"

        dirs = set()
        files = {}

        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix):]
                if "/" in rel:
                    dir_name = rel.split("/")[0]
                    dirs.add(dir_name)
                else:
                    files[rel] = obj

        yield self._from_key(prefix), sorted(dirs), sorted(files.keys())

        for d in sorted(dirs):
            yield from self.walk(self.join(path, d))

    def glob(self, path: str, pattern: str) -> list[str]:
        matches = []
        for dirpath, dirnames, filenames in self.walk(path):
            for name in dirnames + filenames:
                if fnmatch.fnmatch(name, pattern):
                    matches.append(self.join(dirpath, name))
        return matches

    @contextlib.contextmanager
    def open(self, path: str, mode: str = "r", encoding: Optional[str] = None) -> Union[TextIO, BinaryIO]:
        import io
        key = self._to_key(path)

        if "r" in mode:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            body = resp["Body"]
            if "b" in mode:
                yield body
            else:
                wrapper = io.TextIOWrapper(body, encoding=encoding or "utf-8")
                yield wrapper
        else:
            buffer = io.BytesIO()
            if "b" in mode:
                yield buffer
            else:
                wrapper = io.TextIOWrapper(buffer, encoding=encoding or "utf-8", write_through=True)
                yield wrapper
            buffer.seek(0)
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=buffer.getvalue())

    def resolve(self, path: str) -> str:
        if not path.startswith("/"):
            path = posixpath.join(self._cwd, path)
        parts = []
        for part in path.split("/"):
            if part in ("", "."):
                continue
            elif part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        return "/" + "/".join(parts)

    def expanduser(self, path: str) -> str:
        return path

    def cwd(self) -> str:
        return self._cwd

    def home(self) -> str:
        return self._root

    def chdir(self, path: str) -> None:
        self._cwd = self.resolve(path)


def register_s3_vfs() -> None:
    if boto3 is None:
        return
    register_vfs("s3", S3VFS)