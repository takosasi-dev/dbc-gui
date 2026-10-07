"""入口。

    python -m dbc_gui gui   --host arch-tunnel     # 画面を出す
    python -m dbc_gui check --host arch-tunnel     # 繋がるかだけ確かめる
    python -m dbc_gui check --url http://127.0.0.1:18765   # トンネルは自分で張る

`check` は繋がらないときの切り分け用に残してある。画面が出ないときに、
どこで止まっているかが1段ずつ見えるようにするため。
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .client import Client, ClientError, IncompatibleApi, load_token
from .tunnel import DEFAULT_LOCAL_PORT, Tunnel, TunnelError, find_ssh
from .worker import Target


def _ok(msg: str) -> None:
    print(f"  OK   {msg}", flush=True)


def _ng(msg: str) -> None:
    # stdout と stderr が別に溜まると、成功と失敗の行が入れ替わって出る
    sys.stdout.flush()
    print(f"  NG   {msg}", file=sys.stderr, flush=True)


def check(args: argparse.Namespace) -> int:
    tunnel = None
    try:
        token = load_token(args.token_file)
        _ok("トークンを読めた")

        if args.url:
            url = args.url.rstrip("/")
            print(f"  --   トンネルは張りません。{url} へ直接繋ぎます")
        else:
            if not args.host:
                _ng("--host か --url のどちらかが必要です")
                return 2
            _ok(f"使う ssh: {find_ssh()}")
            tunnel = Tunnel(
                args.host, local_port=args.local_port, user=args.user,
                ssh_port=args.ssh_port, identity_file=args.identity_file,
                ssh_config=args.ssh_config,
            )
            print(f"  --   {' '.join(tunnel.command())}")
            tunnel.start()
            tunnel.wait_ready(timeout=args.timeout)
            _ok(f"トンネルが張れた 127.0.0.1:{tunnel.local_port}")
            url = tunnel.url

        client = Client(url, token)

        version = client.version()
        _ok(f"/version: agent {version['agent_version']} / API v{version['api_version']}")
        _ok(f"対応機能: {', '.join(version.get('features', []))}")

        snap = client.snapshot()
        cpu = snap.get("cpu")
        mem = snap.get("memory")
        if cpu:
            _ok(f"CPU {cpu['percent']}%  load {cpu['load1']}  ({cpu['cores']} cores)")
        if mem:
            gib = 1024 ** 3
            _ok(f"メモリ {mem['used_bytes'] / gib:.2f} / {mem['total_bytes'] / gib:.2f} GiB")
        _ok(f"unit {len(snap.get('units', []))} 件")

        # 非対応・古い項目をここで出しておく。実機で何が取れないかが分かる
        cols = snap.get("collectors", {})
        bad = {k: v for k, v in sorted(cols.items()) if v != "ok"}
        if bad:
            print("  --   ok ではない collector: "
                  + "  ".join(f"{k}:{v}" for k, v in bad.items()))
        else:
            _ok("collector はすべて ok")

        health = client.health()
        rss = health.get("memory_rss_bytes")
        if rss:
            # 性能要件は 80MB 以下(Python プロトタイプ)
            mark = "OK  " if rss <= 80 * 1024 ** 2 else "注意"
            print(f"  {mark} エージェントの常駐メモリ {rss / 1024 ** 2:.1f} MB (目標 80MB 以下)")

        print("\n通りました。PC からサーバの値が取れています。")
        return 0

    except IncompatibleApi as e:
        _ng(str(e))
        return 1
    except (ClientError, TunnelError) as e:
        _ng(str(e))
        print("\n切り分けの順番は dbc-agent の docs/ssh.md 「5. つながらないときの順番」を見る",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if tunnel is not None:
            tunnel.stop()


def gui(args: argparse.Namespace) -> int:
    """画面を出す。"""
    if not args.host and not args.url:
        _ng("--host か --url のどちらかが必要です")
        return 2
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        _ng("画面には PySide6 と pyqtgraph が要ります: pip install PySide6 pyqtgraph")
        return 2

    from .window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("DBC")
    window = MainWindow(Target(
        host=args.host, url=args.url, local_port=args.local_port,
        user=args.user, ssh_port=args.ssh_port,
        identity_file=args.identity_file, ssh_config=args.ssh_config,
        token_file=args.token_file,
    ))
    window.show()
    return app.exec()


def _add_connection_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default=None,
                   help="~/.ssh/config の Host 名(例: arch-tunnel)")
    p.add_argument("--url", default=None,
                   help="トンネルを自分で張る場合の URL(例: http://127.0.0.1:18765)")
    p.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    # ~/.ssh/config の Host 名で済むならそのほうがよいが、
    # 直接指定もできる必要がある(仕様書「接続方式」)
    p.add_argument("--user", default=None, help="接続先の利用者名")
    p.add_argument("--ssh-port", type=int, default=None, help="サーバの sshd のポート")
    p.add_argument("--identity-file", type=Path, default=None, help="使う秘密鍵")
    p.add_argument("--ssh-config", type=Path, default=None,
                   help="~/.ssh/config の代わりに使う設定ファイル")
    p.add_argument("--token-file", default=None, type=Path,
                   help="トークンの平文を書いたファイル")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="dbc_gui",
        description="DBC の PC 側。SSH トンネル越しに Linux サーバの状態を見る",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gui", help="画面を出す")
    _add_connection_options(g)
    g.set_defaults(func=gui)

    c = sub.add_parser("check", help="トンネルを張って値が取れるか確かめる")
    _add_connection_options(c)
    c.add_argument("--timeout", type=float, default=20.0)
    c.set_defaults(func=check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
