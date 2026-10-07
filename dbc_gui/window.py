"""画面。

左が時間の流れ(グラフ)、右が今の状態(異常とサービス)の2カラム。
異常が常に目に入るようにするため、タブに隠さない。

ここは値を並べるだけで、通信は worker.Connection が全部やる。
"""

from __future__ import annotations

import math
import time
from collections import deque

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from . import __version__
from .charts import SEVERITY_COLORS, STATUS_COLORS, Series, TimeChart, human_bytes
from .worker import CONNECTING, ONLINE, OFFLINE, RETRYING, Connection, Target

NAN = float("nan")

# エージェントの保持とそろえる(2秒 × 30分)
MAX_POINTS = 900
# 2秒間隔の3倍空いたら、繋がっていなかったとみなして線を切る
GAP_AFTER_S = 6.0

LAMP = {
    ONLINE: ("●", "#5fb85f", "接続中"),
    CONNECTING: ("●", "#d8b43a", "接続しています"),
    RETRYING: ("●", "#d8b43a", "再接続中"),
    OFFLINE: ("●", "#e05252", "切断"),
}


def _dig(d: dict, *path, default=NAN):
    """入れ子の辞書から取る。途中が無ければ default。

    取れていない項目はキーごと来ないので、ここで NaN にしてグラフの線を切る。
    0 を入れると「0 だった」と読めてしまう。
    """
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur if isinstance(cur, (int, float)) else default


def extract(snap: dict) -> dict[str, float]:
    """1 点ぶんの値を、グラフの系列ごとに取り出す。"""
    disk = (snap.get("disk") or {}).get("devices") or []
    read = sum(d.get("read_bytes_per_sec", 0.0) for d in disk) if disk else NAN
    write = sum(d.get("write_bytes_per_sec", 0.0) for d in disk) if disk else NAN
    return {
        "cpu": _dig(snap, "cpu", "percent"),
        "load": _dig(snap, "cpu", "load1"),
        "mem_used": _dig(snap, "memory", "used_bytes"),
        "swap_used": _dig(snap, "memory", "swap_used_bytes"),
        "psi_cpu_some": _dig(snap, "psi", "cpu", "some_avg10"),
        "psi_mem_some": _dig(snap, "psi", "memory", "some_avg10"),
        "psi_mem_full": _dig(snap, "psi", "memory", "full_avg10"),
        "psi_io_some": _dig(snap, "psi", "io", "some_avg10"),
        "psi_io_full": _dig(snap, "psi", "io", "full_avg10"),
        "disk_read": read,
        "disk_write": write,
    }


KEYS = tuple(extract({}))


class History:
    """グラフに出す点の並び。切れていた区間を覚えておく。"""

    def __init__(self, maxlen: int = MAX_POINTS):
        self.times: deque[float] = deque(maxlen=maxlen)
        self.series: dict[str, deque[float]] = {k: deque(maxlen=maxlen) for k in KEYS}
        self.gaps: list[tuple[float, float]] = []

    def add(self, snap: dict) -> None:
        ts = snap.get("ts")
        if not ts:
            return
        t = ts / 1000.0
        if self.times and t <= self.times[-1]:
            return  # 取り直しで重なった分は捨てる
        if self.times and t - self.times[-1] > GAP_AFTER_S:
            # 線を繋がない。繋ぐと「ずっと平らだった」と読めてしまう
            self.gaps.append((self.times[-1], t))
            self.times.append(self.times[-1] + 0.001)
            for q in self.series.values():
                q.append(NAN)
        values = extract(snap)
        self.times.append(t)
        for key, q in self.series.items():
            q.append(values[key])
        # 窓から外れた区間はもう描かない
        if self.times:
            left = self.times[0]
            self.gaps = [g for g in self.gaps if g[1] >= left]

    def extend(self, points: list[dict]) -> None:
        for p in sorted(points, key=lambda p: p.get("ts") or 0):
            self.add(p)

    def arrays(self) -> tuple[list[float], dict[str, list[float]]]:
        return list(self.times), {k: list(v) for k, v in self.series.items()}

    def __len__(self) -> int:
        return len(self.times)


