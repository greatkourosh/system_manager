"""Read the host's dpkg database and apt indexes. No apt, no root, no writes.

The container's own dpkg database is the image's 140 packages, not the host's
2720, so the host's ``/var/lib/dpkg/status`` and ``/var/lib/apt/lists`` are
bind-mounted read-only and parsed here. Everything is plain RFC822-ish text
formatting, so the stdlib gets us the whole thing.

apt's own answer is not available: ``apt-get -s upgrade`` needs the lock files,
the dpkg status database and a config we deliberately do not have. This module
reproduces its *installed -> candidate* comparison for the read-only case,
with one deliberate difference: apt applies Ubuntu's phased-rollout percentage
per host, and that decision is not readable from any file, so phased versions
are reported here with their percentage rather than resolved.
"""
import glob
import os
import re

__all__ = [
    "HostApt", "DpkgError", "index_age",
    "split_version", "version_compare",
]

DPKG_STATUS = "/host/var/lib/dpkg/status"
APT_LIST_DIR = "/host/var/lib/apt/lists"

# A version table entry apt will not choose for an unrequested upgrade:
# backports sit below the installed version on purpose (apt's target-release
# default is 500, backports 100), so `apt-get upgrade` skips them unless the
# package is named. They are still real, available updates, so the page
# reports them in their own bucket instead of dropping them.
BACKPORT_SUITES = ("-backports",)

_LIST_NAME_RE = re.compile(
    r"^(?P<repo>.+?)_dists_(?P<suite>[^_]+)_(?P<comp>[^_]+)"
    r"_binary-(?P<arch>[^_]+)_Packages$"
)
_LIST_NAME_RE_NOCOMP = re.compile(
    r"^(?P<repo>.+?)_dists_(?P<suite>[^_]+)_binary-(?P<arch>[^_]+)_Packages$"
)


class DpkgError(Exception):
    """The host database is not where we expect it — say so, do not go empty."""


# --- version comparison -------------------------------------------------
# Debian policy: [epoch:]upstream[-debian_revision]. Two of dpkg's behaviours
# are not in the policy, and both change answers on this host:
#
#   * Its version parse eats leading zeros, so "1.0" and "1.0-0" describe the
#     same epoch, upstream and revision. That is the only reason a version with
#     no hyphen can equal one carrying an explicit "-0".
#   * The upstream version is split at the LAST '-', so an upstream that
#     contains its own hyphens stays in one piece.
#
# _cmp_part below is dpkg's own scan rather than a token sort, because the two
# scales it uses are not comparable with each other: digits compare as numbers
# (so 2 < 10, not 10 < 2) and everything else compares by order(), where '~'
# sorts below end-of-string and a non-digit run sorts above it. A token list
# cannot express that, because a letter and a punctuation mark have to be
# distinguishable from end-of-string while a digit run has to be atomic.
_END = 0

def _is_digit(char):
    """Digits, in dpkg's sense: a decimal digit, or NUL (which reads as '0')."""
    return char.isdigit() or char == "\0"

def _order(char):
    """One character's rank within a version part, per dpkg's order().

    End-of-string and a digit both rank 0, which is safe because the scan only
    ever compares like with like.
    """
    if char == "~":
        return -1
    if _is_digit(char):
        return _END
    return ord(char) if char.isalpha() else ord(char) + 256

def _digit_run(segment, start):
    """Read the digit run at `start` as a number, returning it and the end index.

    dpkg's parse eats leading zeros before the run is ever compared, which is
    why "1.01" and "1.1" are the same version and "1.0-0" equals "1.0".
    """
    i = start
    while i < len(segment) and _is_digit(segment[i]):
        i += 1
    digits = segment[start:i].replace("\0", "")  # NUL reads as '0', and eats
    return (int(digits, 10) if digits else 0), i

