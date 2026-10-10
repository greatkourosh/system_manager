Media Organizer — commands_to_run
Generated 2026-09-01 by Media Organizer Assistant

WHAT'S HERE
===========
files_to_delete_safe.txt     Human-readable manifest, grouped by TYPE (video/music/image/
                             program/archive/document), each group sorted by wasted space:
                             "KEEP <path>" then "DELETE <path>" lines. 3,770 groups /
                             4,039 files / ~30.72 GB. SHA-256 verified.

delete_list_safe.txt         All 4,039 paths in one file, sorted by type (header lines start
                             with "#"), then by address. Input for the scripts below.

by_type/                     Per-type runnable lists:
                               delete_video.txt     147 files, 13.16 GB
                               delete_music.txt   2,917 files, 11.56 GB
                               delete_image.txt     775 files,  1.82 GB
                               delete_program.txt   144 files,  3.43 GB
                               delete_archive.txt    35 files,  0.69 GB
                               delete_document.txt   21 files,  0.06 GB

delete_duplicates.ps1        PowerShell deletion script (recommended on Windows).
                             - Parameter -List to run a single type, e.g.:
                                 .\delete_duplicates.ps1 -List .\by_type\delete_video.txt
                             - Logs every action to delete_log.txt
                             - Guards against project/system paths
                             - Skips files that no longer exist
                             - Set $UseRecycleBin = $true at top for recycle-bin mode (safer first run)

delete_duplicates.sh         Linux/WSL variant (same list, /mnt/<drive> mapping, --dry-run flag).

review_groups_reference.txt  The 447 REVIEW groups (11.3 GB) — NO commands, reference only.
                             These have differing filenames and need human decisions; we'll go
                             through them in chat after the safe batch.

review/                     (empty) after review batches are exported, per-batch lists land here.

HOW TO RUN
==========
1. Review files_to_delete_safe.txt (search for any path you care about).
2. Optional safer first pass — edit delete_duplicates.ps1: $UseRecycleBin = $true
3. Run:
     powershell -ExecutionPolicy Bypass -File delete_duplicates.ps1
4. Send me the last lines of delete_log.txt (or just say "done") — I will:
   - verify freed space, update catalogs, re-run the scan delta,
   - then export REVIEW batch 1 for the 447 review groups.
