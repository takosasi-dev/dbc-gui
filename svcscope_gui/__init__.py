"""SvcScope の PC 側。

Windows の PC から、サーバのエージェントへ SSH のポートフォワード越しに
繋いで値を見る。エージェントとは HTTP の API だけで結び、相手の実装は
参照しない(仕様書「リポジトリ構成」)。

v0.1 のこの時点では、接続の確認(`python -m svcscope_gui check`)までが入っている。
PySide6 + pyqtgraph の画面は次の段で足す。
"""

__version__ = "0.1.0"
