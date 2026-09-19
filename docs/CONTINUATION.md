# System Manager — Continuation & Future Documents

## Completed milestones

**2026-09-19 — System Manager skeleton & folder_organizer module**
- Created `/media/kourosh/VMSSD/projects/system_manager` as a Flask dashboard that hosts feature modules.
- Added `system_manager/` package with `create_app()` factory and `organizer` module loader.
- The organizer module **imports the existing `folder_organizer/app.py` in place** (no copy, no move) and remounts its routes under `/organizer`.
- Routes: `GET /health`, `GET /` (overview), `GET /organizer/<path:subpath>…` proxies to the inner app.
- Templates: `base.html`, `index.html`; static `app.css`, `favicon.svg`.
- Entry point: `run.py` (defaults to port 8000). `requirements.txt` mirrors the organizer.
- Verified: `python -m system_manager` starts without import errors.

**2026-09-19 — Absolute URL rewriting for organizer proxy**
- Added response-time HTML rewrite in `system_manager/organizer.py` to prefix `href="/..."`, `action="/..."`, `src="/..."` with `/organizer`.
- Verified across pages: dashboard (`/`), scan (`/scan`), select (`/select`), music (`/music`), videos (`/videos`), cleanup (`/cleanup`), folders (`/folders`), tags (`/music/tags`), programs (`/programs`), skipped (`/skipped`).
- Verified API endpoints: `/api/summary`, `/api/duplicates`, `/api/catalog/*`, `/api/video/poster`, `/api/folderfix/*`, `/api/selection/*`, `/api/tags/*`, `/api/skip-rules`, `/api/cleanup`, `/api/music/tags`, `/health`.
- Verified POST/DELETE/PUT pass through (folder fix accept/edit/export, selection bulk/export, skip rules CRUD, tags propose/set-entry/bulk/export/merge-mb/clear-plan).

## Remaining integration tasks

| Task | Description | Effort | Notes |
|------|-------------|--------|-------|
| **Blueprint URL fix** | Replace WSGI proxy with true Flask blueprint registration so `url_for('organizer.scan_page')` works inside organizer templates. | M | Requires `app.py` → `create_app()` refactor in `folder_organizer` (outside System Manager scope). |
| **Data path isolation** | Organizer expects `DATA = BASE/"data"` relative to its own checkout. Works today because we keep `ORGANIZER_PATH` on `sys.path`. Document and test. | S | Verified working. |
| **Static asset serving** | Organizer serves `/static/…` from its own `static/` directory. Current proxy forwards but mounts at `/organizer/static/…`. Ensure no conflicts. | S | Works; no collision with System Manager `/static`. |
| **Session / cookie scope** | If organizer adds auth, ensure cookies are scoped to `/organizer`. | L | Deferred until auth exists. |
| **Docker/Compose** | Add `Dockerfile`, `docker-compose.yml` that builds both services (or one combined image) and mounts the shared data volume. | M | See `folder_organizer/Dockerfile` for reference. |
| **Tests** | Add `tests/test_system_manager.py` with basic probe tests: `/health`, `/`, `/organizer/health` (should 503 when checkout missing, 200 when present). | S | Use `pytest`. |

## Future features (create docs on demand)
- `01_MODULE_LOADER.md` — generic module registration API.
- `02_AUTH_AND_ROLES.md` — single sign-on across modules.
- `03_METRICS_EXPORT.md` — Prometheus `/metrics` per module.
- `04_CLI_MANAGEMENT.md` — `smctl` to enable/disable modules, view logs.

## Next
Say **"Create CONTINUATION_01.md"** to spawn the next milestone doc, or **"Add feature: X"** to queue a new integration task.

---

## Feature Proposal: Hardware & Components Inventory Module (`inventory`)

### Overview
A System Manager plugin to track and manage hardware inventory — PC components, network devices (active/passive), electric/electronic devices, and other personal devices & parts. Mounted at `/inventory`.

### Domain Model
| Category | Examples | Key Attributes |
|----------|----------|----------------|
| **PC Components** | CPU, GPU, RAM, motherboard, PSU, storage, case, cooler | model, serial, purchase_date, warranty_end, location (build_id), status (installed/spare/retired), benchmarks, notes |
| **Network Devices (Active)** | Routers, switches, APs, firewalls, servers | model, serial, MAC, IP, firmware, config_backup_path, location, status |
| **Network Devices (Passive)** | Patch panels, keystones, fiber cassettes, cable runs | type, spec (Cat6/OM4), length, endpoints, location, certification_date |
| **Electric/Electronic** | UPS, PDUs, smart plugs, sensors, Raspberry Pi/Arduino, IoT | model, serial, firmware, power_rating, protocol (Matter/Zigbee/ESPHome), location |
| **Personal Devices** | Phones, tablets, laptops, wearables, headphones | model, serial, OS, purchase_date, warranty, assigned_user, status |
| **Parts & Consumables** | Cables, adapters, screws, thermal paste, labels | category, spec, quantity, min_stock, location_bin |

