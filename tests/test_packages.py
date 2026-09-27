"""Tests the packages module: version ordering, index parsing, and the page.

Every fixture is inline, so these run without the host mounts the module
reads in production. What they pin down is the behaviour that is easy to get
subtly wrong: Debian version ordering, arch:all stanzas, and the difference
between "no updates" and "cannot see the database".
"""
import os
import tempfile
import unittest

from system_manager import create_app
from system_manager.packages import dpkg


class VersionOrderTests(unittest.TestCase):
    """The comparator has to match dpkg --compare-versions exactly."""

    def assertOrder(self, older, newer):
        self.assertEqual(dpkg.version_compare(older, newer), -1, f"{older} < {newer}")
        self.assertEqual(dpkg.version_compare(newer, older), 1, f"{newer} > {older}")
        self.assertEqual(dpkg.version_compare(older, older), 0)

    def test_numeric_runs_compare_as_numbers(self):
        # The classic trap: as strings "10" < "9".
        self.assertOrder("2.3.4-1", "2.3.10-1")
        self.assertOrder("1.138.0-1", "1.139.1-1")

    def test_tilde_sorts_before_end_of_string(self):
        self.assertOrder("1.0~beta1", "1.0")
        self.assertOrder("2.3.5-1~ubuntu.24.04~noble", "2.3.6-1~ubuntu.24.04~noble")

    def test_epoch_beats_upstream(self):
        # 3.1.2-2.1ubuntu0.1 has an epoch of 1, so it outranks a plain 3.1.2
        # no matter how the rest sorts.
        self.assertOrder("2.0-1", "1:1.0-1")
        self.assertOrder("1.0-1", "1:1.0-1")

    def test_trailing_letter_and_dot_both_sort_above_end_of_string(self):
        # dpkg gives end-of-string 0 and non-alphanumerics +256, so 1.0.1 is
        # newer than 1.0 and 1.0a is newer than 1.0 but older than 1.0.1.
        self.assertOrder("1.0", "1.0.1")
        self.assertOrder("1.0", "1.0a")
        self.assertOrder("1.0a", "1.0.1")

    def test_debian_revision_is_the_last_hyphen(self):
        # Upstream versions may themselves contain hyphens, so rpartition
        # is the only correct split.
        self.assertOrder("1.0-1ubuntu0.1", "1.0-1ubuntu0.2")
        self.assertEqual(dpkg.split_version("4.0.1really4.0.1-0ubuntu0.24.04.8"),
                         ("0", "4.0.1really4.0.1", "0ubuntu0.24.04.8"))

    def test_leading_zeros_are_eaten(self):
        # dpkg's parse drops leading zeros from a digit run before comparing it,
        # so "1.01" and "1.1" are one version and "1.0-0" is "1.0". This is why
        # a version with no hyphen can equal one that carries an explicit "-0".
        self.assertEqual(dpkg.version_compare("1.01", "1.1"), 0)
        self.assertEqual(dpkg.version_compare("1.0-0", "1.0"), 0)
        self.assertEqual(dpkg.version_compare("1.0-00", "1.0-0"), 0)
        self.assertEqual(dpkg.version_compare("1.0-01", "1.0-1"), 0)
        self.assertOrder("1.0", "1.0-01")

    def test_missing_revision_is_the_empty_string(self):
        # Not a marker of its own: a bare "1.0" and "1.0-0" tie, and a bare
        # "1.0" loses to anything with real content in the revision.
        self.assertEqual(dpkg.version_compare("1.0", "1.0-0"), 0)
        self.assertEqual(dpkg.version_compare("1.0", "1.0-1"), -1)
        self.assertOrder("1.0-0", "1.0-1")

    def test_no_colon_means_epoch_zero(self):
        # The epoch is only read when a colon is there, so "1.0" is epoch 0 with
        # upstream "1.0" and any explicit epoch outranks it.
        self.assertEqual(dpkg.split_version("1.0"), ("0", "1.0", ""))
        self.assertOrder("1.0", "1:0")
        self.assertOrder("1.0", "1:0.1")

    def test_equal_versions(self):
        self.assertEqual(dpkg.version_compare("1:2.39.3-9ubuntu6", "1:2.39.3-9ubuntu6"), 0)
        self.assertEqual(dpkg.version_compare("0", "0"), 0)


STATUS_FIXTURE = """Package: alpha
Status: install ok installed
Architecture: amd64
Version: 1.0-1

Package: beta
Status: install ok installed
Architecture: all
Version: 2.0-1

Package: gamma
Status: install ok installed
Architecture: i386
Version: 3.0-1

Package: delta
Status: install ok installed
Architecture: amd64
Version: 4.0-1

Package: removed
Status: deinstall ok config-files
Architecture: amd64
Version: 5.0-1
"""

