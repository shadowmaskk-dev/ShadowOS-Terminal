#!/usr/bin/env python3
"""
ShadowOS -- a terminal AI assistant for Termux with shell/file access,
notes/tasks/reminders, multi-provider chat (streaming, retries, context
compaction, personas, save/load), all under one interface.

This file is just the entry point -- the implementation lives in the
shadowos_app package (see shadowos_app/__init__.py for the module map).

Setup:
    pip install -r requirements.txt
    cp .env.example .env    # then fill in the key(s) for providers you'll use

Run:
    python main.py
"""

from shadowos_app.core import main

if __name__ == "__main__":
    main()
