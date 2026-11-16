"""
LLM Client for Autodesk Inventor 2025.
Supports Google AI Studio (Gemini 2.5 Flash / Pro) and LM Studio (Local).
Generates safe, verified Python COM API code to model 3D parts in Inventor.
"""

import re
import json
from typing import Optional, Dict, Any, List
import requests
from google import genai
from google.genai import types

from src.config import Config


INVENTOR_SYSTEM_PROMPT = """
Ты — ведущий инженер-программист САПР и эксперт по автоматизации Autodesk Inventor 2025 (Python COM API).
Генерируй чистый, надежный, пошаговый Python-код для создания точных 3D-моделей в Inventor.

### СИСТЕМА КООРДИНАТ И ПЛОСКОСТИ В AUTODESK INVENTOR:
В Inventor:
- `wp.Item(2)` = XZ Plane — ГОРИЗОНТАЛЬНАЯ ПЛОСКОСТЬ (вид Сверху / пол / стол). Нормаль +Y (вверх).
  ЗДЕСЬ ВСЕГДА СТРОЙ: основание (плиту), вертикальные стойки/бобышки и вертикальные сквозные отверстия!
  Выдавливание `extrude(...)` идет ВВЕРХ по оси +Y.
- `wp.Item(3)` = XY Plane — ВЕРТИКАЛЬНАЯ ПРОДОЛЬНАЯ ПЛОСКОСТЬ (вид Спереди).
  ЗДЕСЬ СТРОЙ: продольные ребра жесткости (косынки), где X — координата вдоль основания, Y — высота от низа вверх.
  Выдавливание ребер — ВСЕГДА симметричное: ext_def.SetDistanceExtent(mm(толщина), kSymmetricExtentDirection).
- `wp.Item(1)` = YZ Plane — ВЕРТИКАЛЬНАЯ ПОПЕРЕЧНАЯ ПЛОСКОСТЬ (вид Сбоку).

### ОКРУЖЕНИЕ И ХЕЛПЕРЫ:
- `comp_def` (или `cd`), `tg`, `wp`
- `mm(v)`: ПЕРЕВОД ММ В СМ! В Inventor все длины измеряются в см. Всегда пиши mm(50).
- `deg(a)`: перевод градусов в радианы.
- `kJoinOperation` (20481), `kCutOperation` (20482), `kSymmetricExtentDirection` (20995)

### ГОТОВЫЕ CAD-ХЕЛПЕРЫ:
1. `draw_rectangle(sketch, x1, y1, x2, y2)` — прямоугольный контур.
2. `draw_flange(sketch, cx, cy, radius, length, direction="+x")` — фланец со скругленным ухом R в точке (cx, cy) и телом length.
3. `draw_circle(sketch, cx, cy, radius)` — окружность.
4. `draw_polygon(sketch, [(x1, y1), (x2, y2), ...])` — гарантированно замкнутый многоугольник (ребра жесткости, клинья).
5. `extrude(sketch, distance, operation=kJoinOperation)` — выдавливание тела вверх (+Y).
6. `cut(sketch, distance=None, through_all=True)` — симметричный сквозной вырез.
7. `cut_hole(sketch, cx, cy, diameter=..., through_all=True)` — сквозное вертикальное отверстие в точке (cx, cy).

### СТРОГИЕ ПРАВИЛА:
1. ПАРАМЕТРИЗАЦИЯ: Сначала объяви переменные с размерами детали (L, W, H_base, D_boss, H_boss, D_hole, rib_th), затем используй их в построении.
2. Создавай ТОЛЬКО ТЕ ЭЛЕМЕНТЫ, которые есть на чертеже/изображении.
3. Каждое отверстие создается РОВНО 1 РАЗ.
4. В конце скрипта ОБЯЗАТЕЛЬНО напиши: print("Модель успешно построена") и закрой блок ```.

### ПРИМЕР (Основание со стойкой, центральным отверстием и ребрами):
```python
# Размеры (в мм):
L = mm(100)      # длина основания
W = mm(60)       # ширина основания
H_base = mm(20)  # толщина основания
D_boss = mm(40)  # диаметр стойки
H_boss = mm(60)  # общая высота стойки
D_hole = mm(20)  # диаметр центрального отверстия
rib_th = mm(10)  # толщина ребра жесткости

# ШАГ 1: Горизонтальное основание на плоскости XZ (wp.Item(2))
sk_base = comp_def.Sketches.Add(wp.Item(2))
draw_rectangle(sk_base, -L/2, -W/2, L/2, W/2)
extrude(sk_base, H_base)

# ШАГ 2: Центральная цилиндрическая стойка (на плоскости XZ, выдавливание вверх)
sk_boss = comp_def.Sketches.Add(wp.Item(2))
draw_circle(sk_boss, 0, 0, D_boss/2)
extrude(sk_boss, H_boss)

# ШАГ 3: Центральное сквозное отверстие
sk_hole = comp_def.Sketches.Add(wp.Item(2))
cut_hole(sk_hole, 0, 0, diameter=D_hole)

# ШАГ 4: Ребра жесткости на вертикальной плоскости XY (wp.Item(3))
# На плоскости XY: ось X — длина основания, ось Y — высота от стола вверх
sk_rib1 = comp_def.Sketches.Add(wp.Item(3))
draw_polygon(sk_rib1, [(D_boss/2, H_base), (L/2, H_base), (D_boss/2, H_boss - mm(5))])
ext_def1 = comp_def.Features.ExtrudeFeatures.CreateExtrudeDefinition(sk_rib1.Profiles.AddForSolid(), kJoinOperation)
ext_def1.SetDistanceExtent(rib_th, kSymmetricExtentDirection)
comp_def.Features.ExtrudeFeatures.Add(ext_def1)

sk_rib2 = comp_def.Sketches.Add(wp.Item(3))
draw_polygon(sk_rib2, [(-D_boss/2, H_base), (-L/2, H_base), (-D_boss/2, H_boss - mm(5))])
ext_def2 = comp_def.Features.ExtrudeFeatures.CreateExtrudeDefinition(sk_rib2.Profiles.AddForSolid(), kJoinOperation)
ext_def2.SetDistanceExtent(rib_th, kSymmetricExtentDirection)
comp_def.Features.ExtrudeFeatures.Add(ext_def2)

print("Модель успешно построена")
```
"""


