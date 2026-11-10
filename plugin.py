# -*- coding: utf-8 -*-
"""WindLab – klasa wtyczki QGIS: przycisk na pasku narzędzi i wpis w menu Wtyczki."""
from __future__ import annotations

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

_HERE = os.path.dirname(os.path.abspath(__file__))


class WindLabPlugin:
    MENU = "&WindLab"

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.window = None

    def initGui(self):  # noqa: N802
        self.action = QAction(QIcon(os.path.join(_HERE, "icon.png")), "WindLab", self.iface.mainWindow())
        self.action.setToolTip("WindLab – analizy farm wiatrowych")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.MENU, self.action)

    def unload(self):
        if self.window is not None:
            self.window.close()
            self.window = None
        if self.action is not None:
            self.iface.removePluginMenu(self.MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self):
        if self.window is None:
            from .gui.main_tiles import WindLabMainWindow
            self.window = WindLabMainWindow(self.iface.mainWindow())
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
