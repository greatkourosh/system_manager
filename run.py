#!/usr/bin/env python3
"""System Manager entry point."""
import os

from system_manager import create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8200"))
    app = create_app()
    app.run(host="0.0.0.0", port=port)