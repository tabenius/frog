"""SSH VFS implementation for Frog."""

from __future__ import annotations

import contextlib
import fnmatch
import os
import posixpath
import stat
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional, TextIO, Union

from .vfs import VFS, VFSStat, register_vfs

try:
    import paramiko
    from paramiko.sftp_client import SFTPClient
    from paramiko.sftp_attr import SFTPAttributes
except ImportError:
    paramiko = None
    SFTPClient = None
    SFTPAttributes = None


@dataclass
class SSHConnection:
    host: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    timeout: int = 30

    def connect(self) -> "paramiko.SSHClient":
        if paramiko is None:
            raise RuntimeError("paramiko not installed: pip install paramiko")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            key_filename=self.key_filename,
            timeout=self.timeout,
        )
        return client


class SSHVFS(VFS):
    """SSH/SFTP virtual filesystem."""

    scheme = "ssh"

    def __init__(
        self,
        uri: str,
        host: str,
        username: str,
        root: str = "/",
        port: int = 22,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: int = 30,
    ):
        self._conn = SSHConnection(host, username, port, password, key_filename, timeout)
        self._root = root.rstrip("/") or "/"
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[SFTPClient] = None
        self._cwd = self._root

    def _ensure_connected(self) -> SFTPClient:
        if self._sftp is None:
            self._client = self._conn.connect()
            self._sftp = self._client.open_sftp()
        return self._sftp

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> SSHVFS:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

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

    def _stat_from_attr(self, attr: SFTPAttributes, path: str) -> VFSStat:
        return VFSStat(
            size=attr.st_size,
            mtime=attr.st_mtime,
            mode=attr.st_mode,
            is_dir=stat.S_ISDIR(attr.st_mode),
            is_file=stat.S_ISREG(attr.st_mode),
        )

    def exists(self, path: str) -> bool:
        try:
            self._ensure_connected().stat(self._to_remote(path))
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def is_file(self, path: str) -> bool:
        try:
            attr = self._ensure_connected().stat(self._to_remote(path))
            return stat.S_ISREG(attr.st_mode)
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        try:
            attr = self._ensure_connected().stat(self._to_remote(path))
            return stat.S_ISDIR(attr.st_mode)
        except OSError:
            return False

    def stat(self, path: str) -> VFSStat:
        attr = self._ensure_connected().stat(self._to_remote(path))
        return self._stat_from_attr(attr, path)

    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        data = self.read_bytes(path)
        return data.decode(encoding, errors=errors)

    def read_bytes(self, path: str) -> bytes:
        sftp = self._ensure_connected()
        remote = self._to_remote(path)
        with sftp.open(remote, "rb") as f:
            return f.read()

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def write_bytes(self, path: str, content: bytes) -> None:
        sftp = self._ensure_connected()
        remote = self._to_remote(path)
        self._mkdir_parents(sftp, posixpath.dirname(remote))
        with sftp.open(remote, "wb") as f:
            f.write(content)

    def _mkdir_parents(self, sftp: SFTPClient, remote_dir: str) -> None:
        if remote_dir in ("", "/", self._root):
            return
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            parent = posixpath.dirname(remote_dir)
            if parent != remote_dir:
                self._mkdir_parents(sftp, parent)
            sftp.mkdir(remote_dir)

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        sftp = self._ensure_connected()
        remote = self._to_remote(path)
        if parents:
            self._mkdir_parents(sftp, remote)
        try:
            sftp.mkdir(remote)
        except FileExistsError:
            if not exist_ok:
                raise

    def unlink(self, path: str, missing_ok: bool = True) -> None:
        sftp = self._ensure_connected()
        remote = self._to_remote(path)
        try:
            sftp.remove(remote)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def rename(self, src: str, dst: str) -> None:
        sftp = self._ensure_connected()
        sftp.rename(self._to_remote(src), self._to_remote(dst))

    def listdir(self, path: str) -> list[str]:
        sftp = self._ensure_connected()
        return [attr.filename for attr in sftp.listdir_attr(self._to_remote(path))]

    def walk(self, path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        sftp = self._ensure_connected()
        remote_root = self._to_remote(path)

        def _walk(remote_dir: str) -> Iterator[tuple[str, list[str], list[str]]]:
            dirs = []
            files = []
            for attr in sftp.listdir_attr(remote_dir):
                if stat.S_ISDIR(attr.st_mode):
                    dirs.append(attr.filename)
                else:
                    files.append(attr.filename)
            yield self._from_remote(remote_dir), dirs, files
            for d in dirs:
                yield from _walk(posixpath.join(remote_dir, d))

        yield from _walk(remote_root)

    # --- Optimized remote discovery methods ---
    
    def _exec_command(self, command: str) -> tuple[int, str, str]:
        """Execute a command on the remote host via SSH."""
        if self._client is None:
            self._client = self._conn.connect()
        stdin, stdout, stderr = self._client.exec_command(command, timeout=self._conn.timeout)
        exit_status = stdout.channel.recv_exit_status()
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")
        return exit_status, stdout_text, stderr_text

    def find_manifests(self, manifests: list[str], root: str = "/") -> list[str]:
        """Find all manifest files using remote find command.
        
        Returns list of VFS paths to manifest files.
        """
        root_remote = self._to_remote(root)
        # Build find command with -name predicates for each manifest
        name_predicates = " -o ".join(f"-name {m}" for m in manifests)
        # Use -printf to get relative paths from root
        cmd = f"cd {posixpath.quote(root_remote)} && find . -type f \\( {name_predicates} \\) -printf '%P\\n'"
        exit_status, stdout, stderr = self._exec_command(cmd)
        if exit_status != 0:
            raise RuntimeError(f"Remote find failed: {stderr}")
        matches = [self.join(root, line.strip()) for line in stdout.strip().split("\n") if line.strip()]
        return matches

    def find_git_repos(self, root: str = "/") -> list[str]:
        """Find all git repositories using remote find for .git/HEAD.
        
        Returns list of VFS paths to repository roots.
        """
        root_remote = self._to_remote(root)
        # Find .git/HEAD files (both regular and bare repos), then get their repo root
        # Also find .git files (worktree style)
        # Use double braces to escape in f-string
        cmd = (
            f"cd {posixpath.quote(root_remote)} && "
            f"(find . -type f -name 'HEAD' -path '*/.git/HEAD' -printf '%h\\n' | sed 's|/.git$||'; "
            f"find . -type f -name 'HEAD' -path '*/.git' -printf '%h\\n' | sed 's|/.git$||'; "
            f"find . -type f -name '.git' -exec sh -c 'read line < {{}} && echo $'{{line#gitdir: }}' \\; 2>/dev/null | sed 's|/$||') | sort -u"
        )
        exit_status, stdout, stderr = self._exec_command(cmd)
        if exit_status != 0:
            raise RuntimeError(f"Remote git find failed: {stderr}")
        matches = [self.join("/", line.strip()) for line in stdout.strip().split("\n") if line.strip()]
        return matches

    def git_ls_files(self, repo_root: str) -> list[str]:
        """List tracked files in a git repository using git ls-files.
        
        Returns list of VFS paths relative to repo root.
        """
        repo_remote = self._to_remote(repo_root)
        cmd = f"cd {posixpath.quote(repo_remote)} && git ls-files"
        exit_status, stdout, stderr = self._exec_command(cmd)
        if exit_status != 0:
            # Not a git repo or git not available
            return []
        files = [line.strip() for line in stdout.strip().split("\n") if line.strip()]
        return [self.join(repo_root, f) for f in files]

    def find_manifests_in_repos(self, manifests: list[str], root: str = "/") -> dict[str, list[str]]:
        """Find manifest files grouped by their git repository root.
        
        Returns dict mapping repo_root -> list of manifest paths within that repo.
        """
        # First find all git repos
        repo_roots = self.find_git_repos(root)
        result = {}
        for repo_root in repo_roots:
            # For each repo, find manifests within it using git ls-files
            tracked_files = self.git_ls_files(repo_root)
            manifests_in_repo = [
                f for f in tracked_files 
                if any(f.endswith(m) for m in manifests)
            ]
            if manifests_in_repo:
                result[repo_root] = manifests_in_repo
        return result

    def glob(self, path: str, pattern: str) -> list[str]:
        matches = []
        for dirpath, dirnames, filenames in self.walk(path):
            for name in dirnames + filenames:
                if self._match(pattern, name):
                    matches.append(self.join(dirpath, name))
        return matches

    def _match(self, pattern: str, name: str) -> bool:
        return fnmatch.fnmatch(name, pattern)

    @contextlib.contextmanager
    def open(self, path: str, mode: str = "r", encoding: Optional[str] = None) -> Union[TextIO, BinaryIO]:
        import io
        sftp = self._ensure_connected()
        remote = self._to_remote(path)
        if "b" in mode:
            f = sftp.open(remote, mode)
            yield f
        else:
            f = sftp.open(remote, mode.replace("t", "").replace("b", ""))
            wrapper = io.TextIOWrapper(f, encoding=encoding or "utf-8")
            yield wrapper

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
        if path.startswith("~"):
            path = path.replace("~", self.home(), 1)
        return path

    def cwd(self) -> str:
        return self._cwd

    def home(self) -> str:
        return self._root

    def chdir(self, path: str) -> None:
        self._cwd = self.resolve(path)


def register_ssh_vfs() -> None:
    if paramiko is None:
        return
    register_vfs("ssh", SSHVFS)