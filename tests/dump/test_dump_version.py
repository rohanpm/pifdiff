from unittest import mock
from pathlib import Path

import pidiff
from pidiff._impl.dump.dump import get_version
import pidiff._impl.dump.dump


def pkgpath(value):
    locate = lambda: Path(value)
    return mock.Mock(locate=locate)


def no_files(name):
    return []


def test_version_from_egg(tmpdir, monkeypatch):
    # Prevent any importlib.metadata matches for this test
    monkeypatch.setattr(pidiff._impl.dump.dump, "dist_files", no_files)

    # make some fake egg files
    tmpdir.mkdir("egg1").join("top_level.txt").write("foo\nbar\n")
    tmpdir.mkdir("egg4").join("top_level.txt").write("baz\nsome.test.module\n")
    tmpdir.mkdir("egg5").join("top_level.txt").write("quux\n")

    # Set up return values for pkg_resources
    fake_dists = [
        str(tmpdir.join("egg1")),
        # this dist has no egg_info available
        None,
        # this dist has an unreadable egg_info
        str(tmpdir.join("notexist-egg")),
        str(tmpdir.join("egg4")),
        str(tmpdir.join("egg5")),
    ]

    fake_dists = [mock.Mock(egg_info=path, project_name="x") for path in fake_dists]

    # Let some of them have defined versions
    fake_dists[1].version = "2.0"
    fake_dists[3].version = "1.2.3"
    fake_dists[4].version = "5.0"

    with mock.patch("pkg_resources.working_set", new=fake_dists):
        version = get_version("some.test.module", pidiff)

    # It should pick the version from the dist where top_level.txt
    # contained a reference to this module
    assert version == "1.2.3"


def test_version_from_importlib(tmpdir, monkeypatch):
    module = mock.Mock(__file__="/some/great/file.pyc")

    def mything_files(name):
        assert name == "mything"
        return [pkgpath("foo"), pkgpath("bar"), pkgpath("/some/great/file.py")]

    def mything_version(name):
        assert name == "mything"
        return "1.2.3"

    monkeypatch.setattr(pidiff._impl.dump.dump, "dist_files", mything_files)
    monkeypatch.setattr(pidiff._impl.dump.dump, "dist_version", mything_version)

    with mock.patch(
        "pkg_resources.working_set", new=[mock.Mock(project_name="mything")]
    ):
        version = get_version("mything", module)

    assert version == "1.2.3"
