"""
Drawing parser and preprocessor for Autodesk Inventor + LLM.
Handles PDF, DWG, DXF, and raster images (PNG, JPG).
Prepares payloads for multimodal LLMs (Gemini / LM Studio).
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, List
import base64
import pypdf
import ezdxf


class DrawingParser:
    SUPPORTED_EXTENSIONS = {".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".bmp"}

    def __init__(self, inventor_bridge=None):
        self.bridge = inventor_bridge

    def process_drawing(self, file_path: str) -> Dict[str, Any]:
        """
        Process drawing file and return structured info and payload for LLM.
        Result dict contains:
        - 'type': 'pdf' | 'dwg' | 'dxf' | 'image'
        - 'file_name': str
        - 'file_path': str
        - 'text_summary': str (extracted text/dimensions/layers)
        - 'raw_bytes': bytes (for Gemini file upload or inline data)
        - 'mime_type': str
        - 'image_base64': Optional[str] (for OpenAI/LM Studio format)
        """
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {path}")

        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"Неподдерживаемый формат чертежа: {ext}. Поддерживаются: {', '.join(self.SUPPORTED_EXTENSIONS)}")

        with open(path, "rb") as f:
            raw_bytes = f.read()

        if ext == ".pdf":
            return self._process_pdf(path, raw_bytes)
        elif ext == ".dxf":
            return self._process_dxf(path, raw_bytes)
        elif ext == ".dwg":
            return self._process_dwg(path, raw_bytes)
        else: # Image
            return self._process_image(path, raw_bytes, ext)

    def _process_pdf(self, path: Path, raw_bytes: bytes) -> Dict[str, Any]:
        """Extract text content and page count from PDF."""
        extracted_text = []
        try:
            reader = pypdf.PdfReader(path)
            num_pages = len(reader.pages)
            for i, page in enumerate(reader.pages):
                txt = page.extract_text()
                if txt:
                    extracted_text.append(f"--- Страница {i+1} ---\n{txt.strip()}")
            summary = f"PDF чертеж: {path.name} ({num_pages} стр.)\n"
            if extracted_text:
                summary += "\nИзвлеченный текст и размеры:\n" + "\n".join(extracted_text)
            else:
                summary += "(Векторный или графический чертеж без текстового слоя)"
        except Exception as e:
            summary = f"PDF чертеж: {path.name} (ошибка извлечения текста: {e})"

        b64 = base64.b64encode(raw_bytes).decode("utf-8")

        return {
            "type": "pdf",
            "file_name": path.name,
            "file_path": str(path),
            "text_summary": summary,
            "raw_bytes": raw_bytes,
            "mime_type": "application/pdf",
            "image_base64": b64,
        }

    def _process_dxf(self, path: Path, raw_bytes: bytes) -> Dict[str, Any]:
        """Extract geometric entities and layers from DXF."""
        summary_lines = [f"DXF чертеж: {path.name}"]
        try:
            doc = ezdxf.readfile(str(path))
            msp = doc.modelspace()
            
            # Layers
            layers = [layer.dxf.name for layer in doc.layers]
            summary_lines.append(f"Слои: {', '.join(layers[:10])}")

            # Counts of entities
            entity_counts = {}
            dimensions = []
            texts = []

            for entity in msp:
                dxftype = entity.dxftype()
                entity_counts[dxftype] = entity_counts.get(dxftype, 0) + 1
                
                if dxftype in ("TEXT", "MTEXT"):
                    txt = getattr(entity.dxf, "text", "")
                    if txt:
                        texts.append(txt.strip())
                elif "DIMENSION" in dxftype:
                    dim_text = getattr(entity.dxf, "text", "")
                    if dim_text:
                        dimensions.append(dim_text.strip())

            summary_lines.append("Элементы геометрии: " + ", ".join(f"{k}: {v}" for k, v in entity_counts.items()))
            if texts:
                summary_lines.append("Надписи на чертеже: " + ", ".join(texts[:20]))
            if dimensions:
                summary_lines.append("Размеры на чертеже: " + ", ".join(dimensions[:20]))

        except Exception as e:
            summary_lines.append(f"Ошибка парсинга DXF: {e}")

        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return {
            "type": "dxf",
            "file_name": path.name,
            "file_path": str(path),
            "text_summary": "\n".join(summary_lines),
            "raw_bytes": raw_bytes,
            "mime_type": "application/dxf",
            "image_base64": b64,
        }

    def _process_dwg(self, path: Path, raw_bytes: bytes) -> Dict[str, Any]:
        """
        Process DWG file. If Inventor bridge is active, Inventor can open/inspect
        or convert DWG, or we summarize metadata.
        """
        summary_lines = [f"DWG чертеж: {path.name} (размер {len(raw_bytes) / 1024:.1f} КБ)"]
        dwg_preview_path = None

        if self.bridge and self.bridge.is_connected():
            try:
                # We can note that Inventor can open or import this DWG directly into a sketch
                summary_lines.append("Autodesk Inventor 2025 доступен для прямого импорта геометрии DWG.")
            except Exception:
                pass

        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return {
            "type": "dwg",
            "file_name": path.name,
            "file_path": str(path),
            "text_summary": "\n".join(summary_lines),
            "raw_bytes": raw_bytes,
            "mime_type": "application/acad",
            "image_base64": b64,
        }

    def _process_image(self, path: Path, raw_bytes: bytes, ext: str) -> Dict[str, Any]:
        """Process raster image of drawing or sketch with vision-token optimization."""
        from PIL import Image
        import io

        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".bmp": "image/bmp",
        }
        mime = mime_map.get(ext, "image/jpeg")

        # Optimize image resolution if too large to prevent vision token explosion in local LLMs
        final_bytes = raw_bytes
        width, height = 0, 0
        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                width, height = img.size
                max_dim = 1400
                if max(width, height) > max_dim:
                    scale = max_dim / max(width, height)
                    new_size = (int(width * scale), int(height * scale))
                    rgb_img = img.convert("RGB") if img.mode in ("RGBA", "P") else img
                    resized = rgb_img.resize(new_size, Image.Resampling.LANCZOS)
                    out_io = io.BytesIO()
                    resized.save(out_io, format="JPEG", quality=90, optimize=True)
                    final_bytes = out_io.getvalue()
                    mime = "image/jpeg"
        except Exception:
            final_bytes = raw_bytes

        b64 = base64.b64encode(final_bytes).decode("utf-8")
        dim_str = f", разрешение {width}x{height} px" if width > 0 else ""
        summary = f"Изображение чертежа/эскиза: {path.name} ({len(final_bytes) / 1024:.1f} КБ{dim_str})"

        return {
            "type": "image",
            "file_name": path.name,
            "file_path": str(path),
            "text_summary": summary,
            "raw_bytes": final_bytes,
            "mime_type": mime,
            "image_base64": b64,
        }
