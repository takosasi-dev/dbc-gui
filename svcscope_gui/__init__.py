"""SvcScope の PC 側。

Windows の PC から、サーバのエージェントへ SSH のポートフォワード越しに
繋いで値を見る。エージェントとは HTTP の API だけで結び、相手の実装は
参照しない(仕様書「リポジトリ構成」)。

v0.1 のこの時点では、接続の確認(`python -m svcscope_gui check`)までが入っている。
PySide6 + pyqtgraph の画面は次の段で足す。
"""

__version__ = "0.1.0"


def _use_utf8_output() -> None:
    """出力を UTF-8 にする。Windows ではコンソール側も合わせる。

    Windows のコンソールの既定のコードページ(日本語環境なら cp932、英語環境なら
    cp1252)では、日本語を print した時点で UnicodeEncodeError で落ちる。
    メッセージが出せないせいでツールごと止まるのは本末転倒。

    Python 側を UTF-8 にするだけでは落ちなくなる代わりに文字化けするので、
    コンソールの出力コードページも 65001 に替える。コンソールの設定は
    プロセスを抜けても残るため、終了時に元へ戻す。

    pythonw.exe のようにコンソールを持たない起動では該当しないので、
    失敗しても黙って続ける。
    """
    import sys

    if sys.platform == "win32":
        try:
            import atexit
            import ctypes

            kernel32 = ctypes.windll.kernel32
            before = kernel32.GetConsoleOutputCP()
            # 0 はコンソールが無いとき。替えるものが無いので何もしない
            if before and before != 65001 and kernel32.SetConsoleOutputCP(65001):
                atexit.register(kernel32.SetConsoleOutputCP, before)
        except (OSError, AttributeError):
            pass

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


# パッケージを読み込んだ時点で効かせる。入口が増えても付け忘れないため。
_use_utf8_output()
