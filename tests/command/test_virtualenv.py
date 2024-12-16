from unittest import mock
from pytest import raises

from virtualenvapi.exceptions import PackageInstallationException

from pidiff._impl.command import VirtualEnvironmentExt


def test_install_fail_with_missing_logs(caplog, tmpdir):
    env = VirtualEnvironmentExt(str(tmpdir))
    env.install = mock.Mock()
    env.install.side_effect = PackageInstallationException("something went wrong")

    with raises(SystemExit) as exc:
        env.install_or_die("some-package")

    assert exc.value.code == 16

    assert caplog.messages == [
        "An exception was encountered while accessing install logs",
        "Failed to install: some-package",
    ]


def test_install_fail_with_logs(caplog, tmpdir):
    envdir = tmpdir.mkdir("env")
    envdir.join("build.log").write("build log here")
    envdir.join("build.err").write("build err here")

    env = VirtualEnvironmentExt(str(envdir))
    env.install = mock.Mock()
    env.install.side_effect = PackageInstallationException("something went wrong")

    with raises(SystemExit) as exc:
        env.install_or_die("some-package")

    assert exc.value.code == 16

    assert caplog.messages == [
        "build log here",
        "build err here",
        "Failed to install: some-package",
    ]


def test_install_uses_editable_for_local(caplog, tmpdir):
    envdir = tmpdir.mkdir("env")

    env = VirtualEnvironmentExt(str(envdir))
    env.install = mock.Mock()

    tmpdir.join("setup.py").write("")
    env.install_or_die(str(tmpdir))

    # It should have been called with -e for editable.
    # Note: this looks wrong (embedding option with package
    # name in a single string like this), but it's correct;
    # virtualenvapi splits internally
    env.install.assert_called_once_with("-e %s" % tmpdir)
