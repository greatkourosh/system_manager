# Requirements (2026-09-01)

### Functional
- Full recursive scan of all folders on Windows 11 and Ubuntu 24.04
- Duplicate detection (music, videos, programs, ISO, documents)
- MP3 tag updater (artist, title, album, year, genre, etc.)
- Video catalog: auto-name fix, subtitle auto-download (my preferred languages), poster generation
- Program categorization & cleanup (delete old/duplicate executables)
- Folder reorganization, name fixing, evaluation scoring
- Searchable web dashboard (Flask)
- Continuous improvement: new features added via updated Claude instructions + new Flask routes

### Non-functional
- Cross-platform (Docker on both OS)
- Zero manual intervention after initial setup
- All outputs in clean JSON + Markdown
- Security: sandboxed Docker, no destructive actions without explicit confirmation

### Preferred Languages (user input needed)
- Subtitles: [ask me once]
- Music genres: [ask me once]

### Exclusions
- Program files in /Program Files/ and /Program Files (x86)/
- System folders (Windows: C:\Windows, Ubuntu: /var, /usr, /boot, /proc)
- Temporary folders (/tmp, /var/tmp)
- ISO images in any /ISO/ folder (handled separately)