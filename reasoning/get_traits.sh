#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
python3 reasoning/refresh_traits.py "$@"
