"""接続の確認。

    python -m svcscope_gui check --host arch-tunnel
    python -m svcscope_gui check --url http://127.0.0.1:18765   # トンネルは自分で張る

トンネルを張って /version と /snapshot が取れるところまでを、1段ずつ
確かめて表示する。画面を作る前に「PC からサーバの値が取れている」ことを
ここで確定させるため。
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .client import Client, ClientError, IncompatibleApi, load_token
from .tunnel import DEFAULT_LOCAL_PORT, Tunnel, TunnelError, find_ssh


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
        print("\n切り分けの順番は svcscope-agent の docs/ssh.md 「5. つながらないときの順番」を見る",
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if tunnel is not None:
            tunnel.stop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="svcscope_gui",
        description="SvcScope の PC 側。今は接続の確認まで。画面は次の段で足す",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="トンネルを張って値が取れるか確かめる")
    c.add_argument("--host", default=None,
                   help="~/.ssh/config の Host 名(例: arch-tunnel)")
    c.add_argument("--url", default=None,
                   help="トンネルを自分で張る場合の URL(例: http://127.0.0.1:18765)")
    c.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT)
    # ~/.ssh/config の Host 名で済むならそのほうがよいが、
    # 設定画面から直接指定できる必要もある(仕様書「接続方式」)
    c.add_argument("--user", default=None, help="接続先の利用者名")
    c.add_argument("--ssh-port", type=int, default=None, help="サーバの sshd のポート")
    c.add_argument("--identity-file", type=Path, default=None, help="使う秘密鍵")
    c.add_argument("--ssh-config", type=Path, default=None,
                   help="~/.ssh/config の代わりに使う設定ファイル")
    c.add_argument("--token-file", default=None, type=Path,
                   help="トークンの平文を書いたファイル")
    c.add_argument("--timeout", type=float, default=20.0)
    c.set_defaults(func=check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
