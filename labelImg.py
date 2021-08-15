#!/usr/bin/env python
# -*- coding: utf-8 -*-
import codecs
import distutils.spawn
import os
import shutil
import platform
import re
import sys
import subprocess
import xml.etree.ElementTree as EleTr

from datetime import datetime
from functools import partial
from collections import defaultdict

try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    # needed for py3+qt4
    # Ref:
    # http://pyqt.sourceforge.net/Docs/PyQt4/incompatible_apis.html
    # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string
    if sys.version_info.major >= 3:
        import sip
        sip.setapi('QVariant', 2)
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

import resources
# Add internal libs
from libs.constants import *
from libs.utils import *
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.stringBundle import StringBundle
from libs.canvas import Canvas
from libs.zoomWidget import ZoomWidget
from libs.labelDialog import LabelDialog
from libs.colorDialog import ColorDialog
from libs.labelFile import LabelFile, LabelFileError
from libs.toolBar import ToolBar
from libs.pascal_voc_io import PascalVocReader
from libs.pascal_voc_io import XML_EXT
from libs.yolo_io import YoloReader
from libs.yolo_io import TXT_EXT
from libs.ustr import ustr
from libs.version import __version__
from libs.hashableQListWidgetItem import HashableQListWidgetItem

__appname__ = 'labelImg'

def xml_object_finder(xml_file):
    tree = EleTr.parse(xml_file)
    root = tree.getroot()
    ob_ori = root.find('object')
    output_TF = bool(ob_ori)
    return output_TF

def annoDuplicatecheck(objectlist, namelist):
    for a in objectlist:
        name = a.find('name').text
        points = a.find('bndbox')
        xmin = points.find('xmin').text
        ymin = points.find('ymin').text
        xmax = points.find('xmax').text
        ymax = points.find('ymax').text
        namelist.append([name, xmin, ymin, xmax, ymax])

    return namelist

def next_xml_maker(pre_xml_name, next_xml_name, next_jpg_name):
    tree = EleTr.parse(pre_xml_name)
    root = tree.getroot()
    f_ori = root.find('filename')
    p_ori = root.find('path')
    slashpoint = next_jpg_name.rfind('\\')

    f_ori.text = next_jpg_name[slashpoint+1:]
    p_ori.text = next_jpg_name
    tree.write(next_xml_name, encoding="UTF-8")

def xmlmerge(prexml, nextxml, nextjpg):
    pretree = EleTr.parse(prexml)
    preroot = pretree.getroot()
    prefilename = preroot.find('filename')
    prepath = preroot.find('path')

    slashpoint = nextjpg.rfind('\\')
    prefilename.text = nextjpg[slashpoint+1:]
    prepath.text = nextjpg

    tree = EleTr.parse(nextxml)
    root = tree.getroot()
    addobject = root.findall('object')
    preobjectall = preroot.findall('object')

    originlist = []
    addlist = []

    originlist = annoDuplicatecheck(preobjectall, originlist)
    addlist = annoDuplicatecheck(addobject, addlist)

    for aa in range(0, len(addobject)):
        if addlist[aa] not in originlist:
            preroot.append(addobject[aa])
    pretree.write(nextxml, encoding="UTF-8")

def previmageSize(filename):
    # based by xmlfile
    tree = EleTr.parse(filename)
    root = tree.getroot()
    size = root.find('size')
    width = size.find('width')
    height = size.find('height')
    return [int(width.text), int(height.text)]

def nextimageSize(filename):
    # based by imgfile
    nextImage = read(ustr(filename), None)
    nextImage = QImage.fromData(nextImage)
    nextImage = (QPixmap.fromImage(nextImage))
    return [nextImage.width(), nextImage.height()]

