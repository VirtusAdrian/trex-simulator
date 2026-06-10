import paramiko
import shlex
import time
import sys
from typing import Callable, Optional


class SSHClient:
    def __init__(self, host: str, user: str, password: str = None, key_file: str = None, port: int = 22):
        self.host = host
        self.user = user
        self.password = password
        self.key_file = key_file
        self.port = port
        # Privilege escalation: when the login user is not root, privileged
        # commands are wrapped with sudo. sudo_password is fed via stdin (-S).
        self.sudo = False
        self.sudo_password: Optional[str] = None
        self._client: Optional[paramiko.SSHClient] = None

    def _wrap(self, command: str) -> str:
        """Wrap a command with sudo when running as a non-root user."""
        if not self.sudo:
            return command
        if self.sudo_password:
            return f"sudo -S -p '' bash -c {shlex.quote(command)}"
        return f"sudo -n bash -c {shlex.quote(command)}"

    def connect(self):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(hostname=self.host, port=self.port, username=self.user, timeout=30)
        if self.key_file:
            kwargs["key_filename"] = self.key_file
        else:
            kwargs["password"] = self.password
        client.connect(**kwargs)
        self._client = client

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def exec(self, command: str, timeout: int = 60, raw: bool = False) -> tuple[int, str, str]:
        """Run a command, return (exit_code, stdout, stderr).

        Wrapped with sudo when self.sudo is set, unless raw=True (used for
        privilege detection itself, which must run as the login user).
        """
        cmd = command if raw else self._wrap(command)
        stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
        if not raw and self.sudo and self.sudo_password:
            try:
                stdin.write(self.sudo_password + "\n")
                stdin.flush()
            except Exception:
                pass
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode(), stderr.read().decode()

    def exec_stream(self, command: str, on_line: Callable[[str], None], timeout: int = 3600):
        """Run a long-running command and stream stdout line by line."""
        transport = self._client.get_transport()
        channel = transport.open_session()
        channel.settimeout(timeout)
        channel.exec_command(self._wrap(command))
        if self.sudo and self.sudo_password:
            try:
                channel.sendall((self.sudo_password + "\n").encode())
            except Exception:
                pass

        buf = ""
        while True:
            if channel.recv_ready():
                chunk = channel.recv(4096).decode(errors="replace")
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    on_line(line)
            if channel.exit_status_ready():
                # drain remaining output
                while channel.recv_ready():
                    chunk = channel.recv(4096).decode(errors="replace")
                    buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    on_line(line)
                if buf:
                    on_line(buf)
                break
            time.sleep(0.1)
        return channel.recv_exit_status()

    def put_file(self, local_path: str, remote_path: str):
        sftp = self._client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def put_content(self, content: str, remote_path: str):
        sftp = self._client.open_sftp()
        with sftp.file(remote_path, "w") as f:
            f.write(content)
        sftp.close()

    def put_privileged(self, content: str, remote_path: str):
        """Write to a root-owned path. SFTP runs as the login user, so when in
        sudo mode we stage in /tmp and move into place with sudo."""
        if not self.sudo:
            self.put_content(content, remote_path)
            return
        tmp = "/tmp/.trexsim_upload.tmp"
        self.put_content(content, tmp)
        code, _, err = self.exec(f"mv {shlex.quote(tmp)} {shlex.quote(remote_path)} && chmod 644 {shlex.quote(remote_path)}")
        if code != 0:
            raise RuntimeError(f"写入 {remote_path} 失败: {err.strip()}")

    def enable_sudo_if_needed(self, sudo_password: str = None) -> dict:
        """Detect whether the login user is root; if not, enable sudo wrapping.
        Returns {is_root, sudo_enabled, sudo_ok, detail}."""
        code, out, _ = self.exec("id -u", raw=True)
        is_root = code == 0 and out.strip() == "0"
        if is_root:
            return {"is_root": True, "sudo_enabled": False, "sudo_ok": True, "detail": "root 账号，无需 sudo"}

        self.sudo = True
        self.sudo_password = sudo_password
        # Verify sudo actually works (wrapped exec now applies)
        code, out, err = self.exec("id -u")
        sudo_ok = code == 0 and out.strip() == "0"
        if sudo_ok:
            mode = "免密 sudo" if not sudo_password else "sudo + 密码"
            detail = f"非 root 账号，特权命令将通过 {mode} 执行"
        else:
            detail = (err.strip() or "sudo 验证失败")
        return {"is_root": False, "sudo_enabled": True, "sudo_ok": sudo_ok, "detail": detail}

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()
