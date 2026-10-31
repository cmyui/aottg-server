#!/bin/zsh
set -eu
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
cd -- "$(dirname -- "$0")"
exec uv run --locked aottg-server