class WindowMixin(object):

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            addActions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(self, defaultFilename=None, defaultPrefdefClassFile=None, defaultSaveDir=None):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()
        settings = self.settings

        # Load string bundle for i18n
        self.stringBundle = StringBundle.getBundle()
        getStr = lambda strId: self.stringBundle.getString(strId)

        # Save as Pascal voc xml
        self.defaultSaveDir = defaultSaveDir
        self.usingPascalVocFormat = True
        self.usingYoloFormat = False

        # For loading all image under a directory
        self.mImgList = []
        self.dirname = None
        self.labelHist = []
        self.lastOpenDir = None

        # Preset Shape
        self.PresetShapeOne = None
        self.PresetShapeTwo = None
        self.PresetShapeThree = None
        self.PresetShapeFour = None
        self.PresetShapeFive = None

        # Shape Cut Status
        self.horiStatus = False
        self.vertStatus = False
        self.crossStatus = False
        self.resizeStatus = False

        # Undo / Redo
        self.undoList = []
        self.redoList = []
        self.mergeShapeChecker = False

        #Auto Input
        self.AutoInputPresetShape = None
        self.AutoInputStatus = False

        # Filename log
        self.desctext = None

        # line - Vertex Color
        self.presetColor = {"smoke": QColor(100, 227, 104, 180), "smoke_light": QColor(196, 255, 198, 180),
                            "light": QColor(255, 242, 0, 180), "person": QColor(22, 166, 238, 180),
                            "n_fire": QColor(243, 23, 31, 180), "a_fire": QColor(237, 105, 14, 180),
                            "cloud": QColor(0, 0, 0, 180), "fire": QColor(243, 23, 31, 180), "car": QColor(193, 166, 255, 180), "f_fire": QColor(12, 232, 224, 180), "f_smoke": QColor(155, 28, 151, 180), "f_person": QColor(233, 89, 17, 180)}

        self.presetColor_light = {"smoke": QColor(100, 227, 104, 80), "smoke_light": QColor(196, 255, 198, 80),
                             "light": QColor(255, 242, 0, 80), "person": QColor(22, 166, 238, 80),
                             "n_fire": QColor(243, 23, 31, 80), "a_fire": QColor(237, 105, 14, 80),
                             "cloud": QColor(0, 0, 0, 80), "fire": QColor(243, 23, 31, 80), "car": QColor(193, 166, 255, 80), "f_fire": QColor(12, 232, 224, 80), "f_smoke": QColor(155, 28, 151, 80), "f_person": QColor(233, 89, 17, 80)}

        # Whether we need to save or not.
        self.dirty = False

        self._noSelectionSlot = False
        self._beginner = True
        self.screencastViewer = self.getAvailableScreencastViewer()
        self.screencast = "https://youtu.be/p0nR2YsCY_U"

        # Load predefined classes to the list
        self.loadPredefinedClasses(defaultPrefdefClassFile)

        # Main widgets and related state.
        self.labelDialog = LabelDialog(parent=self, listItem=self.labelHist)

        self.itemsToShapes = {}
        self.shapesToItems = {}
        self.prevLabelText = ''
        self.selectedItemrow = None

        listLayout = QVBoxLayout()
        listLayout.setContentsMargins(0, 0, 0, 0)

        # Create a widget for using default label
        self.useDefaultLabelCheckbox = QCheckBox(getStr('useDefaultLabel'))
        self.useDefaultLabelCheckbox.setChecked(False)
        self.defaultLabelTextLine = QLineEdit()
        useDefaultLabelQHBoxLayout = QHBoxLayout()
        useDefaultLabelQHBoxLayout.addWidget(self.useDefaultLabelCheckbox)
        useDefaultLabelQHBoxLayout.addWidget(self.defaultLabelTextLine)
        useDefaultLabelContainer = QWidget()
        useDefaultLabelContainer.setLayout(useDefaultLabelQHBoxLayout)

        # Create a widget for auto input label
        self.useAutoInputCheckbox = QCheckBox(u'저장된 라벨 자동 추가 모드(F9)')
        self.useAutoInputCheckbox.setChecked(False)
        self.useAutoInputCheckbox.setShortcut("F9")
        self.useAutoInputBrowser = QLineEdit()
        self.useAutoInputBrowser.setReadOnly(True)

        useAutoInputQHBoxLayout = QHBoxLayout()
        useAutoInputQHBoxLayout.addWidget(self.useAutoInputCheckbox)
        useAutoInputQHBoxLayout.addWidget(self.useAutoInputBrowser)

        useAutoInputContainer = QWidget()
        useAutoInputContainer.setLayout(useAutoInputQHBoxLayout)

        # Create a widget for edit and diffc button
        self.diffcButton = QCheckBox(getStr('useDifficult'))
        self.diffcButton.setChecked(False)
        self.diffcButton.stateChanged.connect(self.btnstate)
        self.editButton = QToolButton()
        self.editButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.xmlautocopyMode = QCheckBox(u'이전 라벨 파일 자동 복사 모드 (Ctrl+Shift+M)')
        self.xmlautocopyMode.setChecked(False)
        self.xmlautocopyMode.setShortcut("Ctrl+Shift+M")

        # Preset Label Browser
        presetLayout = QGridLayout()

        self.presetLabel1 = QLabel("프리셋 1")
        self.presetLabel2 = QLabel("프리셋 2")
        self.presetLabel3 = QLabel("프리셋 3")
        self.presetLabel4 = QLabel("프리셋 4")
        self.presetLabel5 = QLabel("프리셋 5")

        self.presetBrowser1 = QLineEdit()
        self.presetBrowser1.setReadOnly(True)
        self.presetBrowser2 = QLineEdit()
        self.presetBrowser2.setReadOnly(True)
        self.presetBrowser3 = QLineEdit()
        self.presetBrowser3.setReadOnly(True)
        self.presetBrowser4 = QLineEdit()
        self.presetBrowser4.setReadOnly(True)
        self.presetBrowser5 = QLineEdit()
        self.presetBrowser5.setReadOnly(True)

        presetLayout.addWidget(self.presetLabel1, 0, 0)
        presetLayout.addWidget(self.presetLabel2, 0, 1)
        presetLayout.addWidget(self.presetLabel3, 0, 2)
        presetLayout.addWidget(self.presetLabel4, 0, 3)
        presetLayout.addWidget(self.presetLabel5, 0, 4)

        presetLayout.addWidget(self.presetBrowser1, 1, 0)
        presetLayout.addWidget(self.presetBrowser2, 1, 1)
        presetLayout.addWidget(self.presetBrowser3, 1, 2)
        presetLayout.addWidget(self.presetBrowser4, 1, 3)
        presetLayout.addWidget(self.presetBrowser5, 1, 4)

        # Edit Browser
        self.textBrowser = QLineEdit()
        self.textBrowser.setReadOnly(True)
        self.textBrowser.setText("이동 모드")
        self.editBrowser = QLineEdit()
        self.editBrowser.setReadOnly(True)

        self.editLabel1 = QLabel("이동 / 확장 / 축소")
        self.editLabel2 = QLabel("분할 / 크기 변경")

        browserLayout = QGridLayout()

        browserLayout.addWidget(self.editLabel1, 0, 0)
        browserLayout.addWidget(self.editLabel2, 0, 1)
        browserLayout.addWidget(self.textBrowser, 1, 0)
        browserLayout.addWidget(self.editBrowser, 1, 1)

        # Lock Mode
        self.vertexoffMode = QCheckBox(u'박스 수정 잠금 모드 (K)')
        self.vertexoffMode.setChecked(False)
        self.vertexoffMode.setShortcut("K")
        self.vertexoffMode.stateChanged.connect(self.boxLockMode)

        self.moveoffMode = QCheckBox(u'박스 이동 잠금 모드 (L)')
        self.moveoffMode.setChecked(False)
        self.moveoffMode.setShortcut("L")
        self.moveoffMode.stateChanged.connect(self.boxLockMode)

        offmodeLayout = QHBoxLayout()
        offmodeLayout.addWidget(self.vertexoffMode)
        offmodeLayout.addWidget(self.moveoffMode)

        # Add some of widgets to listLayout
        listLayout.addWidget(self.editButton)
        listLayout.addWidget(self.diffcButton)
        listLayout.addWidget(useDefaultLabelContainer)
        listLayout.addLayout(browserLayout)
        listLayout.addWidget(self.xmlautocopyMode)
        listLayout.addWidget(useAutoInputContainer)
        listLayout.addLayout(presetLayout)
        listLayout.addLayout(offmodeLayout)

        # Create and add a widget for showing current label items
        self.labelList = QListWidget()
        labelListContainer = QWidget()
        labelListContainer.setLayout(listLayout)
        self.labelList.itemActivated.connect(self.labelSelectionChanged)
        self.labelList.itemSelectionChanged.connect(self.labelSelectionChanged)
        self.labelList.itemDoubleClicked.connect(self.editLabel)
        # Connect to itemChanged to detect checkbox changes.
        self.labelList.itemChanged.connect(self.labelItemChanged)
        listLayout.addWidget(self.labelList)

        self.dock = QDockWidget(getStr('boxLabelText'), self)
        self.dock.setObjectName(getStr('labels'))
        self.dock.setWidget(labelListContainer)

        # current / total
        self.currenttotalLabel = QLabel("현재 이미지 / 전체 이미지 : ")
        self.currenttotalBrowser = QLineEdit()
        self.currenttotalBrowser.setReadOnly(True)

        currenttotalQHBoxLayout = QHBoxLayout()
        currenttotalQHBoxLayout.addWidget(self.currenttotalLabel)
        currenttotalQHBoxLayout.addWidget(self.currenttotalBrowser)

        self.fileListWidget = QListWidget()
        self.fileListWidget.itemDoubleClicked.connect(self.fileitemDoubleClicked)
        filelistLayout = QVBoxLayout()
        filelistLayout.setContentsMargins(0, 0, 0, 0)
        filelistLayout.addLayout(currenttotalQHBoxLayout)
        filelistLayout.addWidget(self.fileListWidget)
        fileListContainer = QWidget()
        fileListContainer.setLayout(filelistLayout)
        self.filedock = QDockWidget(getStr('fileList'), self)
        self.filedock.setObjectName(getStr('files'))
        self.filedock.setWidget(fileListContainer)

        # undo dock
        executedlistLayout = QVBoxLayout()
        executedlistLayout.setContentsMargins(0, 0, 0, 0)
        self.executedList = QListWidget()
        executedListContainer = QWidget()
        executedListContainer.setLayout(executedlistLayout)

        # self.executedList.itemActivated.connect(self.undoSelectionChanged)
        #self.executedList.itemSelectionChanged.connect(self.undoSelectionChanged)

        executedlistLayout.addWidget(self.executedList)

        self.undodock = QDockWidget('undo / redo', self)
        self.undodock.setObjectName('undo / redo')
        self.undodock.setWidget(executedListContainer)

        self.addDockWidget(Qt.RightDockWidgetArea, self.undodock)
        self.undodock.setFloating(True)

        self.zoomWidget = ZoomWidget()
        self.colorDialog = ColorDialog(parent=self)

        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoomRequest)
        self.canvas.setDrawingShapeToSquare(settings.get(SETTING_DRAW_SQUARE, False))

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scrollBars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar()
        }
        self.scrollArea = scroll
        self.canvas.scrollRequest.connect(self.scrollRequest)

        self.canvas.newShape.connect(self.newShape)
        self.canvas.shapeMoved.connect(self.setDirty)
        self.canvas.selectionChanged.connect(self.shapeSelectionChanged)
        self.canvas.selectionMulti.connect(self.shapeMultiSelection)
        self.canvas.drawingPolygon.connect(self.toggleDrawingSensitive)

        # for undo / redo signal connect
        self.canvas.rotateUndo.connect(self.rotateUndosave)
        self.canvas.clickmoveUndo.connect(self.clickmoveUndosave)
        self.canvas.modifyingUndo.connect(self.modifiedUndosave)
        self.canvas.keypressUndo.connect(self.keypressUndosave)
        self.canvas.autotrackingUndo.connect(self.autotrackingUndosave)

        self.setCentralWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.filedock)
        self.filedock.setFeatures(QDockWidget.DockWidgetFloatable)

        self.dockFeatures = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)

        # Actions
        action = partial(newAction, self)
        quit = action(getStr('quit'), self.close,
                      'Ctrl+Q', 'quit', getStr('quitApp'))

        open = action(getStr('openFile'), self.openFile,
                      'Ctrl+O', 'open', getStr('openFileDetail'))

        opendir = action(getStr('openDir'), self.openDirDialog,
                         'Ctrl+u', 'open', getStr('openDir'))

        changeSavedir = action(getStr('changeSaveDir'), self.changeSavedirDialog,
                               'Ctrl+r', 'open', getStr('changeSavedAnnotationDir'))

        openAnnotation = action(getStr('openAnnotation'), self.openAnnotationDialog,
                                'Ctrl+Shift+O', 'open', getStr('openAnnotationDetail'))

        openNextImg = action(getStr('nextImg'), self.openNextImg,
                             'd', 'next', getStr('nextImgDetail'))

        openPrevImg = action(getStr('prevImg'), self.openPrevImg,
                             'a', 'prev', getStr('prevImgDetail'))

        openPrevXml = action('&Add Prev Image Annotation', self.openPrevXml,
                             'Ctrl+G', 'open', u'Add Prev Image Annotation')

        verify = action(getStr('verifyImg'), self.verifyImg,
                        'space', 'verify', getStr('verifyImgDetail'))

        save = action(getStr('save'), self.saveFile,
                      'Ctrl+S', 'save', getStr('saveDetail'), enabled=False)

        save_format = action('&PascalVOC', self.change_format,
                      'Ctrl+', 'format_voc', getStr('changeSaveFormat'), enabled=True)

        saveAs = action(getStr('saveAs'), self.saveFileAs,
                        'Ctrl+Shift+S', 'save-as', getStr('saveAsDetail'), enabled=False)

        close = action(getStr('closeCur'), self.closeFile, 'Ctrl+W', 'close', getStr('closeCurDetail'))

        resetAll = action(getStr('resetAll'), self.resetAll, None, 'resetall', getStr('resetAllDetail'))

        color1 = action(getStr('boxLineColor'), self.chooseColor1,
                        'Ctrl+L', 'color_line', getStr('boxLineColorDetail'))

        createMode = action(getStr('crtBox'), self.setCreateMode,
                            'Ctrl+N', 'new', getStr('crtBoxDetail'), enabled=False)
        editMode = action('&Edit\nRectBox', self.setEditMode,
                          'Ctrl+J', 'edit', u'Move and edit Boxs', enabled=False)

        create = action(getStr('crtBox'), self.createShape,
                        'w', 'new', getStr('crtBoxDetail'), enabled=False)
        delete = action(getStr('delBox'), self.deleteSelectedShape,
                        'r', 'delete', getStr('delBoxDetail'), enabled=False)
        copy = action(getStr('dupBox'), self.copySelectedShape,
                      'Ctrl+D', 'copy', getStr('dupBoxDetail'),
                      enabled=False)

        advancedMode = action(getStr('advancedMode'), self.toggleAdvancedMode,
                              'Ctrl+Shift+A', 'expert', getStr('advancedModeDetail'),
                              checkable=True)

        hideAll = action('&Hide\nRectBox', partial(self.togglePolygons, False),
                         'Ctrl+H', 'hide', getStr('hideAllBoxDetail'),
                         enabled=False)
        showAll = action('&Show\nRectBox', partial(self.togglePolygons, True),
                         'Ctrl+A', 'hide', getStr('showAllBoxDetail'),
                         enabled=False)

        help = action(getStr('tutorial'), self.showTutorialDialog, None, 'help', getStr('tutorialDetail'))
        showInfo = action(getStr('info'), self.showInfoDialog, None, 'help', getStr('info'))

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoomWidget)
        self.zoomWidget.setWhatsThis(
            u"Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas." % (fmtShortcut("Ctrl+[-+]"),
                                             fmtShortcut("Ctrl+Wheel")))
        self.zoomWidget.setEnabled(False)

        zoomIn = action(getStr('zoomin'), partial(self.addZoom, 10),
                        'Ctrl++', 'zoom-in', getStr('zoominDetail'), enabled=False)
        zoomOut = action(getStr('zoomout'), partial(self.addZoom, -10),
                         'Ctrl+-', 'zoom-out', getStr('zoomoutDetail'), enabled=False)
        zoomOrg = action(getStr('originalsize'), partial(self.setZoom, 100),
                         'Ctrl+=', 'zoom', getStr('originalsizeDetail'), enabled=False)
        fitWindow = action(getStr('fitWin'), self.setFitWindow,
                           'Ctrl+F', 'fit-window', getStr('fitWinDetail'),
                           checkable=True, enabled=False)
        fitWidth = action(getStr('fitWidth'), self.setFitWidth,
                          'Ctrl+Shift+F', 'fit-width', getStr('fitWidthDetail'),
                          checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoomActions = (self.zoomWidget, zoomIn, zoomOut,
                       zoomOrg, fitWindow, fitWidth)
        self.zoomMode = self.MANUAL_ZOOM
        self.scalers = {
            self.FIT_WINDOW: self.scaleFitWindow,
            self.FIT_WIDTH: self.scaleFitWidth,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action(getStr('editLabel'), self.editLabel,
                      'Ctrl+E', 'edit', getStr('editLabelDetail'),
                      enabled=False)
        self.editButton.setDefaultAction(edit)

        shapeLineColor = action(getStr('shapeLineColor'), self.chshapeLineColor,
                                icon='color_line', tip=getStr('shapeLineColorDetail'),
                                enabled=False)
        shapeFillColor = action(getStr('shapeFillColor'), self.chshapeFillColor,
                                icon='color', tip=getStr('shapeFillColorDetail'),
                                enabled=False)

        autotrackingLabel = action('&선택된 라벨 자동 추적', self.autotrackingLabelmethod, "t", None, None,
                                   enabled=False, checkable=True)

        labels = self.dock.toggleViewAction()
        labels.setText(getStr('showHide'))
        labels.setShortcut('Ctrl+Shift+L')

        # Lavel list context menu.
        labelMenu = QMenu()
        addActions(labelMenu, (edit, delete))
        self.labelList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.labelList.customContextMenuRequested.connect(
            self.popLabelListMenu)

        # Draw squares/rectangles
        self.drawSquaresOption = QAction('Draw Squares', self)
        self.drawSquaresOption.setShortcut('Ctrl+Shift+R')
        self.drawSquaresOption.setCheckable(True)
        self.drawSquaresOption.setChecked(settings.get(SETTING_DRAW_SQUARE, False))
        self.drawSquaresOption.triggered.connect(self.toogleDrawSquare)

        # Set Preset Shape
        PresetShapeOne = action('&프리셋 1번에 저장', self.inputPresetShapeOne,
                             'Ctrl+1', 'new', u'프리셋 1번에 저장')

        PresetShapeTwo = action('&프리셋 2번에 저장', self.inputPresetShapeTwo,
                             'Ctrl+2', 'new', u'프리셋 2번에 저장')

        PresetShapeThree = action('&프리셋 3번에 저장', self.inputPresetShapeThree,
                             'Ctrl+3', 'new', u'프리셋 3번에 저장')

        PresetShapeFour = action('&프리셋 4번에 저장', self.inputPresetShapeFour,
                             'Ctrl+4', 'new', u'프리셋 4번에 저장')

        PresetShapeFive = action('&프리셋 5번에 저장', self.inputPresetShapeFive,
                             'Ctrl+5', 'new', u'프리셋 5번에 저장')

        PresetShapeAutoInput = action('&자동 추가 라벨 저장', self.inputPresetAutoInputShape,
                             'Ctrl+F9', 'new', u'자동 추가 라벨 저장')

        # Store actions for further handling.
        self.actions = struct(save=save, save_format=save_format, saveAs=saveAs, open=open, close=close, resetAll = resetAll,
                              lineColor=color1, create=create, delete=delete, edit=edit, copy=copy,
                              createMode=createMode, editMode=editMode, advancedMode=advancedMode,
                              shapeLineColor=shapeLineColor, shapeFillColor=shapeFillColor,
                              zoom=zoom, zoomIn=zoomIn, zoomOut=zoomOut, zoomOrg=zoomOrg,
                              fitWindow=fitWindow, fitWidth=fitWidth,
                              zoomActions=zoomActions, autotrackingLabel=autotrackingLabel,
                              fileMenuActions=(
                                  open, opendir, save, saveAs, close, resetAll, quit),
                              beginner=(), advanced=(),
                              editMenu=(edit, copy, delete,
                                        None, color1, self.drawSquaresOption),
                              beginnerContext=(create, edit, copy, delete),
                              advancedContext=(createMode, editMode, edit, copy,
                                               delete, shapeLineColor, shapeFillColor),
                              onLoadActive=(
                                  close, create, createMode, editMode),
                              onShapesPresent=(saveAs, hideAll, showAll))

        self.menus = struct(
            file=self.menu('&File'),
            edit=self.menu('&Edit'),
            view=self.menu('&View'),
            mode=self.menu('&Mode'),
            shapemenu=self.menu('&Shape'),
            help=self.menu('&Help'),
            recentFiles=QMenu('Open &Recent'),
            labelList=labelMenu)

        # Auto saving : Enable auto saving if pressing next
        self.autoSaving = QAction(getStr('autoSaveMode'), self)
        self.autoSaving.setCheckable(True)
        self.autoSaving.setChecked(settings.get(SETTING_AUTO_SAVE, False))
        # Sync single class mode from PR#106
        self.singleClassMode = QAction(getStr('singleClsMode'), self)
        self.singleClassMode.setShortcut("Ctrl+Shift+S")
        self.singleClassMode.setCheckable(True)
        self.singleClassMode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))
        self.lastLabel = None
        # Add option to enable/disable labels being displayed at the top of bounding boxes
        self.displayLabelOption = QAction(getStr('displayLabel'), self)
        self.displayLabelOption.setShortcut("Ctrl+Shift+P")
        self.displayLabelOption.setCheckable(True)
        self.displayLabelOption.setChecked(settings.get(SETTING_PAINT_LABEL, False))
        self.displayLabelOption.triggered.connect(self.togglePaintLabelsOption)

        # Arrow Keys Mode
        self.modeGroup = QActionGroup(self)
        self.modeGroup.setExclusive(True)

        self.moveMode = QAction('&이동 모드', self)
        self.moveMode.setShortcut("z")
        self.moveMode.setChecked(True)
        self.moveMode.setCheckable(True)
        self.moveMode.triggered.connect(self.ArrowKeysMode)

        self.expansionMode = QAction('&확장 모드', self)
        self.expansionMode.setShortcut("x")
        self.expansionMode.setCheckable(True)
        self.expansionMode.triggered.connect(self.ArrowKeysMode)

        self.reductionMode = QAction('&축소 모드', self)
        self.reductionMode.setShortcut("c")
        self.reductionMode.setCheckable(True)
        self.reductionMode.triggered.connect(self.ArrowKeysMode)

        self.modeGroup.addAction(self.moveMode)
        self.modeGroup.addAction(self.expansionMode)
        self.modeGroup.addAction(self.reductionMode)

        # Q / E button Navigate Box
        self.nextAnnotationBox = QAction('&다음 박스 선택', self)
        self.nextAnnotationBox.setShortcut("e")
        self.nextAnnotationBox.triggered.connect(self.nextBoxSelect)

        self.prevAnnotationBox = QAction('&이전 박스 선택', self)
        self.prevAnnotationBox.setShortcut("q")
        self.prevAnnotationBox.triggered.connect(self.prevBoxSelect)

        #Filename save
        self.checkImageLog = QAction('&현재 파일이름 텍스트 문서에 저장', self)
        self.checkImageLog.setShortcut("m")
        self.checkImageLog.triggered.connect(self.appendFilenameLog)

        #Shape Preset Use
        self.PresetOne = QAction('&프리셋 1 사용', self)
        self.PresetOne.setShortcut("1")
        self.PresetOne.triggered.connect(self.usePresetShapeOne)

        self.PresetTwo = QAction('&프리셋 2 사용', self)
        self.PresetTwo.setShortcut("2")
        self.PresetTwo.triggered.connect(self.usePresetShapeTwo)

        self.PresetThree = QAction('&프리셋 3 사용', self)
        self.PresetThree.setShortcut("3")
        self.PresetThree.triggered.connect(self.usePresetShapeThree)

        self.PresetFour = QAction('&프리셋 4 사용', self)
        self.PresetFour.setShortcut("4")
        self.PresetFour.triggered.connect(self.usePresetShapeFour)

        self.PresetFive = QAction('&프리셋 5 사용', self)
        self.PresetFive.setShortcut("5")
        self.PresetFive.triggered.connect(self.usePresetShapeFive)

        # Rotate / Move / Cut
        self.horizontalcutShape = QAction('&가로방향 자르기', self)
        self.horizontalcutShape.setShortcut("f")
        self.horizontalcutShape.triggered.connect(self.horizontalcutShapemethod)

        self.verticalcutShape = QAction('&세로방향 자르기', self)
        self.verticalcutShape.setShortcut("v")
        self.verticalcutShape.triggered.connect(self.verticalcutShapemethod)

        self.crosscutShape = QAction('&십자 자르기', self)
        self.crosscutShape.setShortcut("g")
        self.crosscutShape.triggered.connect(self.crosscutShapemethod)

        self.resizeBox = QAction('&라벨 박스 클릭 수정', self)
        self.resizeBox.setShortcut("b")
        self.resizeBox.triggered.connect(self.resizeBoxmethod)

        # self.clickmoveShape = QAction('&라벨 박스 클릭 이동', self)
        # self.clickmoveShape.setShortcut("t")
        # self.clickmoveShape.triggered.connect(self.clickmoveShapemethod)

        self.rotateShape = QAction('&라벨 박스 회전', self)
        self.rotateShape.setShortcut("s")
        self.rotateShape.triggered.connect(self.rotateShapemethod)

        self.mergeShape = QAction('&라벨 박스 합치기', self)
        self.mergeShape.setShortcut("h")
        self.mergeShape.triggered.connect(self.mergeShapemethod)

        self.selectedShapeZoom = QAction('&선택된 라벨 확대', self)
        self.selectedShapeZoom.setShortcut("Shift+Z")
        self.selectedShapeZoom.triggered.connect(self.selectedShapeZoommethod)

        self.cancelCut = QAction('&자르기 활성화 취소', self)
        self.cancelCut.setShortcut("F12")
        self.cancelCut.triggered.connect(self.cancelCutmethod)

        self.changeShapeVisible = QAction('&선택한 라벨 보이기/숨기기', self)
        self.changeShapeVisible.setShortcut("U")
        self.changeShapeVisible.triggered.connect(self.changeShapeVisibleMethod)

        # Image Delete
        self.fileDelete = QAction('&파일 영구 삭제', self)
        self.fileDelete.setShortcut("Shift+Delete")
        self.fileDelete.triggered.connect(self.fileDeletemethod)

        #textfile open
        self.textfileOpen = QAction('&파일 이름 텍스트 문서 열기', self)
        self.textfileOpen.setShortcut("Shift+M")
        self.textfileOpen.triggered.connect(self.textfileOpenmethod)

        # save file restore
        self.rollbackSavefile = QAction('&이전 저장 파일 복원', self)
        self.rollbackSavefile.setShortcut("F8")
        self.rollbackSavefile.triggered.connect(self.rollbackSavefilemethod)

        # undo / redo Qaction
        self.undoShapes = QAction('&실행 취소', self)
        self.undoShapes.setShortcut("Ctrl+Z")
        self.undoShapes.triggered.connect(self.undomethod)

        self.redoShapes = QAction('&다시 실행', self)
        self.redoShapes.setShortcut("Ctrl+Y")
        self.redoShapes.triggered.connect(self.redomethod)

        # manual
        self.manualOpen = QAction('&labelimg 설명서', self)
        self.manualOpen.setShortcut("F1")
        self.manualOpen.triggered.connect(self.manualOpenmethod)

        addActions(self.menus.file,
                   (open, opendir, changeSavedir, openAnnotation, self.menus.recentFiles, openPrevXml, save, save_format, saveAs, close, resetAll, quit))
        addActions(self.menus.help, (help, showInfo, self.manualOpen))
        addActions(self.menus.mode, (
            self.moveMode, self.expansionMode, self.reductionMode, None, PresetShapeOne, PresetShapeTwo,
            PresetShapeThree, PresetShapeFour, PresetShapeFive, None, self.PresetOne, self.PresetTwo, self.PresetThree,
            self.PresetFour, self.PresetFive, None, PresetShapeAutoInput, self.checkImageLog, self.textfileOpen, self.fileDelete, self.rollbackSavefile))
        addActions(self.menus.shapemenu, (self.undoShapes, self.redoShapes, None,
        self.prevAnnotationBox, self.nextAnnotationBox, None, self.rotateShape, self.resizeBox, autotrackingLabel, self.selectedShapeZoom, None,
        self.horizontalcutShape, self.verticalcutShape, self.crosscutShape, self.mergeShape, self.cancelCut, None, self.changeShapeVisible))
        addActions(self.menus.view, (
            self.autoSaving,
            self.singleClassMode,
            self.displayLabelOption,
            labels, advancedMode, None,
            hideAll, showAll, None,
            zoomIn, zoomOut, zoomOrg, None,
            fitWindow, fitWidth))

        self.menus.file.aboutToShow.connect(self.updateFileMenu)

        # Custom context menu for the canvas widget:
        addActions(self.canvas.menus[0], self.actions.beginnerContext)
        addActions(self.canvas.menus[1], (
            action('&Copy here', self.copyShape),
            action('&Move here', self.moveShape)))

        self.tools = self.toolbar('Tools')
        self.actions.beginner = (
            open, opendir, changeSavedir, openNextImg, openPrevImg, verify, save, save_format, None, create, copy, delete, None,
            zoomIn, zoom, zoomOut, fitWindow, fitWidth)

        self.actions.advanced = (
            open, opendir, changeSavedir, openNextImg, openPrevImg, save, save_format, None,
            createMode, editMode, None,
            hideAll, showAll)

        self.statusBar().showMessage('%s started.' % __appname__)
        self.statusBar().show()

        # Application state.
        self.image = QImage()
        self.filePath = ustr(defaultFilename)
        self.recentFiles = []
        self.maxRecent = 7
        self.lineColor = None
        self.fillColor = None
        self.zoom_level = 100
        self.fit_window = False
        # Add Chris
        self.difficult = False

        ## Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recentFileQStringList = settings.get(SETTING_RECENT_FILES)
                self.recentFiles = [ustr(i) for i in recentFileQStringList]
            else:
                self.recentFiles = recentFileQStringList = settings.get(SETTING_RECENT_FILES)

        size = settings.get(SETTING_WIN_SIZE, QSize(600, 500))
        position = QPoint(0, 0)
        saved_position = settings.get(SETTING_WIN_POSE, position)
        # Fix the multiple monitors issue
        for i in range(QApplication.desktop().screenCount()):
            if QApplication.desktop().availableGeometry(i).contains(saved_position):
                position = saved_position
                break
        self.resize(size)
        self.move(position)
        saveDir = ustr(settings.get(SETTING_SAVE_DIR, None))
        self.lastOpenDir = ustr(settings.get(SETTING_LAST_OPEN_DIR, None))
        if self.defaultSaveDir is None and saveDir is not None and os.path.exists(saveDir):
            self.defaultSaveDir = saveDir
            self.statusBar().showMessage('%s started. Annotation will be saved to %s' %
                                         (__appname__, self.defaultSaveDir))
            self.statusBar().show()

        self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        Shape.line_color = self.lineColor = QColor(settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR))
        Shape.fill_color = self.fillColor = QColor(settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR))
        self.canvas.setDrawingColor(self.lineColor)
        # Add chris
        Shape.difficult = self.difficult

        def xbool(x):
            if isinstance(x, QVariant):
                return x.toBool()
            return bool(x)

        if xbool(settings.get(SETTING_ADVANCE_MODE, False)):
            self.actions.advancedMode.setChecked(True)
            self.toggleAdvancedMode()

        # Populate the File menu dynamically.
        self.updateFileMenu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.filePath and os.path.isdir(self.filePath):
            self.queueEvent(partial(self.importDirImages, self.filePath or ""))
        elif self.filePath:
            self.queueEvent(partial(self.loadFile, self.filePath or ""))

        # Callbacks:
        self.zoomWidget.valueChanged.connect(self.paintCanvas)

        self.populateModeActions()

        # Display cursor coordinates at the right of status bar
        self.labelCoordinates = QLabel('')
        self.statusBar().addPermanentWidget(self.labelCoordinates)

        # Open Dir if deafult file
        if self.filePath and os.path.isdir(self.filePath):
            self.openDirDialog(dirpath=self.filePath)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Control:
            self.canvas.setDrawingShapeToSquare(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Control:
            # Draw rectangle if Ctrl is pressed
            self.canvas.setDrawingShapeToSquare(True)

    def ArrowKeysMode(self):
        if self.moveMode.isChecked():
            self.canvas.setArrowKeysMode("Move")
            self.textBrowser.setText("이동 모드")
        if self.expansionMode.isChecked():
            self.canvas.setArrowKeysMode("Expansion")
            self.textBrowser.setText("확장 모드")
        if self.reductionMode.isChecked():
            self.canvas.setArrowKeysMode("Reduction")
            self.textBrowser.setText("축소 모드")

    def appendFilenameLog(self):
        today = datetime.now()
        todaystr = today.strftime('%Y%m%d')
        slashpoint = self.filePath.rfind('\\')
        filename = self.filePath[slashpoint + 1:]
        precontext = None
        dupcheck = False
        self.desctext = None

        logsavefolder = self.defaultSaveDir + '/SaveLog/'

        foldercheck = os.path.isdir(logsavefolder)
        if foldercheck is False:
            os.makedirs(logsavefolder)

        textname = logsavefolder + todaystr + "_Checklog.txt"
        texist = os.path.isfile(textname)

        if texist is True:
            prefile = open(textname, 'r', encoding="utf-8")
            precontext = prefile.read()
            prefile.close()

        if precontext is not None:
            if filename in precontext:
                dupcheck = True

        if dupcheck is True:
            self.infoMessage('Message', '이미 저장된 파일이름 입니다.')
            return
        else:
            userwant = self.descriptDialog()
            if userwant is True:
                if precontext is not None:
                    context = precontext + filename + " : " + self.desctext + "\n"
                else:
                    context = filename + " : " + self.desctext + "\n"

                open_write = open(textname, 'w', encoding="UTF-8")
                open_write.write(context)
                open_write.close()
            else:
                self.infoMessage('Message', '파일이름 저장이 취소되었습니다.')
                return

    def descriptDialog(self):
        text, ok = QInputDialog.getText(self, '파일 이름 저장', '이유를 적어주세요')
        if ok:
            self.desctext = str(text)
            return True
        else:
            return False

    def getAvailableTextEditor(self):
        osName = platform.system()

        if osName == 'Windows':
            return ['notepad.exe']
        elif osName == 'Linux':
            return ['xdg-open']

    def textfileOpenmethod(self):
        textmemoViewer = self.getAvailableTextEditor()
        textfilepath = self.defaultSaveDir + '/SaveLog/'
        textfilename = textfilepath + datetime.now().strftime('%Y%m%d') + "_Checklog.txt"

        if os.path.isfile(textfilename) is True:
            if textmemoViewer[0] == 'notepad.exe':
                subprocess.call(textmemoViewer[0] + " " + textfilename)
            elif textmemoViewer[0] == 'xdg-open':
                subprocess.check_call([textmemoViewer[0], textfilename])

        elif os.path.exists(textfilepath) is True and bool(os.listdir(textfilepath)) is True:
            self.infoMessage('Message', '오늘 저장된 메모 파일이 없습니다. 파일 탐색기를 실행합니다.')
            if textmemoViewer[0] == 'notepad.exe':
                os.startfile(textfilepath)
            elif textmemoViewer[0] == 'xdg-open':
                subprocess.check_call([textmemoViewer[0], textfilepath])

        else:
            self.infoMessage('Message', '저장된 메모 파일이 없습니다.')
            return

    # Color Select
    def presetColorSelect(self, text):
        if text.lower() in self.presetColor:
            color = self.presetColor[text.lower()]
        else:
            color = generateColorByText(text)

        return color

    def presetColorLightSelect(self, text):
        if text.lower() in self.presetColor_light:
            color = self.presetColor_light[text.lower()]
        else:
            color = generateColorByText(text)

        return color

    ## Support Functions ##
    def set_format(self, save_format):
        if save_format == FORMAT_PASCALVOC:
            self.actions.save_format.setText(FORMAT_PASCALVOC)
            self.actions.save_format.setIcon(newIcon("format_voc"))
            self.usingPascalVocFormat = True
            self.usingYoloFormat = False
            LabelFile.suffix = XML_EXT

        elif save_format == FORMAT_YOLO:
            self.actions.save_format.setText(FORMAT_YOLO)
            self.actions.save_format.setIcon(newIcon("format_yolo"))
            self.usingPascalVocFormat = False
            self.usingYoloFormat = True
            LabelFile.suffix = TXT_EXT

    def change_format(self):
        if self.usingPascalVocFormat: self.set_format(FORMAT_YOLO)
        elif self.usingYoloFormat: self.set_format(FORMAT_PASCALVOC)

    def noShapes(self):
        return not self.itemsToShapes

    def toggleAdvancedMode(self, value=True):
        self._beginner = not value
        self.canvas.setEditing(True)
        self.populateModeActions()
        self.editButton.setVisible(not value)
        if value:
            self.actions.createMode.setEnabled(True)
            self.actions.editMode.setEnabled(False)
            self.dock.setFeatures(self.dock.features() | self.dockFeatures)
        else:
            self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)

    def populateModeActions(self):
        if self.beginner():
            tool, menu = self.actions.beginner, self.actions.beginnerContext
        else:
            tool, menu = self.actions.advanced, self.actions.advancedContext
        self.tools.clear()
        addActions(self.tools, tool)
        self.canvas.menus[0].clear()
        addActions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (self.actions.create,) if self.beginner()\
            else (self.actions.createMode, self.actions.editMode)
        addActions(self.menus.edit, actions + self.actions.editMenu)

    def setBeginner(self):
        self.tools.clear()
        addActions(self.tools, self.actions.beginner)

    def setAdvanced(self):
        self.tools.clear()
        addActions(self.tools, self.actions.advanced)

    def setDirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)

    def setClean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)

    def toggleActions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queueEvent(self, function):
        QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def resetState(self):
        self.itemsToShapes.clear()
        self.shapesToItems.clear()
        self.labelList.clear()

        self.undoList = []
        self.redoList = []
        self.executedList.clear()

        self.cancelCutmethod()
        self.filePath = None
        self.imageData = None
        self.labelFile = None
        self.selectedItemrow = None
        self.canvas.resetState()
        self.labelCoordinates.clear()

    def currentItem(self):
        items = self.labelList.selectedItems()
        if items:
            return items[0]
        return None

    def addRecentFile(self, filePath):
        if filePath in self.recentFiles:
            self.recentFiles.remove(filePath)
        elif len(self.recentFiles) >= self.maxRecent:
            self.recentFiles.pop()
        self.recentFiles.insert(0, filePath)

    def beginner(self):
        return self._beginner

    def advanced(self):
        return not self.beginner()

    def getAvailableScreencastViewer(self):
        osName = platform.system()

        if osName == 'Windows':
            return ['C:\\Program Files\\Internet Explorer\\iexplore.exe']
        elif osName == 'Linux':
            return ['xdg-open']
        elif osName == 'Darwin':
            return ['open', '-a', 'Safari']

    ## Callbacks ##
    def showTutorialDialog(self):
        subprocess.Popen(self.screencastViewer + [self.screencast])

    def showInfoDialog(self):
        msg = u'Name:{0} \nApp Version:{1} \n{2} '.format(__appname__, __version__, sys.version_info)
        QMessageBox.information(self, u'Information', msg)

    def manualOpenmethod(self):
        textmemoViewer = self.getAvailableTextEditor()
        textfilename = os.path.join(os.path.dirname(sys.argv[0]), 'data', 'labelimg_manual.txt')

        if os.path.isfile(textfilename) is True:
            if textmemoViewer[0] == 'notepad.exe':
                subprocess.call(textmemoViewer[0] + " " + textfilename)
            elif textmemoViewer[0] == 'xdg-open':
                subprocess.check_call([textmemoViewer[0], textfilename])

    def createShape(self):
        assert self.beginner()
        self.canvas.setEditing(False)
        self.actions.create.setEnabled(False)

    def toggleDrawingSensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        if not drawing and self.beginner():
            # Cancel creation.
            print('Cancel creation.')
            self.canvas.setEditing(True)
            self.canvas.restoreCursor()
            self.actions.create.setEnabled(True)

    def toggleDrawMode(self, edit=True):
        self.canvas.setEditing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)

    def setCreateMode(self):
        assert self.advanced()
        self.toggleDrawMode(False)

    def setEditMode(self):
        assert self.advanced()
        self.toggleDrawMode(True)
        self.labelSelectionChanged()

    def updateFileMenu(self):
        currFilePath = self.filePath

        def exists(filename):
            return os.path.exists(filename)
        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recentFiles if f !=
                 currFilePath and exists(f)]
        for i, f in enumerate(files):
            icon = newIcon('labels')
            action = QAction(
                icon, '&%d %s' % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.loadRecent, f))
            menu.addAction(action)

    def popLabelListMenu(self, point):
        self.menus.labelList.exec_(self.labelList.mapToGlobal(point))

    def editLabel(self):
        if not self.canvas.editing():
            return
        item = self.currentItem()
        # if not item:
        #     return
        if len(self.canvas.selectedMultishape) < 1:
            return

        else:
            undotext = self.undoTextselect(self.canvas.selectedMultishape)
            if len(self.canvas.selectedMultishape) >= 2:
                text = self.labelDialog.popUp()
                if text is not None:
                    for shape in self.canvas.selectedMultishape:
                        shape.label = text
                        shape.line_color = self.presetColorSelect(text)
                        shape.fill_color = self.presetColorSelect(text)
                        multiitem = self.shapesToItems[shape]
                        self.labelList.item(self.labelList.row(multiitem)).setText(text)
                        self.labelList.item(self.labelList.row(multiitem)).setBackground(self.presetColorLightSelect(text))
                    self.setDirty()
                    self.undoAppend('라벨 이름 수정 (' + undotext + ") -> (" + text + ")")
                    return

            else:
                text = self.labelDialog.popUp(item.text())
                if text is not None:
                    item.setText(text)
                    item.setBackground(self.presetColorLightSelect(text))
                    self.canvas.selectedShape.fill_color = self.presetColorSelect(text)
                    self.setDirty()
                    self.undoAppend('라벨 이름 수정 (' + undotext + ") -> (" + self.canvas.selectedShape.label + ")")

    # Tzutalin 20160906 : Add file list and dock to move faster
    def fileitemDoubleClicked(self, item=None):
        if self.autoSaving.isChecked():
            if self.defaultSaveDir is not None:
                if self.dirty is True:
                    self.saveFile()
            else:
                self.changeSavedirDialog()
                return

        if not self.mayContinue():
            return

        currIndex = self.mImgList.index(ustr(item.text()))
        if currIndex < len(self.mImgList):
            filename = self.mImgList[currIndex]
            if filename:
                self.loadFile(filename)

    # Add chris
    def btnstate(self, item= None):
        """ Function to handle difficult examples
        Update on each object """
        if not self.canvas.editing():
            return

        item = self.currentItem()
        if not item: # If not selected Item, take the first one
            item = self.labelList.item(self.labelList.count()-1)

        difficult = self.diffcButton.isChecked()

        try:
            shape = self.itemsToShapes[item]
        except:
            pass
        # Checked and Update
        try:
            if difficult != shape.difficult:
                shape.difficult = difficult
                self.setDirty()
            else:  # User probably changed item visibility
                self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)
                if bool(item.checkState() == Qt.Unchecked) is True:
                    self.canvas.deSelectShape()
                if bool(item.checkState() == Qt.Checked) is True:
                    self.canvas.selectShape(shape)
        except:
            pass

    # React to canvas signals.
    def shapeSelectionChanged(self, selected=False):
        if self._noSelectionSlot:
            self._noSelectionSlot = False
        else:
            shape = self.canvas.selectedShape
            if shape:
                self.shapesToItems[shape].setSelected(True)
            else:
                self.labelList.clearSelection()
        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)

        if selected is False:
            self.actions.autotrackingLabel.setChecked(False)
            self.canvas.autotrackingMode = False
        self.actions.autotrackingLabel.setEnabled(selected)

    def shapeMultiSelection(self, selected=False):
        self.actions.delete.setEnabled(selected)
        self.actions.edit.setEnabled(selected)

    def addLabel(self, shape):
        shape.paintLabel = self.displayLabelOption.isChecked()
        item = HashableQListWidgetItem(shape.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setBackground(self.presetColorLightSelect(shape.label))
        self.itemsToShapes[item] = shape
        self.shapesToItems[shape] = item
        self.labelList.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)

    def addMultiLabel(self, shapelist):
        for shape in shapelist:
            shape.paintLabel = self.displayLabelOption.isChecked()
            item = HashableQListWidgetItem(shape.label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setBackground(self.presetColorLightSelect(shape.label))
            self.itemsToShapes[item] = shape
            self.shapesToItems[shape] = item
            self.labelList.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)

    def remLabel(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapesToItems[shape]
        self.labelList.takeItem(self.labelList.row(item))
        del self.shapesToItems[shape]
        del self.itemsToShapes[item]

    def loadLabels(self, shapes):
        s = []
        for label, points, line_color, fill_color, difficult in shapes:
            shape = Shape(label=label)
            for x, y in points:

                # Ensure the labels are within the bounds of the image. If not, fix them.
                x, y, snapped = self.canvas.snapPointToCanvas(x, y)
                if snapped:
                    self.setDirty()

                shape.addPoint(QPointF(x, y))
            shape.difficult = difficult
            shape.close()
            s.append(shape)

            if line_color:
                shape.line_color = QColor(*line_color)
            else:
                shape.line_color = self.presetColorSelect(label)

            if fill_color:
                shape.fill_color = QColor(*fill_color)
            else:
                shape.fill_color = self.presetColorSelect(label)

            self.addLabel(shape)

        self.canvas.loadShapes(s)

    def saveLabels(self, annotationFilePath):
        annotationFilePath = ustr(annotationFilePath)
        if self.labelFile is None:
            self.labelFile = LabelFile()
            self.labelFile.verified = self.canvas.verified

        def format_shape(s):
            return dict(label=s.label,
                        line_color=s.line_color.getRgb(),
                        fill_color=s.fill_color.getRgb(),
                        points=[(p.x(), p.y()) for p in s.points],
                       # add chris
                        difficult = s.difficult)

        shapes = [format_shape(shape) for shape in self.canvas.shapes]
        # Can add differrent annotation formats here
        try:
            if self.usingPascalVocFormat is True:
                if annotationFilePath[-4:].lower() != ".xml":
                    annotationFilePath += XML_EXT
                self.labelFile.savePascalVocFormat(annotationFilePath, shapes, self.filePath, self.imageData,
                                                   self.lineColor.getRgb(), self.fillColor.getRgb())
            elif self.usingYoloFormat is True:
                if annotationFilePath[-4:].lower() != ".txt":
                    annotationFilePath += TXT_EXT
                self.labelFile.saveYoloFormat(annotationFilePath, shapes, self.filePath, self.imageData, self.labelHist,
                                                   self.lineColor.getRgb(), self.fillColor.getRgb())
            else:
                self.labelFile.save(annotationFilePath, shapes, self.filePath, self.imageData,
                                    self.lineColor.getRgb(), self.fillColor.getRgb())
            if self.AutoInputStatus is False:
                print('Image:{0} -> Annotation:{1}'.format(self.filePath, annotationFilePath))
            return True
        except LabelFileError as e:
            self.errorMessage(u'Error saving label data', u'<b>%s</b>' % e)
            return False

    def copySelectedShape(self):
        if self.canvas.selectedShape:
            self.addLabel(self.canvas.copySelectedShape())
            # fix copy and delete
            self.shapeSelectionChanged(True)
            self.undoAppend('선택된 라벨 복제' + " (" + self.canvas.selectedShape.label + ")")

    def labelSelectionChanged(self):
        item = self.currentItem()
        if item and self.canvas.editing():
            self._noSelectionSlot = True
            self.canvas.selectShape(self.itemsToShapes[item])
            shape = self.itemsToShapes[item]
            # Add Chris
            self.diffcButton.setChecked(shape.difficult)
            self.selectedItemrow = self.labelList.row(self.shapesToItems[self.canvas.selectedShape])

    def labelItemChanged(self, item):
        shape = self.itemsToShapes[item]
        label = item.text()
        if label != shape.label:
            shape.label = item.text()
            shape.line_color = self.presetColorSelect(shape.label)
            self.setDirty()
        else:  # User probably changed item visibility
            self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)
            if bool(item.checkState() == Qt.Unchecked) is True:
                self.canvas.deSelectShape()
            if bool(item.checkState() == Qt.Checked) is True:
                self.canvas.selectShape(shape)

    def nextBoxSelect(self):
        if self.canvas.editing():
            if self.labelList.count() == 0:
                return
            elif self.labelList.count() == 1:
                self.canvas.deSelectShape()
                self.labelList.setCurrentItem(self.labelList.item(0))
                self.labelList.item(0).setSelected(True)
            else:
                if self.selectedItemrow is not None:
                    index = self.selectedItemrow
                else:
                    index = -1
                self.canvas.deSelectShape()
                if index + 1 < self.labelList.count():
                    self.labelList.setCurrentItem(self.labelList.item(index + 1))
                    self.labelList.item(index + 1).setSelected(True)
                else:
                    self.labelList.setCurrentItem(self.labelList.item(0))
                    self.labelList.item(0).setSelected(True)

    def prevBoxSelect(self):
        if self.canvas.editing():
            if self.labelList.count() == 0:
                return
            elif self.labelList.count() == 1:
                self.canvas.deSelectShape()
                self.labelList.setCurrentItem(self.labelList.item(0))
                self.labelList.item(0).setSelected(True)
            else:
                if self.selectedItemrow is not None:
                    index = self.selectedItemrow
                else:
                    index = 1
                totalrow = self.labelList.count()
                self.canvas.deSelectShape()
                if index > 0:
                    self.labelList.setCurrentItem(self.labelList.item(index - 1))
                    self.labelList.item(index - 1).setSelected(True)
                else:
                    self.labelList.setCurrentItem(self.labelList.item(totalrow - 1))
                    self.labelList.item(totalrow - 1).setSelected(True)

    def inputPresetShapeOne(self):
        if bool(self.canvas.selectedMultishape) is True:
            self.PresetShapeOne = []
            self.PresetShapeOne.extend(self.canvas.setPresetShape())
            if len(self.PresetShapeOne) == 1:
                self.presetBrowser1.setText(self.PresetShapeOne[0].label)
            else:
                self.presetBrowser1.setText(str(len(self.PresetShapeOne)) + ' Shapes')

    def inputPresetShapeTwo(self):
        if bool(self.canvas.selectedMultishape) is True:
            self.PresetShapeTwo = []
            self.PresetShapeTwo.extend(self.canvas.setPresetShape())
            if len(self.PresetShapeTwo) == 1:
                self.presetBrowser2.setText(self.PresetShapeTwo[0].label)
            else:
                self.presetBrowser2.setText(str(len(self.PresetShapeTwo)) + ' Shapes')

    def inputPresetShapeThree(self):
        if bool(self.canvas.selectedMultishape) is True:
            self.PresetShapeThree = []
            self.PresetShapeThree.extend(self.canvas.setPresetShape())
            if len(self.PresetShapeThree) == 1:
                self.presetBrowser3.setText(self.PresetShapeThree[0].label)
            else:
                self.presetBrowser3.setText(str(len(self.PresetShapeThree)) + ' Shapes')

    def inputPresetShapeFour(self):
        if bool(self.canvas.selectedMultishape) is True:
            self.PresetShapeFour = []
            self.PresetShapeFour.extend(self.canvas.setPresetShape())
            if len(self.PresetShapeFour) == 1:
                self.presetBrowser4.setText(self.PresetShapeFour[0].label)
            else:
                self.presetBrowser4.setText(str(len(self.PresetShapeFour)) + ' Shapes')

    def inputPresetShapeFive(self):
        if bool(self.canvas.selectedMultishape) is True:
            self.PresetShapeFive = []
            self.PresetShapeFive.extend(self.canvas.setPresetShape())
            if len(self.PresetShapeFive) == 1:
                self.presetBrowser5.setText(self.PresetShapeFive[0].label)
            else:
                self.presetBrowser5.setText(str(len(self.PresetShapeFive)) + ' Shapes')

    def inputPresetAutoInputShape(self):
        if bool(self.canvas.selectedMultishape) is True:
            self.AutoInputPresetShape = []
            self.AutoInputPresetShape.extend(self.canvas.setPresetShape())
            if len(self.AutoInputPresetShape) == 1:
                self.useAutoInputBrowser.setText(self.AutoInputPresetShape[0].label)
            else:
                self.useAutoInputBrowser.setText(str(len(self.AutoInputPresetShape)) + ' Shapes')

    def presetChecker(self, shapelist):
        checklist = []
        for preset in shapelist:
            xlist = [preset.points[0].x(), preset.points[1].x(), preset.points[2].x(), preset.points[3].x()]
            ylist = [preset.points[0].y(), preset.points[1].y(), preset.points[2].y(), preset.points[3].y()]
            imagewidth = self.canvas.pixmap.width()
            imageheight = self.canvas.pixmap.height()

            if max(xlist) <= imagewidth and max(ylist) <= imageheight:
                checklist.append(True)
            else:
                checklist.append(False)
        return checklist

    def usePresetShapeOne(self):
        print(sys.argv)
        if self.canvas.autotrackingMode is False:
            if bool(self.PresetShapeOne) is True:
                undotext = self.undoTextselect(self.PresetShapeOne)
                checklist = self.presetChecker(self.PresetShapeOne)

                if False not in checklist:
                    for inputpreset in self.PresetShapeOne:
                        self.addLabel(self.canvas.usePresetShape(inputpreset))
                        self.setDirty()
                    self.shapeSelectionChanged(True)

                    self.undoAppend('프리셋 1 입력' + " (" + undotext + ")")
                else:
                    self.infoMessage('Message', '설정된 프리셋의 좌표가 이미지 영역 밖에 있어 로드되지 않습니다.')
                    return
            else:
                self.infoMessage('Message', '프리셋이 저장되어 있지 않습니다.')
                return

    def usePresetShapeTwo(self):
        if self.canvas.autotrackingMode is False:
            if bool(self.PresetShapeTwo) is True:
                undotext = self.undoTextselect(self.PresetShapeTwo)
                checklist = self.presetChecker(self.PresetShapeTwo)

                if False not in checklist:
                    for inputpreset in self.PresetShapeTwo:
                        self.addLabel(self.canvas.usePresetShape(inputpreset))
                        self.setDirty()
                    self.shapeSelectionChanged(True)

                    self.undoAppend('프리셋 2 입력' + " (" + undotext + ")")
                else:
                    self.infoMessage('Message', '설정된 프리셋의 좌표가 이미지 영역 밖에 있어 로드되지 않습니다.')
                    return
            else:
                self.infoMessage('Message', '프리셋이 저장되어 있지 않습니다.')
                return

    def usePresetShapeThree(self):
        if self.canvas.autotrackingMode is False:
            if bool(self.PresetShapeThree) is True:
                undotext = self.undoTextselect(self.PresetShapeThree)
                checklist = self.presetChecker(self.PresetShapeThree)

                if False not in checklist:
                    for inputpreset in self.PresetShapeThree:
                        self.addLabel(self.canvas.usePresetShape(inputpreset))
                        self.setDirty()
                    self.shapeSelectionChanged(True)

                    self.undoAppend('프리셋 3 입력' + " (" + undotext + ")")
                else:
                    self.infoMessage('Message', '설정된 프리셋의 좌표가 이미지 영역 밖에 있어 로드되지 않습니다.')
                    return
            else:
                self.infoMessage('Message', '프리셋이 저장되어 있지 않습니다.')
                return

    def usePresetShapeFour(self):
        if self.canvas.autotrackingMode is False:
            if bool(self.PresetShapeFour) is True:
                undotext = self.undoTextselect(self.PresetShapeFour)
                checklist = self.presetChecker(self.PresetShapeFour)

                if False not in checklist:
                    for inputpreset in self.PresetShapeFour:
                        self.addLabel(self.canvas.usePresetShape(inputpreset))
                        self.setDirty()
                    self.shapeSelectionChanged(True)

                    self.undoAppend('프리셋 4 입력' + " (" + undotext + ")")
                else:
                    self.infoMessage('Message', '설정된 프리셋의 좌표가 이미지 영역 밖에 있어 로드되지 않습니다.')
                    return
            else:
                self.infoMessage('Message', '프리셋이 저장되어 있지 않습니다.')
                return

    def usePresetShapeFive(self):
        if self.canvas.autotrackingMode is False:
            if bool(self.PresetShapeFive) is True:
                undotext = self.undoTextselect(self.PresetShapeFive)
                checklist = self.presetChecker(self.PresetShapeFive)

                if False not in checklist:
                    for inputpreset in self.PresetShapeFive:
                        self.addLabel(self.canvas.usePresetShape(inputpreset))
                        self.setDirty()
                    self.shapeSelectionChanged(True)

                    self.undoAppend('프리셋 5 입력' + " (" + undotext + ")")
                else:
                    self.infoMessage('Message', '설정된 프리셋의 좌표가 이미지 영역 밖에 있어 로드되지 않습니다.')
                    return
            else:
                self.infoMessage('Message', '프리셋이 저장되어 있지 않습니다.')
                return

    def AutoInputPreset(self):
        currentshapeValues = self.itemsToShapes.values()
        currentshape = []
        addshape = []

        if len(currentshapeValues) > 0:
            for ccc in currentshapeValues:
                tempxy = []
                for xyxy in ccc.points:
                    tempxy.append((xyxy.x(), xyxy.y()))
                currentshape.append((ccc.label, tempxy, ccc.difficult))

        for adds in self.AutoInputPresetShape:
            tempxy = []
            for xyxyxy in adds.points:
                tempxy.append((int(xyxyxy.x()), int(xyxyxy.y())))
            addshape.append((adds.label, tempxy, adds.difficult))

        checklist = self.presetChecker(self.AutoInputPresetShape)

        if False not in checklist:
            for ii in range(0, len(addshape)):
                dupcheck = False
                for loop in currentshape:
                    if addshape[ii] == loop:
                        dupcheck = True
                        break
                if dupcheck is False:
                    self.addLabel(self.canvas.usePresetShape(self.AutoInputPresetShape[ii]))
                    self.shapeSelectionChanged(True)
            self.saveFile()
        else:
            self.infoMessage('Message', '복사할 라벨이 이미지 영역을 벗어납니다. 다시 저장해주세요.')
            return

    def horizontalcutShapemethod(self):
        if self.canvas.setClickmoveStatus is False and self.canvas.autotrackingMode is False:
            if self.canvas.selectedShape and self.horiStatus is True and self.vertStatus is False and self.crossStatus is False and self.resizeStatus is False:
                self.addLabel(self.canvas.Horizontalcut(self.canvas.prevCutpos))
                self.shapeSelectionChanged(True)
                self.horiStatus = False
                self.editBrowser.setText(" ")

                self.undoAppend('가로방향 자르기' + " (" + self.canvas.selectedShape.label + ")")

            elif self.canvas.selectedShape and self.vertStatus is False and self.crossStatus is False and self.resizeStatus is False:
                self.canvas.setHorizontalcutMode(True)
                self.horiStatus = True
                self.editBrowser.setText("가로방향 자르기 활성화")

    def verticalcutShapemethod(self):
        if self.canvas.setClickmoveStatus is False and self.canvas.autotrackingMode is False:
            if self.canvas.selectedShape and self.horiStatus is False and self.vertStatus is True and self.crossStatus is False and self.resizeStatus is False:
                self.addLabel(self.canvas.Verticalcut(self.canvas.prevCutpos))
                self.shapeSelectionChanged(True)
                self.vertStatus = False
                self.editBrowser.setText(" ")

                self.undoAppend('세로방향 자르기' + " (" + self.canvas.selectedShape.label + ")")

            elif self.canvas.selectedShape and self.horiStatus is False and self.crossStatus is False and self.resizeStatus is False:
                self.canvas.setVerticalcutMode(True)
                self.vertStatus = True
                self.editBrowser.setText("세로방향 자르기 활성화")

    def crosscutShapemethod(self):
        if self.canvas.setClickmoveStatus is False and self.canvas.autotrackingMode is False:
            if self.canvas.selectedShape and self.horiStatus is False and self.vertStatus is False and self.crossStatus is True and self.resizeStatus is False:
                self.addMultiLabel(self.canvas.Crosscut(self.canvas.prevCutpos))
                self.shapeSelectionChanged(True)
                self.crossStatus = False
                self.editBrowser.setText(" ")

                self.undoAppend('십자 자르기' + " (" + self.canvas.selectedShape.label + ")")

            elif self.canvas.selectedShape and self.horiStatus is False and self.vertStatus is False and self.resizeStatus is False:
                self.canvas.setCrosscutMode(True)
                self.crossStatus = True
                self.editBrowser.setText("십자 자르기 활성화")

    def resizeBoxmethod(self):
        if self.canvas.setClickmoveStatus is False and self.canvas.autotrackingMode is False:
            if self.canvas.selectedShape and self.horiStatus is False and self.vertStatus is False and self.crossStatus is False and self.resizeStatus is True:
                self.canvas.Resizebox(self.canvas.prevCutpos)
                self.resizeStatus = False
                self.editBrowser.setText(" ")

                self.undoAppend('라벨 박스 클릭 수정' + " (" + self.canvas.selectedShape.label + ")")

            elif self.canvas.selectedShape and self.horiStatus is False and self.vertStatus is False and self.crossStatus is False:
                self.canvas.setResizeboxMode(True)
                self.resizeStatus = True
                self.editBrowser.setText("라벨 박스 크기 수정 활성화")

    def autotrackingLabelmethod(self):
        if self.canvas.selectedShape:
            if self.actions.autotrackingLabel.isChecked():
                self.canvas.setClickmoveStatus = False
                self.canvas.autotrackingMode = True
                self.canvas.autotrackingStart()
            else:
                self.canvas.autotrackingMode = False
                self.canvas.autotrackingEnd()

    def clickmoveShapemethod(self):
        if self.canvas.selectedShape and self.horiStatus is False and self.vertStatus is False and self.crossStatus is False and self.resizeStatus is False:
            if self.canvas.autotrackingMode is False:
                self.canvas.setClickmoveStatus = True

    def rotateShapemethod(self):
        if self.canvas.selectedShape and self.canvas.autotrackingMode is False:
            self.canvas.rotateShape()

    def mergeShapemethod(self):
        if len(self.canvas.selectedMultishape) > 1:
            undotext = self.undoTextselect(self.canvas.selectedMultishape)
            labellist = []
            difficultlist = []
            for shape in self.canvas.selectedMultishape:
                labellist.append(shape.label)
                difficultlist.append(shape.difficult)

            if len(list(set(labellist))) > 1:
                text = self.labelDialog.popUp()
                if text is None:
                    return

            pickshape = self.canvas.mergeShape()
            if len(list(set(labellist))) > 1:
                if 'text' in locals():
                    if text is not None:
                        pickshape.label = text
                        pickshape.line_color = self.presetColorSelect(text)
                        multiitem = self.shapesToItems[pickshape]
                        self.labelList.item(self.labelList.row(multiitem)).setText(text)
                        self.labelList.item(self.labelList.row(multiitem)).setBackground(self.presetColorLightSelect(text))
            if len(list(set(difficultlist))) == 2:
                pickshape.difficult = True

            self.canvas.selectedMultishape.remove(pickshape)
            pickshape.multifill = False
            if len(self.canvas.selectedMultishape) >= 2:
                self.mergeShapeChecker = True
                self.deleteSelectedShape()
                self.mergeShapeChecker = False
            elif len(self.canvas.selectedMultishape) == 1:
                delshape = self.canvas.selectedMultishape[0]
                self.canvas.selectShapeMulti(delshape)
                self.remLabel(self.canvas.deleteSelected())
                self.setDirty()
                if self.noShapes():
                    for action in self.actions.onShapesPresent:
                        action.setEnabled(False)

            self.canvas.selectShape(pickshape)
            self.undoAppend('라벨 박스 합치기 (' + undotext + ") -> (" + self.canvas.selectedShape.label + ")")

    def selectedShapeZoommethod(self):
        if self.canvas.selectedShape:
            shape = self.canvas.selectedShape
            xlist = [shape.points[0].x(), shape.points[1].x(), shape.points[2].x(), shape.points[3].x()]
            ylist = [shape.points[0].y(), shape.points[1].y(), shape.points[2].y(), shape.points[3].y()]
            imagewidth = self.canvas.pixmap.width()
            imageheight = self.canvas.pixmap.height()

            hratio = imagewidth / (max(xlist) - min(xlist))
            vratio = imageheight / (max(ylist) - min(ylist))

            ratiolist = [hratio * 100, vratio * 100]
            zoomvalue = int(min(ratiolist))
            if int(min(ratiolist)) > 500:
                zoomvalue = 500

            self.setZoom(zoomvalue)

            xzoomcenter = ((max(xlist) + min(xlist)) / 2) * (zoomvalue/100)
            yzoomcenter = ((max(ylist) + min(ylist)) / 2) * (zoomvalue/100)

            hmax = self.scrollBars[Qt.Horizontal].maximum()
            vmax = self.scrollBars[Qt.Vertical].maximum()
            hpagestep = self.scrollBars[Qt.Horizontal].pageStep()
            vpagestep = self.scrollBars[Qt.Vertical].pageStep()

            if xzoomcenter <= (hpagestep / 2):
                hvalue = 0
            elif xzoomcenter >= hmax + (hpagestep / 2):
                hvalue = hmax
            else:
                hvalue = int(xzoomcenter - (hpagestep / 2))

            if yzoomcenter <= (vpagestep / 2):
                vvalue = 0
            elif yzoomcenter >= vmax + (vpagestep / 2):
                vvalue = vmax
            else:
                vvalue = int(yzoomcenter - (vpagestep / 2))

            self.scrollBars[Qt.Horizontal].setValue(int(hvalue))
            self.scrollBars[Qt.Vertical].setValue(int(vvalue))

    def cancelCutmethod(self):
        self.canvas.cancelCutShape()
        self.crossStatus = False
        self.horiStatus = False
        self.vertStatus = False
        self.resizeStatus = False
        self.editBrowser.setText(" ")

    def emptyLabeldelete(self):
        xmlpath = self.filePath[:-4] + ".xml"
        xmlexist = os.path.isfile(xmlpath)
        if xmlexist is True:
           os.remove(xmlpath)

    def fileDeletemethod(self):
        question = self.deleteMessage()
        imglistlength = len(self.mImgList)
        if question == QMessageBox.No:
            return
        else:
            deleteindex = self.mImgList.index(self.filePath)
            deletefilepath = self.filePath
            del self.mImgList[deleteindex]
            self.fileListWidget.takeItem(deleteindex)
            self.dirty = False
            self.appendDeletefilenameLog()

            xmlpath = deletefilepath[:-4] + ".xml"
            xmlexist = os.path.isfile(xmlpath)

            if xmlexist is True:
                os.remove(xmlpath)
            os.remove(deletefilepath)

            if imglistlength == 1:
                self.infoMessage('Message', '이미지가 한 장 뿐입니다. 삭제 후 폴더 열기로 넘어갑니다.')
                self.closeFile()
                self.openDirDialog()

            else:
                self.openIndexImg(deleteindex)

    def appendDeletefilenameLog(self):
        today = datetime.now()
        todaystr = today.strftime('%Y%m%d')
        precontext = None
        logsavefolder = self.defaultSaveDir + '/DeleteLog/'

        foldercheck = os.path.isdir(logsavefolder)
        if foldercheck is False:
            os.makedirs(logsavefolder)

        textname = logsavefolder + todaystr + "_deletefilelog.txt"
        texist = os.path.isfile(textname)

        if texist is True:
            prefile = open(textname, 'r', encoding="UTF-8")
            precontext = prefile.read()
            prefile.close()

        if precontext is not None:
            context = precontext + self.filePath + "\n"
        else:
            context = self.filePath + "\n"

        open_write = open(textname, 'w', encoding="UTF-8")
        open_write.write(context)
        open_write.close()

    def openIndexImg(self, imgindex, _value=False):
        try:
            filename = self.mImgList[imgindex]
        except IndexError:
            filename = self.mImgList[-1]

        self.loadFile(filename)

    def boxLockMode(self):
        if bool(self.vertexoffMode.isChecked()) is True:
            self.canvas.setVertexoffMode = True
        elif bool(self.vertexoffMode.isChecked()) is False:
            self.canvas.setVertexoffMode = False

        if bool(self.moveoffMode.isChecked()) is True:
            self.canvas.setMoveoffMode = True
        elif bool(self.moveoffMode.isChecked()) is False:
            self.canvas.setMoveoffMode = False

    def changeShapeVisibleMethod(self):
        if len(self.canvas.selectedMultishape) < 1:
            return
        else:
            if len(self.canvas.selectedMultishape) >= 2:
                indexlist = []
                for i in self.canvas.selectedMultishape:
                    indexlist.append(self.canvas.shapes.index(i))
                self.canvas.deSelectShape()

                for num in indexlist:
                    shape = self.canvas.shapes[num]
                    self.canvas.selectShape(shape)
                    item = self.labelList.item(num)

                    self.labelList.setCurrentItem(item)
                    item.setSelected(True)

                    if item.checkState() == Qt.Checked:
                        self.labelList.currentItem().setCheckState(Qt.Unchecked)
                        self.canvas.setShapeVisible(self.canvas.selectedShape, False)
                    self.canvas.deSelectShape()

            else:
                if self.canvas.selectedShape:
                    index = self.canvas.shapes.index(self.canvas.selectedShape)
                    item = self.labelList.item(index)

                    self.labelList.setCurrentItem(item)
                    item.setSelected(True)

                    if item.checkState() == Qt.Checked:
                        self.labelList.currentItem().setCheckState(Qt.Unchecked)
                        self.canvas.setShapeVisible(self.canvas.selectedShape, False)
                        self.canvas.deSelectShape()
                    else:
                        self.labelList.currentItem().setCheckState(Qt.Checked)
                        self.canvas.setShapeVisible(self.canvas.selectedShape, True)

