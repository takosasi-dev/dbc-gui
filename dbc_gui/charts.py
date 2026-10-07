"""グラフ。

pyqtgraph の薄い包み。ここが知っているのは「時刻の並びと値の並びを描く」
ことだけで、何のメトリクスかは知らない。

色は画面の配色(QPalette)から組む。固定色で書くと、明るいテーマの機械で
背景と同化して読めなくなる。
"""

from __future__ import annotations

import math

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

# 系列の色。背景の明暗どちらでも読める彩度に寄せてある
SERIES_COLORS = {
    "blue": "#4f9ed8",
    "green": "#5fb85f",
    "orange": "#e0853a",
    "red": "#e05252",
    "purple": "#9b7fd4",
    "teal": "#3fb5ab",
}

SEVERITY_COLORS = {
    "critical": "#e05252",
    "error": "#e0853a",
    "warning": "#d8b43a",
    "info": "#6a9ec5",
}

STATUS_COLORS = {
    "ok": "#5fb85f",
    "stale": "#d8b43a",
    "unsupported": "#8a8a8a",
}


def palette_colors() -> tuple[QColor, QColor]:
    """(背景, 文字)。アプリの配色から取る。"""
    app = QApplication.instance()
    pal = app.palette() if app else QPalette()
    return pal.color(QPalette.ColorRole.Base), pal.color(QPalette.ColorRole.Text)


class Series:
    """1本の線。"""

    def __init__(self, key: str, label: str, color: str, dashed: bool = False,
                 secondary: bool = False):
        self.key = key
        self.label = label
        self.color = SERIES_COLORS.get(color, color)
        self.dashed = dashed
        # 右の軸に描く。単位の違うものを1枚に重ねるときだけ使う
        self.secondary = secondary


def human_bytes(n: float | None) -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "-"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024.0 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}T"


class _BytesAxis(pg.AxisItem):
    """軸の目盛を 1.5G のように出す。"""

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - pyqtgraph の規約
        return [human_bytes(v) for v in values]


