.PHONY: setup test lint run clean

setup:
	uv venv
	uv pip install -e ".[dev]"
	@echo "Setup complete. Run 'make run' to start the web server."

test:
	uv run python -m pytest -v

run:
	uv run file-compressor web

lint:
	uv run python -m py_compile src/file_compressor/*.py

clean:
	rm -rf .venv dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
