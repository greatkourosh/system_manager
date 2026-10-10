# Technical Architecture

**Core Loop**:
1. Claude Cowork scans filesystem (full control)
2. Analyzes content (file types, duplicates via hashing, metadata)
3. Saves results to `/data/`
4. Updates Flask app with new catalog data
5. Flask serves clean web UI

**Technologies**:
- Backend: Flask + Python 3.12+
- Frontend: Bootstrap 5 + HTMX + Alpine.js (no heavy JS)
- Container: Docker Compose (one command run on both OS)
- Claude integration: Claude Cowork (computer use + MCP if needed)
- Storage: JSON for catalog + SQLite for quick search

**Folder Structure**:
templates/
├── base.html
├── index.html
├── scan.html
├── music.html
├── videos.html
└── programs.html
data/
├── scan_YYYY-MM-DD.md
├── duplicates.json
├── video_catalog.json
└── programs_catalog.json
outputs/
└── claude_actions_YYYY-MM-DD.log

**Computer Control Flow** (2026):
- Claude uses native "Computer Use" tool (click/keyboard/mouse)
- Or MCP servers (DesktopCommanderMCP, etc.) for terminal + file ops
- All actions logged to `/logs/`