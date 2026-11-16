"""
Verification test suite for Inventor LLM integration.
Tests configuration, inventor bridge, drawing parser, and LLM code extraction.
"""

import sys
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.inventor_bridge import InventorBridge
from src.drawing_parser import DrawingParser
from src.llm_client import LLMClient


class TestInventorLLM(unittest.TestCase):
    def test_config(self):
        self.assertIsNotNone(Config.get_provider())
        self.assertIsNotNone(Config.get_drawings_dir())
        self.assertTrue(Config.get_drawings_dir().exists())

    def test_inventor_connection(self):
        bridge = InventorBridge()
        ok, msg = bridge.connect()
        self.assertTrue(ok, f"Inventor connection failed: {msg}")
        self.assertTrue(bridge.is_connected())
        status = bridge.get_status_info()
        self.assertTrue(status["connected"])
        self.assertIn("2025", status["version"])

    def test_code_extraction(self):
        client = LLMClient()
        markdown_text = """
        Вот ваш код:
        ```python
        # Test code
        x = 10
        print("Hello Inventor")
        ```
        Все готово!
        """
        code = client.extract_code_block(markdown_text)
        self.assertIn('print("Hello Inventor")', code)
        self.assertNotIn("```", code)

        # Unclosed markdown block test
        unclosed_text = "```python\nx = 20\nprint('Unclosed')"
        code_unclosed = client.extract_code_block(unclosed_text)
        self.assertIn("print('Unclosed')", code_unclosed)
        self.assertNotIn("```", code_unclosed)

        # Bridge sanitization
        bridge = InventorBridge()
        clean = bridge.clean_code("```python\na = 1\n```")
        self.assertEqual(clean, "a = 1")

    def test_inventor_safe_execution(self):
        bridge = InventorBridge()
        test_script = """
# Test non-destructive calculation
test_point = tg.CreatePoint2d(mm(50), mm(20))
print(f"Transient point created: X={test_point.X}, Y={test_point.Y}")
"""
        success, out, err = bridge.execute_code(test_script, use_transaction=False)
    def test_step_by_step_execution(self):
        bridge = InventorBridge()
        test_script = """
# ШАГ 1: Тестовая точка 1
pt1 = tg.CreatePoint2d(mm(10), mm(20))

# ШАГ 2: Тестовая точка 2
pt2 = tg.CreatePoint2d(mm(30), mm(40))
"""
        steps_recorded = []
        def cb(idx, total, title, status, err):
            if status == "success":
                steps_recorded.append((idx, title))

        success, out, err = bridge.execute_code_step_by_step(test_script, step_callback=cb, step_delay=0.0)
        self.assertTrue(success, f"Step execution failed: {err}")
        self.assertEqual(len(steps_recorded), 2)


if __name__ == "__main__":
    unittest.main()