class LLMClient:
    def __init__(self):
        pass

    def extract_code_block(self, text: str) -> str:
        """Extract Python code from markdown response, handling closed, unclosed, or raw blocks."""
        if not text:
            return ""

        # Pattern 1: Complete closed markdown block ```python ... ```
        m = re.search(r"```(?:python|py)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
        if m:
            clean = m.group(1).strip()
            if clean:
                return clean

        # Pattern 2: Unclosed markdown block ```python ... (e.g. stopped at EOS before closing ```)
        m_unclosed = re.search(r"```(?:python|py)?\s*\n?(.*)", text, re.DOTALL | re.IGNORECASE)
        if m_unclosed:
            clean = m_unclosed.group(1).strip()
            clean = re.sub(r"```\s*$", "", clean).strip()
            if clean:
                return clean

        # Pattern 3: Fallback - remove any stray markdown fence lines
        lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
        return "\n".join(lines).strip()

    def generate_code(
        self,
        prompt: str,
        drawing_data: Optional[Dict[str, Any]] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        lm_url: Optional[str] = None,
        timeout: Optional[int] = None,
        phased: bool = False,
        status_cb = None,
    ) -> str:
        """Generate Autodesk Inventor Python COM API code based on prompt and optional drawing."""
        provider = provider or Config.get_provider()

        if provider == "gemini":
            return self._generate_gemini(
                prompt=prompt,
                drawing_data=drawing_data,
                model_name=model_name or Config.get_gemini_model(),
                api_key=api_key or Config.get_gemini_api_key(),
            )
        elif provider == "lm_studio":
            return self._generate_lm_studio(
                prompt=prompt,
                drawing_data=drawing_data,
                model_name=model_name or Config.get_lm_studio_model(),
                lm_url=lm_url or Config.get_lm_studio_url(),
                timeout=timeout or Config.get_lm_studio_timeout(),
                phased=phased,
                status_cb=status_cb,
            )
        else:
            raise ValueError(f"Неизвестный провайдер: {provider}. Допустимы: 'gemini' или 'lm_studio'")

    def _generate_gemini(
        self,
        prompt: str,
        drawing_data: Optional[Dict[str, Any]],
        model_name: str,
        api_key: str,
    ) -> str:
        """Call Google Gemini API via official google-genai SDK."""
        if not api_key:
            raise ValueError("Не указан Google Gemini API ключ. Укажите его в настройках или .env файле.")

        client = genai.Client(api_key=api_key)

        contents = []

        # If drawing file is attached
        drawing_context = ""
        if drawing_data:
            summary = drawing_data.get("text_summary", "")
            raw_bytes = drawing_data.get("raw_bytes")
            mime_type = drawing_data.get("mime_type", "application/pdf")
            file_name = drawing_data.get("file_name", "drawing")

            drawing_context = f"\n\n[ПРИКРЕПЛЕН 2D ЧЕРТЕЖ: {file_name}]\n{summary}"

            # Pass binary drawing content to Gemini (PDF or images)
            if raw_bytes and mime_type in ("application/pdf", "image/png", "image/jpeg", "image/bmp"):
                part = types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)
                contents.append(part)

        user_text = (
            f"Запрос пользователя на построение 3D-модели в Autodesk Inventor:\n{prompt}"
            f"{drawing_context}\n\n"
            f"Сгенерируй точный, готовый к запуску Python код для построения данной 3D модели."
        )
        contents.append(user_text)

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=INVENTOR_SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )

        return self.extract_code_block(response.text)

    def _generate_lm_studio(
        self,
        prompt: str,
        drawing_data: Optional[Dict[str, Any]],
        model_name: str,
        lm_url: str,
        timeout: int = 600,
        phased: bool = True,
        status_cb = None,
    ) -> str:
        """
        Call LM Studio local OpenAI-compatible endpoint.
        If phased=True and image attached, uses 2-stage prompt (extract dimensions first, then code).
        """
        url = lm_url.rstrip("/") + "/chat/completions"

        # Check if we should use 2-stage phased generation
        has_image = bool(drawing_data and drawing_data.get("image_base64"))
        
        if phased and has_image:
            b64_image = drawing_data.get("image_base64")
            mime_type = drawing_data.get("mime_type", "image/jpeg")
            file_name = drawing_data.get("file_name", "drawing")

            # --- ЭТАП 1: Быстрое считывание размеров чертежа ---
            if status_cb:
                status_cb("Порция 1/2: Анализ чертежа и распознавание размеров...")

            phase1_payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Внимательно изучи прикрепленный 2D чертеж/эскиз. "
                                    "Твоя задача — извлечь ВСЕ числовые размеры и геометрию детали для САПР.\n"
                                    "Выпиши кратко по пунктам:\n"
                                    "1. Форма и габаритные размеры основания (длина, ширина, толщина, радиусы скруглений фланцев).\n"
                                    "2. Дополнительные стойки и объемы (размеры сечений, высоты, положение).\n"
                                    "3. Отверстия и вырезы (диаметры, глубины, координаты центров).\n"
                                    "4. Пазы, ребра жесткости и фаски (толщины, размеры).\n"
                                    "Пиши только структурированный список размеров, без Python кода."
                                )
                            }
                        ]
                    }
                ],
                "temperature": 0.1,
                "presence_penalty": 0.2,
                "max_tokens": 1000,
                "stop": ["<|im_end|>", "<|endoftext|>"],
                "stream": False,
            }

            try:
                r1 = requests.post(url, json=phase1_payload, timeout=timeout)
                if not r1.ok:
                    self._handle_http_error(r1, model_name)
                dims_text = r1.json()["choices"][0]["message"]["content"].strip()
                if status_cb:
                    status_cb(f"Размеры с чертежа успешно извлечены:\n{dims_text[:150]}...")
            except requests.exceptions.Timeout:
                raise TimeoutError(
                    f"Превышено время ожидания при анализе чертежа в LM Studio ({timeout} сек).\n"
                    f"Увеличьте GPU Offload в LM Studio или используйте Google Gemini."
                )

            # --- ЭТАП 2: Пошаговая генерация Python-кода по распознанным размерам ---
            if status_cb:
                status_cb("Порция 2/2: Генерация пошагового CAD-кода для Autodesk Inventor...")

            phase2_msg = (
                f"Пользовательский запрос: {prompt}\n\n"
                f"[ТОЧНЫЕ ЧИСЛОВЫЕ РАЗМЕРЫ С ЧЕРТЕЖА {file_name}]:\n{dims_text}\n\n"
                f"Сгенерируй точный пошаговый Python код для Autodesk Inventor 2025 с разбивкой на шаги:\n"
                f"# ШАГ 1: Базовые габариты основания\n"
                f"# ШАГ 2: Вторичные объемы (стойки, выступы)\n"
                f"# ШАГ 3: Отверстия и вырезы\n"
                f"# ШАГ 4: Ребра жесткости и пазы\n"
                f"# ШАГ 5: Скругления и фаски\n"
                f"Обязательно используй готовые CAD-хелперы: draw_rectangle, draw_polygon, draw_circle, extrude, cut."
            )

            phase2_payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": INVENTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": phase2_msg}
                ],
                "temperature": 0.1,
                "presence_penalty": 0.5,
                "max_tokens": 2500,
                "stop": ["```\n\n", "<|im_end|>", "<|endoftext|>"],
                "stream": False,
            }

            try:
                r2 = requests.post(url, json=phase2_payload, timeout=timeout)
                if not r2.ok:
                    self._handle_http_error(r2, model_name)
                code_reply = r2.json()["choices"][0]["message"]["content"]
                return self.extract_code_block(code_reply)
            except requests.exceptions.Timeout:
                raise TimeoutError(
                    f"Превышено время ожидания генерации кода в LM Studio ({timeout} сек)."
                )

        # Standard monolithic call
        messages = [
            {"role": "system", "content": INVENTOR_SYSTEM_PROMPT},
        ]
        user_content: List[Dict[str, Any]] = []
        drawing_context = ""
        if drawing_data:
            summary = drawing_data.get("text_summary", "")
            b64_image = drawing_data.get("image_base64")
            mime_type = drawing_data.get("mime_type", "image/jpeg")
            file_name = drawing_data.get("file_name", "drawing")
            drawing_context = f"\n\n[ПРИКРЕПЛЕН ЧЕРТЕЖ: {file_name}]\n{summary}"

            if b64_image and mime_type.startswith("image/"):
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}
                })

        text_msg = (
            f"Запрос пользователя на построение 3D-модели в Autodesk Inventor:\n{prompt}"
            f"{drawing_context}\n\n"
            f"Сгенерируй точный, готовый к запуску Python код для построения данной 3D модели."
        )

        if user_content:
            user_content.append({"type": "text", "text": text_msg})
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": text_msg})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.1,
            "presence_penalty": 0.5,
            "max_tokens": 2500,
            "stop": ["```\n\n", "<|im_end|>", "<|endoftext|>"],
            "stream": False,
        }

        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            if not resp.ok:
                self._handle_http_error(resp, model_name)
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            return self.extract_code_block(reply)
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"Превышено время ожидания ответа от LM Studio ({timeout} сек).\n"
                f"Локальная модель на вашей видеокарте/процессоре выполняла инференс слишком долго.\n"
                f"Совет:\n"
                f"1. В настройках LM Studio увеличьте параметр GPU Offload (перенесите больше слоев на видеокарту).\n"
                f"2. Включите галочку 'Порционная генерация чертежей' в интерфейсе.\n"
                f"3. Либо переключитесь на Google Gemini (AI Studio), который отвечает за 2-4 секунды."
            )
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Не удалось подключиться к LM Studio по адресу {lm_url}.\n"
                f"Убедитесь, что LM Studio запущен и в меню 'Local Server' включен сервер (порт 1234)."
            )
        except (ValueError, RuntimeError, TimeoutError):
            raise
        except Exception as e:
            raise RuntimeError(f"Ошибка вызова LM Studio: {e}")

    def _handle_http_error(self, resp: requests.Response, model_name: str):
        """Parse error details from HTTP response."""
        try:
            err_json = resp.json()
            err_msg = err_json.get("error", {}).get("message", resp.text)
            if "does not support image inputs" in err_msg:
                raise ValueError(
                    f"Модель '{model_name}' в LM Studio не поддерживает обработку изображений/чертежей!\n\n"
                    f"Решение:\n"
                    f"1. В LM Studio загрузите мультимодальную модель (например, qwen/qwen3-vl-8b).\n"
                    f"2. Либо переключитесь на Google AI Studio (Gemini), которая нативно распознает PDF и изображения.\n"
                    f"3. Либо удалите прикрепленный чертеж и введите текстовое описание детали."
                )
            raise RuntimeError(f"LM Studio вернул ошибку: {err_msg}")
        except (ValueError, RuntimeError):
            raise
        except Exception:
            resp.raise_for_status()

    @staticmethod
    def fetch_lm_studio_models(lm_url: str = "http://localhost:1234/v1") -> List[str]:
        """Fetch list of available models from running LM Studio instance."""
        url = lm_url.rstrip("/") + "/models"
        try:
            r = requests.get(url, timeout=5)
            if r.ok:
                data = r.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return []

    def fix_code(
        self,
        original_code: str,
        error_message: str,
        original_prompt: str,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        lm_url: Optional[str] = None,
    ) -> str:
        """Self-correction: send error traceback back to LLM to fix the script."""
        fix_prompt = (
            f"Предыдущий скрипт для Autodesk Inventor завершился с ошибкой.\n\n"
            f"ИСХОДНЫЙ ЗАПРОС: {original_prompt}\n\n"
            f"КОД С ОШИБКОЙ:\n```python\n{original_code}\n```\n\n"
            f"ОШИБКА COM API / PYTHON:\n{error_message}\n\n"
            f"Исправь ошибку и верни полностью исправленный, готовый к запуску Python код для Inventor."
        )

        return self.generate_code(
            prompt=fix_prompt,
            drawing_data=None,
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            lm_url=lm_url,
        )
