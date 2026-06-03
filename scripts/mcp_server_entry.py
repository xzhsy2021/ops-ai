"""Stable MCP stdio entrypoint for clients that do not handle .bat wrappers well.

This file is safe to invoke directly from Windows MCP clients:
  D:\\code\\ops-ai\\venv\\Scripts\\python.exe D:\\code\\ops-ai\\scripts\\mcp_server_entry.py

It forces the project root onto sys.path and switches cwd to the root before
starting app.mcp.server, so the client does not need a cwd setting.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
root_s = str(ROOT)
if root_s not in sys.path:
    sys.path.insert(0, root_s)

from app.mcp.server import main

if __name__ == "__main__":
    main()
