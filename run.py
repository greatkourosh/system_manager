#!/usr/bin/env python3
"""System Manager entry point (WSGI app + dev server)."""
import os

from system_manager import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8200"))
    app.run(host="0.0.0.0", port=port)