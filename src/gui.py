"""
PySide6 Graphical Interface for Autodesk Inventor 2025 + LLM integration.
Modern CAD dark theme, asynchronous execution, model/provider switching,
drawing importer, code viewer, and auto-fix capabilities.
"""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QPlainTextEdit, QLineEdit,
    QComboBox, QRadioButton, QButtonGroup, QTabWidget, QFileDialog,
    QProgressBar, QGroupBox, QSplitter, QMessageBox, QCheckBox,
    QFrame, QSizePolicy, QSpinBox
)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QSyntaxHighlighter, QIcon

from src.config import Config
from src.inventor_bridge import InventorBridge
from src.drawing_parser import DrawingParser
from src.llm_client import LLMClient


class PythonHighlighter(QSyntaxHighlighter):
    """Simple syntax highlighter for Python code in Inventor viewer."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rules = []

        # Keywords
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor("#569cd6"))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "def", "class", "import", "from", "return", "if", "elif", "else",
            "for", "while", "try", "except", "finally", "with", "as", "in",
            "is", "not", "and", "or", "True", "False", "None"
        ]
        for kw in keywords:
            self.rules.append((rf"\b{kw}\b", kw_fmt))

        # Strings
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor("#ce9178"))
        self.rules.append((r'"[^"\\]*(\\.[^"\\]*)*"', str_fmt))
        self.rules.append((r"'[^'\\]*(\\.[^'\\]*)*'", str_fmt))

        # Comments
        cmt_fmt = QTextCharFormat()
        cmt_fmt.setForeground(QColor("#6a9955"))
        self.rules.append((r"#[^\n]*", cmt_fmt))

        # Numbers
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor("#b5cea8"))
        self.rules.append((r"\b\d+(\.\d+)?\b", num_fmt))

        # Inventor helpers
        inv_fmt = QTextCharFormat()
        inv_fmt.setForeground(QColor("#4ec9b0"))
        inv_fmt.setFontWeight(QFont.Weight.Bold)
        for sym in ["comp_def", "tg", "wp", "mm", "deg", "app", "doc", "Sketches", "Features"]:
            self.rules.append((rf"\b{sym}\b", inv_fmt))

    def highlightBlock(self, text):
        import re
        for pattern, fmt in self.rules:
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class GenerationWorker(QThread):
    """Background worker for LLM code generation and optional Inventor execution."""
    log_signal = Signal(str, str) # message, level ("info", "success", "error", "warning")
    code_ready_signal = Signal(str)
    finished_signal = Signal(bool, str) # success, message
    
    def __init__(
        self,
        prompt: str,
        drawing_path: Optional[str],
        provider: str,
        model_name: str,
        api_key: str,
        lm_url: str,
        execute_in_inventor: bool,
        auto_fix: bool,
        bridge: InventorBridge,
        pre_existing_code: Optional[str] = None,
        step_by_step: bool = True,
        lm_timeout: int = 600,
        phased: bool = True
    ):
        super().__init__()
        self.prompt = prompt
        self.drawing_path = drawing_path
        self.provider = provider
        self.model_name = model_name
        self.api_key = api_key
        self.lm_url = lm_url
        self.execute_in_inventor = execute_in_inventor
        self.auto_fix = auto_fix
        self.bridge = bridge
        self.pre_existing_code = pre_existing_code
        self.step_by_step = step_by_step
        self.lm_timeout = lm_timeout
        self.phased = phased

    def run(self):
        llm = LLMClient()
        code = self.pre_existing_code

        # Step 1: Parse drawing if provided
        drawing_data = None
        if self.drawing_path:
            self.log_signal.emit(f"Обработка чертежа: {os.path.basename(self.drawing_path)}...", "info")
            try:
                parser = DrawingParser(self.bridge)
                drawing_data = parser.process_drawing(self.drawing_path)
                self.log_signal.emit(f"Чертеж успешно проанализирован ({drawing_data['type']}).", "success")
            except Exception as e:
                self.log_signal.emit(f"Ошибка чтения чертежа: {e}", "warning")

        # Step 2: Generate code if not just executing pre-existing code
        if not code:
            self.log_signal.emit(f"Отправка запроса в {self.provider.upper()} ({self.model_name})...", "info")
            try:
                def _llm_status_cb(msg):
                    self.log_signal.emit(msg, "info")

                code = llm.generate_code(
                    prompt=self.prompt,
                    drawing_data=drawing_data,
                    provider=self.provider,
                    model_name=self.model_name,
                    api_key=self.api_key,
                    lm_url=self.lm_url,
                    timeout=self.lm_timeout,
                    phased=self.phased,
                    status_cb=_llm_status_cb
                )
                self.code_ready_signal.emit(code)
                self.log_signal.emit("Код для Autodesk Inventor успешно сгенерирован!", "success")
            except Exception as e:
                self.log_signal.emit(f"Ошибка генерации кода: {str(e)}", "error")
                self.finished_signal.emit(False, str(e))
                return

        # If only code generation was requested
        if not self.execute_in_inventor:
            self.finished_signal.emit(True, "Код успешно сформирован.")
            return

        # Step 3: Execute in Inventor
        if self.step_by_step:
            self.log_signal.emit("Запуск пошагового построения в Autodesk Inventor...", "info")
            def _step_cb(idx, total, title, status, err):
                if status == "running":
                    self.log_signal.emit(f"[{idx}/{total}] {title}...", "info")
                elif status == "success":
                    self.log_signal.emit(f"[OK] [{idx}/{total}] {title} — успешно!", "success")
                elif status == "warning":
                    self.log_signal.emit(f"[WARN] [{idx}/{total}] {title} ({err})", "warning")
                elif status == "error":
                    self.log_signal.emit(f"[ERR] [{idx}/{total}] {title} — сбой", "error")

            success, out_msg, err_msg = self.bridge.execute_code_step_by_step(
                code, step_callback=_step_cb, step_delay=0.4
            )
        else:
            self.log_signal.emit("Выполнение скрипта в Autodesk Inventor 2025...", "info")
            success, out_msg, err_msg = self.bridge.execute_code(code)

        if success:
            if out_msg:
                self.log_signal.emit(f"Inventor stdout:\n{out_msg}", "info")
            self.log_signal.emit("3D модель успешно построена в Autodesk Inventor!", "success")
            self.finished_signal.emit(True, "3D модель успешно создана!")
            return

        # Error handling & Auto-fix loop
        self.log_signal.emit(f"Ошибка при построении в Inventor:\n{err_msg}", "error")

        if not self.auto_fix:
            self.finished_signal.emit(False, f"Ошибка построения: {err_msg}")
            return

        # Attempt auto-correction
        self.log_signal.emit("Включено авто-исправление. Отправка ошибки в LLM для корректировки...", "warning")
        try:
            fixed_code = llm.fix_code(
                original_code=code,
                error_message=err_msg,
                original_prompt=self.prompt,
                provider=self.provider,
                model_name=self.model_name,
                api_key=self.api_key,
                lm_url=self.lm_url
            )
            self.code_ready_signal.emit(fixed_code)
            self.log_signal.emit("Исправленный код получен. Повторный запуск в Inventor...", "info")
            
            success2, out_msg2, err_msg2 = self.bridge.execute_code(fixed_code)
            if success2:
                self.log_signal.emit("Исправленный скрипт успешно выполнил построение 3D детали!", "success")
                self.finished_signal.emit(True, "Модель создана после авто-исправления!")
            else:
                self.log_signal.emit(f"Повторная ошибка построения:\n{err_msg2}", "error")
                self.finished_signal.emit(False, "Не удалось исправить ошибку автоматически.")
        except Exception as e:
            self.log_signal.emit(f"Сбой при попытке исправления: {e}", "error")
            self.finished_signal.emit(False, str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kinetix — CAD AI Assistant (Autodesk Inventor 2025)")
        self.resize(1100, 780)

        self.bridge = InventorBridge()
        self.selected_drawing_path: Optional[str] = None
        self.worker: Optional[GenerationWorker] = None

        self._init_ui()
        self._apply_styles()
        self.refresh_inventor_status()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # 1. Top Header: Status bar & Reconnect
        header_frame = QFrame()
        header_frame.setObjectName("headerFrame")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(12, 8, 12, 8)

        self.lbl_status_indicator = QLabel("●")
        self.lbl_status_indicator.setObjectName("statusDot")
        self.lbl_status_text = QLabel("Проверка подключения к Inventor...")
        self.lbl_status_text.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

        self.lbl_active_doc = QLabel("Активная деталь: -")
        self.lbl_active_doc.setStyleSheet("color: #a0a0a0; font-style: italic;")

        btn_reconnect = QPushButton("Переподключить Inventor")
        btn_reconnect.clicked.connect(self.refresh_inventor_status)

        btn_new_part = QPushButton("Создать новую деталь")
        btn_new_part.clicked.connect(self.on_create_new_part)

        header_layout.addWidget(self.lbl_status_indicator)
        header_layout.addWidget(self.lbl_status_text)
        header_layout.addSpacing(20)
        header_layout.addWidget(self.lbl_active_doc)
        header_layout.addStretch()
        header_layout.addWidget(btn_new_part)
        header_layout.addWidget(btn_reconnect)

        main_layout.addWidget(header_frame)

        # 2. Main content split into Left (Controls) and Right (Output/Tabs)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left Panel ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        # Provider Settings Group
        grp_settings = QGroupBox("Настройки LLM")
        settings_layout = QVBoxLayout(grp_settings)

        # Provider radio buttons
        prov_layout = QHBoxLayout()
        self.rb_gemini = QRadioButton("Google AI Studio (Gemini)")
        self.rb_lmstudio = QRadioButton("LM Studio (Локально)")
        self.provider_group = QButtonGroup()
        self.provider_group.addButton(self.rb_gemini)
        self.provider_group.addButton(self.rb_lmstudio)

        prov = Config.get_provider()
        if prov == "lm_studio":
            self.rb_lmstudio.setChecked(True)
        else:
            self.rb_gemini.setChecked(True)

        self.rb_gemini.toggled.connect(self.on_provider_changed)
        prov_layout.addWidget(self.rb_gemini)
        prov_layout.addWidget(self.rb_lmstudio)
        settings_layout.addLayout(prov_layout)

        # Gemini controls
        self.widget_gemini = QWidget()
        gemini_layout = QVBoxLayout(self.widget_gemini)
        gemini_layout.setContentsMargins(0, 4, 0, 0)

        lbl_api = QLabel("Gemini API Key:")
        self.txt_gemini_key = QLineEdit(Config.get_gemini_api_key())
        self.txt_gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_gemini_key.setPlaceholderText("Вставьте AI Studio API Key (AIzaSy...)")

        key_row = QHBoxLayout()
        key_row.addWidget(self.txt_gemini_key)
        self.btn_toggle_eye = QPushButton("Показать")
        self.btn_toggle_eye.setFixedWidth(70)
        self.btn_toggle_eye.clicked.connect(self.toggle_key_visibility)
        key_row.addWidget(self.btn_toggle_eye)

        model_row = QHBoxLayout()
        lbl_mod = QLabel("Модель:")
        self.cb_gemini_model = QComboBox()
        self.cb_gemini_model.addItems(["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"])
        cur_mod = Config.get_gemini_model()
        if cur_mod in ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]:
            self.cb_gemini_model.setCurrentText(cur_mod)
        model_row.addWidget(lbl_mod)
        model_row.addWidget(self.cb_gemini_model)

        gemini_layout.addWidget(lbl_api)
        gemini_layout.addLayout(key_row)
        gemini_layout.addLayout(model_row)
        settings_layout.addWidget(self.widget_gemini)

        # LM Studio controls
        self.widget_lmstudio = QWidget()
        lm_layout = QVBoxLayout(self.widget_lmstudio)
        lm_layout.setContentsMargins(0, 4, 0, 0)

        lbl_lm_url = QLabel("LM Studio URL:")
        self.txt_lm_url = QLineEdit(Config.get_lm_studio_url())
        self.txt_lm_url.setPlaceholderText("http://localhost:1234/v1")

        lbl_lm_mod = QLabel("Модель в LM Studio:")
        lm_mod_row = QHBoxLayout()
        self.cb_lm_model = QComboBox()
        self.cb_lm_model.setEditable(True)
        cur_lm_mod = Config.get_lm_studio_model()
        self.cb_lm_model.addItem(cur_lm_mod)
        self.cb_lm_model.setCurrentText(cur_lm_mod)

        self.btn_refresh_lm_models = QPushButton("Список моделей")
        self.btn_refresh_lm_models.setToolTip("Запросить доступные модели из LM Studio")
        self.btn_refresh_lm_models.clicked.connect(self.on_refresh_lm_models)

        lm_mod_row.addWidget(self.cb_lm_model, 3)
        lm_mod_row.addWidget(self.btn_refresh_lm_models, 2)

        timeout_row = QHBoxLayout()
        lbl_to = QLabel("Таймаут (сек):")
        self.spn_lm_timeout = QSpinBox()
        self.spn_lm_timeout.setRange(30, 3600)
        self.spn_lm_timeout.setValue(Config.get_lm_studio_timeout())
        self.spn_lm_timeout.setSingleStep(60)
        self.spn_lm_timeout.setToolTip("Максимальное время ожидания ответа от локальной модели в LM Studio")
        timeout_row.addWidget(lbl_to)
        timeout_row.addWidget(self.spn_lm_timeout)

        self.chk_phased = QCheckBox("Двухэтапный режим (размеры → код; для слабых ПК)")
        self.chk_phased.setToolTip(
            "Выключено (по умолчанию) = быстрый прямой режим (15-20 сек для Qwen 7B / Gemini).\n"
            "Включено = порционная отправка в 2 запроса (для слабых моделей 3B/4B с нехваткой VRAM)."
        )
        self.chk_phased.setChecked(False)

        lm_layout.addWidget(lbl_lm_url)
        lm_layout.addWidget(self.txt_lm_url)
        lm_layout.addWidget(lbl_lm_mod)
        lm_layout.addLayout(lm_mod_row)
        lm_layout.addLayout(timeout_row)
        lm_layout.addWidget(self.chk_phased)
        settings_layout.addWidget(self.widget_lmstudio)

        # Save settings button
        btn_save_cfg = QPushButton("Сохранить настройки в .env")
        btn_save_cfg.clicked.connect(self.on_save_config)
        settings_layout.addWidget(btn_save_cfg)

        left_layout.addWidget(grp_settings)

        # Drawing Import Group
        grp_drawing = QGroupBox("Импорт 2D-чертежа (PDF / DWG / DXF / PNG)")
        drawing_layout = QVBoxLayout(grp_drawing)

        draw_btn_row = QHBoxLayout()
        btn_select_drawing = QPushButton("Выбрать чертеж")
        btn_select_drawing.clicked.connect(self.on_select_drawing)

        btn_open_folder = QPushButton("Открыть папку drawings")
        btn_open_folder.clicked.connect(self.on_open_drawings_dir)

        draw_btn_row.addWidget(btn_select_drawing)
        draw_btn_row.addWidget(btn_open_folder)
        drawing_layout.addLayout(draw_btn_row)

        self.lbl_selected_file = QLabel("Чертеж не выбран (построение только по тексту)")
        self.lbl_selected_file.setStyleSheet("color: #888888; font-size: 11px;")
        self.lbl_selected_file.setWordWrap(True)

        clear_row = QHBoxLayout()
        clear_row.addWidget(self.lbl_selected_file)
        self.btn_clear_drawing = QPushButton("Очистить")
        self.btn_clear_drawing.setFixedWidth(70)
        self.btn_clear_drawing.setVisible(False)
        self.btn_clear_drawing.clicked.connect(self.on_clear_drawing)
        clear_row.addWidget(self.btn_clear_drawing)

        drawing_layout.addLayout(clear_row)
        left_layout.addWidget(grp_drawing)

        # Prompt input Group
        grp_prompt = QGroupBox("Промпт для 3D-моделирования")
        prompt_layout = QVBoxLayout(grp_prompt)

        self.txt_prompt = QTextEdit()
        self.txt_prompt.setPlaceholderText(
            "Опишите деталь для построения в Autodesk Inventor...\n\n"
            "Пример: 'Создай круглый фланец диаметром 160 мм, толщиной 20 мм. "
            "По центру сквозное отверстие диаметром 40 мм. "
            "На диаметре 120 мм расположи 6 крепежных отверстий диаметром 12 мм.'"
        )
        self.txt_prompt.setMinimumHeight(140)
        prompt_layout.addWidget(self.txt_prompt)

        # Quick template chips
        tpl_label = QLabel("Быстрые примеры:")
        tpl_label.setStyleSheet("color: #888; font-size: 11px;")
        prompt_layout.addWidget(tpl_label)

        tpl_row = QHBoxLayout()
        btn_tpl1 = QPushButton("Фланец")
        btn_tpl1.clicked.connect(lambda: self.set_template_prompt(
            "Создай круглый фланец диаметром 150 мм и толщиной 18 мм. "
            "Центральное сквозное отверстие диаметром 45 мм. "
            "Крепежные отверстия: 6 отверстий диаметром 12 мм на делительной окружности 110 мм."
        ))
        btn_tpl2 = QPushButton("Ступенчатый вал")
        btn_tpl2.clicked.connect(lambda: self.set_template_prompt(
            "Создай ступенчатый цилиндрический вал: "
            "Первая ступень: диаметр 60 мм, длина 40 мм. "
            "Вторая ступень: диаметр 40 мм, длина 70 мм. "
            "Третья ступень: диаметр 25 мм, длина 30 мм с центральным осевым отверстием 10 мм глубиной 20 мм."
        ))
        btn_tpl3 = QPushButton("Кронштейн с пазом")
        btn_tpl3.clicked.connect(lambda: self.set_template_prompt(
            "Создай плоский монтажный кронштейн 120х80 мм толщиной 15 мм. "
            "В центре прямоугольный вырез 50х30 мм. "
            "По четырем углам отверстия диаметром 9 мм на расстоянии 12 мм от краев."
        ))
        tpl_row.addWidget(btn_tpl1)
        tpl_row.addWidget(btn_tpl2)
        tpl_row.addWidget(btn_tpl3)
        prompt_layout.addLayout(tpl_row)

        left_layout.addWidget(grp_prompt)

        # Execution controls
        self.chk_autofix = QCheckBox("Автоматически исправлять ошибки (Auto-fix loop)")
        self.chk_autofix.setChecked(True)
        left_layout.addWidget(self.chk_autofix)

        self.chk_step_by_step = QCheckBox("Пошаговое построение в Inventor (с визуализацией)")
        self.chk_step_by_step.setChecked(True)
        left_layout.addWidget(self.chk_step_by_step)

        btn_action_row = QHBoxLayout()
        self.btn_run_all = QPushButton("Сгенерировать и построить в Inventor")
        self.btn_run_all.setObjectName("btnPrimary")
        self.btn_run_all.setMinimumHeight(42)
        self.btn_run_all.clicked.connect(self.on_run_generate_and_build)

        self.btn_gen_only = QPushButton("Только код")
        self.btn_gen_only.setMinimumHeight(42)
        self.btn_gen_only.clicked.connect(self.on_run_generate_only)

        btn_action_row.addWidget(self.btn_run_all, 3)
        btn_action_row.addWidget(self.btn_gen_only, 1)
        left_layout.addLayout(btn_action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Indeterminate
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # --- Right Panel (Tabs: Code Viewer & Logs) ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)

        self.tab_widget = QTabWidget()

        # Tab 1: Python Code
        tab_code = QWidget()
        tab_code_layout = QVBoxLayout(tab_code)
        tab_code_layout.setContentsMargins(4, 4, 4, 4)

        code_toolbar = QHBoxLayout()
        btn_run_existing_code = QPushButton("Выполнить этот код в Inventor")
        btn_run_existing_code.clicked.connect(self.on_execute_editor_code)

        btn_copy_code = QPushButton("Скопировать код")
        btn_copy_code.clicked.connect(self.on_copy_code)

        code_toolbar.addWidget(btn_run_existing_code)
        code_toolbar.addWidget(btn_copy_code)
        code_toolbar.addStretch()
        tab_code_layout.addLayout(code_toolbar)

        self.editor_code = QPlainTextEdit()
        self.editor_code.setFont(QFont("Consolas", 10))
        self.highlighter = PythonHighlighter(self.editor_code.document())
        self.editor_code.setPlaceholderText("# Здесь появится сгенерированный Python код для Inventor COM API...")
        tab_code_layout.addWidget(self.editor_code)

        self.tab_widget.addTab(tab_code, "Python код для Inventor")

        # Tab 2: Execution Log
        tab_log = QWidget()
        tab_log_layout = QVBoxLayout(tab_log)
        tab_log_layout.setContentsMargins(4, 4, 4, 4)

        log_toolbar = QHBoxLayout()
        btn_clear_log = QPushButton("Очистить лог")
        btn_clear_log.clicked.connect(self.on_clear_log)
        log_toolbar.addStretch()
        log_toolbar.addWidget(btn_clear_log)
        tab_log_layout.addLayout(log_toolbar)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Consolas", 9))
        tab_log_layout.addWidget(self.txt_log)

        self.tab_widget.addTab(tab_log, "Лог выполнения")

        right_layout.addWidget(self.tab_widget)
        splitter.addWidget(right_widget)

        # Set splitter proportions (45% left, 55% right)
        splitter.setSizes([480, 580])
        main_layout.addWidget(splitter)
        self.on_provider_changed()

    def _apply_styles(self):
        """Dark theme stylesheet matching Inventor styling."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
                color: #d4d4d4;
            }
            QWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            #headerFrame {
                background-color: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 6px;
            }
            #statusDot {
                font-size: 16px;
                color: #e51400;
            }
            QGroupBox {
                border: 1px solid #3c3c3c;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 14px;
                font-weight: bold;
                color: #cccccc;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
                background-color: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                color: #f1f1f1;
                padding: 6px;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border: 1px solid #007acc;
            }
            QPushButton {
                background-color: #333333;
                border: 1px solid #444444;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #3e3e42;
                border: 1px solid #007acc;
            }
            QPushButton:pressed {
                background-color: #007acc;
            }
            #btnPrimary {
                background-color: #0e639c;
                border: 1px solid #1177bb;
                font-weight: bold;
                font-size: 13px;
            }
            #btnPrimary:hover {
                background-color: #1177bb;
            }
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                background-color: #252526;
                border-radius: 4px;
            }
            QTabBar::tab {
                background-color: #2d2d30;
                color: #999999;
                padding: 8px 16px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #252526;
                color: #ffffff;
                font-weight: bold;
                border-bottom: 2px solid #007acc;
            }
            QProgressBar {
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                text-align: center;
                background-color: #252526;
                height: 12px;
            }
            QProgressBar::chunk {
                background-color: #007acc;
            }
        """)

    def refresh_inventor_status(self):
        """Update connection status to Autodesk Inventor."""
        ok, msg = self.bridge.connect()
        status = self.bridge.get_status_info()
        if status["connected"]:
            self.lbl_status_indicator.setStyleSheet("color: #4ec9b0; font-size: 16px;")
            self.lbl_status_text.setText(f"Inventor: Подключен ({status['version']})")
            self.lbl_active_doc.setText(f"Активная деталь: {status['active_doc']}")
            self.log(f"Подключено к Autodesk Inventor: {status['version']}", "success")
        else:
            self.lbl_status_indicator.setStyleSheet("color: #f14c4c; font-size: 16px;")
            self.lbl_status_text.setText("Inventor: Не подключен")
            self.lbl_active_doc.setText("Активная деталь: -")
            self.log("Autodesk Inventor 2025 не обнаружен или не отвечает.", "warning")

    def on_provider_changed(self):
        is_gemini = self.rb_gemini.isChecked()
        self.widget_gemini.setVisible(is_gemini)
        self.widget_lmstudio.setVisible(not is_gemini)
        if not is_gemini and self.cb_lm_model.count() <= 1:
            self.on_refresh_lm_models()

    def on_refresh_lm_models(self):
        """Fetch models from running LM Studio instance."""
        url = self.txt_lm_url.text().strip() or "http://localhost:1234/v1"
        self.log(f"Запрос списка моделей из LM Studio ({url})...", "info")
        models = LLMClient.fetch_lm_studio_models(url)
        if models:
            cur = self.cb_lm_model.currentText()
            self.cb_lm_model.clear()
            for m in models:
                tag = " [Vision]" if any(v in m.lower() for v in ["vl", "vision", "multimodal"]) else ""
                self.cb_lm_model.addItem(f"{m}{tag}", m)

            # Try restoring selection
            idx = self.cb_lm_model.findData(cur)
            if idx >= 0:
                self.cb_lm_model.setCurrentIndex(idx)
            else:
                # If vision model exists, select it
                vl_idx = self.cb_lm_model.findData("qwen/qwen3-vl-8b")
                if vl_idx >= 0:
                    self.cb_lm_model.setCurrentIndex(vl_idx)

            self.log(f"Найдено моделей в LM Studio: {len(models)}. Выберите нужную в списке.", "success")
        else:
            self.log("Не удалось получить список моделей из LM Studio. Убедитесь, что сервер включен.", "warning")

    def toggle_key_visibility(self):
        if self.txt_gemini_key.echoMode() == QLineEdit.EchoMode.Password:
            self.txt_gemini_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_eye.setText("Скрыть")
        else:
            self.txt_gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_eye.setText("Показать")

    def on_save_config(self):
        prov = "gemini" if self.rb_gemini.isChecked() else "lm_studio"
        key = self.txt_gemini_key.text().strip()
        model = self.cb_gemini_model.currentText().strip()
        lm_url = self.txt_lm_url.text().strip()
        selected_data = self.cb_lm_model.currentData()
        lm_mod = selected_data if selected_data else self.cb_lm_model.currentText().strip()

        Config.save_settings(
            provider=prov,
            gemini_api_key=key,
            gemini_model=model,
            lm_studio_url=lm_url,
            lm_studio_model=lm_mod,
            lm_studio_timeout=self.spn_lm_timeout.value()
        )
        self.log("Настройки успешно сохранены в .env", "success")
        QMessageBox.information(self, "Сохранено", "Настройки сохранены в файл .env")

    def on_select_drawing(self):
        start_dir = str(Config.get_drawings_dir())
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл чертежа",
            start_dir,
            "Чертежи и изображения (*.pdf *.dwg *.dxf *.png *.jpg *.jpeg *.bmp);;Все файлы (*.*)"
        )
        if file_path:
            self.selected_drawing_path = file_path
            fn = os.path.basename(file_path)
            size_kb = os.path.getsize(file_path) / 1024
            self.lbl_selected_file.setText(f"Прикреплен чертеж: <b>{fn}</b> ({size_kb:.1f} КБ)")
            self.lbl_selected_file.setStyleSheet("color: #4ec9b0; font-size: 11px;")
            self.btn_clear_drawing.setVisible(True)
            self.log(f"Выбран чертеж: {file_path}", "info")

    def on_clear_drawing(self):
        self.selected_drawing_path = None
        self.lbl_selected_file.setText("Чертеж не выбран (построение только по тексту)")
        self.lbl_selected_file.setStyleSheet("color: #888888; font-size: 11px;")
        self.btn_clear_drawing.setVisible(False)

    def on_open_drawings_dir(self):
        p = Config.get_drawings_dir()
        p.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(p))
        else:
            subprocess.run(["explorer", str(p)])

    def set_template_prompt(self, text: str):
        self.txt_prompt.setPlainText(text)

    def on_create_new_part(self):
        try:
            self.bridge.create_new_part()
            self.refresh_inventor_status()
            self.log("Создан новый документ детали в Autodesk Inventor.", "success")
        except Exception as e:
            self.log(f"Ошибка создания новой детали: {e}", "error")

    def on_run_generate_and_build(self):
        self._start_generation_pipeline(execute_in_inventor=True)

    def on_run_generate_only(self):
        self._start_generation_pipeline(execute_in_inventor=False)

    def on_execute_editor_code(self):
        code = self.editor_code.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "Внимание", "Окно кода пусто. Сгенерируйте или напишите код.")
            return

        self._start_worker(
            prompt="Ручной запуск из редактора",
            drawing_path=None,
            execute_in_inventor=True,
            pre_existing_code=code
        )

    def _start_generation_pipeline(self, execute_in_inventor: bool):
        prompt = self.txt_prompt.toPlainText().strip()
        if not prompt and not self.selected_drawing_path:
            QMessageBox.warning(self, "Внимание", "Введите текстовый промпт или прикрепите чертеж!")
            return

        if not prompt and self.selected_drawing_path:
            prompt = "Построй точную 3D деталь по прикрепленному 2D чертежу."

        self._start_worker(
            prompt=prompt,
            drawing_path=self.selected_drawing_path,
            execute_in_inventor=execute_in_inventor,
            pre_existing_code=None
        )

    def _start_worker(
        self,
        prompt: str,
        drawing_path: Optional[str],
        execute_in_inventor: bool,
        pre_existing_code: Optional[str] = None
    ):
        provider = "gemini" if self.rb_gemini.isChecked() else "lm_studio"
        key = self.txt_gemini_key.text().strip()
        mod = self.cb_gemini_model.currentText().strip()
        lm_url = self.txt_lm_url.text().strip()
        selected_data = self.cb_lm_model.currentData()
        lm_mod = selected_data if selected_data else self.cb_lm_model.currentText().strip()

        if provider == "lm_studio" and drawing_path:
            is_vision = any(v in lm_mod.lower() for v in ["vl", "vision", "multimodal"])
            if not is_vision:
                self.log(
                    f"Внимание: выбранная модель '{lm_mod}' в LM Studio не является моделью зрения! "
                    f"Для чертежей переключите модель на qwen/qwen3-vl-8b или используйте Gemini.",
                    "warning"
                )

        if provider == "gemini" and not key and not pre_existing_code:
            QMessageBox.warning(
                self,
                "API Key",
                "Укажите Google Gemini API Key в поле настроек или переключитесь на LM Studio!"
            )
            return

        # UI state during run
        self.btn_run_all.setEnabled(False)
        self.btn_gen_only.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.worker = GenerationWorker(
            prompt=prompt,
            drawing_path=drawing_path,
            provider=provider,
            model_name=mod if provider == "gemini" else lm_mod,
            api_key=key,
            lm_url=lm_url,
            execute_in_inventor=execute_in_inventor,
            auto_fix=self.chk_autofix.isChecked(),
            bridge=self.bridge,
            pre_existing_code=pre_existing_code,
            step_by_step=self.chk_step_by_step.isChecked(),
            lm_timeout=self.spn_lm_timeout.value(),
            phased=self.chk_phased.isChecked()
        )

        self.worker.log_signal.connect(self.log)
        self.worker.code_ready_signal.connect(self.on_code_received)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

    @Slot(str)
    def on_code_received(self, code: str):
        self.editor_code.setPlainText(code)
        self.tab_widget.setCurrentIndex(0) # Switch to Code tab

    @Slot(bool, str)
    def on_worker_finished(self, success: bool, message: str):
        self.btn_run_all.setEnabled(True)
        self.btn_gen_only.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.refresh_inventor_status()

        if success:
            self.log(f"Процесс завершен: {message}", "success")
        else:
            self.log(f"Процесс завершен с ошибкой: {message}", "error")
            self.tab_widget.setCurrentIndex(1) # Switch to Log tab

    def on_copy_code(self):
        text = self.editor_code.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.log("Код скопирован в буфер обмена.", "info")

    def on_clear_log(self):
        self.txt_log.clear()

    def log(self, message: str, level: str = "info"):
        """Append formatted timestamped message to log window."""
        from datetime import datetime
        now = datetime.now().strftime("%H:%M:%S")

        colors = {
            "info": "#9cdcfe",
            "success": "#4ec9b0",
            "warning": "#ce9178",
            "error": "#f14c4c"
        }
        color = colors.get(level, "#d4d4d4")

        html = f"<span style='color: #6a9955;'>[{now}]</span> <span style='color: {color};'>{message}</span><br>"
        if hasattr(self, "txt_log") and self.txt_log is not None:
            self.txt_log.append(html)
        else:
            print(f"[{now}] [{level}] {message}")
