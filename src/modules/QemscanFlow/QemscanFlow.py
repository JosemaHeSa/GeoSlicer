from ltrace.flow.thin_section import qemscanFlowWidget
from ltrace.slicer_utils import LTracePlugin, LTracePluginWidget
from ltrace.slicer_utils import getResourcePath
from pathlib import Path
import os


class QemscanFlow(LTracePlugin):
    SETTING_KEY = "QemscanFlow"
    MODULE_DIR = Path(os.path.dirname(os.path.realpath(__file__)))

    def __init__(self, parent):
        LTracePlugin.__init__(self, parent)
        self.parent.title = "QEMSCAN Flow"
        self.parent.categories = ["Tools", "Thin Section"]
        self.parent.contributors = ["LTrace Geophysics Team"]
        self.parent.helpText = (
            f"file:///{(getResourcePath('manual') / 'Modules/Thin_section/Fluxo%20QEMSCAN.html').as_posix()}"
        )

    @classmethod
    def readme_path(cls):
        return str(cls.MODULE_DIR / "README.md")


class QemscanFlowWidget(LTracePluginWidget):
    def __init__(self, parent):
        LTracePluginWidget.__init__(self, parent)

    def setup(self):
        LTracePluginWidget.setup(self)
        self.flowWidget = qemscanFlowWidget()
        self.layout.addWidget(self.flowWidget)
        self.layout.addStretch(1)

    def enter(self):
        self.flowWidget.enter()

    def exit(self):
        self.flowWidget.exit()
