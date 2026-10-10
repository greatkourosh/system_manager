#!/usr/bin/env bash
# Media Organizer — SAFE duplicate deletion (Ubuntu/Linux run of same drive set)
# Only useful if any manifest paths are mounted under WSL/Linux; Windows paths are
# handled by delete_duplicates.ps1. Kept for cross-platform completeness per docs.
# Usage: bash delete_duplicates.sh [--dry-run]

set -u
LIST="$(dirname "$0")/delete_list_safe.txt"
LOG="$(dirname "$0")/delete_log.txt"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

converted=0
while IFS= read -r p; do
  [ -z "$p" ] && continue
  case "$p" in \#*) continue ;; esac
  # convert "E:\a\b" -> "/mnt/e/a/b"
  drive="${p:0:1}"
  linux="/mnt/$(echo "$drive" | tr 'A-Z' 'a-z')/${p:3//\\//}"
  if [ -e "$linux" ]; then
    converted=$((converted+1))
    if [ $DRY -eq 1 ]; then
      echo "WOULD-DELETE: $linux"
    else
      size=$(stat -c%s "$linux" 2>/dev/null || echo 0)
      rm -f -- "$linux" && echo -e "DELETED\t$size\t$linux" >> "$LOG"
    fi
  fi
done < "$LIST"

if [ $DRY -eq 1 ]; then
  echo "Dry run complete: $converted accessible paths found"
else
  echo "Done. $converted paths processed. Log: $LOG"
fi
