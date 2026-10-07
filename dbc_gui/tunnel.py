"""SSH トンネル。

エージェントは サーバの `127.0.0.1:8765` にしか待ち受けないので、
PC 側の `127.0.0.1:18765` へ転送して使う(仕様書「接続方式」)。

独自の SSH 実装は持たない。Windows 標準の OpenSSH クライアントを
子プロセスとして使うだけ。
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Windows 標準の OpenSSH。WSL の中の ssh ではない。
# PATH の先頭が WSL 側になっている機械があるので、先にここを見る。
WINDOWS_SSH = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")

# 常に付ける指定(仕様書「接続方式」GUI 側の要件)
SSH_OPTIONS = (
    # 落ちたトンネルを掴んだままにしない
    "ServerAliveInterval=15",
    "ServerAliveCountMax=3",
    # 転送に失敗したら、繋がったふりをせずに終了する
    "ExitOnForwardFailure=yes",
)

DEFAULT_LOCAL_PORT = 18765
DEFAULT_REMOTE_PORT = 8765


class TunnelError(Exception):
    pass


def find_ssh() -> str:
    """使う ssh の実体を決める。"""
    if os.name == "nt" and WINDOWS_SSH.exists():
        return str(WINDOWS_SSH)
    found = shutil.which("ssh")
    if not found:
        raise TunnelError(
            "ssh が見つかりません。Windows なら「設定 > アプリ > オプション機能」で "
            "OpenSSH クライアントを入れてください"
        )
    return found


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


class Tunnel:
    """`ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote> <host>` を張る。

    host は `~/.ssh/config` の Host 名でよい(例: arch-tunnel)。そのほうが
    アドレスと鍵の指定をこちら側に持たずに済む。
    """

    def __init__(
        self,
        host: str,
        local_port: int = DEFAULT_LOCAL_PORT,
        remote_port: int = DEFAULT_REMOTE_PORT,
        ssh: str | None = None,
        user: str | None = None,
        ssh_port: int | None = None,
        identity_file: str | Path | None = None,
        ssh_config: str | Path | None = None,
    ):
        self.host = host
        self.local_port = local_port
        self.remote_port = remote_port
        self.ssh = ssh or find_ssh()
        # 接続先は ~/.ssh/config の Host 名だけで済ませるのが本筋だが、
        # 設定画面から直接指定できる必要もある(仕様書「接続方式」GUI 側の要件)。
        self.user = user
        self.ssh_port = ssh_port
        self.identity_file = Path(identity_file) if identity_file else None
        self.ssh_config = Path(ssh_config) if ssh_config else None
        self._proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def command(self) -> list[str]:
        args = [self.ssh, "-N"]
        if self.ssh_config:
            # 指定したときだけ。既定では ~/.ssh/config がそのまま効く
            args += ["-F", str(self.ssh_config)]
        for opt in SSH_OPTIONS:
            args += ["-o", opt]
        if self.identity_file:
            args += ["-i", str(self.identity_file)]
            # 鍵を指定したら、それだけを使う。agent に入っている別の鍵で
            # 試されて「鍵が多すぎる」と断られるのを避ける
            args += ["-o", "IdentitiesOnly=yes"]
        if self.ssh_port:
            args += ["-p", str(self.ssh_port)]
        # 左側のアドレスを 127.0.0.1 と明示する。ポート番号だけ書くと
        # 環境によって全インターフェースに出て、同じ LAN から見えてしまう。
        args += ["-L", f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}"]
        args.append(f"{self.user}@{self.host}" if self.user else self.host)
        return args

    def start(self) -> None:
        if self._proc is not None:
            raise TunnelError("すでに張っています")
        if port_is_open(self.local_port):
            raise TunnelError(
                f"127.0.0.1:{self.local_port} は既に使われています。"
                "別のトンネルが残っていないか確認してください"
            )
        # ホスト鍵の確認を利用者に出せるよう、標準入出力はそのまま渡す。
        # StrictHostKeyChecking=no は使わない(中間者を黙って受け入れるため)。
        self._proc = subprocess.Popen(self.command())

    def wait_ready(self, timeout: float = 20.0) -> None:
        """転送先のポートが開くまで待つ。開かなければ TunnelError。"""
        if self._proc is None:
            raise TunnelError("start() を呼んでいません")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_is_open(self.local_port):
                return
            code = self._proc.poll()
            if code is not None:
                raise TunnelError(
                    f"ssh が終了しました (終了コード {code})。"
                    "鍵・Host 名・サーバ側の AllowTcpForwarding を確認してください"
                )
            time.sleep(0.3)
        raise TunnelError(f"{timeout:.0f} 秒待っても 127.0.0.1:{self.local_port} が開きません")

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def __enter__(self) -> "Tunnel":
        self.start()
        self.wait_ready()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def demo() -> None:
    """ssh を起動せずに、組み立てと判定だけを見る。"""
    t = Tunnel("arch-tunnel", ssh="ssh")
    cmd = t.command()
    assert cmd[0] == "ssh" and cmd[-1] == "arch-tunnel", cmd
    # 左側を 127.0.0.1 と明示していること
    assert "127.0.0.1:18765:127.0.0.1:8765" in cmd, cmd
    # 常に付ける指定が入っていること
    for opt in SSH_OPTIONS:
        assert opt in cmd, opt
    # 中間者を黙って受け入れる指定を入れていないこと
    assert not any("StrictHostKeyChecking=no" in a for a in cmd), cmd
    assert t.url == "http://127.0.0.1:18765"
    assert not t.is_alive()

    # 設定画面から直接指定する形(ホスト・利用者・ポート・鍵)
    t2 = Tunnel("10.0.0.2", ssh="ssh", user="ops", ssh_port=2222,
                identity_file="/k/id_ed25519", ssh_config="/k/cfg")
    c2 = t2.command()
    assert c2[-1] == "ops@10.0.0.2", c2
    assert c2[1:3] == ["-N", "-F"], c2        # -F は他の指定より先
    assert "-p" in c2 and c2[c2.index("-p") + 1] == "2222", c2
    # パスは Path を通すので、区切りは OS のものになる
    assert "-i" in c2 and c2[c2.index("-i") + 1] == str(Path("/k/id_ed25519")), c2
    assert c2[3] == str(Path("/k/cfg")), c2
    # 鍵を指定したら、それだけを使う
    assert "IdentitiesOnly=yes" in c2, c2
    # 利用者を指定しなければホストだけ(~/.ssh/config の Host 名が使える)
    assert Tunnel("arch-tunnel", ssh="ssh").command()[-1] == "arch-tunnel"

    # 実際に待ち受けを作って、空き/使用中の判定が合うか見る
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        # 確認のための接続を accept しないまま重ねるので、待ち行列を広く取る。
        # listen(1) にすると 2 回目の接続が蹴られて「空いている」と誤判定する。
        s.listen(8)
        used = s.getsockname()[1]
        assert port_is_open(used)
        busy = Tunnel("x", local_port=used, ssh="ssh")
        try:
            busy.start()
        except TunnelError as e:
            assert "既に使われています" in str(e), e
        else:
            raise AssertionError("使用中のポートで start できてしまった")
    assert not port_is_open(used)

    # start() を呼ばずに wait_ready すると怒る
    try:
        Tunnel("x", ssh="ssh").wait_ready()
    except TunnelError:
        pass
    else:
        raise AssertionError("start なしで wait_ready が通った")

    # この機械で使う ssh がどれになるか表示しておく
    try:
        print(f"  使う ssh: {find_ssh()}")
    except TunnelError as e:
        print(f"  ssh が見つかりません: {e}", file=sys.stderr)

    print("tunnel OK")


if __name__ == "__main__":
    demo()
