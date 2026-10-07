"""つなぎ役。

トンネルを張って /history で過去ぶんを取り、続けて /stream に繋ぐ。
切れたら間隔を広げながら張り直す(仕様書「接続方式」)。

通信はすべてこのスレッドの中で行い、画面へは Signal でしか渡さない。
Qt の仮想メソッドの中で例外を出すとプロセスごと落ちるので、
`run()` の中では例外を外に漏らさない。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .client import Client, ClientError, IncompatibleApi, load_token
from .tunnel import DEFAULT_LOCAL_PORT, Tunnel, TunnelError

# 再接続の間隔。最大 60 秒まで広げる
BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 60.0
# 異常とエージェントの状態を取り直す間隔。/stream は現在値しか流さない
SIDE_FETCH_S = 30.0

# 画面のランプ。色だけに頼らず文字も出す
ONLINE, CONNECTING, RETRYING, OFFLINE = "online", "connecting", "retrying", "offline"


@dataclass
class Target:
    """どこへどう繋ぐか。"""

    host: str | None = None
    url: str | None = None
    local_port: int = DEFAULT_LOCAL_PORT
    user: str | None = None
    ssh_port: int | None = None
    identity_file: Path | None = None
    ssh_config: Path | None = None
    token_file: Path | None = None
    extra: dict = field(default_factory=dict)

    def label(self) -> str:
        if self.host:
            return f"{self.user}@{self.host}" if self.user else self.host
        return self.url or "(未設定)"


class Connection(QThread):
    """繋ぎっぱなしにして、来た値を流すだけのスレッド。"""

    state = Signal(str, str)        # ランプの状態, 添える説明
    version = Signal(dict)
    history = Signal(dict)
    snapshot = Signal(dict)
    alerts = Signal(dict)
    health = Signal(dict)

    def __init__(self, target: Target):
        super().__init__()
        self.target = target
        self._stop = False
        self._client: Client | None = None
        # 復帰したとき、ここから後ろを取り直して欠損を埋める
        self._last_ts: int | None = None

    def stop(self) -> None:
        self._stop = True
        if self._client is not None:
            # 次の値を待っているところを叩き起こす
            self._client.abort()

    def _sleep(self, seconds: float) -> None:
        """止めろと言われたらすぐ抜ける待ち。"""
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            time.sleep(0.1)

    def run(self) -> None:  # noqa: C901 - 繋ぐ・読む・張り直すの一本道
        backoff = BACKOFF_START_S
        while not self._stop:
            tunnel = None
            try:
                token = load_token(self.target.token_file)

                if self.target.host:
                    self.state.emit(CONNECTING, "トンネルを張っています")
                    tunnel = Tunnel(
                        self.target.host, local_port=self.target.local_port,
                        user=self.target.user, ssh_port=self.target.ssh_port,
                        identity_file=self.target.identity_file,
                        ssh_config=self.target.ssh_config,
                    )
                    tunnel.start()
                    tunnel.wait_ready()
                    url = tunnel.url
                else:
                    url = (self.target.url or "").rstrip("/")
                    if not url:
                        raise ClientError("接続先が設定されていません")

                self.state.emit(CONNECTING, "版を確かめています")
                self._client = Client(url, token)
                self.version.emit(self._client.version())

                # 先に過去ぶんを取る。空のグラフが埋まっていくのを見せない
                self.history.emit(self._client.history(since=self._last_ts))
                self.alerts.emit(self._client.alerts())
                self.health.emit(self._client.health())

                self.state.emit(ONLINE, "")
                backoff = BACKOFF_START_S
                next_side = time.monotonic() + SIDE_FETCH_S

                for snap in self._client.stream():
                    if self._stop:
                        break
                    self._last_ts = snap.get("ts") or self._last_ts
                    self.snapshot.emit(snap)
                    # /stream は現在値しか流さないので、異常と健康状態は
                    # ときどき取り直す
                    if time.monotonic() >= next_side:
                        next_side = time.monotonic() + SIDE_FETCH_S
                        self.alerts.emit(self._client.alerts())
                        self.health.emit(self._client.health())

                if not self._stop:
                    raise ClientError("ストリームが切れました")

            except IncompatibleApi as e:
                # 版が合わないのは待っても直らない。繰り返さずに止める
                self.state.emit(OFFLINE, str(e))
                return
            except (ClientError, TunnelError) as e:
                if self._stop:
                    break
                self.state.emit(RETRYING, f"{e}（{backoff:.0f} 秒後に試します）")
                self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
            except Exception as e:  # noqa: BLE001 - スレッドごと落とさない
                if self._stop:
                    break
                self.state.emit(RETRYING, f"{type(e).__name__}: {e}")
                self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
            finally:
                self._client = None
                if tunnel is not None:
                    tunnel.stop()

        self.state.emit(OFFLINE, "止めました")


def demo() -> None:
    """通信せずに、組み立てと止め方だけ見る。"""
    t = Target(host="arch-tunnel", user="ops")
    assert t.label() == "ops@arch-tunnel"
    assert Target(url="http://127.0.0.1:18765").label() == "http://127.0.0.1:18765"
    assert Target().label() == "(未設定)"

    c = Connection(Target(url="http://127.0.0.1:1"))
    assert c._last_ts is None
    # 繋ぐ前に止めても落ちない
    c.stop()
    assert c._stop
    began = time.monotonic()
    c._sleep(5.0)
    assert time.monotonic() - began < 0.5, "止めろと言ったのに待ち続けた"

    for name in ("state", "version", "history", "snapshot", "alerts", "health"):
        assert hasattr(c, name), name
    assert BACKOFF_MAX_S == 60.0
    print("worker OK")


if __name__ == "__main__":
    demo()