UPDATES_INDEX = """Package: alpha
Architecture: amd64
Version: 1.0-2

Package: beta
Architecture: all
Version: 2.0-2

Package: gamma
Architecture: i386
Version: 3.0-2

Package: delta
Architecture: amd64
Version: 4.0-1

Package: phantom
Architecture: amd64
Version: 9.9-1
"""


def _write(directory, status, indexes):
    os.makedirs(os.path.join(directory, "var/lib/dpkg"), exist_ok=True)
    lists = os.path.join(directory, "var/lib/apt/lists")
    os.makedirs(lists, exist_ok=True)
    status_path = os.path.join(directory, "var/lib/dpkg/status")
    with open(status_path, "w") as handle:
        handle.write(status)
    for name, body in indexes.items():
        with open(os.path.join(lists, name), "w") as handle:
            handle.write(body)
    return directory


class ParseStatusTests(unittest.TestCase):
    def setUp(self):
        self.dir = _write(tempfile.mkdtemp(prefix="sm-pkg-"), STATUS_FIXTURE, {})

    def test_reads_installed_packages(self):
        installed = dpkg.parse_status(os.path.join(self.dir, "var/lib/dpkg/status"))
        self.assertEqual(len(installed), 4)
        self.assertEqual(installed["alpha"]["version"], "1.0-1")

    def test_skips_packages_that_are_not_installed(self):
        installed = dpkg.parse_status(os.path.join(self.dir, "var/lib/dpkg/status"))
        self.assertNotIn("removed", installed)

    def test_foreign_arch_is_qualified_and_arch_all_is_not(self):
        installed = dpkg.parse_status(os.path.join(self.dir, "var/lib/dpkg/status"))
        # amd64 is the host arch so it needs no suffix; i386 does.
        self.assertIn("alpha", installed)
        self.assertIn("gamma:i386", installed)
        # Architecture: all applies to every arch, so it is never suffixed.
        self.assertIn("beta", installed)


class ParseIndexTests(unittest.TestCase):
    def setUp(self):
        self.dir = _write(tempfile.mkdtemp(prefix="sm-pkg-idx-"), STATUS_FIXTURE, {
            "example_dists_noble-updates_main_binary-amd64_Packages": UPDATES_INDEX,
        })
        self.lists = os.path.join(self.dir, "var/lib/apt/lists")

    def test_arch_all_survives_the_per_arch_filter(self):
        # apt repeats arch:all stanzas into every binary index, so filtering
        # them out by Architecture would lose them entirely.
        entries = dpkg.parse_index(
            os.path.join(self.lists, "example_dists_noble-updates_main_binary-amd64_Packages"), "amd64")
        self.assertIn("beta", entries)

    def test_wrong_arch_stanzas_are_dropped(self):
        entries = dpkg.parse_index(
            os.path.join(self.lists, "example_dists_noble-updates_main_binary-amd64_Packages"), "amd64")
        self.assertNotIn("gamma:i386", entries)

    def test_suite_comes_from_the_filename(self):
        entries = dpkg.parse_index(
            os.path.join(self.lists, "example_dists_noble-updates_main_binary-amd64_Packages"), "amd64")
        self.assertEqual(entries["alpha"]["suite"], "noble-updates")

    def test_phased_percentage_is_recorded(self):
        body = "Package: phased\nArchitecture: amd64\nVersion: 1.0-2\nPhased-Update-Percentage: 60\n"
        self.dir = _write(tempfile.mkdtemp(prefix="sm-pkg-ph-"), STATUS_FIXTURE, {
            "example_dists_noble-updates_main_binary-amd64_Packages": body})
        entries = dpkg.parse_index(
            os.path.join(self.dir, "var/lib/apt/lists", "example_dists_noble-updates_main_binary-amd64_Packages"), "amd64")
        self.assertEqual(entries["phased"]["phased"], 60)

    def test_unphased_entries_have_no_percentage(self):
        entries = dpkg.parse_index(
            os.path.join(self.lists, "example_dists_noble-updates_main_binary-amd64_Packages"), "amd64")
        self.assertIsNone(entries["alpha"]["phased"])