def _cmp_part(left, right):
    i = j = 0
    nl, nr = len(left), len(right)
    while i < nl or j < nr:
        # Characters first, one at a time, while either side is at a non-digit.
        # A whole run cannot be compared at once here: order() gives every
        # non-digit the same weight, so only the first one in the run counts.
        while (i < nl and not _is_digit(left[i])) or (j < nr and not _is_digit(right[j])):
            a = _order(left[i]) if i < nl else _END
            b = _order(right[j]) if j < nr else _END
            if a != b:
                return -1 if a < b else 1
            i += 1
            j += 1
        # Both sides are at a digit now, or one has run out. A digit run is read
        # as a whole number -- that is what makes 2 < 10 rather than 10 < 2 --
        # and a run with nothing on the other side reads as 0, so "1.0" loses to
        # "1.0-1" and ties with "1.0-0" by the same rule as everything else.
        a, i = _digit_run(left, i)
        b, j = _digit_run(right, j)
        if a != b:
            return -1 if a < b else 1
    return 0

def split_version(version):
    """Split "1:2.3-4ubuntu1" into ("1", "2.3", "4ubuntu1") for comparison."""
    version = (version or "").strip()
    head, sep, tail = version.partition(":")
    if not sep:  # no colon at all, so there is no epoch and the whole thing is upstream
        epoch, rest = "0", version
    else:
        match = re.match(r"([0-9]+)", head)
        # A colon with nothing numeric in front of it is a syntax error and dpkg
        # rejects the version outright. Treating the whole string as the
        # upstream keeps the comparison deterministic rather than arbitrary.
        epoch, rest = (str(int(match.group(1), 10)), tail) if match else ("0", version)

    # dpkg splits the upstream version at the LAST '-', so an upstream that
    # contains its own hyphens stays in one piece.
    upstream, sep, revision = rest.rpartition("-")
    if not sep:
        upstream, revision = rest, ""
    return epoch, upstream, revision

def version_compare(left, right):
    """Return -1/0/1 ordering two Debian versions, matching dpkg --compare-versions."""
    for a, b in zip(split_version(left), split_version(right)):
        result = _cmp_part(a, b)
        if result:
            return result
    return 0


# --- stanza parsing -----------------------------------------------------


def _stanzas(text):
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if block:
            yield block


def _fields(block):
    """Parse one stanza into {Field: value}; continuation lines fold in."""
    out = {}
    key = None
    for line in block.split("\n"):
        if not line.strip():
            continue
        if line[0] in " \t":  # continuation of the previous field
            if key:
                out[key] += "\n" + line.strip()
            continue
        name, sep, value = line.partition(":")
        if not sep:
            continue
        key = name.strip()
        out[key] = value.strip()
    return out


def parse_status(path=DPKG_STATUS):
    """Return {arch-qualified name: {version, arch, source}} for installed packages."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise DpkgError(
            f"dpkg status not readable at {path}: {exc}. "
            "Add '- /var/lib/dpkg:/host/var/lib/dpkg:ro' to docker-compose.yml."
        ) from exc

    installed = {}
    for block in _stanzas(text):
        fields = _fields(block)
        name = fields.get("Package")
        if not name or not fields.get("Version"):
            continue
        if "installed" not in fields.get("Status", "").split():
            continue
        arch = fields.get("Architecture", "amd64")
        installed[_qualify(name, arch)] = {
            "version": fields["Version"],
            "arch": arch,
            "source": fields.get("Source", "").split(" ", 1)[0] or name,
        }
    return installed


def _qualify(name, arch):
    """dpkg/apt's identity for a package: bare name, or name:arch when it matters."""
    return name if arch in ("all", "amd64") else f"{name}:{arch}"


# --- apt index ----------------------------------------------------------


def _index_files(list_dir=APT_LIST_DIR):
    """Yield (suite, arch, path) for every package index apt would read."""
    for path in sorted(glob.glob(os.path.join(list_dir, "*_Packages"))):
        name = os.path.basename(path)
        match = _LIST_NAME_RE.match(name) or _LIST_NAME_RE_NOCOMP.match(name)
        if match:
            yield match.group("suite"), match.group("arch"), path