# undo / redo method

    def undoShapedatamake(self):
        currentshapes = []
        for ccc in self.canvas.shapes:
            tempxy = []
            for xyxy in ccc.points:
                tempxy.append((xyxy.x(), xyxy.y()))
            currentshapes.append((ccc.label, tempxy, None, None, ccc.difficult))

        return currentshapes

    def undoShapevisible(self):
        visibleShapelist = []
        for i in range(0, len(self.canvas.shapes)):
            check = self.labelList.item(i).checkState()
            if check == Qt.Unchecked:
                visibleShapelist.append(i)

        return visibleShapelist

    def reloadShapes(self, currentshapes):
        self.itemsToShapes.clear()
        self.shapesToItems.clear()
        self.labelList.clear()
        self.labelCoordinates.clear()

        self.loadLabels(currentshapes)

        if self.labelList.count() > 0:
            self.labelList.setCurrentItem(self.labelList.item(self.labelList.count() - 1))
            self.labelList.item(self.labelList.count() - 1).setSelected(True)
        self.setDirty()

    # After Shapes Save
    def undoAppend(self, do_name):
        inputdata = self.undoShapedatamake()
        visibledata = self.undoShapevisible()
        self.undoList.append([do_name, inputdata, visibledata])
        self.redoList.clear()
        self.executedList_update()

    # Ctrl + Z
    def undomethod(self):
        if self.canvas.autotrackingMode is True:
            self.canvas.autotracking_undochecker = True
            self.canvas.autotrackingEnd()
        if len(self.undoList) > 1:
            shape = self.undoList.pop(-1)
            self.redoList.append(shape)
            self.reloadShapes(self.undoList[-1][1])
            for cc in self.undoList[-1][2]:
                item = self.labelList.item(cc)
                self.labelList.setCurrentItem(item)
                item.setSelected(True)
                self.labelList.currentItem().setCheckState(Qt.Unchecked)
                self.canvas.setShapeVisible(self.canvas.selectedShape, False)
                self.canvas.deSelectShape()
            self.executedList_update()
            self.cancelCutmethod()
            self.canvas.autotracking_undochecker = False

            if len(self.canvas.shapes) == 0:
                self.actions.delete.setEnabled(False)
                self.actions.copy.setEnabled(False)
                self.actions.edit.setEnabled(False)
                self.actions.shapeLineColor.setEnabled(False)
                self.actions.shapeFillColor.setEnabled(False)

                self.actions.autotrackingLabel.setChecked(False)
                self.canvas.autotrackingMode = False
                self.actions.autotrackingLabel.setEnabled(False)

    # Ctrl + Y
    def redomethod(self):
        if self.canvas.autotrackingMode is True:
            self.canvas.autotracking_undochecker = True
            self.canvas.autotrackingEnd()
        if len(self.redoList) > 0:
            shape = self.redoList.pop(-1)
            self.undoList.append(shape)
            self.reloadShapes(self.undoList[-1][1])
            for cc in self.undoList[-1][2]:
                item = self.labelList.item(cc)
                self.labelList.setCurrentItem(item)
                item.setSelected(True)
                self.labelList.currentItem().setCheckState(Qt.Unchecked)
                self.canvas.setShapeVisible(self.canvas.selectedShape, False)
                self.canvas.deSelectShape()
            self.executedList_update()
            self.cancelCutmethod()
            self.canvas.autotracking_undochecker = False

            if len(self.canvas.shapes) == 0:
                self.actions.delete.setEnabled(False)
                self.actions.copy.setEnabled(False)
                self.actions.edit.setEnabled(False)
                self.actions.shapeLineColor.setEnabled(False)
                self.actions.shapeFillColor.setEnabled(False)

                self.actions.autotrackingLabel.setChecked(False)
                self.canvas.autotrackingMode = False
                self.actions.autotrackingLabel.setEnabled(False)

    def executedList_update(self):
        self.executedList.clear()
        if bool(self.undoList) is True:
            for a in self.undoList:
                name = a[0]
                self.executedList.addItem(name)

        if bool(self.redoList) is True:
            for b in reversed(self.redoList):
                name = b[0]
                self.executedList.addItem(name)

        self.executedList.setCurrentItem(self.executedList.item(len(self.undoList) - 1))
        self.executedList.item(len(self.undoList) - 1).setSelected(True)

        currentitem = self.executedList.selectedItems()
        currentitem[0].setBackground(QColor(255, 125, 125))

    # def undoSelectionChanged(self):
    #     currentpos = len(self.undoList) - 1
    #     currentitem = self.executedList.item(currentpos)
        # print(currentpos)
        # print(currentitem)
        #currentitem[0].setBackground(QColor(255, 125, 125))
        # selecteditem = self.executedList.selectedItems()
        # print(self.undoList)
        # print(self.executedList.count())
        # print(selecteditem.)
        # # selecteditem[0].setBackground(QColor(255, 125, 125))
        # clickpos = self.executedList.row(selecteditem[0])
        #
        # if clickpos > currentpos:
        #     for aa in range(0, clickpos - currentpos):
        #         ss = self.redoList.pop(-1)
        #         self.undoList.append(ss)
        #     self.reloadShapes(self.undoList[-1][1])
        # elif clickpos < currentpos:
        #     for aa in range(0, currentpos - clickpos):
        #         ss = self.undoList.pop(-1)
        #         self.redoList.append(ss)
        #     self.reloadShapes(self.undoList[-1][1])

    def clickmoveUndosave(self):
        self.undoAppend('라벨 박스 클릭 이동' + " (" + self.canvas.selectedShape.label + ")")

    def rotateUndosave(self):
        self.undoAppend('라벨 박스 회전' + " (" + self.canvas.selectedShape.label + ")")

    def autotrackingUndosave(self):
        self.undoAppend('라벨 박스 자동 추적' + " (" + self.canvas.selectedShape.label + ")")

    def modifiedUndosave(self):
        if self.canvas.multiShapeMoveStatus is True and len(self.canvas.selectedMultishape) >= 2:
            undotext = self.undoTextselect(self.canvas.selectedMultishape)
            self.undoAppend('라벨 박스 위치 이동' + " (" + undotext + ")")

        if self.canvas.modifyingVertexStatus is True and self.canvas.multiShapeMoveStatus is False:
            self.undoAppend('라벨 박스 크기 수정' + " (" + self.canvas.selectedShape.label + ")")
        if self.canvas.modifyingShapeStatus is True and self.canvas.multiShapeMoveStatus is False:
            self.undoAppend('라벨 박스 위치 이동' + " (" + self.canvas.selectedShape.label + ")")

    def keypressUndosave(self):
        changeValue = self.canvas.arrowkeysPixelValue[0] + ' ' + str(self.canvas.arrowkeysPixelValue[1]) + ' 픽셀'

        if self.canvas.keypressChecker == 'Reduce':
            self.undoAppend('라벨 박스 크기 감소 - 방향키 (' + changeValue + ") (" + self.canvas.selectedShape.label + ")")
        elif self.canvas.keypressChecker == 'Expan':
            self.undoAppend('라벨 박스 크기 증가 - 방향키 (' + changeValue + ") (" + self.canvas.selectedShape.label + ")")
        else:
            self.undoAppend('라벨 박스 이동 - 방향키 (' + changeValue + ") (" + self.canvas.selectedShape.label + ")")

        self.canvas.keypressChecker = None

    def undoTextselect(self, shapes):
        labellist = []
        for s in shapes:
            labellist.append(s.label)

        if len(shapes) == 1:
            undotext = shapes[0].label
        elif len(shapes) > 1 and len(list(set(labellist))) > 1:
            undotext = str(len(shapes)) + ' Shapes (다중 라벨)'
        else:
            undotext = str(len(shapes)) + ' Shapes (' + shapes[0].label + ')'
        return undotext

    # file restore
    def rollbackSavefilemethod(self):
        question = self.rollbackMessage()
        if question == QMessageBox.No:
            return
        else:
            currentxmlpath = self.filePath[:-4] + '.xml'
            slashpoint = self.filePath.rfind('\\')
            filename = self.filePath[slashpoint + 1:-4] + '_backup.xml'
            path = self.defaultSaveDir + '/BackupXml/' + filename
            exist = os.path.isfile(path)
            if exist is True:
                os.remove(currentxmlpath)
                os.rename(path, currentxmlpath)
                tVocParseReader = PascalVocReader(currentxmlpath)
                prevshapes = tVocParseReader.getShapes()

                self.itemsToShapes.clear()
                self.shapesToItems.clear()
                self.labelList.clear()
                self.labelCoordinates.clear()

                self.loadLabels(prevshapes)

                self.labelList.setCurrentItem(self.labelList.item(self.labelList.count() - 1))
                self.labelList.item(self.labelList.count() - 1).setSelected(True)
                return
            else:
                self.infoMessage('Message', '저장된 파일이 없습니다.')
                return

    def automergeBackupxml(self, xmlpath):
        backupsavefolder = self.defaultSaveDir + '/BackupXml/'
        foldercheck = os.path.isdir(backupsavefolder)
        if foldercheck is False:
            os.makedirs(backupsavefolder)

        slashpoint = xmlpath.rfind('\\')
        filename = xmlpath[slashpoint + 1:-4] + '_backup.xml'
        backupPath = backupsavefolder + filename

        shutil.copy(xmlpath, backupPath)

    # Callback functions:
    def newShape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        if not self.useDefaultLabelCheckbox.isChecked() or not self.defaultLabelTextLine.text():
            if len(self.labelHist) > 0:
                self.labelDialog = LabelDialog(
                    parent=self, listItem=self.labelHist)

            # Sync single class mode from PR#106
            if self.singleClassMode.isChecked() and self.lastLabel:
                text = self.lastLabel
            else:
                text = self.labelDialog.popUp(text=self.prevLabelText)
                self.lastLabel = text
        else:
            text = self.defaultLabelTextLine.text()

        # Add Chris
        self.diffcButton.setChecked(False)
        if text is not None:
            self.prevLabelText = text
            generate_color = self.presetColorSelect(text)
            shape = self.canvas.setLastLabel(text, generate_color, generate_color)
            self.addLabel(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.setEditing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.setDirty()

            if text not in self.labelHist:
                self.labelHist.append(text)

            self.undoAppend('새 라벨 생성' + " (" + text + ")")
        else:
            # self.canvas.undoLastLine()
            self.canvas.resetAllLines()

    def scrollRequest(self, delta, orientation):
        units = - delta / (8 * 15)
        bar = self.scrollBars[orientation]
        bar.setValue(bar.value() + bar.singleStep() * units)

    def setZoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.MANUAL_ZOOM
        self.zoomWidget.setValue(value)

    def addZoom(self, increment=10):
        self.setZoom(self.zoomWidget.value() + increment)

    def zoomRequest(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scrollBars[Qt.Horizontal]
        v_bar = self.scrollBars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scrollArea.width()
        h = self.scrollArea.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta / (8 * 15)
        scale = 10
        self.addZoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = h_bar.value() + move_x * d_h_bar_max
        new_v_bar_value = v_bar.value() + move_y * d_v_bar_max

        h_bar.setValue(new_h_bar_value)
        v_bar.setValue(new_v_bar_value)

    def setFitWindow(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoomMode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjustScale()

    def setFitWidth(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjustScale()

    def togglePolygons(self, value):
        for item, shape in self.itemsToShapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def loadFile(self, filePath=None):
        """Load the specified file, or the last opened file if None."""
        self.resetState()
        self.canvas.setEnabled(False)
        if filePath is None:
            filePath = self.settings.get(SETTING_FILENAME)

        # Make sure that filePath is a regular python string, rather than QString
        filePath = ustr(filePath)

        unicodeFilePath = ustr(filePath)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if unicodeFilePath and self.fileListWidget.count() > 0:
            index = self.mImgList.index(unicodeFilePath)
            fileWidgetItem = self.fileListWidget.item(index)
            fileWidgetItem.setSelected(True)

        if unicodeFilePath and os.path.exists(unicodeFilePath):
            if LabelFile.isLabelFile(unicodeFilePath):
                try:
                    self.labelFile = LabelFile(unicodeFilePath)
                except LabelFileError as e:
                    self.errorMessage(u'Error opening file',
                                      (u"<p><b>%s</b></p>"
                                       u"<p>Make sure <i>%s</i> is a valid label file.")
                                      % (e, unicodeFilePath))
                    self.status("Error reading %s" % unicodeFilePath)
                    return False
                self.imageData = self.labelFile.imageData
                self.lineColor = QColor(*self.labelFile.lineColor)
                self.fillColor = QColor(*self.labelFile.fillColor)
                self.canvas.verified = self.labelFile.verified
            else:
                # Load image:
                # read data first and store for saving into label file.
                self.imageData = read(unicodeFilePath, None)
                self.labelFile = None
                self.canvas.verified = False

            image = QImage.fromData(self.imageData)
            if image.isNull():
                self.errorMessage(u'Error opening file',
                                  u"<p>Make sure <i>%s</i> is a valid image file." % unicodeFilePath)
                self.status("Error reading %s" % unicodeFilePath)
                return False
            self.status("Loaded %s" % os.path.basename(unicodeFilePath))
            self.image = image
            self.filePath = unicodeFilePath
            self.canvas.loadPixmap(QPixmap.fromImage(image))
            if self.labelFile:
                self.loadLabels(self.labelFile.shapes)
            self.setClean()
            self.canvas.setEnabled(True)
            self.adjustScale(initial=True)
            self.paintCanvas()
            self.addRecentFile(self.filePath)
            self.toggleActions(True)

            # Label xml file and show bound box according to its filename
            # if self.usingPascalVocFormat is True:
            if self.defaultSaveDir is not None:
                basename = os.path.basename(
                    os.path.splitext(self.filePath)[0])
                xmlPath = os.path.join(self.defaultSaveDir, basename + XML_EXT)
                txtPath = os.path.join(self.defaultSaveDir, basename + TXT_EXT)

                """Annotation file priority:
                PascalXML > YOLO
                """
                if os.path.isfile(xmlPath):
                    self.loadPascalXMLByFilename(xmlPath)
                elif os.path.isfile(txtPath):
                    self.loadYOLOTXTByFilename(txtPath)
            else:
                xmlPath = os.path.splitext(filePath)[0] + XML_EXT
                txtPath = os.path.splitext(filePath)[0] + TXT_EXT
                if os.path.isfile(xmlPath):
                    self.loadPascalXMLByFilename(xmlPath)
                elif os.path.isfile(txtPath):
                    self.loadYOLOTXTByFilename(txtPath)

            self.setWindowTitle(__appname__ + ' ' + filePath)

            # Default : select last item if there is at least one item
            if self.labelList.count():
                self.labelList.setCurrentItem(self.labelList.item(self.labelList.count()-1))
                self.labelList.item(self.labelList.count()-1).setSelected(True)

            self.canvas.setFocus(True)
            # current / total  number
            if bool(self.mImgList) is True:
                currentindex = self.mImgList.index(self.filePath) + 1
                total_length = (len(self.mImgList))
                indexstring = str(currentindex) + " / " + str(total_length)
                self.currenttotalBrowser.setText(indexstring)
            else:
                self.currenttotalBrowser.setText("1 / 1")
            # undo / redo setting
            self.undoAppend('파일 열기')
            return True
        return False

    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull()\
           and self.zoomMode != self.MANUAL_ZOOM:
            self.adjustScale()
        super(MainWindow, self).resizeEvent(event)

    def paintCanvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoomWidget.value()
        self.canvas.adjustSize()
        self.canvas.update()

    def adjustScale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoomMode]()
        self.zoomWidget.setValue(int(100 * value))

    def scaleFitWindow(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scaleFitWidth(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        if not self.mayContinue():
            event.ignore()
        settings = self.settings
        # If it loads images from dir, don't load it at the begining
        if self.dirname is None:
            settings[SETTING_FILENAME] = self.filePath if self.filePath else ''
        else:
            settings[SETTING_FILENAME] = ''

        settings[SETTING_WIN_SIZE] = self.size()
        settings[SETTING_WIN_POSE] = self.pos()
        settings[SETTING_WIN_STATE] = self.saveState()
        settings[SETTING_LINE_COLOR] = self.lineColor
        settings[SETTING_FILL_COLOR] = self.fillColor
        settings[SETTING_RECENT_FILES] = self.recentFiles
        settings[SETTING_ADVANCE_MODE] = not self._beginner
        if self.defaultSaveDir and os.path.exists(self.defaultSaveDir):
            settings[SETTING_SAVE_DIR] = ustr(self.defaultSaveDir)
        else:
            settings[SETTING_SAVE_DIR] = ''

        if self.lastOpenDir and os.path.exists(self.lastOpenDir):
            settings[SETTING_LAST_OPEN_DIR] = self.lastOpenDir
        else:
            settings[SETTING_LAST_OPEN_DIR] = ''

        settings[SETTING_AUTO_SAVE] = self.autoSaving.isChecked()
        settings[SETTING_SINGLE_CLASS] = self.singleClassMode.isChecked()
        settings[SETTING_PAINT_LABEL] = self.displayLabelOption.isChecked()
        settings[SETTING_DRAW_SQUARE] = self.drawSquaresOption.isChecked()
        settings.save()

    def loadRecent(self, filename):
        if self.mayContinue():
            self.loadFile(filename)

    def scanAllImages(self, folderPath):
        extensions = ['.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        images = []

        for root, dirs, files in os.walk(folderPath):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relativePath = os.path.join(root, file)
                    path = ustr(os.path.abspath(relativePath))
                    images.append(path)
        natural_sort(images, key=lambda x: x.lower())
        return images

    def changeSavedirDialog(self, _value=False):
        if self.defaultSaveDir is not None:
            path = ustr(self.defaultSaveDir)
        else:
            path = '.'

        dirpath = ustr(QFileDialog.getExistingDirectory(self,
                                                       '%s - Save annotations to the directory' % __appname__, path,  QFileDialog.ShowDirsOnly
                                                       | QFileDialog.DontResolveSymlinks))

        if dirpath is not None and len(dirpath) > 1:
            self.defaultSaveDir = dirpath

        self.statusBar().showMessage('%s . Annotation will be saved to %s' %
                                     ('Change saved folder', self.defaultSaveDir))
        self.statusBar().show()

    def openAnnotationDialog(self, _value=False):
        if self.filePath is None:
            self.statusBar().showMessage('Please select image first')
            self.statusBar().show()
            return

        path = os.path.dirname(ustr(self.filePath))\
            if self.filePath else '.'
        if self.usingPascalVocFormat:
            filters = "Open Annotation XML file (%s)" % ' '.join(['*.xml'])
            filename = ustr(QFileDialog.getOpenFileName(self,'%s - Choose a xml file' % __appname__, path, filters))
            if filename:
                if isinstance(filename, (tuple, list)):
                    filename = filename[0]
            self.loadPascalXMLByFilename(filename)

    def openPrevXml(self, _value=False):
        prevIndex = self.mImgList.index(self.filePath) - 1
        if prevIndex < 0:
            self.infoMessage('Message', '이 이미지가 현재 폴더의 첫번째 이미지입니다.')
            return

        else:
            shapes = []
            prevfilename = self.mImgList[prevIndex]
            prevfilename = prevfilename[:-3] + 'xml'

            previmagesize = previmageSize(prevfilename)
            imagesize = [self.canvas.pixmap.width(), self.canvas.pixmap.height()]
            if previmagesize[0] <= imagesize[0] and previmagesize[1] <= imagesize[1]:
                tVocParseReader = PascalVocReader(prevfilename)
                prevshapes = tVocParseReader.getShapes()

                currentshapeValues = self.itemsToShapes.values()
                currentshape = []

                if len(currentshapeValues) > 0:
                    for ccc in currentshapeValues:
                        tempxy = []
                        for xyxy in ccc.points:
                            tempxy.append((xyxy.x(), xyxy.y()))
                        currentshape.append((ccc.label, tempxy, None, None, ccc.difficult))

                for prev in prevshapes:
                    for cur in currentshape:
                        if cur == prev:
                            del currentshape[currentshape.index(cur)]

                shapes.extend(currentshape)
                shapes.extend(prevshapes)

                if bool(prevshapes) is True:
                    self.itemsToShapes.clear()
                    self.shapesToItems.clear()
                    self.labelList.clear()
                    self.labelCoordinates.clear()

                    self.loadLabels(shapes)

                    self.labelList.setCurrentItem(self.labelList.item(self.labelList.count()-1))
                    self.labelList.item(self.labelList.count()-1).setSelected(True)
                    self.setDirty()

                    self.undoAppend('이전 라벨 파일 덧붙이기')
                else:
                    self.infoMessage('Message', '이전 이미지의 라벨 파일이 없습니다.')
                    return
            else:
                self.infoMessage('Message', '이전 이미지가 현재 이미지보다 큽니다.')
                return

    def openDirDialog(self, _value=False, dirpath=None):
        if not self.mayContinue():
            return

        defaultOpenDirPath = dirpath if dirpath else '.'
        if self.lastOpenDir and os.path.exists(self.lastOpenDir):
            defaultOpenDirPath = self.lastOpenDir
        else:
            defaultOpenDirPath = os.path.dirname(self.filePath) if self.filePath else '.'

        targetDirPath = ustr(QFileDialog.getExistingDirectory(self,
                                                     '%s - Open Directory' % __appname__, defaultOpenDirPath,
                                                     QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks))
        self.defaultSaveDir = targetDirPath
        self.importDirImages(targetDirPath)

    def importDirImages(self, dirpath):
        if not self.mayContinue() or not dirpath:
            return

        self.lastOpenDir = dirpath
        self.dirname = dirpath
        self.filePath = None
        self.fileListWidget.clear()
        self.mImgList = self.scanAllImages(dirpath)
        self.openNextImg()
        for imgPath in self.mImgList:
            item = QListWidgetItem(imgPath)
            self.fileListWidget.addItem(item)

    def verifyImg(self, _value=False):
        # Proceding next image without dialog if having any label
        if self.filePath is not None:
            try:
                self.labelFile.toggleVerify()
            except AttributeError:
                # If the labelling file does not exist yet, create if and
                # re-save it with the verified attribute.
                self.saveFile()
                if self.labelFile != None:
                    self.labelFile.toggleVerify()
                else:
                    return

            self.canvas.verified = self.labelFile.verified
            self.paintCanvas()
            self.saveFile()

    def openPrevImg(self, _value=False):
        # Proceding prev image without dialog if having any label
        if self.autoSaving.isChecked():
            if self.defaultSaveDir is not None:
                if self.dirty is True:
                    self.saveFile()
            else:
                self.changeSavedirDialog()
                return

        if not self.mayContinue():
            return

        if len(self.mImgList) <= 0:
            return

        if self.filePath is None:
            return

        currIndex = self.mImgList.index(self.filePath)
        if currIndex - 1 >= 0:
            filename = self.mImgList[currIndex - 1]
            if filename:
                self.loadFile(filename)

    def openNextImg(self, _value=False):
        # Proceding prev image without dialog if having any label
        if self.autoSaving.isChecked():
            if self.defaultSaveDir is not None:
                if self.dirty is True:
                    self.saveFile()
            else:
                self.changeSavedirDialog()
                return

        if not self.mayContinue():
            return

        if len(self.mImgList) <= 0:
            return

        if self.useAutoInputCheckbox.isChecked():
            if self.AutoInputPresetShape is None:
                self.infoMessage('Message', '복사할 라벨이 저장되어 있지 않습니다.')
                return

        filename = None
        if self.filePath is None:
            filename = self.mImgList[0]
        else:
            currIndex = self.mImgList.index(self.filePath)
            if currIndex + 1 < len(self.mImgList):
                filename = self.mImgList[currIndex + 1]
                if self.xmlautocopyMode.isChecked():
                    pre_filename = self.mImgList[currIndex]
                    pre_xml_name = pre_filename[:-4] + '.xml'
                    next_xml_name = filename[:-4] + '.xml'

                    filesize = nextimageSize(filename)
                    if self.canvas.pixmap.width() <= filesize[0] and self.canvas.pixmap.height() <= filesize[1]:

                        pre_file_exist = os.path.isfile(pre_xml_name)
                        if pre_file_exist == True:
                            pre_object_checker = xml_object_finder(pre_xml_name)

                            if pre_object_checker == True:
                                next_file_exist = os.path.isfile(next_xml_name)
                                if next_file_exist == True:
                                    next_object_checker = xml_object_finder(next_xml_name)
                                    if next_object_checker == False:
                                        next_xml_maker(pre_xml_name, next_xml_name, filename)
                                    else:
                                        self.automergeBackupxml(next_xml_name)
                                        xmlmerge(pre_xml_name, next_xml_name, filename)
                                else:
                                    next_xml_maker(pre_xml_name, next_xml_name, filename)
                    else:
                        self.infoMessage('Message', '현재 이미지가 다음 이미지보다 큽니다. 파일 복사를 하지 않습니다.')

        if filename:
            self.loadFile(filename)

        if filename is not None:
            if self.useAutoInputCheckbox.isChecked():
                self.AutoInputStatus = True
                self.AutoInputPreset()
                self.AutoInputStatus = False

    def openFile(self, _value=False):
        if not self.mayContinue():
            return
        path = os.path.dirname(ustr(self.filePath)) if self.filePath else '.'
        formats = ['*.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(formats + ['*%s' % LabelFile.suffix])
        filename = QFileDialog.getOpenFileName(self, '%s - Choose Image or Label file' % __appname__, path, filters)
        if filename:
            if isinstance(filename, (tuple, list)):
                filename = filename[0]
            self.loadFile(filename)

    def saveFile(self, _value=False):
        savecheck = self.checkShapespoints()
        if savecheck is True:
            if self.defaultSaveDir is not None and len(ustr(self.defaultSaveDir)):
                if self.filePath:
                    imgFileName = os.path.basename(self.filePath)
                    savedFileName = os.path.splitext(imgFileName)[0]
                    savedPath = os.path.join(ustr(self.defaultSaveDir), savedFileName)
                    self._saveFile(savedPath)
            else:
                imgFileDir = os.path.dirname(self.filePath)
                imgFileName = os.path.basename(self.filePath)
                savedFileName = os.path.splitext(imgFileName)[0]
                savedPath = os.path.join(imgFileDir, savedFileName)
                self._saveFile(savedPath)
                #self._saveFile(savedPath if self.labelFile
                #              else self.saveFileDialog(removeExt=False))
        else:
            self.infoMessage('Message', '이미지 영역 밖에 라벨이 존재합니다. 수정해주세요.')
            return

    def saveFileAs(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._saveFile(self.saveFileDialog())

    def saveFileDialog(self, removeExt=True):
        caption = '%s - Choose File' % __appname__
        filters = 'File (*%s)' % LabelFile.suffix
        openDialogPath = self.currentPath()
        dlg = QFileDialog(self, caption, openDialogPath, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filenameWithoutExtension = os.path.splitext(self.filePath)[0]
        dlg.selectFile(filenameWithoutExtension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            fullFilePath = ustr(dlg.selectedFiles()[0])
            if removeExt:
                return os.path.splitext(fullFilePath)[0] # Return file path without the extension.
            else:
                return fullFilePath
        return ''

    def _saveFile(self, annotationFilePath):
        if annotationFilePath and self.saveLabels(annotationFilePath):
            self.setClean()
            self.statusBar().showMessage('Saved to  %s' % annotationFilePath)
            self.statusBar().show()

    def closeFile(self, _value=False):
        if not self.mayContinue():
            return
        self.resetState()
        self.setClean()
        self.toggleActions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    def resetAll(self):
        self.settings.reset()
        self.close()
        proc = QProcess()
        proc.startDetached(os.path.abspath(__file__))

    def mayContinue(self):
        return not (self.dirty and not self.discardChangesDialog())

    def checkShapespoints(self):
        for ss in self.canvas.shapes:
            ww = self.canvas.pixmap.width()
            hh = self.canvas.pixmap.height()
            xlist = [ss.points[0].x(), ss.points[1].x(), ss.points[2].x(), ss.points[3].x()]
            ylist = [ss.points[0].y(), ss.points[1].y(), ss.points[2].y(), ss.points[3].y()]

            if min(xlist) + 1 >= max(xlist) or min(ylist) + 1 >= max(ylist):
                return False
            elif 0 <= min(xlist) <= 1 and min(xlist) + 2 >= max(xlist):
                return False
            elif 0 <= min(ylist) <= 1 and min(ylist) + 2 >= max(ylist):
                return False
            elif ww - 1 <= max(xlist) <= ww and min(xlist) + 2 >= max(xlist):
                return False
            elif hh - 1 <= max(ylist) <= hh and min(ylist) + 2 >= max(ylist):
                return False

            # for pp in ss.points:
            #     if 0 <= pp.x() <= self.canvas.pixmap.width() and 0 <= pp.y() <= self.canvas.pixmap.height():
            #         continue
            #     else:
            #         return False
        return True

    def discardChangesDialog(self):
        yes, no = QMessageBox.Yes, QMessageBox.No
        msg = u'변경사항이 저장되지 않았습니다. 계속 하시겠습니까?'
        return yes == QMessageBox.warning(self, u'주의', msg, yes | no)

    def errorMessage(self, title, message):
        return QMessageBox.critical(self, title,
                                    '<p><b>%s</b></p>%s' % (title, message))

    def infoMessage(self, title, message):
        return QMessageBox.information(self, title, message)

    def deleteMessage(self):
        msg = u'현재 이미지와 라벨 파일이 삭제됩니다.'
        return QMessageBox.warning(self, u'이미지 파일 삭제', msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

    def rollbackMessage(self):
        msg = u'이전에 저장한 파일로 롤백합니다. 계속 하시겠습니까?'
        return QMessageBox.warning(self, u'저장 파일 복구', msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

    def currentPath(self):
        return os.path.dirname(self.filePath) if self.filePath else '.'

    def chooseColor1(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.lineColor = color
            Shape.line_color = color
            self.canvas.setDrawingColor(color)
            self.canvas.update()
            self.setDirty()

    def deleteSelectedShape(self):
        if len(self.canvas.selectedMultishape) >= 1:
            undotext = self.undoTextselect(self.canvas.selectedMultishape)

        if len(self.canvas.selectedMultishape) >= 2:
            indexlist = []
            for i in self.canvas.selectedMultishape:
                for j in self.canvas.shapes:
                    if i == j:
                        indexlist.append(self.canvas.shapes.index(j))

            for shape in self.canvas.selectedMultishape:
                self.canvas.selectShapeMulti(shape)
                self.remLabel(self.canvas.deleteSelected())
                self.setDirty()
                if self.noShapes():
                    for action in self.actions.onShapesPresent:
                        action.setEnabled(False)
            if len(self.canvas.shapes) > 0:
                self.canvas.selectShape(self.canvas.shapes[min(indexlist) - 1])

        else:
            delindex = self.canvas.shapes.index(self.canvas.selectedShape)
            if len(self.canvas.shapes) > 0:
                self.remLabel(self.canvas.deleteSelected())
                self.setDirty()
                if self.noShapes():
                    for action in self.actions.onShapesPresent:
                        action.setEnabled(False)
            if len(self.canvas.shapes) > 0:
                self.canvas.selectShape(self.canvas.shapes[delindex - 1])

        if self.mergeShapeChecker is False:
            self.undoAppend('라벨 박스 삭제 (' + undotext + ")")

        if len(self.canvas.shapes) == 0:
            self.shapeSelectionChanged(False)

    def chshapeLineColor(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.canvas.selectedShape.line_color = color
            self.canvas.update()
            self.setDirty()

    def chshapeFillColor(self):
        color = self.colorDialog.getColor(self.fillColor, u'Choose fill color',
                                          default=DEFAULT_FILL_COLOR)
        if color:
            self.canvas.selectedShape.fill_color = color
            self.canvas.update()
            self.setDirty()

    def copyShape(self):
        self.canvas.endMove(copy=True)
        self.addLabel(self.canvas.selectedShape)
        self.setDirty()

    def moveShape(self):
        self.canvas.endMove(copy=False)
        self.setDirty()

    def loadPredefinedClasses(self, predefClassesFile):
        if os.path.exists(predefClassesFile) is True:
            with codecs.open(predefClassesFile, 'r', 'utf8') as f:
                for line in f:
                    line = line.strip()
                    if self.labelHist is None:
                        self.labelHist = [line]
                    else:
                        self.labelHist.append(line)

    def loadPascalXMLByFilename(self, xmlPath):
        if self.filePath is None:
            return
        if os.path.isfile(xmlPath) is False:
            return

        self.set_format(FORMAT_PASCALVOC)

        tVocParseReader = PascalVocReader(xmlPath)
        shapes = tVocParseReader.getShapes()
        self.loadLabels(shapes)
        self.canvas.verified = tVocParseReader.verified

    def loadYOLOTXTByFilename(self, txtPath):
        if self.filePath is None:
            return
        if os.path.isfile(txtPath) is False:
            return

        self.set_format(FORMAT_YOLO)
        tYoloParseReader = YoloReader(txtPath, self.image)
        shapes = tYoloParseReader.getShapes()
        print (shapes)
        self.loadLabels(shapes)
        self.canvas.verified = tYoloParseReader.verified

    def togglePaintLabelsOption(self):
        for shape in self.canvas.shapes:
            shape.paintLabel = self.displayLabelOption.isChecked()

    def toogleDrawSquare(self):
        self.canvas.setDrawingShapeToSquare(self.drawSquaresOption.isChecked())

def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        with open(filename, 'rb') as f:
            return f.read()
    except:
        return default


def get_main_app(argv=[]):
    """
    Standard boilerplate Qt application code.
    Do everything but app.exec_() -- so that we can test the application in one thread
    """
    app = QApplication(argv)
    app.setApplicationName(__appname__)
    app.setWindowIcon(newIcon("app"))
    # Tzutalin 201705+: Accept extra agruments to change predefined class file
    # Usage : labelImg.py image predefClassFile saveDir
    win = MainWindow(argv[1] if len(argv) >= 2 else None,
                     argv[2] if len(argv) >= 3 else os.path.join(
                         os.path.dirname(sys.argv[0]),
                         'data', 'predefined_classes.txt'),
                     argv[3] if len(argv) >= 4 else None)
    win.show()
    return app, win


def main():
    '''construct main app and run it'''
    app, _win = get_main_app(sys.argv)
    return app.exec_()

if __name__ == '__main__':
    sys.exit(main())