class MainWindow(QMainWindow):
    def __init__(self, target: Target):
        super().__init__()
        self.target = target
        self.history = History()
        self._collectors: dict[str, str] = {}

        self.setWindowTitle(f"DBC — {target.label()}")
        self.resize(1280, 800)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        outer.addLayout(self._build_header())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        # setStretchFactor は「余った幅」の配り方しか決めない。グラフ4枚の
        # 推奨サイズが大きく、右が潰れて unit 名が読めなくなるので初期幅を決める
        split.setSizes([760, 520])
        outer.addWidget(split, 1)

        self.conn = Connection(target)
        self.conn.state.connect(self.on_state)
        self.conn.version.connect(self.on_version)
        self.conn.history.connect(self.on_history)
        self.conn.snapshot.connect(self.on_snapshot)
        self.conn.alerts.connect(self.on_alerts)
        self.conn.health.connect(self.on_health)
        self.conn.start()

    # --- 組み立て ---

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.lamp = QLabel("●")
        self.lamp.setStyleSheet(f"color: {LAMP[CONNECTING][1]}; font-size: 15px;")
        self.state_label = QLabel("接続しています")
        self.host_label = QLabel(self.target.label())
        self.host_label.setStyleSheet("font-weight: 600;")
        self.agent_label = QLabel("agent -")
        self.rss_label = QLabel("")
        self.alert_badge = QLabel("")
        for w in (self.lamp, self.state_label, self.host_label):
            row.addWidget(w)
        row.addStretch(1)
        for w in (self.alert_badge, self.rss_label, self.agent_label):
            row.addWidget(w)
        return row

    def _build_left(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.summary: dict[str, QLabel] = {}
        grid = QVBoxLayout()
        grid.setSpacing(1)
        for key, title in (("cpu", "CPU"), ("mem", "MEM"), ("psi", "PSI"), ("net", "NET")):
            line = QHBoxLayout()
            name = QLabel(title)
            name.setFixedWidth(40)
            name.setStyleSheet("font-weight: 600;")
            value = QLabel("-")
            value.setFont(mono)
            line.addWidget(name)
            line.addWidget(value, 1)
            grid.addLayout(line)
            self.summary[key] = value
        col.addLayout(grid)

        # ネットワークは数字だけにしてグラフを1本減らす。対象の用途では
        # 帯域が問題になる見込みが薄く、縦を空けたほうが効く
        self.charts = {
            "cpu": TimeChart("CPU 使用率と load", [
                Series("cpu", "使用率 %", "blue"),
                Series("load", "load1", "orange", secondary=True),
            ], y_max=100, y_label="%", y2_label="load", y2_min_span=1.0),
            "mem": TimeChart("メモリと swap", [
                Series("mem_used", "使用", "green"),
                Series("swap_used", "swap", "purple"),
            ], y_bytes=True, y_min_span=64 * 1024 ** 2),
            "psi": TimeChart("待たされ率 (avg10)", [
                Series("psi_cpu_some", "cpu some", "blue"),
                Series("psi_mem_some", "mem some", "green"),
                Series("psi_mem_full", "mem full", "green", dashed=True),
                Series("psi_io_some", "io some", "orange"),
                Series("psi_io_full", "io full", "orange", dashed=True),
            ], y_label="%", y_min_span=5.0),
            "disk": TimeChart("ディスク I/O", [
                Series("disk_read", "読み", "teal"),
                Series("disk_write", "書き", "red"),
            ], y_bytes=True, y_min_span=1024 ** 2),
        }
        for chart in self.charts.values():
            col.addWidget(chart, 1)
        return box

    def _build_right(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        self.alert_box = QGroupBox("異常")
        alert_layout = QVBoxLayout(self.alert_box)
        alert_layout.setContentsMargins(6, 6, 6, 6)
        self.alert_tree = QTreeWidget()
        self.alert_tree.setHeaderLabels(["重さ", "元", "内容"])
        self.alert_tree.setRootIsDecorated(False)
        self.alert_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.alert_tree.setColumnWidth(0, 70)
        self.alert_tree.setColumnWidth(1, 70)
        alert_layout.addWidget(self.alert_tree)
        col.addWidget(self.alert_box, 1)

        unit_box = QGroupBox("サービス (重い順)")
        unit_layout = QVBoxLayout(unit_box)
        unit_layout.setContentsMargins(6, 6, 6, 6)
        self.unit_table = QTableWidget(0, 4)
        self.unit_table.setHorizontalHeaderLabels(["unit", "cpu%", "メモリ", "I/O 読/書"])
        self.unit_table.verticalHeader().setVisible(False)
        self.unit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.unit_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(48)
        for column, width in ((1, 58), (2, 68), (3, 96)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.unit_table.setColumnWidth(column, width)
        # 横スクロールを出さない。出ると数字の列が画面の外へ行く
        self.unit_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        unit_layout.addWidget(self.unit_table)
        col.addWidget(unit_box, 2)

        self.collector_label = QLabel("")
        self.collector_label.setWordWrap(True)
        col.addWidget(self.collector_label)
        return box

    # --- 受け取り ---

    @Slot(str, str)
    def on_state(self, state: str, detail: str) -> None:
        mark, color, text = LAMP.get(state, LAMP[OFFLINE])
        self.lamp.setStyleSheet(f"color: {color}; font-size: 15px;")
        # 色だけに頼らず文字でも出す
        self.state_label.setText(f"{text}{'  ' + detail if detail else ''}")

    @Slot(dict)
    def on_version(self, body: dict) -> None:
        self.agent_label.setText(
            f"agent {body.get('agent_version', '?')} / API v{body.get('api_version', '?')}"
            f"  (画面 {__version__})")

    @Slot(dict)
    def on_history(self, body: dict) -> None:
        self.history.extend(body.get("points") or [])
        self._redraw()

    @Slot(dict)
    def on_snapshot(self, snap: dict) -> None:
        self.history.add(snap)
        self._collectors = snap.get("collectors") or {}
        self._update_summary(snap)
        self._update_units(snap.get("units") or [])
        self._update_collectors()
        self._redraw()

    @Slot(dict)
    def on_alerts(self, body: dict) -> None:
        alerts = body.get("alerts") or []
        self.alert_box.setTitle(f"異常 ({len(alerts)})")
        self.alert_badge.setText(f"⚠ {len(alerts)} 件" if alerts else "")
        self.alert_badge.setStyleSheet(
            f"color: {SEVERITY_COLORS['warning']}; font-weight: 600;" if alerts else "")

        self.alert_tree.clear()
        for a in alerts:
            severity = a.get("severity", "info")
            item = QTreeWidgetItem([
                severity, a.get("source", ""), a.get("message", ""),
            ])
            item.setForeground(0, QColor(SEVERITY_COLORS.get(severity, "#999999")))
            # 幅に収まらないので、全文と詳細の URL をマウスで出す
            tip = a.get("message", "")
            if a.get("url"):
                tip += "\n" + a["url"]
            item.setToolTip(2, tip)
            item.setToolTip(1, a.get("package") or a.get("unit") or a.get("source", ""))
            self.alert_tree.addTopLevelItem(item)

    @Slot(dict)
    def on_health(self, body: dict) -> None:
        rss = body.get("memory_rss_bytes")
        self.rss_label.setText(f"常駐 {human_bytes(rss)}" if rss else "")

    # --- 描き直し ---

    def _update_summary(self, snap: dict) -> None:
        cpu = snap.get("cpu") or {}
        if cpu:
            self.summary["cpu"].setText(
                f"{cpu.get('percent', 0):5.1f} %   "
                f"load {cpu.get('load1', 0):.2f} / {cpu.get('load5', 0):.2f}"
                f" / {cpu.get('load15', 0):.2f}   ({cpu.get('cores', '?')} cores)")

        mem = snap.get("memory") or {}
        if mem:
            text = (f"{human_bytes(mem.get('used_bytes'))} / "
                    f"{human_bytes(mem.get('total_bytes'))}")
            if mem.get("swap_total_bytes"):
                text += (f"   swap {human_bytes(mem.get('swap_used_bytes'))}"
                         f" / {human_bytes(mem.get('swap_total_bytes'))}")
            z = mem.get("zram")
            if z:
                text += (f"   zram {human_bytes(z.get('orig_data_bytes'))}"
                         f"→{human_bytes(z.get('compr_data_bytes'))}")
            self.summary["mem"].setText(text)

        psi = snap.get("psi") or {}
        if psi:
            parts = []
            for kind, label in (("cpu", "cpu"), ("memory", "mem"), ("io", "io")):
                d = psi.get(kind) or {}
                if not d:
                    continue
                bit = f"{label} some {d.get('some_avg10', 0):5.2f}"
                if "full_avg10" in d:
                    bit += f" full {d['full_avg10']:5.2f}"
                parts.append(bit)
            self.summary["psi"].setText("   ".join(parts))

        nets = (snap.get("net") or {}).get("interfaces") or []
        if nets:
            self.summary["net"].setText("   ".join(
                f"{n['name']} rx {human_bytes(n.get('rx_bytes_per_sec'))}/s"
                f" tx {human_bytes(n.get('tx_bytes_per_sec'))}/s" for n in nets[:3]))

    def _update_units(self, units: list[dict]) -> None:
        # 何が食っているかを探すのが目的なので重い順
        rows = sorted(units, key=lambda u: -(u.get("cpu_percent") or 0.0))
        self.unit_table.setRowCount(len(rows))
        for i, u in enumerate(rows):
            name = u.get("name", "")
            cells = [
                # .service は全行に付くので落とす。その分 unit 名が読める
                name[:-len(".service")] if name.endswith(".service") else name,
                f"{u.get('cpu_percent', 0.0):.2f}",
                human_bytes(u.get("memory_bytes")),
                f"{human_bytes(u.get('io_read_bytes'))} / "
                f"{human_bytes(u.get('io_write_bytes'))}",
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if j:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.unit_table.setItem(i, j, item)

    def _update_collectors(self) -> None:
        bad = {k: v for k, v in sorted(self._collectors.items()) if v != "ok"}
        if not bad:
            self.collector_label.setText("collector はすべて ok")
            self.collector_label.setStyleSheet(f"color: {STATUS_COLORS['ok']};")
            return
        self.collector_label.setText(
            "ok でない collector: " + "  ".join(f"{k}:{v}" for k, v in bad.items()))
        self.collector_label.setStyleSheet(f"color: {STATUS_COLORS['stale']};")

    def _redraw(self) -> None:
        times, values = self.history.arrays()
        gaps = list(self.history.gaps)
        for name, chart in self.charts.items():
            # 非対応は枠を残して理由を書く。枠ごと消すと気づけない
            if name == "psi" and self._collectors.get("psi") == "unsupported":
                chart.set_note("このカーネルでは PSI が無効です")
                continue
            if name == "disk" and self._collectors.get("disk_io") == "unsupported":
                chart.set_note("ディスクの I/O を取れません")
                continue
            chart.set_data(times, values)
            chart.set_gaps(gaps)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt の規約
        self.conn.stop()
        # 止まらなくても窓は閉じる。待ち続けると落ちないアプリになる
        self.conn.wait(3000)
        super().closeEvent(event)


def demo() -> None:
    """窓を出さずに、値の流し込みと描き直しを確かめる。"""
    import os
    import sys

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    # 取れていない項目は NaN になる(0 にしない)
    assert math.isnan(extract({})["cpu"])
    full = extract({
        "cpu": {"percent": 12.5, "load1": 0.4},
        "memory": {"used_bytes": 100},
        "psi": {"io": {"some_avg10": 1.5, "full_avg10": 0.5}},
        "disk": {"devices": [{"read_bytes_per_sec": 10.0, "write_bytes_per_sec": 20.0},
                             {"read_bytes_per_sec": 5.0, "write_bytes_per_sec": 1.0}]},
    })
    assert full["cpu"] == 12.5 and full["mem_used"] == 100
    assert full["disk_read"] == 15.0 and full["disk_write"] == 21.0
    assert math.isnan(full["psi_cpu_some"])

    h = History()
    base = int(time.time() * 1000)
    for i in range(5):
        h.add({"ts": base + i * 2000, "cpu": {"percent": float(i), "load1": 0.1}})
    assert len(h) == 5 and h.gaps == []
    # 時刻が戻ったり重なったりしたら捨てる
    h.add({"ts": base})
    assert len(h) == 5
    # 間が空いたら線を切り、区間を覚える
    h.add({"ts": base + 60_000, "cpu": {"percent": 9.0, "load1": 0.1}})
    assert len(h.gaps) == 1, h.gaps
    times, values = h.arrays()
    assert len(times) == 7                      # 5 + 切れ目 + 1
    assert math.isnan(values["cpu"][5]), values["cpu"]
    assert times == sorted(times)

    w = MainWindow(Target(url="http://127.0.0.1:1"))
    w.conn.stop()
    w.conn.wait(2000)
    w.on_version({"agent_version": "0.1.0", "api_version": 1})
    w.on_health({"memory_rss_bytes": 37 * 1024 ** 2})
    w.on_history({"points": [
        {"ts": base + i * 2000, "cpu": {"percent": float(i), "load1": 0.2},
         "collectors": {"cpu": "ok"}} for i in range(10)]})
    assert len(w.history) == 10, len(w.history)

    w.on_snapshot({
        "ts": base + 20_000,
        "cpu": {"percent": 3.0, "load1": 0.3, "load5": 0.2, "load15": 0.1, "cores": 4},
        "memory": {"used_bytes": 2 * 1024 ** 3, "total_bytes": 4 * 1024 ** 3,
                   "swap_total_bytes": 1024 ** 3, "swap_used_bytes": 0,
                   "zram": {"orig_data_bytes": 1024 ** 3, "compr_data_bytes": 1024 ** 2}},
        "net": {"interfaces": [{"name": "eno1", "rx_bytes_per_sec": 1.0,
                                "tx_bytes_per_sec": 2.0}]},
        "units": [{"name": "a.service", "cpu_percent": 0.1, "memory_bytes": 4096},
                  {"name": "b.service", "cpu_percent": 9.9, "memory_bytes": 8192}],
        "collectors": {"cpu": "ok", "psi": "unsupported", "disk_io": "ok"},
    })
    assert "4 cores" in w.summary["cpu"].text()
    assert "zram" in w.summary["mem"].text()
    # 重い順に並ぶ。.service は全行に付くので落として出す
    assert w.unit_table.item(0, 0).text() == "b", w.unit_table.item(0, 0).text()
    assert w.unit_table.item(1, 0).text() == "a"
    assert w.unit_table.rowCount() == 2
    # 数字の列が横スクロールの外へ行かないこと
    assert w.unit_table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    # 非対応は枠を残して札を出す
    assert w.charts["psi"]._note.isVisible()
    assert not w.charts["cpu"]._note.isVisible()
    assert "psi:unsupported" in w.collector_label.text()

    w.on_alerts({"alerts": [
        {"ts": base, "source": "smart", "severity": "critical", "message": "こわれた"},
        {"ts": base, "source": "news", "severity": "info", "message": "おしらせ",
         "url": "https://example.invalid/"},
    ]})
    assert w.alert_tree.topLevelItemCount() == 2
    assert w.alert_box.title() == "異常 (2)"
    assert "2 件" in w.alert_badge.text()
    w.on_alerts({"alerts": []})
    assert w.alert_badge.text() == ""

    # 項目が空の snapshot でも落ちない
    w.on_snapshot({"ts": base + 22_000, "collectors": {}})
    w.on_state(RETRYING, "切れました")
    assert "再接続中" in w.state_label.text()

    w.close()
    print("window OK")


if __name__ == "__main__":
    demo()
