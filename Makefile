.PHONY: test test-unit test-integration test-coverage test-fast

# Все тесты
test:
	pytest tests/ -v

# Только unit тесты
test-unit:
	pytest tests/unit/ -v -m unit

# Только integration тесты
test-integration:
	pytest tests/integration/ -v -m integration

# Быстрые тесты (без slow)
test-fast:
	pytest tests/ -v -m "not slow"

# С покрытием
test-coverage:
	pytest tests/ -v --cov=src --cov-report=html --cov-report=term

# Конкретный файл
test-file:
	pytest $(FILE) -v

# Watch mode
test-watch:
	pytest-watch tests/ -- -v
