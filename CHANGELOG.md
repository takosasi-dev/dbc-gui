# CHANGELOG

[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 形式。
バージョンはセマンティックバージョニング。番号は agent 側と揃えない
(互換性は API のメジャーバージョンで管理する)。

## [Unreleased]

### Added

- SSH トンネル(`dbc_gui/tunnel.py`)。Windows 標準の `ssh.exe` を
  子プロセスで動かす。独自の SSH 実装は持たない。
  - 常に `ServerAliveInterval=15` / `ServerAliveCountMax=3` /
    `ExitOnForwardFailure=yes` を付ける。
  - 転送の待ち受けは `127.0.0.1` と明示する(同じ LAN から見えないように)。
  - `StrictHostKeyChecking=no` は使わない。
  - PATH より先に `C:\Windows\System32\OpenSSH\ssh.exe` を見る(WSL の ssh を拾わない)。
  - 使おうとしたポートが既に埋まっていれば、黙って繋がったふりをせずに止まる。
- API クライアント(`dbc_gui/client.py`)。接続時に `/version` で
  API のメジャーバージョンを突き合わせる。トークンは環境変数か
  権限を絞ったファイルから読み、コマンドライン引数では受け取らない。
- 接続の確認コマンド `python -m dbc_gui check`。トンネルを張って
  `/version`・`/snapshot`・`/health` が取れるところまでを1段ずつ表示する。
  接続先は `~/.ssh/config` の Host 名のほか、`--user` / `--ssh-port` /
  `--identity-file` / `--ssh-config` で直接も指定できる(仕様書「接続方式」の
  GUI 側の要件。設定画面はこれを呼ぶ)。鍵を指定したときは `IdentitiesOnly=yes`
  を付けて、agent に入っている別の鍵で試されないようにする。

- **画面**(`python -m dbc_gui gui`)。PySide6 + pyqtgraph の2カラム。
  左が時間の流れ(CPU と load / メモリと swap / 待たされ率 / ディスク I/O の
  30分ぶん)、右が今の状態(異常とサービスの一覧)。異常をタブの裏に隠さない。
  - 繋いだらまず `/history` で過去ぶんを取ってから `/stream` へ。空のグラフが
    埋まっていくのを見せない。
  - 切れたら最大60秒まで間隔を広げて張り直し、復帰時は `since` で欠損を埋める。
    **切れていた区間は線を切って灰色に塗る**(繋ぐと「ずっと平らだった」と読める)。
  - 取れていない項目は NaN にして線を切る。0 を入れない。
  - PSI が無効なカーネルでは枠を残して「非対応」と中に書く。枠ごと消すと
    出ていないことに気づけない。
  - ランプは色だけでなく文字でも状態を出す。

### Changed

- プロジェクト名を **DBC** に決め、仮称 SvcScope から改名した。リポジトリ名・
  Python パッケージ・systemd unit・`/etc` と `/run` と `/opt` のパス・専用ユーザ・
  環境変数(`DBC_TOKEN` ほか)をすべて揃えた。GitHub は旧名から転送されるが、
  リンクは新しい名前に貼り替えること。

### Fixed

- Windows のコンソールで日本語を出した時点で `UnicodeEncodeError` で落ちていた。
  既定のコードページ(日本語環境は cp932、英語環境は cp1252)では日本語が
  encode できない。パッケージの読み込み時に出力を UTF-8 にし、Windows では
  コンソールの出力コードページも 65001 に替える(終了時に元へ戻す)。
  CI を windows-latest で回していて見つかった。

### 未了

- PySide6 + pyqtgraph のダッシュボード。
- 切断時の自動再接続。
- トークンを Windows 資格情報マネージャーへ移す。
- 署名(minisign)の検証と自己更新、PyInstaller での実行ファイル化。

