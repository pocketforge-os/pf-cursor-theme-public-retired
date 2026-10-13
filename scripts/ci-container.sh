#!/bin/sh
set -eu

python3 build_cursors.py --out build
python3 test_cursors.py
