#!/usr/bin/env python3
"""Stable local entry points; the old detector repository is never imported."""
import sys
import os
from importlib import import_module
if sys.platform not in ('linux', 'win32'):
    raise SystemExit('Fluff Monitor supports Linux and Windows frontends; this platform is not supported.')
if len(sys.argv)<2 or sys.argv[1] not in ('pet','activity','capture'):
    raise SystemExit('Usage: run.py {pet|activity|capture} [arguments]')
mode = sys.argv[1]
if mode == 'pet' and (sys.platform == 'win32' or os.environ.get('FLUFF_UI') == 'qt'):
    mode = 'qt_pet'
module=import_module('fluff_monitor.'+mode)
raise SystemExit(module.main(sys.argv[2:]))
