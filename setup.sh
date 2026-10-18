#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "$(id -u)" -eq 0 ]; then
    echo '请不要 sudo setup.sh；相机操作时才需要 sudo。' >&2
    exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "需要 Python 3.10 或更新版本"'
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m macfl23 setup
.venv/bin/python -m macfl23 check
