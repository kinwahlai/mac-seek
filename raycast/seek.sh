#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Seek
# @raycast.mode fullOutput
# @raycast.packageName Seek

# Optional parameters:
# @raycast.icon 🔍
# @raycast.argument1 { "type": "text", "placeholder": "describe the file you're looking for" }

# Documentation:
# @raycast.description Semantic file search — find files by natural language description
# @raycast.author dlai

export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
export DASHSCOPE_API_KEY="$(grep DASHSCOPE_API_KEY "$HOME/dev_repo/AI-projects/mac-seek/.env" | cut -d= -f2)"

/opt/homebrew/anaconda3/bin/python3 "$HOME/dev_repo/AI-projects/mac-seek/seek.py" "$1"
