#!/usr/bin/env python3
"""Translate the library's Windows paths into real paths on this machine.

video_library.json was scanned on Windows, so every card carries a `G:\\...` path.
The G: drive is an ntfs3 volume mounted here at /media/kourosh/Multimedia, so
`G:\\Serials\\X` -> `/media/kourosh/Multimedia/Serials/X`. Other drives (E:, F:)
and deleted folders have no local equivalent and translate to a path that does
not exist, which callers can detect rather than silently mishandle.
"""
import os
import re

MULTIMEDIA = "/media/kourosh/Multimedia"

# Only the G: volume is mounted here. E:/F: are absent, so they are left to
# translate to a non-existent path instead of being guessed at.
DRIVE_ROOTS = {"G:": MULTIMEDIA}

_WIN_ABS = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def to_host_path(path):
    """Rewrite a Windows library path to its local equivalent.

    Returns `path` unchanged when it is not an absolute Windows path, so a
    caller can pass any card field through unconditionally.
    """
    if not path:
        return path
    m = _WIN_ABS.match(path)
    if not m:
        return path
    drive, rest = m.group(1).upper() + ":", m.group(2)
    root = DRIVE_ROOTS.get(drive)
    if root is None:
        return path
    return os.path.join(root, *rest.replace("\\", "/").split("/"))


def is_reachable(path):
    """True when `path` names something that exists on this machine."""
    return bool(path) and os.path.exists(to_host_path(path))