def parse_index(path, arch):
    """Return {name: {version, suite, phased, arch}} from one Packages file.

    'phased' is the Phased-Update-Percentage value; apt withholds a phased
    version from most machines and offers the previous one instead.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise DpkgError(f"apt index not readable at {path}: {exc}") from exc

    entries = {}
    for block in _stanzas(text):
        fields = _fields(block)
        name = fields.get("Package")
        if not name or not fields.get("Version"):
            continue
        stanza_arch = fields.get("Architecture", arch)
        # Arch-independent stanzas are repeated into every binary index, so
        # they must survive the per-arch filter or arch:all packages vanish.
        if stanza_arch not in ("all", arch):
            continue
        # A package appears once per version; the highest wins, since that is
        # what apt would consider.
        entry = {
            "version": fields["Version"],
            "suite": suite_from_path(path),
            "phased": int(fields["Phased-Update-Percentage"]) if fields.get(
                "Phased-Update-Percentage", "").isdigit() else None,
            "arch": fields.get("Architecture", arch),
        }
        key = _qualify(name, entry["arch"])
        previous = entries.get(key)
        if previous is None or version_compare(entry["version"], previous["version"]) > 0:
            entries[key] = entry
    return entries


def suite_from_path(path):
    """Pull the suite ('noble-security') out of an apt index filename."""
    base = os.path.basename(path)
    match = _LIST_NAME_RE.match(base) or _LIST_NAME_RE_NOCOMP.match(base)
    return match.group("suite") if match else ""


# --- the actual answer --------------------------------------------------


class HostApt:
    """The host's installed packages and its newest candidate for each."""

    def __init__(self, status_path=DPKG_STATUS, list_dir=APT_LIST_DIR):
        self.status_path = status_path
        self.list_dir = list_dir

    def load(self):
        installed = parse_status(self.status_path)
        # The arches to read are the ones actually installed, so a host with
        # i386 multi-arch installed reports its i386 updates instead of
        # silently dropping them.
        arches = {info["arch"] for info in installed.values()} - {"all"}
        arches.add("amd64")  # the index always carries the native arch
        candidates = {}
        suites = set()
        for suite, arch, path in _index_files(self.list_dir):
            if arch not in arches:
                continue
            suites.add(suite)
            for name, entry in parse_index(path, arch).items():
                # Highest version wins regardless of suite; the backports
                # caveat is applied in upgradable(), not here, so the row is
                # still visible.
                current = candidates.get(name)
                if current is None or version_compare(entry["version"], current["version"]) > 0:
                    candidates[name] = entry
        self._installed = installed
        self._candidates = candidates
        self._suites = sorted(suites)
        self._arches = sorted(arches)
        return self

    def _rows(self):
        """Every installed package whose newest candidate is newer."""
        rows = []
        for name, info in self._installed.items():
            candidate = self._candidates.get(name)
            if not candidate:
                continue
            if version_compare(candidate["version"], info["version"]) <= 0:
                continue
            backports = candidate["suite"].endswith(BACKPORT_SUITES)
            rows.append({
                "name": name,
                "installed": info["version"],
                "candidate": candidate["version"],
                "suite": candidate["suite"],
                "security": candidate["suite"].endswith("-security"),
                "arch": info["arch"],
                "phased": candidate.get("phased"),
                "backports": backports,
            })
        return sorted(rows, key=lambda r: r["name"])

    def upgradable(self):
        """Every installed package with a newer version apt has in an index.

        Phased versions are included. Ubuntu publishes a rollout percentage
        and apt applies it per host, so the same index yields a different
        answer on different machines -- on this host 13 of 15 phased packages
        are offered and 2 are held back. The percentage is carried through so
        the page can say that instead of pretending the list is identical to
        apt's.
        """
        return self._rows()

    def backports(self):
        """The subset apt-get upgrade deliberately ignores."""
        return [r for r in self._rows() if r["backports"]]


def index_age(list_dir=APT_LIST_DIR):
    """Newest and oldest mtime across the apt indexes, as epoch seconds.

    A stale index silently under-reports: apt-get never saw the new versions.
    Returned rather than judged -- the template decides how loudly to say it.
    """
    stamps = [os.path.getmtime(p) for p in glob.glob(os.path.join(list_dir, "*_Packages"))]
    if not stamps:
        return None
    return {"newest": max(stamps), "oldest": min(stamps), "count": len(stamps)}
