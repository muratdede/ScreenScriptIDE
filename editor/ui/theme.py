"""Colors and the dark theme (modeled on IntelliJ "New UI" Dark).
For another theme derive from Theme and override the colors or stylesheet()."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from . import icons


class Theme:
    # surfaces
    background = QColor("#1e1f22")      # editor
    surface = QColor("#2b2d30")         # panels, toolbars
    border = QColor("#1e1f22")          # line between panels
    hover = QColor("#393b40")
    pressed = QColor("#43454a")
    input_border = QColor("#4e5157")

    text = QColor("#dfe1e5")
    muted = QColor("#868a91")
    line_number = QColor("#4b5059")
    line_number_current = QColor("#a1a3ab")
    accent = QColor("#3574f0")
    current_line = QColor("#26282e")
    debug_line = QColor("#2d3f63")
    breakpoint = QColor("#db5c5c")
    error = QColor("#f75464")           # code inspection: error
    warning = QColor("#e0a84a")         # code inspection: warning
    debug_arrow = QColor("#f2c55c")
    selection = QColor("#214283")
    find_match = QColor("#32593d")
    tree_selection = QColor("#2e436e")

    run_green = QColor("#5fb865")
    stop_red = QColor("#db5c5c")
    icon = QColor("#ced0d6")

    # syntax
    keyword = QColor("#cf8e6d")
    builtin = QColor("#8888c6")
    api = QColor("#56b6c2")
    string = QColor("#6aab73")
    comment = QColor("#7a7e85")
    number = QColor("#2aacb8")
    definition = QColor("#56a8f5")
    decorator = QColor("#b3ae60")
    self_ = QColor("#94558d")
    # semantic highlighting (names defined by the user)
    class_ref = QColor("#4ec9b0")        # class usage
    function_call = QColor("#57aaf7")    # function / method call
    parameter = QColor("#d6a3c9")        # parameter and keyword argument
    module = QColor("#b0c4de")           # imported module

    # console
    console_err = QColor("#f07178")
    console_warn = QColor("#e5c07b")
    console_sys = QColor("#61afef")
    console_eval = QColor("#98c379")

    @staticmethod
    def _first_family(candidates, fallback: QFont) -> QFont:
        families = set(QFontDatabase.families())
        for family in candidates:
            if family in families:
                return QFont(family)
        return fallback

    @classmethod
    def code_font(cls, size: int = 11) -> QFont:
        font = cls._first_family(("JetBrains Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono"),
                                 QFontDatabase.systemFont(QFontDatabase.FixedFont))
        font.setPointSize(size)
        font.setStyleHint(QFont.Monospace)
        return font

    @classmethod
    def ui_font(cls) -> QFont:
        font = cls._first_family(("Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Ubuntu"),
                                 QFontDatabase.systemFont(QFontDatabase.GeneralFont))
        font.setPixelSize(13)
        return font

    def apply(self, app: QApplication) -> None:
        app.setStyle("Fusion")
        app.setFont(self.ui_font())
        p = QPalette()
        p.setColor(QPalette.Window, self.surface)
        p.setColor(QPalette.WindowText, self.text)
        p.setColor(QPalette.Base, self.background)
        p.setColor(QPalette.AlternateBase, QColor("#2f3134"))
        p.setColor(QPalette.ToolTipBase, self.surface)
        p.setColor(QPalette.ToolTipText, self.text)
        p.setColor(QPalette.Text, self.text)
        p.setColor(QPalette.Button, self.surface)
        p.setColor(QPalette.ButtonText, self.text)
        p.setColor(QPalette.Highlight, self.tree_selection)
        p.setColor(QPalette.HighlightedText, self.text)
        p.setColor(QPalette.Link, QColor("#548af7"))
        p.setColor(QPalette.PlaceholderText, QColor("#6f737a"))
        p.setColor(QPalette.Mid, self.pressed)
        p.setColor(QPalette.Dark, self.background)
        p.setColor(QPalette.Light, self.hover)
        for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
            p.setColor(QPalette.Disabled, role, QColor("#6f737a"))
        app.setPalette(p)
        app.setStyleSheet(self.stylesheet())

    def stylesheet(self) -> str:
        down = icons.icon_file("chevron_down", "#9da0a8")
        up = icons.icon_file("chevron_up", "#9da0a8")
        right = icons.icon_file("chevron_right", "#868a91")
        check = icons.icon_file("check", "#ffffff")
        return f"""
            * {{ outline: 0; }}
            QMainWindow, QDialog {{ background: #2b2d30; }}
            QMainWindow::separator {{ background: #1e1f22; width: 3px; height: 3px; }}
            QMainWindow::separator:hover {{ background: #3574f0; }}

            QToolTip {{ color: #dfe1e5; background: #2b2d30; border: 1px solid #43454a;
                        border-radius: 6px; padding: 6px 8px; }}

            QMenu {{ background: #2b2d30; border: 1px solid #43454a; border-radius: 8px; padding: 6px; }}
            QMenu::item {{ color: #dfe1e5; padding: 5px 28px 5px 10px; border-radius: 4px; }}
            QMenu::item:selected {{ background: #2e436e; }}
            QMenu::item:disabled {{ color: #6f737a; }}
            QMenu::separator {{ height: 1px; background: #43454a; margin: 4px 6px; }}
            QMenu::icon {{ padding-left: 6px; }}
            QMenu::right-arrow {{ image: url({right}); width: 12px; height: 12px; margin-right: 6px; }}
            QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; }}

            QToolBar {{ background: #2b2d30; border: none; spacing: 3px; padding: 3px 6px; }}
            QToolBar#main_toolbar {{ border-bottom: 1px solid #1e1f22; padding: 5px 8px; spacing: 4px; }}
            QToolBar#stripe_left {{ border-right: 1px solid #1e1f22; padding: 6px 5px; spacing: 6px; }}
            QToolBar#stripe_right {{ border-left: 1px solid #1e1f22; padding: 6px 5px; spacing: 6px; }}
            QToolBar::separator {{ background: #43454a; width: 1px; height: 1px; margin: 4px 6px; }}

            QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px;
                           color: #dfe1e5; }}
            QToolButton:hover {{ background: #393b40; }}
            QToolButton:pressed {{ background: #43454a; }}
            QToolButton:checked {{ background: #43454a; }}
            QToolButton:disabled {{ color: #6f737a; }}
            QToolButton::menu-indicator {{ image: none; width: 0; }}
            QToolButton#dropdown {{ padding: 4px 22px 4px 6px; font-weight: 500; }}
            QToolButton#live_on {{ background: #5c2b2d; color: #ff9a9a; }}
            QToolButton#live_on:hover {{ background: #6e3336; }}

            QWidget#dock_titlebar {{ background: #2b2d30; }}
            QLabel#dock_title {{ color: #dfe1e5; font-weight: 600; }}
            QWidget#editor_area {{ background: #1e1f22; }}

            QTabWidget::pane {{ border: none; }}
            QTabBar {{ qproperty-drawBase: 0; }}
            QTabBar::tab {{ background: transparent; color: #b4b8bf; padding: 7px 10px; border: none;
                            border-bottom: 2px solid transparent; margin: 0; }}
            QTabBar::tab:selected {{ color: #dfe1e5; border-bottom: 2px solid #3574f0; }}
            QTabBar::tab:hover:!selected {{ background: #2f3136; }}
            QTabWidget#editor_tabs > QTabBar {{ background: #1e1f22; }}
            QTabWidget#editor_tabs > QTabBar::tab {{ padding: 8px 6px 8px 10px; }}
            QTabWidget#editor_tabs::tab-bar {{ left: 0; }}
            QTabBar::scroller {{ width: 22px; }}

            QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
            QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
            QScrollBar::handle {{ background: rgba(255, 255, 255, 38); border-radius: 3px; margin: 3px; }}
            QScrollBar::handle:hover {{ background: rgba(255, 255, 255, 70); }}
            QScrollBar::handle:vertical {{ min-height: 28px; }}
            QScrollBar::handle:horizontal {{ min-width: 28px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
            QAbstractScrollArea::corner {{ background: transparent; }}

            QLineEdit, QComboBox, QAbstractSpinBox {{ background: #2b2d30; color: #dfe1e5;
                border: 1px solid #4e5157; border-radius: 5px; padding: 3px 6px;
                selection-background-color: #214283; min-height: 18px; }}
            QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {{ border: 1px solid #3574f0; }}
            QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ color: #6f737a;
                border-color: #393b40; }}
            QComboBox {{ padding-right: 22px; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
            QComboBox QAbstractItemView {{ background: #2b2d30; border: 1px solid #43454a; padding: 4px;
                selection-background-color: #2e436e; outline: 0; }}
            QAbstractSpinBox {{ padding-right: 18px; }}
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; width: 16px;
                subcontrol-origin: border; }}
            QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
            QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
            QAbstractSpinBox::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
            QAbstractSpinBox::down-arrow {{ image: url({down}); width: 9px; height: 9px; }}

            QCheckBox {{ spacing: 7px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #6f737a; border-radius: 3px;
                background: #2b2d30; }}
            QCheckBox::indicator:hover {{ border-color: #9da0a8; }}
            QCheckBox::indicator:checked {{ background: #3574f0; border-color: #3574f0; image: url({check}); }}

            QRadioButton {{ spacing: 6px; }}
            QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid #6f737a;
                background: #2b2d30; }}
            QRadioButton::indicator:hover {{ border-color: #9da0a8; }}
            QRadioButton::indicator:checked {{ border: 4px solid #3574f0; background: #ffffff; width: 8px;
                height: 8px; }}

            QPushButton {{ background: #2b2d30; color: #dfe1e5; border: 1px solid #4e5157; border-radius: 5px;
                padding: 5px 14px; }}
            QPushButton:hover {{ background: #393b40; }}
            QPushButton:pressed {{ background: #43454a; }}
            QPushButton:default {{ background: #3574f0; border-color: #3574f0; color: #ffffff; }}
            QPushButton:default:hover {{ background: #4682fa; }}

            QGroupBox {{ border: 1px solid #393b40; border-radius: 6px; margin-top: 14px; padding-top: 8px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9da0a8; }}

            QTreeView, QListView {{ background: #2b2d30; border: none; outline: 0;
                show-decoration-selected: 1; }}
            QTreeView::item, QListView::item {{ padding: 3px 2px; color: #dfe1e5; }}
            QTreeView::item:hover, QListView::item:hover {{ background: #323438; }}
            QTreeView::item:selected:active, QListView::item:selected:active {{ background: #2e436e; }}
            QTreeView::item:selected:!active, QListView::item:selected:!active {{ background: #393b40; }}
            QTreeView::item:disabled, QListView::item:disabled {{ color: #6f737a; }}
            QTreeView::branch {{ background: transparent; }}
            QTreeView::branch:selected:active {{ background: #2e436e; }}
            QTreeView::branch:selected:!active {{ background: #393b40; }}
            QTreeView::branch:has-children:closed {{ image: url({right}); }}
            QTreeView::branch:has-children:open {{ image: url({down}); }}
            QHeaderView {{ background: #2b2d30; }}
            QHeaderView::section {{ background: #2b2d30; color: #868a91; border: none;
                border-bottom: 1px solid #393b40; border-right: 1px solid #393b40; padding: 4px 8px; }}
            QHeaderView::section:last {{ border-right: none; }}

            QSplitter::handle {{ background: #1e1f22; }}
            QSplitter::handle:horizontal {{ width: 1px; }}
            QSplitter::handle:vertical {{ height: 1px; }}

            QStatusBar {{ background: #2b2d30; border-top: 1px solid #1e1f22; color: #9da0a8; }}
            QStatusBar::item {{ border: none; }}
            QStatusBar QLabel {{ color: #9da0a8; padding: 2px 6px; }}

            QPlainTextEdit, QTextEdit {{ background: #1e1f22; border: none;
                selection-background-color: #214283; }}
            QGraphicsView, QScrollArea {{ border: none; }}
            QLabel#hint {{ color: #868a91; }}
        """


THEME = Theme()
