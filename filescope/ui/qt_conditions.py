"""A literal condition builder that preserves the manually written expression."""

from ..core.query import parse_query
from ..errors import QuerySyntaxError


def build_condition(mode, words, count=2, distance=100):
    words = [word.strip() for word in words if word.strip()]
    if not words:
        raise ValueError("候補の言葉を入力してください。")
    if mode == 5 and len(words) != 2:
        raise ValueError("近くにある語句を2つ入力してください。")
    if mode == 2 and not 1 <= count <= len(words):
        raise ValueError("必要な一致数を候補数の範囲で指定してください。")
    quoted = ['"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"' for word in words]
    if mode == 2:
        return f"{count}of({', '.join(quoted)})"
    if mode == 3:
        return " & ".join("!" + word for word in quoted)
    if mode == 5:
        return f"NEAR({quoted[0]}, {quoted[1]}, {distance})"
    return (" | " if mode == 1 else " & ").join(quoted)


def condition_dialog(parent, original, legacy_operator):
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QSpinBox,
    )

    dialog = QDialog(parent)
    dialog.setWindowTitle("検索条件を作る")
    dialog.resize(540, 390)
    layout = QFormLayout(dialog)
    base = QLineEdit(original)
    layout.addRow("現在の検索式", base)
    mode = QComboBox()
    mode.addItems(["すべて含む", "どれか含む", "指定数以上含む", "含まない", "この語句のまま", "近くにある"])
    mode.setCurrentIndex(2)
    layout.addRow("追加する条件", mode)
    words = QPlainTextEdit()
    words.setPlaceholderText("1行に1語句\n例：680S\nDEV\n温度サイクル")
    layout.addRow("候補の言葉", words)
    number = QSpinBox()
    number.setRange(1, 2)
    number.setValue(2)
    layout.addRow("必要な一致数", number)
    distance = QSpinBox()
    distance.setRange(1, 10000)
    distance.setValue(100)
    layout.addRow("最大距離（文字）", distance)
    preview = QLabel()
    preview.setWordWrap(True)
    layout.addRow("検索式", preview)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    layout.addRow(buttons)
    expression = ""

    def update():
        nonlocal expression
        lines = words.toPlainText().splitlines()
        count = sum(bool(line.strip()) for line in lines)
        number.setMaximum(max(1, count) if count else 2)
        number.setEnabled(mode.currentIndex() == 2)
        distance.setEnabled(mode.currentIndex() == 5)
        existing = base.text().strip()
        try:
            extra = build_condition(mode.currentIndex(), lines, number.value(), distance.value()) if count else ""
            expression = f"({existing}) & ({extra})" if existing and extra else existing or extra
            if expression:
                parse_query(expression, legacy_operator=legacy_operator)
            label = f"{count}つのうち{number.value()}つ以上を含む\n" if count and mode.currentIndex() == 2 else ""
            preview.setText(label + expression)
            buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        except (ValueError, QuerySyntaxError) as exc:
            preview.setText(str(exc))
            buttons.button(QDialogButtonBox.Ok).setEnabled(False)

    base.textChanged.connect(update)
    words.textChanged.connect(update)
    mode.currentIndexChanged.connect(update)
    number.valueChanged.connect(update)
    distance.valueChanged.connect(update)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    update()
    return expression if dialog.exec() else None
