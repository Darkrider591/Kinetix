"""
Main entry point for Autodesk Inventor 2025 + LLM application.
"""

import sys
from PySide6.QtWidgets import QApplication
from src.gui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Inventor LLM 3D Studio")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
