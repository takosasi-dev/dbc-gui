"""全部の検査を順に走らせる。

    python tests/run_all.py

各モジュールが自分の自己検査(`demo()`)を持っている。ssh を実際に起動する
検査は入れていない。相手のサーバが必要になり、機械によって結果が変わるため。
通しの確認は `python -m dbc_gui check` で手で行う。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 読み込むと標準出力が UTF-8 になる。Windows の既定のコードページでは
# 日本語を print した時点で落ちるため
import dbc_gui  # noqa: E402,F401

CHECKS = [
    ["-m", "dbc_gui.tunnel"],
    ["-m", "dbc_gui.client"],
    # 引数が足りないときに使い方を出して終わること
    ["-m", "dbc_gui", "--version"],
]


def run(args: list[str]) -> bool:
    label = " ".join(args)
    proc = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode == 0:
        print(f"[ OK ] {label}")
        return True
    print(f"[ NG ] {label}")
    print((proc.stdout + proc.stderr).rstrip())
    return False


def main() -> int:
    failed = [a for a in CHECKS if not run(a)]
    if failed:
        print(f"\n{len(failed)} / {len(CHECKS)} 件が失敗しました")
        return 1
    print(f"\n{len(CHECKS)} / {len(CHECKS)} 件 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
