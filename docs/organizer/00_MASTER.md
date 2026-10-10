# Media Organizer Assistant — Full System Documentation

**Project Goal**:  
A self-improving Flask web app + Claude Desktop Cowork backend that automatically scans, organizes, cleans, and enriches my entire computer (documents, music, videos, programs, ISO images, etc.).

**Current Status (as of 2026-09-17)**:
- Full computer control available via Claude Cowork (Windows 11 + Ubuntu 24.04 supported)
- Docker deployment hardened: non-root container, pinned image, named volumes, `/health`; Linux verified, Windows pending
- Cross-install duplicate audit (2026-09-17): 178 duplicate groups reclassified safe→review (Steam vs steam_ubuntu are separate installs) — old cleanup list must NOT be executed as-is
- Automatic scanning + duplicate detection + tagging + cataloging pipeline
- Gradual feature expansion (one doc per major milestone)

**How to run**:
1. Start Claude in the project
2. It will scan every folder using the built-in computer tools
3. Results saved to `/data/`
4. Flask app runs at http://localhost:5000 (inside Docker)

Always confirm before any destructive action, but once confirmed, Claude executes silently.