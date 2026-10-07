"""エージェントの API を叩く。

エージェント側のコードは参照しない。2つのリポジトリは API 仕様だけで結ぶ
(仕様書「リポジトリ構成」共有コードを持たない)。仕様の正本は
dbc-agent の docs/api/。
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

# このクライアントが話す API のメジャー版。接続時に /version で突き合わせる。
API_VERSION = 1

# トークンの平文の置き場。権限は利用者が守る前提。
# v0.2 で Windows 資格情報マネージャーに移す。
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "dbc" / "token"
TIMEOUT_S = 10.0


class ClientError(Exception):
    pass


class IncompatibleApi(ClientError):
    """エージェントの API メジャー版がこちらと合わない。"""


def load_token(path: Path | None = None) -> str:
    """トークンを読む。引数 > 環境変数 > 既定のファイル の順。

    平文をコマンドライン引数で受け取る口は作らない。他の利用者に見えるため。
    """
    if path is not None:
        return _read(path)
    env = os.environ.get("DBC_TOKEN")
    if env:
        return env.strip()
    if DEFAULT_TOKEN_FILE.exists():
        return _read(DEFAULT_TOKEN_FILE)
    raise ClientError(
        f"トークンがありません。{DEFAULT_TOKEN_FILE} に1行で置くか、"
        "環境変数 DBC_TOKEN で渡してください"
    )


def _read(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise ClientError(f"トークンを読めません: {e}") from e
    # 空ファイルで IndexError を出さない。原因の分かるエラーにする
    token = lines[0].strip() if lines else ""
    if not token:
        raise ClientError(f"{path} が空です")
    return token


class Client:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self._token = token

    def _get(self, path: str):
        req = urllib.request.Request(self.url + path)
        req.add_header("Authorization", f"Bearer {self._token}")
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ClientError("トークンが一致しません") from e
            if e.code == 429:
                raise ClientError("認証の連続失敗で一時的に拒否されています。1分ほど待ってください") from e
            try:
                detail = json.loads(e.read())["error"]["message"]
            except Exception:  # noqa: BLE001
                detail = str(e.reason)
            raise ClientError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise ClientError(
                f"{self.url} につながりません: {e.reason}\n"
                "SSH のトンネルが張れているか、エージェントが動いているかを確認してください"
            ) from e

    def get(self, path: str) -> dict:
        with self._get(path) as r:
            return json.loads(r.read())

    def version(self) -> dict:
        """繋いだ相手の版を取り、API の互換性を確かめる。

        /version は版に依存しない固定パスで、認証は免除されない。
        """
        body = self.get("/version")
        remote = body.get("api_version")
        if remote != API_VERSION:
            raise IncompatibleApi(
                f"API の版が合いません(エージェント v{remote} / こちら v{API_VERSION})。"
                "どちらかを更新してください"
            )
        return body

    def snapshot(self) -> dict:
        return self.get(f"/api/v{API_VERSION}/snapshot")

    def units(self) -> dict:
        return self.get(f"/api/v{API_VERSION}/units")

    def alerts(self) -> dict:
        return self.get(f"/api/v{API_VERSION}/alerts")

    def health(self) -> dict:
        return self.get(f"/api/v{API_VERSION}/health")

    def history(self, since: int | None = None) -> dict:
        path = f"/api/v{API_VERSION}/history"
        if since is not None:
            path += f"?since={int(since)}"
        return self.get(path)

    def stream(self):
        """SSE。2秒ごとに現在値が流れてくる。切れたら終わる。"""
        with self._get(f"/api/v{API_VERSION}/stream") as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("data: "):
                    yield json.loads(line[6:])


def demo() -> None:
    """通信せずに確かめられるところだけ見る。通しの確認は __main__.py。"""
    import tempfile

    c = Client("http://127.0.0.1:18765/", "x")
    assert c.url == "http://127.0.0.1:18765"

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "token"
        p.write_text("abc123\n", encoding="utf-8")
        assert load_token(p) == "abc123"
        p.write_text("", encoding="utf-8")
        try:
            load_token(p)
        except ClientError as e:
            assert "空です" in str(e), e
        else:
            raise AssertionError("空のトークンが通った")
        try:
            load_token(Path(d) / "none")
        except ClientError:
            pass
        else:
            raise AssertionError("無いファイルが通った")

    # つながらない先はエラーの文面で原因が分かること
    try:
        Client("http://127.0.0.1:1/", "x").snapshot()
    except ClientError as e:
        assert "トンネル" in str(e), e
    else:
        raise AssertionError("繋がらないのに成功した")

    print("client OK")


if __name__ == "__main__":
    demo()