### Core Features
1. **CRUD + Search/Filter** — Full inventory with category tree, fuzzy search, multi-filter (status, location, warranty_expiring)
2. **Build Tracking** — Assemble components into "builds" (PC, rack, workstation) with compatibility checks (CPU socket ↔ motherboard, PSU wattage, RAM slots)
3. **Warranty & Lifecycle** — Auto-flag expiring warranties, EOL notices, retirement planning
4. **Location Hierarchy** — Room → Rack/Shelf → Bin/Slot → Device, with QR code labels for physical tagging
5. **Network Topology** — Visual map of active/passive network devices, cable runs, VLANs
6. **Change Log / Audit** — Immutable log of moves, status changes, config updates (reuse `ActionAudit` pattern)
7. **Import/Export** — CSV, JSON, YAML; barcode/QR scan entry via webcam
8. **Attachments** — Photos, PDFs (invoices, datasheets), config backups per item

### Technical Design

#### Module Structure (mirrors `organizer`)
```
system_manager/inventory/
├── __init__.py          # Blueprint loader, PREFIX="/inventory"
├── models.py            # SQLAlchemy models (or dataclasses + SQLite)
├── api.py               # REST endpoints
├── templates/           # Jinja2 templates (extend base.html)
├── static/              # CSS/JS specific to inventory
└── migrations/          # Schema versioning (alembic or simple SQL)
```

#### Integration Points
- **Blueprint registration** in `system_manager/__init__.py` alongside `organizer_blueprint()`
- **Shared auth** — Reuse session/credential from main app (cookie `sm_session`)
- **Shared audit** — Use `ActionAudit` from `server.py` for change tracking
- **Database** — Separate SQLite file `inventory.db` in module data dir (or shared with configurable prefix)

#### API Surface (REST + HTMX-friendly)
```
GET    /inventory/                    # Dashboard: stats, alerts, recent changes
GET    /inventory/items               # Paginated, filterable table (JSON + HTML)
POST   /inventory/items               # Create item
GET    /inventory/items/<id>          # Detail view
PATCH  /inventory/items/<id>          # Update (partial)
DELETE /inventory/items/<id>          # Soft-delete (status=retired)
GET    /inventory/builds              # Build list
POST   /inventory/builds              # Create build
GET    /inventory/builds/<id>         # Build detail with compatibility report
GET    /inventory/topology            # Network topology SVG/JSON
GET    /inventory/export              # CSV/JSON/YAML download
POST   /inventory/import              # CSV/JSON upload with preview
GET    /inventory/qr/<id>             # QR code SVG for labeling
```

#### UI/UX
- **Dashboard cards**: Total items, warranty expiring (30d), low stock, recent moves
- **Table view**: Virtualized, sortable, column picker, inline edit (HTMX)
- **Detail drawer**: Slide-over panel (not new page) for quick edits
- **Build wizard**: Step-by-step with live compatibility validation
- **Topology canvas**: Pan/zoom SVG (D3 or raw SVG), click node → detail
- **Dark mode**: Inherit CSS variables from `base.html`

#### Data Files
```
/data/inventory/
├── inventory.db          # SQLite
├── attachments/          # item_<id>/ (photos, PDFs)
├── backups/              # config backups for network devices
└── labels/               # generated QR/barcode PDFs
```

### Implementation Phases

| Phase | Scope | Effort |
|-------|-------|--------|
| **0 — Scaffold** | Module loader, blueprint, empty routes, template skeleton | S |
| **1 — Data Model & CRUD** | SQLite schema, models, basic API, list/detail pages | M |
| **2 — Build Tracking** | Build assembly, compatibility rules, validation | M |
| **3 — Network Topology** | Graph model, SVG renderer, cable run mapping | M |
| **4 — Warranty/Lifecycle** | Alerts, calendar feed (.ics), email/webhook hooks | S |
| **5 — Import/Export & QR** | CSV/JSON round-trip, QR generation, label PDF | S |
| **6 — Polish** | Search, filters, attachments, dark mode, tests | M |

### Configuration
```yaml
# config/inventory.yaml (optional, loaded by module)
data_dir: "/data/inventory"
enable_topology: true
enable_builds: true
warn_days: 30
qr_base_url: "https://sm.local/inventory/items/"
```

### Open Questions
1. **Database** — SQLite (simple) vs PostgreSQL (if multi-user)? Start with SQLite.
2. **Auth** — Single-user (current) vs multi-user with roles? Defer to `02_AUTH_AND_ROLES.md`.
3. **Real-time** — WebSocket for live topology updates? Phase 3+.
4. **External sync** — NetBox, i-doit, Home Assistant? API-only for now.

### Next Steps
- Say **"Create inventory scaffold"** to generate Phase 0 files
- Say **"Plan Phase 1"** for detailed data model & API spec