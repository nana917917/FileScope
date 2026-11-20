"""Virtual result table shared by the Qt search view and its benchmark."""

from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from . import results
from .state import display_kind

# Original FileScope document glyphs. No shell or network icon lookups.
COLORS = {
    "Excel": "#16764d", "CSV": "#207d84", "PDF": "#a62939",
    "Word": "#2457a6", "PPT": "#a54c22", "Text": "#5e6573",
}
ROOT_INDEX = QModelIndex()


class ResultTable(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.icons = {}
        self.cells = OrderedDict()
        self.sort_keys = {}

    def rowCount(self, parent=ROOT_INDEX):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=ROOT_INDEX):
        return 0 if parent.isValid() else len(results.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return results.COLUMNS[section][1]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        result = row.result
        column = index.column()
        if role == Qt.DisplayRole:
            key = index.row()
            values = self.cells.get(key)
            if values is None:
                values = results.values_for(row)
                self.cells[key] = values
                if len(self.cells) > 1024:
                    self.cells.popitem(last=False)
            return values[column]
        if role == Qt.DecorationRole and column == 1:
            return self.icon(display_kind(result))
        if role == Qt.ToolTipRole:
            return "\n".join((result.path, " / ".join(result.displays)))
        return None

    def icon(self, kind):
        if kind not in self.icons:
            pixmap = QPixmap(18, 18)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS.get(kind, "#606870")))
            painter.drawRoundedRect(2, 1, 14, 16, 2, 2)
            painter.setPen(Qt.white)
            painter.drawText(pixmap.rect(), Qt.AlignCenter, kind[0])
            painter.end()
            self.icons[kind] = QIcon(pixmap)
        return self.icons[kind]

    def extend(self, rows):
        if rows:
            first = len(self.rows)
            self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
            self.rows.extend(rows)
            self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.rows.clear()
        self.cells.clear()
        self.sort_keys.clear()
        self.endResetModel()

    def refresh(self, row, first, last):
        self.cells.pop(row, None)
        self.sort_keys.pop(row, None)
        self.dataChanged.emit(self.index(row, first), self.index(row, last))


class ResultView(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.kinds = set()
        self.sources = set()
        self.ocr = False
        self.review = 0
        self.text = ""

    def filterAcceptsRow(self, row, _parent):
        if not (self.kinds or self.sources or self.ocr or self.review or self.text):
            return True
        item = self.sourceModel().rows[row]
        result = item.result
        return (
            (not self.kinds or display_kind(result) in self.kinds)
            and (not self.sources or result.source_type.value in self.sources)
            and (not self.ocr or result.ocr_pages)
            and (not self.review or item.confirmed == (self.review == 1))
            and (not self.text or self.text in result.path.casefold())
        )

    def sort_key(self, index):
        model = self.sourceModel()
        number = index.row()
        if number in model.sort_keys:
            return model.sort_keys[number][index.column()]
        row = model.rows[number]
        result = row.result
        keys = (
            row.confirmed, result.name.casefold(), result.displays, result.hit_count,
            display_kind(result), result.mtime_ns, result.size, result.source_type.value,
            result.ocr_pages, result.path.casefold(),
        )
        model.sort_keys[number] = keys
        return keys[index.column()]

    def lessThan(self, left, right):
        return self.sort_key(left) < self.sort_key(right)