class UpgradableTests(unittest.TestCase):
    def setUp(self):
        self.dir = _write(tempfile.mkdtemp(prefix="sm-pkg-up-"), STATUS_FIXTURE, {
            "example_dists_noble-updates_main_binary-amd64_Packages": UPDATES_INDEX,
            "example_dists_noble-updates_main_binary-i386_Packages": UPDATES_INDEX,
        })
        self.host = dpkg.HostApt(
            status_path=os.path.join(self.dir, "var/lib/dpkg/status"),
            list_dir=os.path.join(self.dir, "var/lib/apt/lists"),
        ).load()

    def test_only_newer_versions_are_reported(self):
        names = [r["name"] for r in self.host.upgradable()]
        # delta is already at the indexed version, so it is not an update.
        self.assertNotIn("delta", names)
        self.assertIn("alpha", names)

    def test_installed_arches_are_covered(self):
        # i386 is installed here, so its update must not be dropped for want
        # of an index for that arch.
        self.assertIn("gamma:i386", [r["name"] for r in self.host.upgradable()])

    def test_installed_but_unindexed_packages_are_absent(self):
        self.assertNotIn("phantom", [r["name"] for r in self.host.upgradable()])

    def test_results_are_name_sorted(self):
        names = [r["name"] for r in self.host.upgradable()]
        self.assertEqual(names, sorted(names))

    def test_security_flag_comes_from_the_suite(self):
        self.assertFalse(any(r["security"] for r in self.host.upgradable()))
        self.dir2 = _write(tempfile.mkdtemp(prefix="sm-pkg-sec-"), STATUS_FIXTURE, {
            "example_dists_noble-security_main_binary-amd64_Packages": UPDATES_INDEX})
        host = dpkg.HostApt(
            status_path=os.path.join(self.dir2, "var/lib/dpkg/status"),
            list_dir=os.path.join(self.dir2, "var/lib/apt/lists")).load()
        self.assertTrue(all(r["security"] for r in host.upgradable() if r["name"] == "alpha"))

    def test_backports_are_reported_but_bucketed(self):
        self.dir3 = _write(tempfile.mkdtemp(prefix="sm-pkg-bp-"), STATUS_FIXTURE, {
            "example_dists_noble-backports_main_binary-amd64_Packages": UPDATES_INDEX})
        host = dpkg.HostApt(
            status_path=os.path.join(self.dir3, "var/lib/dpkg/status"),
            list_dir=os.path.join(self.dir3, "var/lib/apt/lists")).load()
        # apt-get upgrade leaves backports alone, but they are still real.
        self.assertIn("alpha", [r["name"] for r in host.backports()])
        self.assertTrue(all(r["backports"] for r in host.backports()))


class MissingMountTests(unittest.TestCase):
    """A blank page and an unreachable database look identical. They must not."""

    def test_missing_status_file_raises_with_the_fix_in_the_message(self):
        with self.assertRaises(dpkg.DpkgError) as caught:
            dpkg.parse_status("/nonexistent/var/lib/dpkg/status")
        self.assertIn("docker-compose.yml", str(caught.exception))


class PackagesPageTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"DISABLE_AUTH": 1, "TESTING": True})
        self.client = self.app.test_client()

    def test_page_renders_without_the_host_mounts(self):
        response = self.client.get("/packages/")
        self.assertEqual(response.status_code, 200)

    def test_missing_mounts_are_explained_not_silently_empty(self):
        body = self.client.get("/packages/").get_data(as_text=True)
        # The page has to say what is missing and how to fix it, because
        # "0 updates" would be indistinguishable from an up-to-date host.
        self.assertIn("docker-compose.yml", body)

    def test_json_api_reports_failure_rather_than_an_empty_list(self):
        payload = self.client.get("/packages/", headers={"Accept": "application/json"}).get_json()
        self.assertIn("ok", payload)

    def test_page_renders_rows_and_commands_with_a_fixture(self):
        self.dir = _write(tempfile.mkdtemp(prefix="sm-pkg-page-"), STATUS_FIXTURE, {
            "example_dists_noble-updates_main_binary-amd64_Packages": UPDATES_INDEX,
        })
        os.environ["PACKAGES_HOST_DIR"] = self.dir
        try:
            payload = self.client.get("/packages/", headers={"Accept": "application/json"}).get_json()
            self.assertTrue(payload["ok"])
            self.assertIn("alpha", [r["name"] for r in payload["upgradable"]])
            self.assertIn("apt-get install --only-upgrade", payload["command"])
            # The dry run must come first: it is the safe one to read.
            self.assertIn("apt-get -s install", payload["dry_run"])
            body = self.client.get("/packages/").get_data(as_text=True)
            self.assertIn("alpha", body)
            self.assertNotIn("not reachable", body)
        finally:
            del os.environ["PACKAGES_HOST_DIR"]

    def test_command_names_every_upgradable_package(self):
        self.dir = _write(tempfile.mkdtemp(prefix="sm-pkg-cmd-"), STATUS_FIXTURE, {
            "example_dists_noble-updates_main_binary-amd64_Packages": UPDATES_INDEX,
            "example_dists_noble-updates_main_binary-i386_Packages": UPDATES_INDEX,
        })
        os.environ["PACKAGES_HOST_DIR"] = self.dir
        try:
            payload = self.client.get("/packages/", headers={"Accept": "application/json"}).get_json()
            for name in [r["name"] for r in payload["upgradable"]]:
                self.assertIn(name, payload["command"])
        finally:
            del os.environ["PACKAGES_HOST_DIR"]


if __name__ == "__main__":
    unittest.main()