class TimeChart(QWidget):
    """横軸が時刻のグラフ。

    値が無い区間は NaN を入れて線を切る。繋いでしまうと「ずっと平らだった」と
    読めてしまい、切断していたことが分からなくなる。
    """

    def __init__(self, title: str, series: list[Series], *,
                 y_bytes: bool = False, y_max: float | None = None,
                 y_label: str = "", y2_label: str = "",
                 y_min_span: float = 1.0, y2_min_span: float = 1.0):
        super().__init__()
        self.series = series
        # 値が 0 付近だけだと自動スケールが ±0.4 のような範囲を選び、
        # 負の目盛と "x0.001" が出て読めなくなる。下限は常に 0、
        # 上限には最低この幅を持たせる。
        self._y_max = y_max
        self._y_min_span = y_min_span
        self._y2_min_span = y2_min_span
        self._items: dict[str, pg.PlotDataItem] = {}
        self._gaps: list[pg.LinearRegionItem] = []
        self._y2 = None

        bg, fg = palette_colors()
        grid = QColor(fg)
        grid.setAlpha(40)

        axis_items = {"bottom": pg.DateAxisItem()}
        if y_bytes:
            axis_items["left"] = _BytesAxis(orientation="left")

        self.plot = pg.PlotWidget(title=title, axisItems=axis_items, background=bg)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.getAxis("left").setLabel(y_label)
        for side in ("left", "bottom"):
            ax = self.plot.getAxis(side)
            ax.setTextPen(fg)
            ax.setPen(grid)
        self.plot.setTitle(title, color=fg.name(), size="10pt")
        if y_max is not None:
            self.plot.setYRange(0, y_max, padding=0.02)
        else:
            self.plot.setYRange(0, y_min_span, padding=0)
        self.plot.getPlotItem().vb.setLimits(yMin=0)
        if not y_bytes:
            # % や load に SI 接頭辞を付けられると "x0.001" になる
            self.plot.getAxis("left").enableAutoSIPrefix(False)

        # 単位の違う系列は右の軸に出す(CPU% と load のような組)
        if any(s.secondary for s in series):
            self._y2 = pg.ViewBox()
            self.plot.scene().addItem(self._y2)
            right = self.plot.getAxis("right")
            self.plot.showAxis("right")
            right.linkToView(self._y2)
            right.setLabel(y2_label)
            right.setTextPen(fg)
            right.setPen(grid)
            right.enableAutoSIPrefix(False)
            self._y2.setLimits(yMin=0)
            self._y2.enableAutoRange(axis="y", enable=False)
            self._y2.setYRange(0, y2_min_span, padding=0)
            self._y2.setXLink(self.plot.getPlotItem())
            self.plot.getPlotItem().vb.sigResized.connect(self._sync_y2)

        for s in series:
            pen = pg.mkPen(
                s.color, width=1.6,
                style=Qt.PenStyle.DashLine if s.dashed else Qt.PenStyle.SolidLine,
            )
            item = pg.PlotDataItem(pen=pen, name=s.label, connect="finite",
                                   antialias=True)
            if s.secondary and self._y2 is not None:
                self._y2.addItem(item)
            else:
                self.plot.addItem(item)
            self._items[s.key] = item

        legend = self.plot.addLegend(offset=(-8, 8), labelTextColor=fg,
                                     brush=pg.mkBrush(0, 0, 0, 0),
                                     pen=pg.mkPen(0, 0, 0, 0))
        for s in series:
            legend.addItem(self._items[s.key], s.label)

        # 非対応のときに枠の中へ出す札。枠ごと消すと「出ていないこと」に
        # 気づけないので、枠は残して理由を書く
        self._note = pg.TextItem("", color=fg, anchor=(0.5, 0.5))
        self._note.setVisible(False)
        self.plot.addItem(self._note)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)

    def _sync_y2(self) -> None:
        if self._y2 is not None:
            self._y2.setGeometry(self.plot.getPlotItem().vb.sceneBoundingRect())

    @staticmethod
    def _top(values: dict[str, list[float]], keys: list[str]) -> float:
        best = 0.0
        for k in keys:
            for v in values.get(k) or ():
                if v == v and v > best:   # NaN を外す
                    best = float(v)
        return best

    def set_data(self, times: list[float], values: dict[str, list[float]]) -> None:
        self._note.setVisible(False)
        for key, item in self._items.items():
            ys = values.get(key)
            if ys:
                item.setData(times, ys)
            else:
                item.clear()

        if self._y_max is None:
            top = self._top(values, [s.key for s in self.series if not s.secondary])
            self.plot.setYRange(0, max(self._y_min_span, top * 1.15), padding=0)
        if self._y2 is not None:
            top2 = self._top(values, [s.key for s in self.series if s.secondary])
            self._y2.setYRange(0, max(self._y2_min_span, top2 * 1.15), padding=0)
            self._sync_y2()

    def set_note(self, text: str) -> None:
        """「このカーネルでは非対応」のような札を枠の真ん中に出す。"""
        for item in self._items.values():
            item.clear()
        vb = self.plot.getPlotItem().vb
        rect = vb.viewRect()
        self._note.setText(text)
        self._note.setPos(rect.center())
        self._note.setVisible(True)

    def set_gaps(self, gaps: list[tuple[float, float]]) -> None:
        """切断していた区間を灰色で塗る。"""
        for item in self._gaps:
            self.plot.removeItem(item)
        self._gaps.clear()
        _, fg = palette_colors()
        brush = QColor(fg)
        brush.setAlpha(28)
        for a, b in gaps:
            region = pg.LinearRegionItem(values=(a, b), movable=False,
                                         brush=pg.mkBrush(brush))
            region.setZValue(-10)
            self.plot.addItem(region)
            self._gaps.append(region)


def demo() -> None:
    """窓を出さずに、組み立てと値の入れ替えだけ確かめる。"""
    import os
    import sys
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)

    assert human_bytes(None) == "-"
    assert human_bytes(float("nan")) == "-"
    assert human_bytes(512) == "512B"
    assert human_bytes(1536) == "1.5K"

    c = TimeChart("CPU", [
        Series("cpu", "使用率 %", "blue"),
        Series("load", "load1", "orange", secondary=True),
    ], y_max=100, y2_label="load")
    now = time.time()
    ts = [now - 60, now - 58, now - 56]
    c.set_data(ts, {"cpu": [1.0, 2.0, float("nan")], "load": [0.1, 0.2, 0.3]})
    assert c._items["cpu"].xData is not None
    # 無い系列は空にするだけで落ちない
    c.set_data(ts, {"cpu": [1.0, 2.0, 3.0]})
    c.set_gaps([(now - 58, now - 56)])
    assert len(c._gaps) == 1
    c.set_gaps([])
    assert c._gaps == []
    c.set_note("このカーネルでは非対応")
    assert c._note.isVisible()
    c.set_data(ts, {"cpu": [1.0, 2.0, 3.0]})
    assert not c._note.isVisible()

    b = TimeChart("メモリ", [Series("used", "使用", "green")], y_bytes=True)
    axis = b.plot.getAxis("left")
    assert axis.tickStrings([1024, 1024 ** 3], 1, 1) == ["1.0K", "1.0G"]

    assert set(SEVERITY_COLORS) == {"critical", "error", "warning", "info"}
    assert set(STATUS_COLORS) == {"ok", "stale", "unsupported"}
    print("charts OK")


if __name__ == "__main__":
    demo()
