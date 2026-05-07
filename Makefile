PYTHON    := $(shell which python3)
REPO_DIR  := $(shell pwd)
BIN_DIR   := $(HOME)/.local/bin
SEEK_BIN  := $(BIN_DIR)/seek
VENV_DIR  := $(REPO_DIR)/.venv
VENV_PY   := $(VENV_DIR)/bin/python3
RAPID_MLX_MODEL ?= qwen3.5-4b

.DEFAULT_GOAL := help

help:
	@echo "seek — semantic file search for macOS"
	@echo ""
	@echo "  make install      Install everything: venv + pip deps + Swift binary + seek command"
	@echo "  make install-mlx  Add local Rapid-MLX inference (Apple Silicon only)"
	@echo "  make deps         Create venv and install Python dependencies only"
	@echo "  make build        Build the seek-caption Swift binary only"
	@echo "  make uninstall    Remove the seek command from $(BIN_DIR)"
	@echo "  make clean        Remove venv and compiled Swift binary"
	@echo ""
	@echo "  Override Python: make install PYTHON=/path/to/python3"

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
	@echo "  Installing rapid-mlx..."
	@$(VENV_PY) -m pip install --quiet rapid-mlx
	@mkdir -p $(BIN_DIR)
	@rm -f $(BIN_DIR)/rapid-mlx
	@ln -s $(VENV_DIR)/bin/rapid-mlx $(BIN_DIR)/rapid-mlx
	@echo "  Linked: $(BIN_DIR)/rapid-mlx -> $(VENV_DIR)/bin/rapid-mlx"
	@echo ""
	@echo "  Rapid-MLX ready. To switch seek to local inference, edit"
	@echo "  ~/.config/seek/config.toml under [llm]:"
	@echo "    base_url = \"http://localhost:8000/v1\""
	@echo "    model = \"default\""
	@echo "    local_model = \"llama3-3b\""
	@echo ""
	@echo "  seek will auto-start the server on first query (~8s cold)."
	@echo "  Manual: seek server start | seek server stop | seek server status"
	@echo ""

uninstall:
	rm -f $(SEEK_BIN) $(BIN_DIR)/rapid-mlx
	@echo "  Removed $(SEEK_BIN) and $(BIN_DIR)/rapid-mlx (if present)"

clean:
	rm -rf $(VENV_DIR)
	rm -f tools/caption/seek-caption tools/caption/*.o
	@echo "  Cleaned"

.PHONY: help deps build install install-mlx uninstall clean
