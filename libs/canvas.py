
try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

#from PyQt4.QtOpenGL import *

from libs.shape import Shape
from libs.utils import distance

CURSOR_DEFAULT = Qt.ArrowCursor
CURSOR_POINT = Qt.PointingHandCursor
CURSOR_DRAW = Qt.CrossCursor
CURSOR_MOVE = Qt.ClosedHandCursor
CURSOR_GRAB = Qt.OpenHandCursor

# class Canvas(QGLWidget):


class Canvas(QWidget):
    zoomRequest = pyqtSignal(int)
    scrollRequest = pyqtSignal(int, int)
    newShape = pyqtSignal()
    selectionChanged = pyqtSignal(bool)
    selectionMulti = pyqtSignal(bool)
    shapeMoved = pyqtSignal()
    drawingPolygon = pyqtSignal(bool)

    # for undo / redo signal
    rotateUndo = pyqtSignal()
    clickmoveUndo = pyqtSignal()
    modifyingUndo = pyqtSignal()
    keypressUndo = pyqtSignal()
    autotrackingUndo = pyqtSignal()

    CREATE, EDIT = list(range(2))

    epsilon = 11.0

    def __init__(self, *args, **kwargs):
        super(Canvas, self).__init__(*args, **kwargs)
        # Initialise local state.
        self.mode = self.EDIT
        self.shapes = []
        self.current = None
        self.selectedShape = None  # save the selected shape here
        self.selectedMultishape = []
        self.selectedShapeCopy = None
        self.drawingLineColor = QColor(0, 0, 255)
        self.drawingRectColor = QColor(0, 0, 255)
        self.line = Shape(line_color=self.drawingLineColor)
        self.prevPoint = QPointF()
        self.offsets = QPointF(), QPointF()
        self.scale = 1.0
        self.pixmap = QPixmap()
        self.visible = {}
        self._hideBackround = False
        self.hideBackround = False
        self.hShape = None
        self.hVertex = None
        self._painter = QPainter()
        self._cursor = CURSOR_DEFAULT
        # Menus:
        self.menus = (QMenu(), QMenu())
        # Set widget options.
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.WheelFocus)
        self.verified = False
        self.drawSquare = False
        self.setArrowKeysStatus = "Move"
        self.setClickmoveStatus = False
        self.prevCutpos = None
        self.setHorizontalcutStatus = False
        self.setVerticalcutStatus = False
        self.setCrosscutStatus = False
        self.setResizeboxStatus = False

        self.startDragpos = None
        self.endDragpos = None
        self.moveDragpos = None
        self.multiDragStatus = False

        #undo / redo - for shape move / vertex move / key press
        self.modifyingStatus = False
        self.modifyingShapeStatus = False
        self.modifyingVertexStatus = False
        self.keypressChecker = None
        self.multiShapeMoveStatus = False

        self.arrowkeysPixelValue = [None, None]
        self.autotrackingShapePoint = []
        self.autotrackingMode = False
        self.autotracking_undochecker = False

        # Lock Mode
        self.setVertexoffMode = False
        self.setMoveoffMode = False

    def setDrawingColor(self, qColor):
        self.drawingLineColor = qColor
        self.drawingRectColor = qColor

    def enterEvent(self, ev):
        self.overrideCursor(self._cursor)

    def leaveEvent(self, ev):
        self.restoreCursor()

    def focusOutEvent(self, ev):
        self.restoreCursor()

    def isVisible(self, shape):
        return self.visible.get(shape, True)

    def drawing(self):
        return self.mode == self.CREATE

    def editing(self):
        return self.mode == self.EDIT

    def setEditing(self, value=True):
        self.mode = self.EDIT if value else self.CREATE
        if not value:  # Create
            self.unHighlight()
            self.deSelectShape()
        self.prevPoint = QPointF()
        self.repaint()

    def unHighlight(self):
        if self.hShape:
            self.hShape.highlightClear()
        self.hVertex = self.hShape = None

    def selectedVertex(self):
        return self.hVertex is not None

    def selectedShapeVertex(self, point, epsilon, previndex):
        for i, p in enumerate(self.selectedShape.points):
            if distance(p - point) <= epsilon:
                return i
        return previndex

    def mouseMoveEvent(self, ev):
        """Update line with last point and current coordinates."""
        pos = self.transformPos(ev.pos())
        mod = ev.modifiers()

        # Update coordinates in status bar if image is opened
        window = self.parent().window()
        if window.filePath is not None:
            self.parent().window().labelCoordinates.setText(
                'X: %d; Y: %d' % (pos.x(), pos.y()))

        # Polygon drawing.
        if self.drawing():
            self.overrideCursor(CURSOR_DRAW)
            if self.current:
                color = self.drawingLineColor
                if self.outOfPixmap(pos):
                    # Don't allow the user to draw outside the pixmap.
                    # Project the point to the pixmap's edges.
                    pos = self.intersectionPoint(self.current[-1], pos)
                elif len(self.current) > 1 and self.closeEnough(pos, self.current[0]):
                    # Attract line to starting point and colorise to alert the
                    # user:
                    pos = self.current[0]
                    color = self.current.line_color
                    self.overrideCursor(CURSOR_POINT)
                    self.current.highlightVertex(0, Shape.NEAR_VERTEX)

                if self.drawSquare:
                    initPos = self.current[0]
                    minX = initPos.x()
                    minY = initPos.y()
                    min_size = min(abs(pos.x() - minX), abs(pos.y() - minY))
                    directionX = -1 if pos.x() - minX < 0 else 1
                    directionY = -1 if pos.y() - minY < 0 else 1
                    self.line[1] = QPointF(minX + directionX * min_size, minY + directionY * min_size)
                else:
                    self.line[1] = pos

                self.line.line_color = color
                self.prevPoint = QPointF()
                self.current.highlightClear()
            else:
                self.prevPoint = pos
            self.repaint()
            return

        # Polygon copy moving.
        if Qt.RightButton & ev.buttons():
            if self.selectedShapeCopy and self.prevPoint:
                self.overrideCursor(CURSOR_MOVE)
                self.boundedMoveShape(self.selectedShapeCopy, pos)
                self.repaint()
            elif self.selectedShape:
                self.selectedShapeCopy = self.selectedShape.copy()
                self.repaint()
            return

        # Polygon/Vertex moving.
        if Qt.LeftButton & ev.buttons():
            if mod != Qt.ShiftModifier:
                if self.setClickmoveStatus is False and self.setHorizontalcutStatus is False and self.setVerticalcutStatus is False and self.setCrosscutStatus is False and self.setResizeboxStatus is False:
                    if mod != Qt.ControlModifier:
                        if self.selectedVertex() and self.setVertexoffMode is False:
                            if self.modifyingStatus is False:
                                self.modifyingStatus = True
                                self.modifyingVertexStatus = True
                            self.boundedMoveVertex(pos)
                            self.shapeMoved.emit()
                            self.repaint()
                        elif self.selectedShape and self.prevPoint and self.setMoveoffMode is False:
                            if self.modifyingStatus is False:
                                self.modifyingStatus = True
                                self.modifyingShapeStatus = True
                            self.overrideCursor(CURSOR_MOVE)
                            self.boundedMoveShape(self.selectedShape, pos)
                            self.shapeMoved.emit()
                            self.repaint()

                    else:
                        if len(self.selectedMultishape) > 1 and self.prevPoint and self.multiShapeMoveStatus is True and self.setMoveoffMode is False:
                            if self.modifyingStatus is False:
                                self.modifyingStatus = True
                                self.modifyingShapeStatus = True
                            self.overrideCursor(CURSOR_MOVE)
                            self.boundedMoveShapeMulti(pos)
                            self.shapeMoved.emit()
                            self.repaint()

                return
            else:
                self.moveDragpos = pos
                self.repaint()

        # Autotracking
        if self.autotrackingMode is True and self.prevPoint and self.selectedShape and len(self.selectedMultishape) == 1:
            b_a = self.minmaxShapepoint(self.selectedShape)
            if b_a[0][0] <= pos.x() <= b_a[1][0] and b_a[0][1] <= pos.y() <= b_a[1][1]:
                self.boundedMoveShape(self.selectedShape, pos)
                self.shapeMoved.emit()
                self.repaint()
            else:
                self.trackingMagnetShape(pos)

        # Just hovering over the canvas, 2 posibilities:
        # - Highlight shapes
        # - Highlight vertex
        # Update shape/vertex fill and tooltip value accordingly.
        self.setToolTip("Image")
        for shape in reversed([s for s in self.shapes if self.isVisible(s)]):
            # Look for a nearby vertex to highlight. If that fails,
            # check if we happen to be inside a shape.

            ####
            if self.selectedShape:
                selectedpoints = self.minmaxShapepoint(self.selectedShape)
                selectedspace = self.selectedShapeBoundary(selectedpoints, self.epsilon, pos)
                if selectedspace is True:
                    shape = self.selectedShape
            ####

            index = shape.nearestVertex(pos, self.epsilon)
            if index is not None:
                if self.selectedVertex():
                    self.hShape.highlightClear()
                self.hVertex, self.hShape = index, shape
                shape.highlightVertex(index, shape.MOVE_VERTEX)
                self.overrideCursor(CURSOR_POINT)
                self.setToolTip("Click & drag to move point")
                self.setStatusTip(self.toolTip())
                self.update()
                break
            elif shape.containsPoint(pos):
                if self.selectedVertex():
                    self.hShape.highlightClear()
                self.hVertex, self.hShape = None, shape
                self.setToolTip(
                    "Click & drag to move shape '%s'" % shape.label)
                self.setStatusTip(self.toolTip())
                if self.setClickmoveStatus is False and self.setHorizontalcutStatus is False and self.setVerticalcutStatus is False and self.setCrosscutStatus is False and self.setResizeboxStatus is False:
                    self.overrideCursor(CURSOR_GRAB)
                else:
                    self.overrideCursor(CURSOR_DEFAULT)
                self.update()
                break
        else:  # Nothing found, clear highlights, reset state.
            if self.hShape:
                self.hShape.highlightClear()
                self.update()
            self.hVertex, self.hShape = None, None
            self.overrideCursor(CURSOR_DEFAULT)

    def mousePressEvent(self, ev):
        pos = self.transformPos(ev.pos())
        mod = ev.modifiers()

        if ev.button() == Qt.LeftButton:
            if self.drawing():
                self.handleDrawing(pos)

            elif self.setClickmoveStatus is True:
                if 0 < pos.x() < self.pixmap.width() and 0 < pos.y() < self.pixmap.height():
                    self.clickmoveShape(pos)
                    self.prevPoint = pos

            elif self.setHorizontalcutStatus is True or self.setVerticalcutStatus is True or self.setCrosscutStatus is True:
                if self.selectedShape:
                    xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(), self.selectedShape.points[2].x(), self.selectedShape.points[3].x()]
                    ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(), self.selectedShape.points[2].y(), self.selectedShape.points[3].y()]
                    if min(xlist) < pos.x() < max(xlist) and min(ylist) < pos.y() < max(ylist):
                        self.prevCutpos = pos

            elif self.setResizeboxStatus is True:
                if self.selectedShape:
                    if 0 < pos.x() < self.pixmap.width() and 0 < pos.y() < self.pixmap.height():
                        self.prevCutpos = pos

            elif mod == Qt.ShiftModifier:
                self.multiDragStatus = True
                self.startDragpos = pos

                # Multi shape status
                # Single shape status
                if len(self.selectedMultishape) >= 1:
                    self.selectShapePointShift(pos)
                    self.repaint()

                # None shape status
                else:
                    self.selectShapePoint(pos)
                    self.prevPoint = pos
                    self.repaint()

            elif mod == Qt.ControlModifier:
                if len(self.selectedMultishape) > 1 and self.prevPoint:
                    for ss in self.selectedMultishape:
                        pointlist = self.minmaxShapepoint(ss)
                        if pointlist[0][0] <= pos.x() <= pointlist[1][0] and pointlist[0][1] <= pos.y() <= pointlist[1][
                            1]:
                            self.multiShapeMoveStatus = True
                            self.prevPoint = pos
                            self.calculateMultiOffsets(pos)
                            break
                else:
                    self.selectShapePoint(pos)
                    self.prevPoint = pos
                    self.repaint()

            else:
                self.selectShapePoint(pos)
                self.prevPoint = pos
                self.repaint()

        elif ev.button() == Qt.RightButton and self.editing():
            self.selectShapePoint(pos)
            self.prevPoint = pos
            self.repaint()

    def mouseReleaseEvent(self, ev):
        pos = self.transformPos(ev.pos())
        mod = ev.modifiers()

        if ev.button() == Qt.RightButton:
            menu = self.menus[bool(self.selectedShapeCopy)]
            self.restoreCursor()
            if not menu.exec_(self.mapToGlobal(ev.pos()))\
               and self.selectedShapeCopy:
                # Cancel the move by deleting the shadow copy.
                self.selectedShapeCopy = None
                self.repaint()

        elif ev.button() == Qt.LeftButton and mod == Qt.ShiftModifier:
            self.endDragpos = pos
            self.multiDragStatus = False
            if bool(self.startDragpos) is True and bool(self.endDragpos) is True:
                self.dragSelectionShape()
            self.startDragpos = None
            self.endDragpos = None
            self.moveDragpos = None
            self.update()

        elif mod == Qt.ControlModifier:
            if ev.button() == Qt.LeftButton:
                if self.multiShapeMoveStatus is True and self.modifyingStatus is True:
                    self.modifyingUndo.emit()
                    self.modifyingStatus = False
                    self.modifyingShapeStatus = False
                    self.multiShapeMoveStatus = False

        elif ev.button() == Qt.LeftButton and self.selectedShape:
            if self.setClickmoveStatus is True:
                self.setClickmoveStatus = False

            if self.modifyingStatus is True:
                self.modifyingUndo.emit()
                self.modifyingStatus = False
                self.modifyingShapeStatus = False
                self.modifyingVertexStatus = False

            if self.selectedVertex():
                self.overrideCursor(CURSOR_POINT)
            else:
                self.overrideCursor(CURSOR_GRAB)
        elif ev.button() == Qt.LeftButton:
            pos = self.transformPos(ev.pos())
            if self.drawing():
                self.handleDrawing(pos)

    def endMove(self, copy=False):
        assert self.selectedShape and self.selectedShapeCopy
        shape = self.selectedShapeCopy
        #del shape.fill_color
        #del shape.line_color
        if copy:
            self.shapes.append(shape)
            self.selectedShape.selected = False
            self.selectedShape = shape
            self.repaint()
        else:
            self.selectedShape.points = [p for p in shape.points]
        self.selectedShapeCopy = None

    def hideBackroundShapes(self, value):
        self.hideBackround = value
        if self.selectedShape:
            # Only hide other shapes if there is a current selection.
            # Otherwise the user will not be able to select a shape.
            self.setHiding(True)
            self.repaint()

    def handleDrawing(self, pos):
        if self.current and self.current.reachMaxPoints() is False:
            initPos = self.current[0]
            minX = initPos.x()
            minY = initPos.y()
            targetPos = self.line[1]
            maxX = targetPos.x()
            maxY = targetPos.y()
            self.current.addPoint(QPointF(maxX, minY))
            self.current.addPoint(targetPos)
            self.current.addPoint(QPointF(minX, maxY))
            self.finalise()
        elif not self.outOfPixmap(pos):
            self.current = Shape()
            self.current.addPoint(pos)
            self.line.points = [pos, pos]
            self.setHiding()
            self.drawingPolygon.emit(True)
            self.update()

    def setHiding(self, enable=True):
        self._hideBackround = self.hideBackround if enable else False

    def canCloseShape(self):
        return self.drawing() and self.current and len(self.current) > 2

    def mouseDoubleClickEvent(self, ev):
        # We need at least 4 points here, since the mousePress handler
        # adds an extra one before this handler is called.
        if self.canCloseShape() and len(self.current) > 3:
            self.current.popPoint()
            self.finalise()

    def selectShape(self, shape, above=False):
        if above is False:
            self.deSelectShape()
            shape.selected = True
            self.selectedShape = shape
            self.selectedMultishape.append(shape)
            self.selectedMultishape[-1].multifill = True
        self.setHiding()
        self.selectionChanged.emit(True)
        self.update()

    def selectShapeMulti(self, shape):
        shape.selected = True
        self.selectedShape = shape

    def selectShapePoint(self, point):
        if self.selectedShape:
            selectedpoints = self.minmaxShapepoint(self.selectedShape)
            selectedspace = self.selectedShapeBoundary(selectedpoints, self.epsilon, point)
            if selectedspace is True:
                if self.selectedVertex():  # A vertex is marked for selection.
                    index, shape = self.hVertex, self.hShape
                    shape.highlightVertex(index, shape.MOVE_VERTEX)
                    if self.setVertexoffMode is True:
                        self.calculateOffsets(self.selectedShape, point)
                        self.selectShape(self.selectedShape, above=True)
                    return
                self.calculateOffsets(self.selectedShape, point)
                self.selectShape(self.selectedShape, above=True)
                return
            else:
                self.deSelectShape()
                if self.selectedVertex():  # A vertex is marked for selection.
                    index, shape = self.hVertex, self.hShape
                    shape.highlightVertex(index, shape.MOVE_VERTEX)
                    self.selectShape(shape)
                    return
                for shape in reversed(self.shapes):
                    if self.isVisible(shape) and shape.containsPoint(point):
                        self.selectShape(shape)
                        self.calculateOffsets(shape, point)
                        return
        else:
            self.deSelectShape()
            if self.selectedVertex():  # A vertex is marked for selection.
                index, shape = self.hVertex, self.hShape
                shape.highlightVertex(index, shape.MOVE_VERTEX)
                self.selectShape(shape)
                return
            for shape in reversed(self.shapes):
                if self.isVisible(shape) and shape.containsPoint(point):
                    self.selectShape(shape)
                    self.calculateOffsets(shape, point)
                    return

    def selectShapePointShift(self, point):
        for shape in reversed(self.shapes):
            if self.isVisible(shape) and shape.containsPoint(point):
                if len(self.selectedMultishape) > 1:
                    if shape not in self.selectedMultishape:
                        self.selectedMultishape.append(shape)
                        self.selectedMultishape[-1].multifill = True
                        self.update()
                        return
                    else:
                        self.selectedMultishape.remove(shape)
                        shape.multifill = False
                        if len(self.selectedMultishape) == 1:
                            self.selectShape(self.selectedMultishape[0])
                            self.selectionMulti.emit(False)
                            self.update()
                        return

                elif len(self.selectedMultishape) == 1:
                    if shape not in self.selectedMultishape:
                        originshape = self.selectedMultishape[0]
                        self.deSelectShape()
                        self.selectedMultishape.append(originshape)
                        self.selectedMultishape.append(shape)
                        self.selectedMultishape[0].multifill = True
                        self.selectedMultishape[1].multifill = True
                        self.selectionMulti.emit(True)
                        self.update()
                        return

    def dragSelectionShape(self):
        pos1 = self.startDragpos
        pos2 = self.endDragpos
        xlist = [pos1.x(), pos2.x()]
        ylist = [pos1.y(), pos2.y()]

        if len(self.shapes) > 0:
            for shape in self.shapes:
                if self.isVisible(shape):
                    xsetlist = set(range(int(min(xlist)), int(max(xlist))))
                    ysetlist = set(range(int(min(ylist)), int(max(ylist))))
                    pp = self.minmaxShapepoint(shape)
                    shapexlist = set(range(int(pp[0][0]), int(pp[1][0])))
                    shapeylist = set(range(int(pp[0][1]), int(pp[1][1])))
                    xinter = xsetlist & shapexlist
                    yinter = ysetlist & shapeylist
                    if bool(xinter) is True and bool(yinter) is True:
                        appendcheck = True
                    else:
                        appendcheck = False

                    if appendcheck is True:
                        if len(self.selectedMultishape) > 1:
                            if shape not in self.selectedMultishape:
                                self.selectedMultishape.append(shape)
                                self.selectedMultishape[-1].multifill = True
                                self.update()

                        elif len(self.selectedMultishape) == 1:
                            if shape not in self.selectedMultishape:
                                originshape = self.selectedMultishape[0]
                                self.deSelectShape()
                                self.selectedMultishape.append(originshape)
                                self.selectedMultishape.append(shape)
                                self.selectedMultishape[0].multifill = True
                                self.selectedMultishape[1].multifill = True
                                self.selectionMulti.emit(True)
                                self.update()

                        elif len(self.selectedMultishape) == 0:
                            self.selectShape(shape)
                            self.prevPoint = pos2

    def calculateOffsets(self, shape, point):
        rect = shape.boundingRect()
        x1 = rect.x() - point.x()
        y1 = rect.y() - point.y()
        x2 = (rect.x() + rect.width()) - point.x()
        y2 = (rect.y() + rect.height()) - point.y()
        self.offsets = QPointF(x1, y1), QPointF(x2, y2)

    def calculateMultiOffsets(self, point):
        if len(self.selectedMultishape) > 1:
            minmaxlist = self.multiShapeBoundary()
            x1 = minmaxlist[0] - point.x()
            y1 = minmaxlist[1] - point.y()
            x2 = minmaxlist[2] - point.x()
            y2 = minmaxlist[3] - point.y()
            self.offsets = QPointF(x1, y1), QPointF(x2, y2)

    def multiShapeBoundary(self):
        if len(self.selectedMultishape) > 1:
            xmin = self.pixmap.width()
            ymin = self.pixmap.height()
            xmax = 0
            ymax = 0
            for shape in self.selectedMultishape:
                pointlist = self.minmaxShapepoint(shape)
                if pointlist[0][0] < xmin:
                    xmin = pointlist[0][0]
                if pointlist[0][1] < ymin:
                    ymin = pointlist[0][1]
                if pointlist[1][0] > xmax:
                    xmax = pointlist[1][0]
                if pointlist[1][1] > ymax:
                    ymax = pointlist[1][1]

            return [xmin, ymin, xmax, ymax]

    def snapPointToCanvas(self, x, y):
        """
        Moves a point x,y to within the boundaries of the canvas.
        :return: (x,y,snapped) where snapped is True if x or y were changed, False if not.
        """
        if x < 0 or x > self.pixmap.width() or y < 0 or y > self.pixmap.height():
            x = max(x, 0)
            y = max(y, 0)
            x = min(x, self.pixmap.width())
            y = min(y, self.pixmap.height())
            return x, y, True

        return x, y, False

    def boundedMoveVertex(self, pos):
        index, shape = self.hVertex, self.hShape
        point = shape[index]
        if self.outOfPixmap(pos):
            pos = self.intersectionPoint(point, pos)

        if self.drawSquare:
            opposite_point_index = (index + 2) % 4
            opposite_point = shape[opposite_point_index]

            min_size = min(abs(pos.x() - opposite_point.x()), abs(pos.y() - opposite_point.y()))
            directionX = -1 if pos.x() - opposite_point.x() < 0 else 1
            directionY = -1 if pos.y() - opposite_point.y() < 0 else 1
            shiftPos = QPointF(opposite_point.x() + directionX * min_size - point.x(),
                               opposite_point.y() + directionY * min_size - point.y())
        else:
            shiftPos = pos - point

        shape.moveVertexBy(index, shiftPos)

        lindex = (index + 1) % 4
        rindex = (index + 3) % 4
        lshift = None
        rshift = None
        if index % 2 == 0:
            rshift = QPointF(shiftPos.x(), 0)
            lshift = QPointF(0, shiftPos.y())
        else:
            lshift = QPointF(shiftPos.x(), 0)
            rshift = QPointF(0, shiftPos.y())
        shape.moveVertexBy(rindex, rshift)
        shape.moveVertexBy(lindex, lshift)

    def boundedMoveShape(self, shape, pos):
        if self.selectedShape:
            if self.outOfPixmap(pos):
                return False  # No need to move
            o1 = pos + self.offsets[0]
            if self.outOfPixmap(o1):
                pos -= QPointF(min(0, o1.x()), min(0, o1.y()))
            o2 = pos + self.offsets[1]
            if self.outOfPixmap(o2):
                pos += QPointF(min(0, self.pixmap.width() - o2.x()),
                               min(0, self.pixmap.height() - o2.y()))
            # The next line tracks the new position of the cursor
            # relative to the shape, but also results in making it
            # a bit "shaky" when nearing the border and allows it to
            # go outside of the shape's area for some reason. XXX
            #self.calculateOffsets(self.selectedShape, pos)
            dp = pos - self.prevPoint
            if dp:
                shape.moveBy(dp)
                self.prevPoint = pos
                return True
            return False

    def boundedMoveShapeMulti(self, pos):
        if self.outOfPixmap(pos):
            return False  # No need to move

        o1 = pos + self.offsets[0]
        if self.outOfPixmap(o1):
            pos -= QPointF(min(0, o1.x()), min(0, o1.y()))
        o2 = pos + self.offsets[1]
        if self.outOfPixmap(o2):
            pos += QPointF(min(0, self.pixmap.width() - o2.x()),
                           min(0, self.pixmap.height() - o2.y()))

        dp = pos - self.prevPoint
        if dp:
            for shape in self.selectedMultishape:
                shape.moveBy(dp)
            self.prevPoint = pos
            return True
        return False

    def deSelectShape(self):
        for shape in self.selectedMultishape:
            shape.multifill = False
        if len(self.selectedMultishape) > 1:
            self.selectionMulti.emit(False)
            self.update()
        self.selectedMultishape = []
        if self.selectedShape:
            if self.autotrackingMode is True:
                self.autotrackingEnd()
            self.selectedShape.selected = False
            self.selectedShape = None
            self.setHiding(False)
            self.selectionChanged.emit(False)
            self.update()

    def deleteSelected(self):
        if self.selectedShape:
            shape = self.selectedShape
            self.shapes.remove(self.selectedShape)
            self.selectedShape = None
            self.update()
            return shape

    def copySelectedShape(self):
        if self.selectedShape:
            shape = self.selectedShape.copy()
            self.deSelectShape()
            self.shapes.append(shape)
            shape.selected = True
            self.selectedShape = shape
            self.boundedShiftShape(shape)
            return shape

    def boundedShiftShape(self, shape):
        # Try to move in one direction, and if it fails in another.
        # Give up if both fail.
        point = shape[0]
        offset = QPointF(2.0, 2.0)
        self.calculateOffsets(shape, point)
        self.prevPoint = point
        if not self.boundedMoveShape(shape, point - offset):
            self.boundedMoveShape(shape, point + offset)

    def setPresetShape(self):
        if bool(self.selectedMultishape) is True:
            shapelist = []
            for preset in self.selectedMultishape:
                shapelist.append(preset.copy())
            return shapelist

    def usePresetShape(self, shape):
        if shape is not None:
            self.deSelectShape()
            shape = shape.copy()
            self.shapes.append(shape)
            shape.selected = True
            self.selectedShape = shape
            diff = QPointF(0, 0)
            shape.moveBy(diff)
            return shape

    def paintEvent(self, event):
        if not self.pixmap:
            return super(Canvas, self).paintEvent(event)

        p = self._painter
        p.begin(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        p.scale(self.scale, self.scale)
        p.translate(self.offsetToCenter())

        p.drawPixmap(0, 0, self.pixmap)
        Shape.scale = self.scale
        for shape in self.shapes:
            if (shape.selected or not self._hideBackround) and self.isVisible(shape):
                shape.fill = shape.selected or shape == self.hShape
                shape.paint(p)
        if self.current:
            self.current.paint(p)
            self.line.paint(p)
        if self.selectedShapeCopy:
            self.selectedShapeCopy.paint(p)

        # Paint rect
        if self.current is not None and len(self.line) == 2:
            leftTop = self.line[0]
            rightBottom = self.line[1]
            rectWidth = rightBottom.x() - leftTop.x()
            rectHeight = rightBottom.y() - leftTop.y()
            p.setPen(self.drawingRectColor)
            brush = QBrush(Qt.BDiagPattern)
            p.setBrush(brush)
            p.drawRect(leftTop.x(), leftTop.y(), rectWidth, rectHeight)

        if self.multiDragStatus is True and bool(self.startDragpos) is True and bool(self.moveDragpos) is True:
            leftTop = self.startDragpos
            rightBottom = self.moveDragpos
            rectWidth = rightBottom.x() - leftTop.x()
            rectHeight = rightBottom.y() - leftTop.y()
            p.setPen(QColor(255, 178, 125))
            brush = QBrush(Qt.Dense6Pattern)
            p.setBrush(brush)
            p.drawRect(leftTop.x(), leftTop.y(), rectWidth, rectHeight)

        if self.drawing() and not self.prevPoint.isNull() and not self.outOfPixmap(self.prevPoint):
            p.setPen(QColor(0, 0, 0))
            p.drawLine(self.prevPoint.x(), 0, self.prevPoint.x(), self.pixmap.height())
            p.drawLine(0, self.prevPoint.y(), self.pixmap.width(), self.prevPoint.y())

        self.setAutoFillBackground(True)
        if self.verified:
            pal = self.palette()
            pal.setColor(self.backgroundRole(), QColor(184, 239, 38, 128))
            self.setPalette(pal)
        else:
            pal = self.palette()
            pal.setColor(self.backgroundRole(), QColor(232, 232, 232, 255))
            self.setPalette(pal)

        p.end()

    def transformPos(self, point):
        """Convert from widget-logical coordinates to painter-logical coordinates."""
        return point / self.scale - self.offsetToCenter()

    def offsetToCenter(self):
        s = self.scale
        area = super(Canvas, self).size()
        w, h = self.pixmap.width() * s, self.pixmap.height() * s
        aw, ah = area.width(), area.height()
        x = (aw - w) / (2 * s) if aw > w else 0
        y = (ah - h) / (2 * s) if ah > h else 0
        return QPointF(x, y)

    def outOfPixmap(self, p):
        w, h = self.pixmap.width(), self.pixmap.height()
        return not (0 <= p.x() <= w and 0 <= p.y() <= h)

    def finalise(self):
        assert self.current
        if self.current.points[0] == self.current.points[-1]:
            self.current = None
            self.drawingPolygon.emit(False)
            self.update()
            return

        self.current.close()
        self.shapes.append(self.current)
        self.current = None
        self.setHiding(False)
        self.newShape.emit()
        self.update()

    def closeEnough(self, p1, p2):
        #d = distance(p1 - p2)
        #m = (p1-p2).manhattanLength()
        # print "d %.2f, m %d, %.2f" % (d, m, d - m)
        return distance(p1 - p2) < self.epsilon

    def intersectionPoint(self, p1, p2):
        # Cycle through each image edge in clockwise fashion,
        # and find the one intersecting the current line segment.
        # http://paulbourke.net/geometry/lineline2d/
        size = self.pixmap.size()
        points = [(0, 0),
                  (size.width(), 0),
                  (size.width(), size.height()),
                  (0, size.height())]
        x1, y1 = p1.x(), p1.y()
        x2, y2 = p2.x(), p2.y()
        d, i, (x, y) = min(self.intersectingEdges((x1, y1), (x2, y2), points))
        x3, y3 = points[i]
        x4, y4 = points[(i + 1) % 4]
        if (x, y) == (x1, y1):
            # Handle cases where previous point is on one of the edges.
            if x3 == x4:
                return QPointF(x3, min(max(0, y2), max(y3, y4)))
            else:  # y3 == y4
                return QPointF(min(max(0, x2), max(x3, x4)), y3)

        # Ensure the labels are within the bounds of the image. If not, fix them.
        x, y, _ = self.snapPointToCanvas(x, y)

        return QPointF(x, y)

    def intersectingEdges(self, x1y1, x2y2, points):
        """For each edge formed by `points', yield the intersection
        with the line segment `(x1,y1) - (x2,y2)`, if it exists.
        Also return the distance of `(x2,y2)' to the middle of the
        edge along with its index, so that the one closest can be chosen."""
        x1, y1 = x1y1
        x2, y2 = x2y2
        for i in range(4):
            x3, y3 = points[i]
            x4, y4 = points[(i + 1) % 4]
            denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
            nua = (x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)
            nub = (x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)
            if denom == 0:
                # This covers two cases:
                #   nua == nub == 0: Coincident
                #   otherwise: Parallel
                continue
            ua, ub = nua / denom, nub / denom
            if 0 <= ua <= 1 and 0 <= ub <= 1:
                x = x1 + ua * (x2 - x1)
                y = y1 + ua * (y2 - y1)
                m = QPointF((x3 + x4) / 2, (y3 + y4) / 2)
                d = distance(m - QPointF(x2, y2))
                yield d, i, (x, y)

    def vertexPosition(self, shape):
        xlist = [shape.points[0].x(), shape.points[1].x(), shape.points[2].x(), shape.points[3].x()]
        ylist = [shape.points[0].y(), shape.points[1].y(), shape.points[2].y(), shape.points[3].y()]
        indexlist = [None, None, None, None]

        for i in range(0, 4):
            if xlist[i] == min(xlist) and ylist[i] == min(ylist):
                indexlist[0] = i
                continue
            if xlist[i] == max(xlist) and ylist[i] == min(ylist):
                indexlist[1] = i
                continue
            if xlist[i] == max(xlist) and ylist[i] == max(ylist):
                indexlist[2] = i
                continue
            if xlist[i] == min(xlist) and ylist[i] == max(ylist):
                indexlist[3] = i
                continue
        return indexlist

    def setArrowKeysMode(self, clicked):
        self.setArrowKeysStatus = clicked

    # These two, along with a call to adjustSize are required for the
    # scroll area.
    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        if self.pixmap:
            return self.scale * self.pixmap.size()
        return super(Canvas, self).minimumSizeHint()

    def wheelEvent(self, ev):
        qt_version = 4 if hasattr(ev, "delta") else 5
        if qt_version == 4:
            if ev.orientation() == Qt.Vertical:
                v_delta = ev.delta()
                h_delta = 0
            else:
                h_delta = ev.delta()
                v_delta = 0
        else:
            delta = ev.angleDelta()
            h_delta = delta.x()
            v_delta = delta.y()

        mods = ev.modifiers()
        if Qt.ControlModifier == int(mods) and v_delta:
            self.zoomRequest.emit(v_delta)
        else:
            v_delta and self.scrollRequest.emit(v_delta, Qt.Vertical)
            h_delta and self.scrollRequest.emit(h_delta, Qt.Horizontal)
        ev.accept()

    def keyPressEvent(self, ev):
        key = ev.key()
        mod = ev.modifiers()
        if key == Qt.Key_Escape and self.current:
            print('ESC press')
            self.current = None
            self.drawingPolygon.emit(False)
            self.update()
        elif key == Qt.Key_Return and self.canCloseShape():
            self.finalise()

        elif key == Qt.Key_Left and mod == Qt.ShiftModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('ShiftLeft')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('ShiftLeftExp')
            else:
                self.moveOnePixel('ShiftLeftRedu')
        elif key == Qt.Key_Right and mod == Qt.ShiftModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('ShiftRight')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('ShiftRightExp')
            else:
                self.moveOnePixel('ShiftRightRedu')
        elif key == Qt.Key_Up and mod == Qt.ShiftModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('ShiftUp')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('ShiftUpExp')
            else:
                self.moveOnePixel('ShiftUpRedu')
        elif key == Qt.Key_Down and mod == Qt.ShiftModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('ShiftDown')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('ShiftDownExp')
            else:
                self.moveOnePixel('ShiftDownRedu')

        elif key == Qt.Key_Left and mod == Qt.ControlModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('CtrlLeft')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('CtrlLeftExp')
            else:
                self.moveOnePixel('CtrlLeftRedu')
        elif key == Qt.Key_Right and mod == Qt.ControlModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('CtrlRight')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('CtrlRightExp')
            else:
                self.moveOnePixel('CtrlRightRedu')
        elif key == Qt.Key_Up and mod == Qt.ControlModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('CtrlUp')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('CtrlUpExp')
            else:
                self.moveOnePixel('CtrlUpRedu')
        elif key == Qt.Key_Down and mod == Qt.ControlModifier and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('CtrlDown')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('CtrlDownExp')
            else:
                self.moveOnePixel('CtrlDownRedu')

        elif key == Qt.Key_Left and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('Left')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('LeftExp')
            else:
                self.moveOnePixel('LeftRedu')
        elif key == Qt.Key_Right and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('Right')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('RightExp')
            else:
                self.moveOnePixel('RightRedu')
        elif key == Qt.Key_Up and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('Up')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('UpExp')
            else:
                self.moveOnePixel('UpRedu')
        elif key == Qt.Key_Down and self.selectedShape and self.autotrackingMode is False:
            if self.setArrowKeysStatus == "Move":
                self.moveOnePixel('Down')
            elif self.setArrowKeysStatus == "Expansion":
                self.moveOnePixel('DownExp')
            else:
                self.moveOnePixel('DownRedu')

    def moveOnePixel(self, direction, reversed=False):
        ilist = self.vertexPosition(self.selectedShape)
        xvalue = 0
        yvalue = 0
        changeStatus = False
        changeValue = 1.0
        changeMethod = 0
        directionVar = None
        self.arrowkeysPixelValue = [None, None]

        # Mode / Value / Direction
        if "Exp" in direction:
            changeMethod = 1
        elif "Redu" in direction:
            changeMethod = 2

        if "Shift" in direction:
            changeValue *= 10
        elif "Ctrl" in direction:
            changeValue *= 100

        if "Left" in direction:
            xvalue = -changeValue
            directionVar = [[0, 3], [1, 2], '-x']
            self.arrowkeysPixelValue[0] = '왼쪽'
            if changeMethod == 2 and reversed is False:
                self.arrowkeysPixelValue[0] = '오른쪽'
        elif "Right" in direction:
            xvalue = changeValue
            directionVar = [[1, 2], [0, 3], '+x']
            self.arrowkeysPixelValue[0] = '오른쪽'
            if changeMethod == 2 and reversed is False:
                self.arrowkeysPixelValue[0] = '왼쪽'
        elif "Up" in direction:
            yvalue = -changeValue
            directionVar = [[0, 1], [2, 3], '-y']
            self.arrowkeysPixelValue[0] = '위쪽'
            if changeMethod == 2 and reversed is False:
                self.arrowkeysPixelValue[0] = '아래쪽'
        elif "Down" in direction:
            yvalue = changeValue
            directionVar = [[2, 3], [0, 1], '+y']
            self.arrowkeysPixelValue[0] = '아래쪽'
            if changeMethod == 2 and reversed is False:
                self.arrowkeysPixelValue[0] = '위쪽'

        # Move or Expansion
        if changeMethod == 0 or changeMethod == 1:
            if not self.moveOutOfBound(QPointF(xvalue, yvalue)):
                changeStatus = True

            elif directionVar[2] == '-x' and self.selectedShape.points[ilist[0]].x() - changeValue < 0 and \
                    self.selectedShape.points[ilist[0]].x() > 0:
                xvalue = -(self.selectedShape.points[ilist[0]].x())
                changeStatus = True

            elif directionVar[2] == '+x' and self.selectedShape.points[
                ilist[1]].x() + changeValue > self.pixmap.width() and self.selectedShape.points[
                ilist[1]].x() < self.pixmap.width():
                xvalue = (self.pixmap.width() - self.selectedShape.points[ilist[1]].x())
                changeStatus = True

            elif directionVar[2] == '-y' and self.selectedShape.points[ilist[0]].y() - changeValue < 0 and \
                    self.selectedShape.points[ilist[0]].y() > 0:
                yvalue = -(self.selectedShape.points[ilist[0]].y())
                changeStatus = True

            elif directionVar[2] == '+y' and self.selectedShape.points[
                ilist[3]].y() + changeValue > self.pixmap.height() and self.selectedShape.points[
                ilist[3]].y() < self.pixmap.height():
                yvalue = (self.pixmap.height() - self.selectedShape.points[ilist[3]].y())
                changeStatus = True

            if changeStatus is True and changeMethod == 0:
                self.selectedShape.points[ilist[0]] += QPointF(xvalue, yvalue)
                self.selectedShape.points[ilist[1]] += QPointF(xvalue, yvalue)
                self.selectedShape.points[ilist[2]] += QPointF(xvalue, yvalue)
                self.selectedShape.points[ilist[3]] += QPointF(xvalue, yvalue)

            if changeStatus is True and changeMethod == 1:
                self.selectedShape.points[ilist[directionVar[0][0]]] += QPointF(xvalue, yvalue)
                self.selectedShape.points[ilist[directionVar[0][1]]] += QPointF(xvalue, yvalue)

        # Reduction (Normal)
        if changeMethod == 2 and reversed is False:
            if directionVar[2] == '-x' or directionVar[2] == '+x':
                if self.selectedShape.points[ilist[0]].x() + changeValue < self.selectedShape.points[ilist[1]].x():
                    self.selectedShape.points[ilist[directionVar[1][0]]] += QPointF(xvalue, yvalue)
                    self.selectedShape.points[ilist[directionVar[1][1]]] += QPointF(xvalue, yvalue)
                    changeStatus = True

            elif directionVar[2] == '-y' or directionVar[2] == '+y':
                if self.selectedShape.points[ilist[0]].y() + changeValue < self.selectedShape.points[ilist[3]].y():
                    self.selectedShape.points[ilist[directionVar[1][0]]] += QPointF(xvalue, yvalue)
                    self.selectedShape.points[ilist[directionVar[1][1]]] += QPointF(xvalue, yvalue)
                    changeStatus = True

        # Reduction (Reversed)
        if changeMethod == 2 and reversed is True:
            if directionVar[2] == '-x' or directionVar[2] == '+x':
                if self.selectedShape.points[ilist[0]].x() + changeValue < self.selectedShape.points[ilist[1]].x():
                    self.selectedShape.points[ilist[directionVar[0][0]]] -= QPointF(xvalue, yvalue)
                    self.selectedShape.points[ilist[directionVar[0][1]]] -= QPointF(xvalue, yvalue)
                    changeStatus = True

            elif directionVar[2] == '-y' or directionVar[2] == '+y':
                if self.selectedShape.points[ilist[0]].y() + changeValue < self.selectedShape.points[ilist[3]].y():
                    self.selectedShape.points[ilist[directionVar[0][0]]] -= QPointF(xvalue, yvalue)
                    self.selectedShape.points[ilist[directionVar[0][1]]] -= QPointF(xvalue, yvalue)
                    changeStatus = True

        if changeStatus is True:
            if "Redu" in direction:
                self.keypressChecker = 'Reduce'
            elif "Exp" in direction:
                self.keypressChecker = 'Expan'
            else:
                self.keypressChecker = 'Move'

            if directionVar[2] == '-x' or directionVar[2] == '+x':
                self.arrowkeysPixelValue[1] = int(abs(xvalue))
            else:
                self.arrowkeysPixelValue[1] = int(abs(yvalue))

            self.keypressUndo.emit()
            self.shapeMoved.emit()
            self.repaint()

    def setHorizontalcutMode(self, clicked):
        self.setHorizontalcutStatus = clicked

    def setVerticalcutMode(self, clicked):
        self.setVerticalcutStatus = clicked

    def setCrosscutMode(self, clicked):
        self.setCrosscutStatus = clicked

    def setResizeboxMode(self, clicked):
        self.setResizeboxStatus = clicked

    def rotateShape(self):
        if self.selectedShape:
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(), self.selectedShape.points[2].x(), self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(), self.selectedShape.points[2].y(), self.selectedShape.points[3].y()]
            xcenter = (min(xlist) + max(xlist)) / 2
            ycenter = (min(ylist) + max(ylist)) / 2
            xdiff = xcenter - min(xlist)
            ydiff = ycenter - min(ylist)
            imagewidth = self.pixmap.width()
            imageheight = self.pixmap.height()

            if xcenter + ydiff <= imagewidth and ycenter + xdiff <= imageheight and ycenter - xdiff >= 0 and xcenter - ydiff >= 0:
                self.selectedShape.points[0] = QPointF(xcenter - ydiff, ycenter - xdiff)
                self.selectedShape.points[1] = QPointF(xcenter + ydiff, ycenter - xdiff)
                self.selectedShape.points[2] = QPointF(xcenter + ydiff, ycenter + xdiff)
                self.selectedShape.points[3] = QPointF(xcenter - ydiff, ycenter + xdiff)
                self.shapeMoved.emit()
                self.rotateUndo.emit()
                self.repaint()

    def clickmoveShape(self, clickpos):
        if self.selectedShape:
            originpoints = self.selectedShape.points[:]
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(),
                     self.selectedShape.points[2].x(),
                     self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(),
                     self.selectedShape.points[2].y(),
                     self.selectedShape.points[3].y()]
            xcenter = (min(xlist) + max(xlist)) / 2
            ycenter = (min(ylist) + max(ylist)) / 2
            xdiff = xcenter - min(xlist)
            ydiff = ycenter - min(ylist)
            imagewidth = self.pixmap.width()
            imageheight = self.pixmap.height()

            # normal
            if clickpos.x() + xdiff <= imagewidth and clickpos.y() + ydiff <= imageheight and clickpos.y() - ydiff >= 0 and clickpos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(clickpos.x() - xdiff, clickpos.y() - ydiff)
                self.selectedShape.points[1] = QPointF(clickpos.x() + xdiff, clickpos.y() - ydiff)
                self.selectedShape.points[2] = QPointF(clickpos.x() + xdiff, clickpos.y() + ydiff)
                self.selectedShape.points[3] = QPointF(clickpos.x() - xdiff, clickpos.y() + ydiff)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Left Top
            elif clickpos.x() + xdiff <= imagewidth and clickpos.y() + ydiff <= imageheight and clickpos.y() - ydiff < 0 and clickpos.x() - xdiff < 0:
                self.selectedShape.points[0] = QPointF(0.0, 0.0)
                self.selectedShape.points[1] = QPointF(xdiff * 2, 0.0)
                self.selectedShape.points[2] = QPointF(xdiff * 2, ydiff * 2)
                self.selectedShape.points[3] = QPointF(0.0, ydiff * 2)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Left
            elif clickpos.x() + xdiff <= imagewidth and clickpos.y() + ydiff <= imageheight and clickpos.y() - ydiff >= 0 and clickpos.x() - xdiff < 0:
                self.selectedShape.points[0] = QPointF(0.0, clickpos.y() - ydiff)
                self.selectedShape.points[1] = QPointF(xdiff * 2, clickpos.y() - ydiff)
                self.selectedShape.points[2] = QPointF(xdiff * 2, clickpos.y() + ydiff)
                self.selectedShape.points[3] = QPointF(0.0, clickpos.y() + ydiff)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Left Bottom
            elif clickpos.x() + xdiff <= imagewidth and clickpos.y() + ydiff > imageheight and clickpos.y() - ydiff >= 0 and clickpos.x() - xdiff < 0:
                self.selectedShape.points[0] = QPointF(0.0, imageheight - (ydiff * 2))
                self.selectedShape.points[1] = QPointF(xdiff * 2, imageheight - (ydiff * 2))
                self.selectedShape.points[2] = QPointF(xdiff * 2, imageheight)
                self.selectedShape.points[3] = QPointF(0.0, imageheight)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Top
            elif clickpos.x() + xdiff <= imagewidth and clickpos.y() + ydiff <= imageheight and clickpos.y() - ydiff < 0 and clickpos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(clickpos.x() - xdiff, 0.0)
                self.selectedShape.points[1] = QPointF(clickpos.x() + xdiff, 0.0)
                self.selectedShape.points[2] = QPointF(clickpos.x() + xdiff, ydiff * 2)
                self.selectedShape.points[3] = QPointF(clickpos.x() - xdiff, ydiff * 2)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Bottom
            elif clickpos.x() + xdiff <= imagewidth and clickpos.y() + ydiff > imageheight and clickpos.y() - ydiff >= 0 and clickpos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(clickpos.x() - xdiff, imageheight - (ydiff * 2))
                self.selectedShape.points[1] = QPointF(clickpos.x() + xdiff, imageheight - (ydiff * 2))
                self.selectedShape.points[2] = QPointF(clickpos.x() + xdiff, imageheight)
                self.selectedShape.points[3] = QPointF(clickpos.x() - xdiff, imageheight)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Right Top
            elif clickpos.x() + xdiff > imagewidth and clickpos.y() + ydiff <= imageheight and clickpos.y() - ydiff < 0 and clickpos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(imagewidth - (xdiff * 2), 0.0)
                self.selectedShape.points[1] = QPointF(imagewidth, 0.0)
                self.selectedShape.points[2] = QPointF(imagewidth, ydiff * 2)
                self.selectedShape.points[3] = QPointF(imagewidth - (xdiff * 2), ydiff * 2)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Right
            elif clickpos.x() + xdiff > imagewidth and clickpos.y() + ydiff <= imageheight and clickpos.y() - ydiff >= 0 and clickpos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(imagewidth - (xdiff * 2), clickpos.y() - ydiff)
                self.selectedShape.points[1] = QPointF(imagewidth, clickpos.y() - ydiff)
                self.selectedShape.points[2] = QPointF(imagewidth, clickpos.y() + ydiff)
                self.selectedShape.points[3] = QPointF(imagewidth - (xdiff * 2), clickpos.y() + ydiff)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # Right Bottom
            elif clickpos.x() + xdiff > imagewidth and clickpos.y() + ydiff > imageheight and clickpos.y() - ydiff >= 0 and clickpos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(imagewidth - (xdiff * 2), imageheight - (ydiff * 2))
                self.selectedShape.points[1] = QPointF(imagewidth, imageheight - (ydiff * 2))
                self.selectedShape.points[2] = QPointF(imagewidth, imageheight)
                self.selectedShape.points[3] = QPointF(imagewidth - (xdiff * 2), imageheight)
                self.shapeMoved.emit()
                if len(self.shapes) > 0:
                    self.clickmoveUndo.emit()
                self.repaint()

            # pointlist = []
            # boollist = []
            # for pp in self.selectedShape.points:
            #     pointlist.append([pp.x(), pp.y()])
            #
            # for bb in pointlist:
            #     if 0 <= bb[0] <= self.pixmap.width() and 0 <= bb[1] <= self.pixmap.height():
            #         boollist.append(True)
            #     else:
            #         boollist.append(False)
            #
            # if False in boollist:
            #     self.selectedShape.points[0] = originpoints[0]
            #     self.selectedShape.points[1] = originpoints[1]
            #     self.selectedShape.points[2] = originpoints[2]
            #     self.selectedShape.points[3] = originpoints[3]
            #     self.shapeMoved.emit()
            #     self.repaint()

    def Horizontalcut(self, clickpos):
        if self.selectedShape:
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(), self.selectedShape.points[2].x(), self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(), self.selectedShape.points[2].y(), self.selectedShape.points[3].y()]
            xcenter = (min(xlist) + max(xlist)) / 2
            ycenter = (min(ylist) + max(ylist)) / 2
            xdiff = xcenter - min(xlist)
            ydiff = ycenter - min(ylist)

            if clickpos is None:
                self.selectedShape.points[0] = QPointF(xcenter - xdiff, ycenter - ydiff)
                self.selectedShape.points[1] = QPointF(xcenter + xdiff, ycenter - ydiff)
                self.selectedShape.points[2] = QPointF(xcenter + xdiff, ycenter)
                self.selectedShape.points[3] = QPointF(xcenter - xdiff, ycenter)
                self.shapeMoved.emit()
                self.repaint()

                shape = self.selectedShape.copy()
                shape.points[0] = QPointF(xcenter - xdiff, ycenter)
                shape.points[1] = QPointF(xcenter + xdiff, ycenter)
                shape.points[2] = QPointF(xcenter + xdiff, ycenter + ydiff)
                shape.points[3] = QPointF(xcenter - xdiff, ycenter + ydiff)

            else:
                self.selectedShape.points[0] = QPointF(xcenter - xdiff, ycenter - ydiff)
                self.selectedShape.points[1] = QPointF(xcenter + xdiff, ycenter - ydiff)
                self.selectedShape.points[2] = QPointF(xcenter + xdiff, clickpos.y())
                self.selectedShape.points[3] = QPointF(xcenter - xdiff, clickpos.y())
                self.shapeMoved.emit()
                self.repaint()

                shape = self.selectedShape.copy()
                shape.points[0] = QPointF(xcenter - xdiff, clickpos.y())
                shape.points[1] = QPointF(xcenter + xdiff, clickpos.y())
                shape.points[2] = QPointF(xcenter + xdiff, ycenter + ydiff)
                shape.points[3] = QPointF(xcenter - xdiff, ycenter + ydiff)

            self.setHorizontalcutStatus = False
            self.prevCutpos = None

            self.deSelectShape()
            self.shapes.append(shape)
            shape.selected = True
            self.selectedShape = shape
            return shape

    def Verticalcut(self, clickpos):
        if self.selectedShape:
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(), self.selectedShape.points[2].x(), self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(), self.selectedShape.points[2].y(), self.selectedShape.points[3].y()]
            xcenter = (min(xlist) + max(xlist)) / 2
            ycenter = (min(ylist) + max(ylist)) / 2
            xdiff = xcenter - min(xlist)
            ydiff = ycenter - min(ylist)

            if clickpos is None:
                self.selectedShape.points[0] = QPointF(xcenter - xdiff, ycenter - ydiff)
                self.selectedShape.points[1] = QPointF(xcenter, ycenter - ydiff)
                self.selectedShape.points[2] = QPointF(xcenter, ycenter + ydiff)
                self.selectedShape.points[3] = QPointF(xcenter - xdiff, ycenter + ydiff)
                self.shapeMoved.emit()
                self.repaint()

                shape = self.selectedShape.copy()
                shape.points[0] = QPointF(xcenter, ycenter - ydiff)
                shape.points[1] = QPointF(xcenter + xdiff, ycenter - ydiff)
                shape.points[2] = QPointF(xcenter + xdiff, ycenter + ydiff)
                shape.points[3] = QPointF(xcenter, ycenter + ydiff)

            else:
                self.selectedShape.points[0] = QPointF(xcenter - xdiff, ycenter - ydiff)
                self.selectedShape.points[1] = QPointF(clickpos.x(), ycenter - ydiff)
                self.selectedShape.points[2] = QPointF(clickpos.x(), ycenter + ydiff)
                self.selectedShape.points[3] = QPointF(xcenter - xdiff, ycenter + ydiff)
                self.shapeMoved.emit()
                self.repaint()

                shape = self.selectedShape.copy()
                shape.points[0] = QPointF(clickpos.x(), ycenter - ydiff)
                shape.points[1] = QPointF(xcenter + xdiff, ycenter - ydiff)
                shape.points[2] = QPointF(xcenter + xdiff, ycenter + ydiff)
                shape.points[3] = QPointF(clickpos.x(), ycenter + ydiff)

            self.setVerticalcutStatus = False
            self.prevCutpos = None

            self.deSelectShape()
            self.shapes.append(shape)
            shape.selected = True
            self.selectedShape = shape
            return shape

    def Crosscut(self, clickpos):
        if self.selectedShape:
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(),
                     self.selectedShape.points[2].x(), self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(),
                     self.selectedShape.points[2].y(), self.selectedShape.points[3].y()]
            xcenter = (min(xlist) + max(xlist)) / 2
            ycenter = (min(ylist) + max(ylist)) / 2
            xdiff = xcenter - min(xlist)
            ydiff = ycenter - min(ylist)
            shapelist = []

            if clickpos is None:
                self.selectedShape.points[0] = QPointF(xcenter - xdiff, ycenter - ydiff)
                self.selectedShape.points[1] = QPointF(xcenter, ycenter - ydiff)
                self.selectedShape.points[2] = QPointF(xcenter, ycenter)
                self.selectedShape.points[3] = QPointF(xcenter - xdiff, ycenter)
                self.shapeMoved.emit()
                self.repaint()

                shape1 = self.selectedShape.copy()
                shape1.points[0] = QPointF(xcenter, ycenter - ydiff)
                shape1.points[1] = QPointF(xcenter + xdiff, ycenter - ydiff)
                shape1.points[2] = QPointF(xcenter + xdiff, ycenter)
                shape1.points[3] = QPointF(xcenter, ycenter)

                shape2 = self.selectedShape.copy()
                shape2.points[0] = QPointF(xcenter - xdiff, ycenter)
                shape2.points[1] = QPointF(xcenter, ycenter)
                shape2.points[2] = QPointF(xcenter, ycenter + ydiff)
                shape2.points[3] = QPointF(xcenter - xdiff, ycenter + ydiff)

                shape3 = self.selectedShape.copy()
                shape3.points[0] = QPointF(xcenter, ycenter)
                shape3.points[1] = QPointF(xcenter + xdiff, ycenter)
                shape3.points[2] = QPointF(xcenter + xdiff, ycenter + ydiff)
                shape3.points[3] = QPointF(xcenter, ycenter + ydiff)

                shapelist.append(shape1)
                shapelist.append(shape2)
                shapelist.append(shape3)

            else:
                self.selectedShape.points[0] = QPointF(xcenter - xdiff, ycenter - ydiff)
                self.selectedShape.points[1] = QPointF(clickpos.x(), ycenter - ydiff)
                self.selectedShape.points[2] = QPointF(clickpos.x(), clickpos.y())
                self.selectedShape.points[3] = QPointF(xcenter - xdiff, clickpos.y())
                self.shapeMoved.emit()
                self.repaint()

                shape1 = self.selectedShape.copy()
                shape1.points[0] = QPointF(clickpos.x(), ycenter - ydiff)
                shape1.points[1] = QPointF(xcenter + xdiff, ycenter - ydiff)
                shape1.points[2] = QPointF(xcenter + xdiff, clickpos.y())
                shape1.points[3] = QPointF(clickpos.x(), clickpos.y())

                shape2 = self.selectedShape.copy()
                shape2.points[0] = QPointF(xcenter - xdiff, clickpos.y())
                shape2.points[1] = QPointF(clickpos.x(), clickpos.y())
                shape2.points[2] = QPointF(clickpos.x(), ycenter + ydiff)
                shape2.points[3] = QPointF(xcenter - xdiff, ycenter + ydiff)

                shape3 = self.selectedShape.copy()
                shape3.points[0] = QPointF(clickpos.x(), clickpos.y())
                shape3.points[1] = QPointF(xcenter + xdiff, clickpos.y())
                shape3.points[2] = QPointF(xcenter + xdiff, ycenter + ydiff)
                shape3.points[3] = QPointF(clickpos.x(), ycenter + ydiff)

                shapelist.append(shape1)
                shapelist.append(shape2)
                shapelist.append(shape3)

            self.setCrosscutStatus = False
            self.prevCutpos = None

            self.deSelectShape()
            for shape in shapelist:
                self.shapes.append(shape)
                shape.selected = True
                self.selectedShape = shape
                self.deSelectShape()

            shapelist[-1].selected = True
            self.selectedShape = shapelist[-1]

            return shapelist

    def Resizebox(self, clickpos):
        if self.selectedShape:
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(), self.selectedShape.points[2].x(), self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(), self.selectedShape.points[2].y(), self.selectedShape.points[3].y()]
            xdiff = max(xlist) - min(xlist)
            ydiff = max(ylist) - min(ylist)

            if clickpos is None:
                if max(xlist) + xdiff < self.pixmap.width() and max(ylist) + ydiff < self.pixmap.height():
                    self.selectedShape.points[0] = QPointF(min(xlist), min(ylist))
                    self.selectedShape.points[1] = QPointF(max(xlist) + xdiff, min(ylist))
                    self.selectedShape.points[2] = QPointF(max(xlist) + xdiff, max(ylist) + ydiff)
                    self.selectedShape.points[3] = QPointF(min(xlist), max(ylist) + ydiff)
                    self.shapeMoved.emit()
                    self.repaint()

            else:
                clickxdiff = clickpos.x() - min(xlist)
                clickydiff = clickpos.y() - min(ylist)

                if clickxdiff > 0 and clickydiff > 0:
                    self.selectedShape.points[0] = QPointF(min(xlist), min(ylist))
                    self.selectedShape.points[1] = QPointF(clickpos.x(), min(ylist))
                    self.selectedShape.points[2] = QPointF(clickpos.x(), clickpos.y())
                    self.selectedShape.points[3] = QPointF(min(xlist), clickpos.y())
                elif clickxdiff < 0 and clickydiff < 0:
                    self.selectedShape.points[0] = QPointF(clickpos.x(), clickpos.y())
                    self.selectedShape.points[1] = QPointF(min(xlist), clickpos.y())
                    self.selectedShape.points[2] = QPointF(min(xlist), min(ylist))
                    self.selectedShape.points[3] = QPointF(clickpos.x(), min(ylist))
                elif clickxdiff > 0 and clickydiff < 0:
                    self.selectedShape.points[0] = QPointF(min(xlist), clickpos.y())
                    self.selectedShape.points[1] = QPointF(clickpos.x(), clickpos.y())
                    self.selectedShape.points[2] = QPointF(clickpos.x(), min(ylist))
                    self.selectedShape.points[3] = QPointF(min(xlist), min(ylist))
                else:
                    self.selectedShape.points[0] = QPointF(clickpos.x(), min(ylist))
                    self.selectedShape.points[1] = QPointF(min(xlist), min(ylist))
                    self.selectedShape.points[2] = QPointF(min(xlist), clickpos.y())
                    self.selectedShape.points[3] = QPointF(clickpos.x(), clickpos.y())
                self.shapeMoved.emit()
                self.repaint()

            self.setResizeboxStatus = False
            self.prevCutpos = None

    def mergeShape(self):
        if len(self.selectedMultishape) > 1:
            xlist = []
            ylist = []
            picklist = []

            for i in self.shapes:
                for shape in self.selectedMultishape:
                    if i == shape:
                        picklist.append(self.shapes.index(i))

            for sh in self.selectedMultishape:
                for p in sh.points:
                    xlist.append(p.x())
                    ylist.append(p.y())

            self.shapes[min(picklist)].points[0] = QPointF(min(xlist), min(ylist))
            self.shapes[min(picklist)].points[1] = QPointF(max(xlist), min(ylist))
            self.shapes[min(picklist)].points[2] = QPointF(max(xlist), max(ylist))
            self.shapes[min(picklist)].points[3] = QPointF(min(xlist), max(ylist))
            self.shapeMoved.emit()
            self.repaint()
            return self.shapes[min(picklist)]

    def cancelCutShape(self):
        self.setHorizontalcutStatus = False
        self.setVerticalcutStatus = False
        self.setCrosscutStatus = False
        self.setResizeboxStatus = False
        self.prevCutpos = None

    def autotrackingStart(self):
        self.autotrackingShapePoint = []
        self.autotrackingShapePoint.append(self.selectedShape.points[0])
        self.autotrackingShapePoint.append(self.selectedShape.points[1])
        self.autotrackingShapePoint.append(self.selectedShape.points[2])
        self.autotrackingShapePoint.append(self.selectedShape.points[3])

    def autotrackingEnd(self):
        finishpoint = []
        finishpoint.append(self.selectedShape.points[0])
        finishpoint.append(self.selectedShape.points[1])
        finishpoint.append(self.selectedShape.points[2])
        finishpoint.append(self.selectedShape.points[3])
        if self.autotracking_undochecker is True:
            return
        if self.autotrackingShapePoint != finishpoint:
            self.autotrackingUndo.emit()

    def trackingMagnetShape(self, pos):
        if self.selectedShape:
            xlist = [self.selectedShape.points[0].x(), self.selectedShape.points[1].x(),
                     self.selectedShape.points[2].x(),
                     self.selectedShape.points[3].x()]
            ylist = [self.selectedShape.points[0].y(), self.selectedShape.points[1].y(),
                     self.selectedShape.points[2].y(),
                     self.selectedShape.points[3].y()]
            xcenter = (min(xlist) + max(xlist)) / 2
            ycenter = (min(ylist) + max(ylist)) / 2
            xdiff = xcenter - min(xlist)
            ydiff = ycenter - min(ylist)
            imagewidth = self.pixmap.width()
            imageheight = self.pixmap.height()

            # condition
            if pos.x() + xdiff <= imagewidth and pos.y() + ydiff <= imageheight and pos.y() - ydiff >= 0 and pos.x() - xdiff >= 0:
                self.selectedShape.points[0] = QPointF(pos.x() - xdiff, pos.y() - ydiff)
                self.selectedShape.points[1] = QPointF(pos.x() + xdiff, pos.y() - ydiff)
                self.selectedShape.points[2] = QPointF(pos.x() + xdiff, pos.y() + ydiff)
                self.selectedShape.points[3] = QPointF(pos.x() - xdiff, pos.y() + ydiff)
                self.shapeMoved.emit()
                self.repaint()

                self.calculateOffsets(self.selectedShape, pos)
                self.prevPoint = pos

    def minmaxShapepoint(self, shape):
        xlist = [shape.points[0].x(), shape.points[1].x(), shape.points[2].x(), shape.points[3].x()]
        ylist = [shape.points[0].y(), shape.points[1].y(), shape.points[2].y(), shape.points[3].y()]
        return [[min(xlist), min(ylist)], [max(xlist), max(ylist)]]

    def selectedShapeBoundary(self, p, eps, pos):
        if p[0][0] <= pos.x() <= p[1][0] and p[0][1] <= pos.y() <= p[1][1]:
            return True
        elif p[0][0] - eps <= pos.x() <= p[0][0] + eps and p[0][1] - eps <= pos.y() <= p[0][1] + eps:
            return True
        elif p[1][0] - eps <= pos.x() <= p[1][0] + eps and p[0][1] - eps <= pos.y() <= p[0][1] + eps:
            return True
        elif p[0][0] - eps <= pos.x() <= p[0][0] + eps and p[1][1] - eps <= pos.y() <= p[1][1] + eps:
            return True
        elif p[1][0] - eps <= pos.x() <= p[1][0] + eps and p[1][1] - eps <= pos.y() <= p[1][1] + eps:
            return True
        else:
            return False

    def moveOutOfBound(self, step):
        points = [p1+p2 for p1, p2 in zip(self.selectedShape.points, [step]*4)]
        return True in map(self.outOfPixmap, points)

    def setLastLabel(self, text, line_color  = None, fill_color = None):
        assert text
        self.shapes[-1].label = text
        if line_color:
            self.shapes[-1].line_color = line_color

        if fill_color:
            self.shapes[-1].fill_color = fill_color

        return self.shapes[-1]

    def undoLastLine(self):
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.setOpen()
        self.line.points = [self.current[-1], self.current[0]]
        self.drawingPolygon.emit(True)

    def resetAllLines(self):
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.setOpen()
        self.line.points = [self.current[-1], self.current[0]]
        self.drawingPolygon.emit(True)
        self.current = None
        self.drawingPolygon.emit(False)
        self.update()

    def loadPixmap(self, pixmap):
        self.pixmap = pixmap
        self.shapes = []
        self.repaint()

    def loadShapes(self, shapes):
        self.shapes = list(shapes)
        self.current = None
        self.repaint()

    def setShapeVisible(self, shape, value):
        self.visible[shape] = value
        self.repaint()

    def currentCursor(self):
        cursor = QApplication.overrideCursor()
        if cursor is not None:
            cursor = cursor.shape()
        return cursor

    def overrideCursor(self, cursor):
        self._cursor = cursor
        if self.currentCursor() is None:
            QApplication.setOverrideCursor(cursor)
        else:
            QApplication.changeOverrideCursor(cursor)

    def restoreCursor(self):
        QApplication.restoreOverrideCursor()

    def resetState(self):
        self.restoreCursor()
        self.pixmap = None
        self.update()

    def setDrawingShapeToSquare(self, status):
        self.drawSquare = status
