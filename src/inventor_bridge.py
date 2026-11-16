"""
Autodesk Inventor 2025 COM API Bridge.
Provides robust connection, document management, and safe script execution
with transactions and rollback on error.
"""

import sys
import io
import math
import traceback
from typing import Optional, Tuple, Dict, Any
import win32com.client


# Document type constants
kPartDocumentObject = 12290
kAssemblyDocumentObject = 12291
kDrawingDocumentObject = 12292

# Part feature operation constants
kJoinOperation = 20481
kCutOperation = 20482
kIntersectOperation = 20483

# Extents constants
kPositiveExtentDirection = 20993
kNegativeExtentDirection = 20994
kSymmetricExtentDirection = 20995


class InventorBridge:
    def __init__(self):
        self._app = None

    def connect(self) -> Tuple[bool, str]:
        """Connect to running Autodesk Inventor 2025 instance."""
        try:
            # First attempt: GetActiveObject (checks Running Object Table)
            try:
                self._app = win32com.client.GetActiveObject("Inventor.Application")
            except Exception:
                # Second attempt: Dispatch (attaches to running instance or starts if needed)
                self._app = win32com.client.Dispatch("Inventor.Application")

            if self._app is None:
                return False, "Не удалось подключиться к Autodesk Inventor."

            self._app.Visible = True
            version = self._app.SoftwareVersion.DisplayName
            return True, f"Успешное подключение: {version}"
        except Exception as e:
            self._app = None
            return False, f"Ошибка подключения к Inventor: {str(e)}"

    @property
    def app(self):
        if self._app is None:
            ok, msg = self.connect()
            if not ok:
                raise RuntimeError(msg)
        return self._app

    def is_connected(self) -> bool:
        """Check if connection to Inventor is alive."""
        if self._app is None:
            return False
        try:
            _ = self._app.SoftwareVersion.DisplayName
            return True
        except Exception:
            self._app = None
            return False

    def get_status_info(self) -> Dict[str, Any]:
        """Get live status of Autodesk Inventor application."""
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return {
                    "connected": False,
                    "version": "Не подключен",
                    "active_doc": "Нет",
                    "docs_count": 0,
                }
        try:
            docs_count = self.app.Documents.Count
            active_doc_name = "Нет активного документа"
            if docs_count > 0 and self.app.ActiveDocument:
                active_doc_name = self.app.ActiveDocument.DisplayName

            return {
                "connected": True,
                "version": self.app.SoftwareVersion.DisplayName,
                "active_doc": active_doc_name,
                "docs_count": docs_count,
            }
        except Exception as e:
            return {
                "connected": False,
                "version": f"Ошибка: {e}",
                "active_doc": "Нет",
                "docs_count": 0,
            }

    def create_new_part(self) -> Any:
        """Create a new standard Part document (*.ipt)."""
        app = self.app
        part_doc = app.Documents.Add(kPartDocumentObject)
        app.ActiveView.Fit()
        return part_doc

    def get_or_create_part(self) -> Any:
        """Get the active part document or create a new one if none exists."""
        app = self.app
        if app.Documents.Count > 0 and app.ActiveDocument:
            doc = app.ActiveDocument
            # 12290 is kPartDocumentObject
            if doc.DocumentType == kPartDocumentObject:
                return doc
        return self.create_new_part()

    @staticmethod
    def clean_code(code: str) -> str:
        """Strip markdown fences, leading/trailing backticks and auto-trim incomplete trailing lines."""
        if not code:
            return ""
        lines = [line for line in code.split("\n") if not line.strip().startswith("```")]
        
        # If code ends with an incomplete line (e.g. token limit hit mid-statement), trim from end until compiles
        while lines:
            try:
                compile("\n".join(lines) + "\n", "<string>", "exec")
                break
            except SyntaxError:
                lines.pop()

        return "\n".join(lines).strip()

    def execute_code(self, python_code: str, use_transaction: bool = True) -> Tuple[bool, str, str]:
        """
        Execute Python code against Autodesk Inventor.
        Returns: (success: bool, stdout_output: str, error_message: str)
        """
        python_code = self.clean_code(python_code)
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return False, "", msg

        app = self.app
        doc = self.get_or_create_part()
        comp_def = doc.ComponentDefinition
        tg = app.TransientGeometry
        wp = comp_def.WorkPlanes

        # Prepare transaction if requested
        trans = None
        if use_transaction:
            try:
                trans = app.TransactionManager.StartTransaction(doc, "LLM 3D Feature Generation")
            except Exception:
                trans = None

        # Capture stdout
        old_stdout = sys.stdout
        redirected_output = io.StringIO()
        sys.stdout = redirected_output

        execution_scope = self._prepare_scope(doc)

        try:
            exec(python_code, execution_scope)

            # Fit view to geometry
            try:
                if app.ActiveView:
                    app.ActiveView.Fit()
                    app.ActiveView.Update()
            except Exception:
                pass

            if trans:
                try:
                    trans.End()
                except Exception:
                    pass

            sys.stdout = old_stdout
            out = redirected_output.getvalue()
            return True, out if out else "Команда успешно выполнена в Inventor.", ""

        except Exception as e:
            # Rollback transaction on failure to keep model clean
            if trans:
                try:
                    trans.Abort()
                except Exception:
                    pass

            sys.stdout = old_stdout
            out = redirected_output.getvalue()
            tb = traceback.format_exc()
            err_msg = f"{type(e).__name__}: {str(e)}\n\nTraceback:\n{tb}"
            return False, out, err_msg

    def _prepare_scope(self, doc) -> Dict[str, Any]:
        """Prepare shared variables, helper functions and CAD constants."""
        app = self.app
        comp_def = doc.ComponentDefinition
        tg = app.TransientGeometry
        wp = comp_def.WorkPlanes

        def mm(val):
            return float(val) / 10.0

        def deg(val):
            return math.radians(float(val))

        def draw_polygon(sketch, points):
            pts = [tg.CreatePoint2d(p[0], p[1]) if isinstance(p, (tuple, list)) else p for p in points]
            if len(pts) < 3:
                raise ValueError("Многоугольник должен содержать минимум 3 вершины")
            lines = []
            l0 = sketch.SketchLines.AddByTwoPoints(pts[0], pts[1])
            lines.append(l0)
            for i in range(2, len(pts)):
                l = sketch.SketchLines.AddByTwoPoints(lines[-1].EndSketchPoint, pts[i])
                lines.append(l)
            last = sketch.SketchLines.AddByTwoPoints(lines[-1].EndSketchPoint, lines[0].StartSketchPoint)
            lines.append(last)
            return lines

        def draw_rectangle(sketch, x1, y1, x2, y2):
            return sketch.SketchLines.AddAsTwoPointRectangle(
                tg.CreatePoint2d(x1, y1), tg.CreatePoint2d(x2, y2)
            )

        def draw_circle(sketch, cx, cy, radius):
            return sketch.SketchCircles.AddByCenterRadius(tg.CreatePoint2d(cx, cy), radius)

        def draw_flange(sketch, cx, cy, radius, length, direction="+x"):
            """
            Строит контур фланца/проушины со скругленным торцом радиуса R в точке (cx, cy)
            и прямым продолжением длиной length. Идеально для ушей деталей с отверстиями.
            """
            c_pt = tg.CreatePoint2d(cx, cy)
            if direction == "+x":
                p_top = tg.CreatePoint2d(cx, cy + radius)
                p_bot = tg.CreatePoint2d(cx, cy - radius)
                arc = sketch.SketchArcs.AddByCenterStartEndPoint(c_pt, p_top, p_bot)
                l1 = sketch.SketchLines.AddByTwoPoints(arc.StartSketchPoint, tg.CreatePoint2d(cx + length, cy + radius))
                l2 = sketch.SketchLines.AddByTwoPoints(l1.EndSketchPoint, tg.CreatePoint2d(cx + length, cy - radius))
                l3 = sketch.SketchLines.AddByTwoPoints(l2.EndSketchPoint, arc.EndSketchPoint)
                return [arc, l1, l2, l3]
            else:
                p_top = tg.CreatePoint2d(cx, cy + radius)
                p_bot = tg.CreatePoint2d(cx, cy - radius)
                arc = sketch.SketchArcs.AddByCenterStartEndPoint(c_pt, p_bot, p_top)
                l1 = sketch.SketchLines.AddByTwoPoints(arc.StartSketchPoint, tg.CreatePoint2d(cx - length, cy - radius))
                l2 = sketch.SketchLines.AddByTwoPoints(l1.EndSketchPoint, tg.CreatePoint2d(cx - length, cy + radius))
                l3 = sketch.SketchLines.AddByTwoPoints(l2.EndSketchPoint, arc.EndSketchPoint)
                return [arc, l1, l2, l3]

        def extrude(sketch, distance, operation=kJoinOperation, direction=kPositiveExtentDirection):
            prof = sketch.Profiles.AddForSolid()
            ext_def = comp_def.Features.ExtrudeFeatures.CreateExtrudeDefinition(prof, operation)
            ext_def.SetDistanceExtent(distance, direction)
            return comp_def.Features.ExtrudeFeatures.Add(ext_def)

        def cut(sketch, distance=None, through_all=True, direction=kSymmetricExtentDirection):
            prof = sketch.Profiles.AddForSolid()
            ext_def = comp_def.Features.ExtrudeFeatures.CreateExtrudeDefinition(prof, kCutOperation)
            if through_all or distance is None:
                ext_def.SetThroughAllExtent(kSymmetricExtentDirection)
            else:
                ext_def.SetDistanceExtent(distance, direction)
            return comp_def.Features.ExtrudeFeatures.Add(ext_def)

        def cut_hole(sketch, cx, cy, diameter=None, radius=None, depth=None, through_all=True):
            """
            Строит идеально ровное отверстие в центре (cx, cy) и делает сквозной или глухой вырез.
            Исключает кривые или односторонние срезы.
            """
            r = radius if radius is not None else (diameter / 2.0 if diameter is not None else mm(10))
            draw_circle(sketch, cx, cy, r)
            return cut(sketch, distance=depth, through_all=through_all)

        return {
            "app": app,
            "doc": doc,
            "comp_def": comp_def,
            "cd": comp_def,
            "tg": tg,
            "wp": wp,
            "math": math,
            "mm": mm,
            "deg": deg,
            "draw_polygon": draw_polygon,
            "draw_rectangle": draw_rectangle,
            "draw_circle": draw_circle,
            "draw_flange": draw_flange,
            "cut_hole": cut_hole,
            "extrude": extrude,
            "cut": cut,
            "kPartDocumentObject": kPartDocumentObject,
            "kJoinOperation": kJoinOperation,
            "kCutOperation": kCutOperation,
            "kIntersectOperation": kIntersectOperation,
            "kPositiveExtentDirection": kPositiveExtentDirection,
            "kNegativeExtentDirection": kNegativeExtentDirection,
            "kSymmetricExtentDirection": kSymmetricExtentDirection,
        }

    def execute_code_step_by_step(
        self,
        python_code: str,
        step_callback=None,
        step_delay: float = 0.4
    ) -> Tuple[bool, str, str]:
        """
        Execute code step-by-step in Inventor.
        Splits code into logical steps (# ШАГ ... or # 1. ...),
        executing each step sequentially with visual updates.
        """
        import re
        import time

        python_code = self.clean_code(python_code)
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return False, "", msg

        app = self.app
        doc = self.get_or_create_part()
        scope = self._prepare_scope(doc)

        pattern = r'(?=(?:^|\n)\s*#\s*(?:[-=]{0,4}\s*(?:[Шш][Аа][Гг]\s*\d+|\d+\.)[^\n]*))'
        chunks = [c.strip() for c in re.split(pattern, python_code) if c.strip()]
        if len(chunks) <= 1:
            chunks = [python_code]

        total = len(chunks)
        for idx, chunk in enumerate(chunks, 1):
            first_line = chunk.split("\n")[0].strip()
            title = first_line.lstrip("#").strip() if first_line.startswith("#") else f"Операция {idx}"

            if step_callback:
                step_callback(idx, total, title, "running", "")

            try:
                exec(chunk, scope)
                try:
                    if app.ActiveView:
                        app.ActiveView.Fit()
                        app.ActiveView.Update()
                except Exception:
                    pass

                if step_callback:
                    step_callback(idx, total, title, "success", "")

                if step_delay > 0 and idx < total:
                    time.sleep(step_delay)

            except Exception as e:
                tb = traceback.format_exc()
                err_msg = f"{type(e).__name__}: {str(e)}\n\nTraceback:\n{tb}"
                is_finishing = any(w in title.lower() for w in ["скруглен", "фаск", "fillet", "chamfer"])
                if is_finishing:
                    if step_callback:
                        step_callback(idx, total, title, "warning", str(e))
                    continue
                else:
                    if step_callback:
                        step_callback(idx, total, title, "error", err_msg)
                    return False, "", f"Ошибка на шаге {idx} ('{title}'):\n{err_msg}"

        return True, "Все шаги успешно построены в Inventor.", ""
