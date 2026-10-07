# CHANGELOG

[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 形式。
バージョンはセマンティックバージョニング。番号は agent 側と揃えない
(互換性は API のメジャーバージョンで管理する)。

## [Unreleased]

### Added

- SSH トンネル(`svcscope_gui/tunnel.py`)。Windows 標準の `ssh.exe` を
  子プロセスで動かす。独自の SSH 実装は持たない。
  - 常に `ServerAliveInterval=15` / `ServerAliveCountMax=3` /
    `ExitOnForwardFailure=yes` を付ける。
  - 転送の待ち受けは `127.0.0.1` と明示する(同じ LAN から見えないように)。
  - `StrictHostKeyChecking=no` は使わない。
  - PATH より先に `C:\Windows\System32\OpenSSH\ssh.exe` を見る(WSL の ssh を拾わない)。
  - 使おうとしたポートが既に埋まっていれば、黙って繋がったふりをせずに止まる。
- API クライアント(`svcscope_gui/client.py`)。接続時に `/version` で
  API のメジャーバージョンを突き合わせる。トークンは環境変数か
  権限を絞ったファイルから読み、コマンドライン引数では受け取らない。
- 接続の確認コマンド `python -m svcscope_gui check`。トンネルを張って
  `/version`・`/snapshot`・`/health` が取れるところまでを1段ずつ表示する。

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

