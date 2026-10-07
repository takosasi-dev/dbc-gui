# DBC (GUI)

Linux サーバの **systemd unit 単位**の負荷と **待たされ率(PSI)** を、
Windows の PC から見るためのクライアント。PC 側のリポジトリ。

サーバ側(エージェント)は別リポジトリ:
[dbc-agent](https://github.com/takosasi-dev/dbc-agent)。
2つはコードを共有せず、
[API 仕様](https://github.com/takosasi-dev/dbc-agent/tree/main/docs/api)
だけで結ぶ。

> 開発中。実機での常駐テストは未了。接続設定は今のところ
> コマンドラインの引数で渡す(設定画面は次の段)。

## なぜ別リポジトリか

片方の変更や不具合が、もう片方のビルド・リリース・署名に影響しないようにする
ため。共有ライブラリ・submodule・サブツリーは使わない。互換性は API の
メジャーバージョン(`/api/v1`)だけで管理し、接続時に `/version` で突き合わせる。

バージョン番号は2つのリポジトリで揃えない。

## 動作環境

| | |
| --- | --- |
| OS | Windows 10 / 11。Linux でも動く(ssh と Python があれば) |
| Python | 3.11 以降 |
| 依存ライブラリ | 画面に PySide6 と pyqtgraph(`pip install -e .[gui]`)。`check` だけなら標準ライブラリで足りる |
| その他 | OpenSSH クライアント(Windows の「オプション機能」で入る) |

使う ssh は Windows 標準の `C:\Windows\System32\OpenSSH\ssh.exe`。
PATH の先頭が WSL 側になっている機械でも、そちらを先に見る。
独自の SSH 実装は持たない。

## 準備

1. サーバ側で [dbc-agent](https://github.com/takosasi-dev/dbc-agent)
   を入れて起動する。
2. サーバ側の `gen-token.sh` が表示したトークンの平文を、PC 側の
   `~/.config/dbc/token`(Windows なら
   `%USERPROFILE%\.config\dbc\token`)に1行で置く。
   自分以外が読めないようにする。
3. `~/.ssh/config` に接続先を書く。手順は
   [agent 側の docs/ssh.md](https://github.com/takosasi-dev/dbc-agent/blob/main/docs/ssh.md)。

トークンをコマンドライン引数で渡す口は作っていない。他の利用者に見えるため。
環境変数 `DBC_TOKEN` か `--token-file` を使う。

## 使う

### 画面を出す

```
python -m dbc_gui gui --host arch-tunnel
```

左が時間の流れ、右が今の状態の2カラム。異常がタブの裏に隠れないよう、
1画面に出す。

| | |
| --- | --- |
| 左上 | CPU・メモリ・PSI・ネットワークの現在値 |
| 左 | CPU 使用率と load / メモリと swap / 待たされ率 / ディスク I/O の30分ぶん |
| 右上 | 異常(journald・SMART・脆弱性・ニュース)。重い順 |
| 右下 | サービスの一覧。CPU の重い順 |
| 上端 | 接続のランプ(色だけでなく文字でも出す)、エージェントの版と常駐メモリ |

繋いだらまず `/history` で30分ぶんを取り、続けて `/stream` に繋ぐ。
切れたら最大60秒まで間隔を広げて張り直し、復帰したら `since` を付けて
取り直して欠損を埋める。**切れていた区間は線を切って灰色に塗る。**
繋いでしまうと「ずっと平らだった」と読めてしまうため。

PSI が無効なカーネルでは、グラフの枠を残して「このカーネルでは非対応」と
中に書く。枠ごと消すと、出ていないことに気づけない。

### 接続の確認

トンネルを張って、値が取れるところまでを1段ずつ確かめる。

```
python -m dbc_gui check --host arch-tunnel
```

```
  OK   トークンを読めた
  OK   使う ssh: C:\Windows\System32\OpenSSH\ssh.exe
  --   ssh -N -o ServerAliveInterval=15 ... -L 127.0.0.1:18765:127.0.0.1:8765 arch-tunnel
  OK   トンネルが張れた 127.0.0.1:18765
  OK   /version: agent 0.1.0 / API v1
  OK   対応機能: snapshot, history, units, alerts, health, stream
  OK   CPU 12.5%  load 0.42  (4 cores)
  OK   メモリ 1.58 / 3.81 GiB
  OK   unit 24 件
  --   ok ではない collector: smart:unsupported
  OK   エージェントの常駐メモリ 38.4 MB (目標 80MB 以下)

通りました。PC からサーバの値が取れています。
```

トンネルを自分で張っている場合は `--url` を使う。

```
ssh -N -L 127.0.0.1:18765:127.0.0.1:8765 arch-tunnel
python -m dbc_gui check --url http://127.0.0.1:18765
```

### 値を見る (CUI)

画面を出さずに見るなら、agent 側の CUI クライアントが早い。

```
python -m dbc.cli --url http://127.0.0.1:18765 watch
```

## 構成

| ファイル | 役割 |
| --- | --- |
| `dbc_gui/tunnel.py` | `ssh.exe` を子プロセスで動かしてポートフォワードを張る |
| `dbc_gui/client.py` | API クライアント。`/version` で互換性を確認する |
| `dbc_gui/__main__.py` | 入口(`gui` と `check`) |
| `dbc_gui/window.py` | 画面。値を並べるだけで、通信はしない |
| `dbc_gui/charts.py` | グラフ。何のメトリクスかは知らない |
| `dbc_gui/worker.py` | 繋ぎ役。トンネル・再接続・受信をこのスレッドで全部やる |

トンネルには常に `ServerAliveInterval=15` / `ServerAliveCountMax=3` /
`ExitOnForwardFailure=yes` を付ける。転送に失敗したときに「繋がったふり」を
させないため。`StrictHostKeyChecking=no` は使わない。中間者を黙って
受け入れる設定で、トークンを流す経路には置けない。

転送の待ち受けは `127.0.0.1` と明示する。ポート番号だけ書くと環境によって
全インターフェースに出て、同じ LAN の他の機械から見えてしまう。

## 開発

```
python tests/run_all.py
```

## これから

- 接続設定の画面(今はコマンドラインの引数で渡す)
- トークンを Windows 資格情報マネージャーに移す
- 署名(minisign)を検証してからの自己更新
- PyInstaller での実行ファイル化

## ライセンス

MIT。[LICENSE](LICENSE) を見る。
