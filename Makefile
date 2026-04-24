PYTHON    := $(shell which python3)
REPO_DIR  := $(shell pwd)
BIN_DIR   := $(HOME)/.local/bin
SEEK_BIN  := $(BIN_DIR)/seek
VENV_DIR  := $(REPO_DIR)/.venv
VENV_PY   := $(VENV_DIR)/bin/python3
MLX_MODEL ?= mlx-community/Qwen3-1.7B-4bit

.DEFAULT_GOAL := help

help:
	@echo "seek — semantic file search for macOS"
	@echo ""
	@echo "  make install      Install everything: venv + pip deps + Swift binary + seek command"
	@echo "  make install-mlx  Add local MLX inference + download default model (Apple Silicon only)"
	@echo "  make deps         Create venv and install Python dependencies only"
	@echo "  make build        Build the seek-caption Swift binary only"
	@echo "  make uninstall    Remove the seek command from $(BIN_DIR)"
	@echo "  make clean        Remove venv and compiled Swift binary"
	@echo ""
	@echo "  Override Python: make install PYTHON=/path/to/python3"
	@echo "  Override MLX model: make install-mlx MLX_MODEL=mlx-community/Qwen3-4B-4bit"

$(VENV_DIR):
	$(PYTHON) -m venv $(VENV_DIR)

deps: $(VENV_DIR)
	@echo "  Installing Python dependencies..."
	@$(VENV_PY) -m pip install --quiet openai
	@echo "  Done"

build:
	@echo "  Building seek-caption..."
	@cd tools/caption && swiftc -O seek-caption.swift -o seek-caption
	@echo "  Built: tools/caption/seek-caption"

install: deps build
	@mkdir -p $(BIN_DIR)
	@rm -f $(SEEK_BIN)
	@printf '#!/bin/bash\nexec "%s" "%s/seek.py" "$$@"\n' "$(VENV_PY)" "$(REPO_DIR)" > $(SEEK_BIN)
	@chmod +x $(SEEK_BIN)
	@echo ""
	@echo "  Installed: $(SEEK_BIN)"
	@echo "  Python:    $(VENV_PY)"
	@echo ""
	@echo "  Next: set your OpenRouter API key"
	@echo "  https://openrouter.ai/keys"
	@echo ""
	@echo "  Add the key to ~/.config/seek/config.toml:"
	@echo "    api_key = \"sk-or-...\""
	@echo ""
	@echo "  Then: seek \"describe the file you're looking for\""

install-mlx: deps
	@echo "  Installing mlx-lm..."
	@$(VENV_PY) -m pip install --quiet mlx-lm
	@echo "  Downloading model $(MLX_MODEL)..."
	@$(VENV_PY) -c "from huggingface_hub import snapshot_download; snapshot_download('$(MLX_MODEL)')"
	@echo ""
	@echo "  MLX ready. Add this to ~/.config/seek/config.toml:"
	@echo "    [llm]"
	@echo "    provider = \"mlx\""
	@echo "    model = \"$(MLX_MODEL)\""
	@echo ""

uninstall:
	rm -f $(SEEK_BIN)
	@echo "  Removed $(SEEK_BIN)"

clean:
	rm -rf $(VENV_DIR)
	rm -f tools/caption/seek-caption tools/caption/*.o
	@echo "  Cleaned"

.PHONY: help deps build install install-mlx uninstall clean
