"""HTTP/HTTPS and WebDAV VFS implementations for Frog."""

from __future__ import annotations

import contextlib
import fnmatch
import posixpath
import stat
import urllib.parse
import urllib.request
from datetime import datetime
from typing import BinaryIO, Iterator, Optional, TextIO, Union

from .vfs import VFS, VFSStat, register_vfs


class HTTPVFS(VFS):
    """Read-only HTTP/HTTPS virtual filesystem."""

    scheme = "https"

    def __init__(
        self,
        uri: str,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        headers: Optional[dict] = None,
        timeout: int = 30,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = None
        if username and password:
            import base64
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._auth = f"Basic {credentials}"
        self._headers = headers or {}
        self._timeout = timeout
        self._cwd = "/"

    def _make_request(self, method: str, path: str, data: Optional[bytes] = None, headers: Optional[dict] = None) -> urllib.request.urlopen:
        url = self._base_url + path
        req_headers = dict(self._headers)
        if headers:
            req_headers.update(headers)
        if self._auth:
            req_headers["Authorization"] = self._auth
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        return urllib.request.urlopen(req, timeout=self._timeout)

    def _to_path(self, path: str) -> str:
        path = self.resolve(path)
        return path.lstrip("/")

    def exists(self, path: str) -> bool:
        try:
            with self._make_request("HEAD", self._to_path(path)) as resp:
                return 200 <= resp.status < 400
        except urllib.error.HTTPError as e:
            return e.code != 404
        except Exception:
            return False

    def is_file(self, path: str) -> bool:
        try:
            with self._make_request("HEAD", self._to_path(path)) as resp:
                return 200 <= resp.status < 400
        except Exception:
            return False

    def is_dir(self, path: str) -> bool:
        return False

    def stat(self, path: str) -> VFSStat:
        try:
            with self._make_request("HEAD", self._to_path(path)) as resp:
                size = int(resp.headers.get("Content-Length", 0))
                last_modified = resp.headers.get("Last-Modified")
                mtime = datetime.now().timestamp()
                if last_modified:
                    from email.utils import parsedate_to_datetime
                    mtime = parsedate_to_datetime(last_modified).timestamp()
                return VFSStat(size, mtime, stat.S_IFREG | 0o644, False, True)
        except Exception:
            return VFSStat(0, datetime.now().timestamp(), stat.S_IFREG | 0o644, False, True)

    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        data = self.read_bytes(path)
        return data.decode(encoding, errors=errors)

    def read_bytes(self, path: str) -> bytes:
        with self._make_request("GET", self._to_path(path)) as resp:
            return resp.read()

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        raise NotImplementedError("HTTPVFS is read-only")

    def write_bytes(self, path: str, content: bytes) -> None:
        raise NotImplementedError("HTTPVFS is read-only")

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        raise NotImplementedError("HTTPVFS is read-only")

    def unlink(self, path: str, missing_ok: bool = True) -> None:
        raise NotImplementedError("HTTPVFS is read-only")

    def rename(self, src: str, dst: str) -> None:
        raise NotImplementedError("HTTPVFS is read-only")

    def listdir(self, path: str) -> list[str]:
        return []

    def walk(self, path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        yield path, [], []

    def glob(self, path: str, pattern: str) -> list[str]:
        return []

    @contextlib.contextmanager
    def open(self, path: str, mode: str = "r", encoding: Optional[str] = None) -> Union[TextIO, BinaryIO]:
        import io
        if "w" in mode or "a" in mode or "+" in mode:
            raise NotImplementedError("HTTPVFS is read-only")
        data = self.read_bytes(path)
        if "b" in mode:
            yield io.BytesIO(data)
        else:
            yield io.TextIOWrapper(io.BytesIO(data), encoding=encoding or "utf-8")

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
        return "/"

    def chdir(self, path: str) -> None:
        self._cwd = self.resolve(path)


class WebDAVVFS(VFS):
    """WebDAV virtual filesystem."""

    scheme = "webdav"

    def __init__(
        self,
        uri: str,
        host: str,
        username: str,
        password: str,
        root: str = "/",
        timeout: int = 30,
    ):
        try:
            import webdav3.client as webdav_client
        except ImportError:
            raise RuntimeError("webdavclient3 not installed: pip install webdavclient3")

        self._host = host.rstrip("/")
        self._root = root.rstrip("/") or "/"
        self._client = webdav_client.Client({
            "webdav_hostname": self._host,
            "webdav_login": username,
            "webdav_password": password,
            "webdav_timeout": timeout,
            "webdav_root": self._root,
        })
        self._cwd = self._root

    def _to_remote(self, path: str) -> str:
        path = self.resolve(path)
        if path == "/":
            return self._root
        return posixpath.join(self._root, path.lstrip("/"))

    def _from_remote(self, path: str) -> str:
        if not path.startswith(self._root):
            return "/" + path.lstrip("/")
        rel = path[len(self._root):].lstrip("/")
        return "/" + rel if rel else "/"

    def exists(self, path: str) -> bool:
        try:
            self._client.check(self._to_remote(path))
            return True
        except Exception:
            return False

    def is_file(self, path: str) -> bool:
        try:
            info = self._client.info(self._to_remote(path))
            return not info.get("isdir", False)
        except Exception:
            return False

    def is_dir(self, path: str) -> bool:
        try:
            info = self._client.info(self._to_remote(path))
            return info.get("isdir", False)
        except Exception:
            return False

    def stat(self, path: str) -> VFSStat:
        try:
            info = self._client.info(self._to_remote(path))
            size = info.get("size", 0)
            mtime = info.get("modified", datetime.now().timestamp())
            is_dir = info.get("isdir", False)
            return VFSStat(
                size=size,
                mtime=mtime,
                mode=stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644,
                is_dir=is_dir,
                is_file=not is_dir,
            )
        except Exception:
            return VFSStat(0, datetime.now().timestamp(), stat.S_IFREG | 0o644, False, True)

    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        data = self.read_bytes(path)
        return data.decode(encoding, errors=errors)

    def read_bytes(self, path: str) -> bytes:
        import io
        remote = self._to_remote(path)
        buffer = io.BytesIO()
        self._client.download_from(remote, buffer)
        return buffer.getvalue()

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def write_bytes(self, path: str, content: bytes) -> None:
        import io
        remote = self._to_remote(path)
        parent = posixpath.dirname(remote)
        if parent and parent != self._root:
            self._mkdir_parents(parent)
        buffer = io.BytesIO(content)
        self._client.upload_to(remote, buffer)

    def _mkdir_parents(self, remote_dir: str) -> None:
        if remote_dir in ("", "/", self._root):
            return
        try:
            self._client.check(remote_dir)
        except Exception:
            parent = posixpath.dirname(remote_dir)
            if parent != remote_dir:
                self._mkdir_parents(parent)
            self._client.mkdir(remote_dir)

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        remote = self._to_remote(path)
        if parents:
            self._mkdir_parents(remote)
        try:
            self._client.mkdir(remote)
        except Exception as e:
            if not exist_ok or "already exists" not in str(e).lower():
                raise

    def unlink(self, path: str, missing_ok: bool = True) -> None:
        remote = self._to_remote(path)
        try:
            self._client.clean(remote)
        except Exception as e:
            if not missing_ok or "not found" not in str(e).lower():
                raise

    def rename(self, src: str, dst: str) -> None:
        self._client.move(self._to_remote(src), self._to_remote(dst))

    def listdir(self, path: str) -> list[str]:
        remote = self._to_remote(path)
        try:
            return self._client.list(remote)
        except Exception:
            return []

    def walk(self, path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        remote = self._to_remote(path)
        try:
            items = self._client.list(remote, get_info=True)
        except Exception:
            yield self._from_remote(remote), [], []
            return

        dirs = []
        files = []
        for item in items:
            if item.get("isdir"):
                dirs.append(item["name"])
            else:
                files.append(item["name"])

        yield self._from_remote(remote), dirs, files

        for d in dirs:
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
        if "b" in mode:
            buffer = io.BytesIO()
            if "r" in mode:
                self._client.download_from(self._to_remote(path), buffer)
                buffer.seek(0)
            yield buffer
            if "w" in mode or "a" in mode:
                buffer.seek(0)
                self._client.upload_to(self._to_remote(path), buffer)
        else:
            buffer = io.BytesIO()
            if "r" in mode:
                self._client.download_from(self._to_remote(path), buffer)
                buffer.seek(0)
            wrapper = io.TextIOWrapper(buffer, encoding=encoding or "utf-8", write_through=True)
            yield wrapper
            if "w" in mode or "a" in mode:
                wrapper.flush()
                buffer.seek(0)
                self._client.upload_to(self._to_remote(path), buffer)

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


def register_http_vfs() -> None:
    register_vfs("http", HTTPVFS)
    register_vfs("https", HTTPVFS)


def register_webdav_vfs() -> None:
    register_vfs("webdav", WebDAVVFS)