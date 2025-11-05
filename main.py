#!/usr/bin/env python3
"""
Точка входа для crypto-trading-bot
Запуск: python main.py
"""

import sys
import os
from pathlib import Path

# Добавляем src в Python path
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

# Устанавливаем переменные окружения по умолчанию
os.environ.setdefault("PYTHONPATH", str(src_path))

try:
    from src.main import main
    
    if __name__ == "__main__":
        print("🚀 Запуск Crypto Trading Bot...")
        print(f"📁 Project root: {project_root}")
        print(f"📁 Source path: {src_path}")
        print("-" * 50)
        
        # Запуск основного приложения
        main()
        
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("Убедитесь что все зависимости установлены:")
    print("pip install -r requirements.txt")
    sys.exit(1)
except KeyboardInterrupt:
    print("\n⏹ Остановка бота пользователем...")
    sys.exit(0)
except Exception as e:
    print(f"❌ Критическая ошибка: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
