from functools import partial
import qt
import slicer
from ltrace.slicer.widgets import SingleShotInputWidget


class SideBySideImageManager:
    def __init__(self):
        self.segmentationNodes = {}
        for i in (1, 2):
            self.segmentationNodes[i] = None
            sliceWidget = slicer.app.layoutManager().sliceWidget(f"SideBySideSlice{i}")
            for widgetName in (
                "SegmentationIconLabel",
                "SegmentationVisibilityButton",
                "SegmentationOpacitySlider",
                "SegmentationOutlineButton",
                "SegmentSelectorWidget",
            ):
                sliceWidget.findChild(qt.QWidget, widgetName).setFixedSize(qt.QSize(0, 0))

            qMRMLSliceControllerWidget = sliceWidget.findChild(qt.QWidget, "qMRMLSliceControllerWidget")
            layout = qMRMLSliceControllerWidget.layout()
            layout.setSizeConstraint(qt.QLayout.SetFixedSize)
            customSegmentSelector = SingleShotInputWidget(
                hideImage=True,
                hideSoi=True,
                hideCalcProp=True,
                requireSourceVolume=False,
                allowedInputNodes=["vtkMRMLSegmentationNode"],
            )
            customSegmentSelector.setObjectName("CustomSegmentSelector")
            customSegmentSelector.onMainSelected = partial(self.onSegmentationChanged, i)

            customSegmentSelector.segmentSelectionChanged.connect(
                lambda segments, i=i: self.onSegmentSelectionChanged(i, segments)
            )
            customSegmentSelector.hide()
            layout.addWidget(customSegmentSelector, 1, 0, 1, 5)
            layout.setRowStretch(1, 1)

            moreButton = sliceWidget.findChild(qt.QWidget, "MoreButton")
            moreButton.toggled.connect(
                lambda toggled, sliceWidget=sliceWidget: self.formatWidgets(toggled, sliceWidget)
            )

            # Undoes the effects FixedSize size constraint has on the width of the controller
            paddingWidget = qt.QWidget()
            paddingWidget.setObjectName("PaddingWidget")
            paddingWidget.setAttribute(qt.Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(paddingWidget, 0, 4, 1, 1)
            paddingWidget.setFixedWidth(sliceWidget.sliceView().width)
            paddingWidget.setVisible(True)
            sliceWidget.sliceView().resized.connect(
                lambda size, paddingWidget=paddingWidget, isChecked=moreButton.isChecked: self._onViewResized(
                    size, paddingWidget, isChecked
                )
            )

    def _onViewResized(self, newSize, paddingWidget, isChecked):
        PADDING_OFFSET = 152  # Measured manually but shouldn't change
        paddingWidget.setFixedWidth(newSize.width() - (isChecked() * PADDING_OFFSET))

    def formatWidgets(self, toggled, sliceWidget):
        QWIDGETSIZE_MAX = (1 << 24) - 1  # constant not available in pyqt, just qt
        PADDING_OFFSET = 152  # Measured manually but shouldn't change

        qMRMLSliceControllerWidget = sliceWidget.findChild(qt.QWidget, "qMRMLSliceControllerWidget")

        customSegmentSelector = qMRMLSliceControllerWidget.findChild(SingleShotInputWidget, "CustomSegmentSelector")
        customSegmentSelector.setVisible(toggled)

        paddingWidget = qMRMLSliceControllerWidget.findChild(qt.QWidget, "PaddingWidget")
        paddingWidget.setFixedWidth(sliceWidget.sliceView().width - (toggled * PADDING_OFFSET))

        if toggled:
            # Undoing the fixed height
            qMRMLSliceControllerWidget.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
            qMRMLSliceControllerWidget.setMinimumSize(0, 0)
        else:
            qMRMLSliceControllerWidget.setFixedHeight(31)

    @staticmethod
    def enterLayout():
        segNodes = slicer.util.getNodesByClass("vtkMRMLSegmentationNode")
        for segNode in segNodes:
            # Image log has its own handling of segmentation visibility
            if not segNode.GetAttribute("ImageLogSegmentation"):
                segNode.CreateDefaultDisplayNodes()
                displayNode = segNode.GetDisplayNode()
                if displayNode.GetVisibility():
                    displayNode.SetAllSegmentsVisibility(False)

    def exitLayout(self):
        for i in (1, 2):
            if not slicer.mrmlScene.IsNodePresent(self.segmentationNodes[i]):
                self.segmentationNodes[i] = None

    @staticmethod
    def getViewId(index):
        sliceWidget = slicer.app.layoutManager().sliceWidget(f"SideBySideSlice{index}")
        return sliceWidget.sliceLogic().GetSliceNode().GetID()

    def onSegmentationChanged(self, sliceIndex, segmentationNode):
        viewId = self.getViewId(sliceIndex)

        if self.segmentationNodes[sliceIndex]:
            displayNode = self.segmentationNodes[sliceIndex].GetNthDisplayNode(sliceIndex)
            if displayNode:
                displayNode.SetAllSegmentsVisibility(False)

        if segmentationNode:
            # Hide default display node (0)
            displayNode = segmentationNode.GetDisplayNode()
            if displayNode:
                displayNode.SetAllSegmentsVisibility(False)

            # Create custom display nodes (1 and 2) if necessary
            for i in (1, 2):
                if not segmentationNode.GetNthDisplayNode(i):
                    displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationDisplayNode")
                    segmentationNode.AddAndObserveDisplayNodeID(displayNode.GetID())

                    # Initialize visibility
                    displayNode.SetDisplayableOnlyInView(self.getViewId(i))
                    displayNode.SetAllSegmentsVisibility(False)

            # Set custom display node visibility (1 or 2)
            displayNode = segmentationNode.GetNthDisplayNode(sliceIndex)

        self.segmentationNodes[sliceIndex] = segmentationNode

    def onSegmentSelectionChanged(self, sliceIndex, selectedSegments):
        segmentationNode = self.segmentationNodes[sliceIndex]
        if not segmentationNode:
            return
        displayNode = segmentationNode.GetNthDisplayNode(sliceIndex)
        displayNode.SetAllSegmentsVisibility(False)

        segmentation = segmentationNode.GetSegmentation()
        for segmentIndex in selectedSegments:
            segmentId = segmentation.GetNthSegmentID(segmentIndex)
            displayNode.SetSegmentVisibility(segmentId, True)
