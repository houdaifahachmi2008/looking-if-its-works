#!/usr/bin/env bash
# Mac / Linux: dubbelklik of voer uit met ./sitevo-starten.sh
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv/bin/python -m leadgen.app
