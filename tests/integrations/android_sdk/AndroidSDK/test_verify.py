import os
import shutil
import sys
from unittest.mock import MagicMock

import pytest
from requests import exceptions as requests_exceptions

from briefcase.exceptions import (
    BriefcaseCommandError,
    MissingToolError,
    NetworkFailure
)
from briefcase.integrations.android_sdk import AndroidSDK


@pytest.fixture
def mock_command(tmp_path):
    command = MagicMock()

    # Mock-out the `sys` module so we can mock out the Python version in some tests.
    command.sys = MagicMock()
    command.tools_path = tmp_path / "tools"

    # Make the `os` module and `host_os` live.
    command.os = os

    # Mock a host platform
    command.host_os = 'Unknown'

    # Override some other modules so we can test side-effects.
    command.download_url = MagicMock()
    command.subprocess = MagicMock()
    command.shutil = MagicMock()
    # Use the original module rmtree implementation
    command.shutil.rmtree = shutil.rmtree

    return command


def mock_unpack(filename, extract_dir):
    # Create a file that would have been created by unpacking the archive
    # This includes the duplicated "cmdline-tools" folder name
    (extract_dir / "cmdline-tools" / "bin").mkdir(parents=True)
    (extract_dir / "cmdline-tools" / "bin" / "sdkmanager").touch(mode=0o644)
    (extract_dir / "cmdline-tools" / "bin" / "avdmanager").touch(mode=0o644)


def accept_license(android_sdk_root_path):
    "Generate a side effect method that will accept a license."
    def _side_effect(*args, **kwargs):
        license_dir = android_sdk_root_path / "licenses"
        license_dir.mkdir(parents=True)
        (license_dir / "android-sdk-license").touch()

    return _side_effect


@pytest.mark.parametrize("host_os", ("ArbitraryNotWindows", "Windows"))
def test_succeeds_immediately_in_happy_path(mock_command, host_os, tmp_path):
    "If verify is invoked on a path containing an Android SDK, it does nothing."
    # If `sdkmanager` exists and has the right permissions, and
    # `android-sdk-license` exists, verify() should
    # succeed, create no subprocesses, make no requests, and return a
    # SDK wrapper.

    # On Windows, this requires `sdkmanager.bat`; on non-Windows, it requires
    # `sdkmanager`.

    # Create `sdkmanager` and the license file.
    android_sdk_root_path = tmp_path / "tools" / "android_sdk"
    tools_bin = android_sdk_root_path / "cmdline-tools" / "latest" / "bin"
    tools_bin.mkdir(parents=True, mode=0o755)
    if host_os == "Windows":
        (tools_bin / "sdkmanager.bat").touch()
    else:
        (tools_bin / "sdkmanager").touch(mode=0o755)

    # Pre-accept the license
    accept_license(android_sdk_root_path)()

    # Configure `mock_command` to assume the `host_os` we parameterized with.
    mock_command.host_os = host_os

    # Expect verify() to succeed
    sdk = AndroidSDK.verify(mock_command, jdk=MagicMock())

    # No calls to download, run or unpack anything.
    mock_command.download_url.assert_not_called()
    mock_command.subprocess.run.assert_not_called()
    mock_command.subprocess.check_output.assert_not_called()
    mock_command.shutil.unpack_archive.assert_not_called()

    # The returned SDK has the expected root path.
    assert sdk.root_path == android_sdk_root_path


def test_user_provided_sdk(mock_command, tmp_path):
    "If the user specifies a valid ANDROID_SDK_ROOT, it is used"
    # Create `sdkmanager` and the license file.
    existing_android_sdk_root_path = tmp_path / "other_sdk"
    tools_bin = existing_android_sdk_root_path / "cmdline-tools" / "latest" / "bin"
    tools_bin.mkdir(parents=True, mode=0o755)
    (tools_bin / "sdkmanager").touch(mode=0o755)

    # Pre-accept the license
    accept_license(existing_android_sdk_root_path)()

    # Set the environment to specify ANDROID_SDK_ROOT
    mock_command.os.environ = {
        'ANDROID_SDK_ROOT': os.fsdecode(existing_android_sdk_root_path)
    }

    # Expect verify() to succeed
    sdk = AndroidSDK.verify(mock_command, jdk=MagicMock())

    # No calls to download, run or unpack anything.
    mock_command.download_url.assert_not_called()
    mock_command.subprocess.run.assert_not_called()
    mock_command.subprocess.check_output.assert_not_called()
    mock_command.shutil.unpack_archive.assert_not_called()

    # The returned SDK has the expected root path.
    assert sdk.root_path == existing_android_sdk_root_path


def test_invalid_user_provided_sdk(mock_command, tmp_path):
    "If the user specifies an invalid ANDROID_SDK_ROOT, it is ignored"

    # Create `sdkmanager` and the license file
    # for the *briefcase* managed version of the SDK.
    android_sdk_root_path = tmp_path / "tools" / "android_sdk"
    tools_bin = android_sdk_root_path / "cmdline-tools" / "latest" / "bin"
    tools_bin.mkdir(parents=True, mode=0o755)
    (tools_bin / "sdkmanager").touch(mode=0o755)

    # Pre-accept the license
    accept_license(android_sdk_root_path)()

    # Set the environment to specify an ANDROID_SDK_ROOT that doesn't exist
    mock_command.os.environ = {
        'ANDROID_SDK_ROOT': os.fsdecode(tmp_path / "other_sdk")
    }

    # Expect verify() to succeed
    sdk = AndroidSDK.verify(mock_command, jdk=MagicMock())

    # No calls to download, run or unpack anything.
    mock_command.download_url.assert_not_called()
    mock_command.subprocess.run.assert_not_called()
    mock_command.subprocess.check_output.assert_not_called()
    mock_command.shutil.unpack_archive.assert_not_called()

    # The returned SDK has the expected root path.
    assert sdk.root_path == android_sdk_root_path


@pytest.mark.parametrize(
    "host_os, dl_name",
    [
        ("Darwin", "mac"),
        ("Windows", "win"),
        ("Linux", "linux"),
    ]
)
def test_download_sdk(mock_command, tmp_path, host_os, dl_name):
    "If an SDK is not available, one will be downloaded"
    android_sdk_root_path = tmp_path / "tools" / "android_sdk"
    cmdline_tools_base_path = android_sdk_root_path / "cmdline-tools"

    # Mock-out `host_os` so we only do our permission check on non-Windows.
    mock_command.host_os = host_os

    # The download will produce a cached file.
    cache_file = MagicMock()
    mock_command.download_url.return_value = cache_file

    # Calling unpack will create files
    mock_command.shutil.unpack_archive.side_effect = mock_unpack

    # Set up a side effect for accepting the license
    mock_command.subprocess.run.side_effect = accept_license(android_sdk_root_path)

    # Call `verify()`
    sdk = AndroidSDK.verify(mock_command, jdk=MagicMock())

    # Validate that the SDK was downloaded and unpacked
    url = f"https://dl.google.com/android/repository/commandlinetools-{dl_name}-8092744_latest.zip"
    mock_command.download_url.assert_called_once_with(
        url=url,
        download_path=mock_command.tools_path,
    )

    mock_command.shutil.unpack_archive.assert_called_once_with(
        cache_file,
        extract_dir=cmdline_tools_base_path
    )

    # The cached file will be deleted
    cache_file.unlink.assert_called_once_with()

    # The commandline tools path exists, in both "latest" and versioned form
    assert sdk.cmdline_tools_path.exists()
    assert sdk.cmdline_tools_version_path.exists()

    # The "latest" path is a symlink; the versioned form is not.
    assert sdk.cmdline_tools_path.is_symlink()
    assert not sdk.cmdline_tools_version_path.is_symlink()

    # The "latest" symlink points at the versioned form.
    assert sdk.cmdline_tools_path.resolve() == sdk.cmdline_tools_version_path

    # On non-Windows, ensure the unpacked binary was made executable
    if host_os != 'Windows':
        assert os.access(cmdline_tools_base_path / 'latest' / 'bin' / 'sdkmanager', os.X_OK)

    # The license has been accepted
    assert (android_sdk_root_path / "licenses" / "android-sdk-license").exists()

    # The returned SDK has the expected root path.
    assert sdk.root_path == android_sdk_root_path


@pytest.mark.parametrize(
    "host_os, dl_name",
    [
        ("Darwin", "mac"),
        ("Windows", "win"),
        ("Linux", "linux"),
    ]
)
def test_download_sdk_legacy_install(mock_command, tmp_path, host_os, dl_name):
    "If the legacy SDK tools are present, they will be deleted"
    android_sdk_root_path = tmp_path / "tools" / "android_sdk"
    cmdline_tools_base_path = android_sdk_root_path / "cmdline-tools"

    # Create files that mock the existence of the *old* SDK tools.
    sdk_tools_base_path = android_sdk_root_path / "tools"
    (sdk_tools_base_path / "bin").mkdir(parents=True)
    (sdk_tools_base_path / "bin" / "sdkmanager").touch(mode=0o755)
    (sdk_tools_base_path / "bin" / "avdmanager").touch(mode=0o755)

    # Create some of the tools that have locations that overlap
    # between legacy and new.
    emulator_path = android_sdk_root_path / "emulator"
    emulator_path.mkdir(parents=True)
    (emulator_path / "emulator").touch(mode=0o755)

    # Mock-out `host_os` so we only do our permission check on non-Windows.
    mock_command.host_os = host_os

    # The download will produce a cached file.
    cache_file = MagicMock()
    mock_command.download_url.return_value = cache_file

    # Calling unpack will create files
    mock_command.shutil.unpack_archive.side_effect = mock_unpack

    # Set up a side effect for accepting the license
    mock_command.subprocess.run.side_effect = accept_license(android_sdk_root_path)

    # Call `verify()`
    sdk = AndroidSDK.verify(mock_command, jdk=MagicMock())

    # Validate that the SDK was downloaded and unpacked
    url = f"https://dl.google.com/android/repository/commandlinetools-{dl_name}-8092744_latest.zip"
    mock_command.download_url.assert_called_once_with(
        url=url,
        download_path=mock_command.tools_path,
    )

    mock_command.shutil.unpack_archive.assert_called_once_with(
        cache_file,
        extract_dir=cmdline_tools_base_path
    )

    # The cached file will be deleted
    cache_file.unlink.assert_called_once_with()

    # The commandline tools path exists, in both "latest" and versioned form
    assert sdk.cmdline_tools_path.exists()
    assert sdk.cmdline_tools_version_path.exists()

    # The "latest" path is a symlink; the versioned form is not.
    assert sdk.cmdline_tools_path.is_symlink()
    assert not sdk.cmdline_tools_version_path.is_symlink()

    # The "latest" symlink points at the versioned form.
    assert sdk.cmdline_tools_path.resolve() == sdk.cmdline_tools_version_path

    # The legacy SDK tools have been removed
    assert not sdk_tools_base_path.exists()
    assert not emulator_path.exists()

    # On non-Windows, ensure the unpacked binary was made executable
    if host_os != 'Windows':
        assert os.access(cmdline_tools_base_path / 'latest' / 'bin' / 'sdkmanager', os.X_OK)

    # The license has been accepted
    assert (android_sdk_root_path / "licenses" / "android-sdk-license").exists()

    # The returned SDK has the expected root path.
    assert sdk.root_path == android_sdk_root_path


def test_no_install(mock_command, tmp_path):
    "If an SDK is not available, and install is not requested, an error is raised"

    # Call `verify()`
    with pytest.raises(MissingToolError):
        AndroidSDK.verify(mock_command, jdk=MagicMock(), install=False)

    assert mock_command.download_url.call_count == 0


@pytest.mark.skipif(
    sys.platform == "win32", reason="executable permission doesn't make sense on Windows"
)
def test_download_sdk_if_sdkmanager_not_executable(mock_command, tmp_path):
    """An SDK will be downloaded and unpackged if `tools/bin/sdkmanager` exists
    but does not have its permissions set properly."""
    android_sdk_root_path = tmp_path / "tools" / "android_sdk"
    cmdline_tools_base_path = android_sdk_root_path / "cmdline-tools"

    # Create pre-existing non-executable `sdkmanager`.
    (cmdline_tools_base_path / "8092744" / "bin").mkdir(parents=True)
    (cmdline_tools_base_path / "8092744" / "bin" / "sdkmanager").touch(mode=0o644)
    (cmdline_tools_base_path / "latest").symlink_to(cmdline_tools_base_path / "8092744")

    # The download will produce a cached file
    cache_file = MagicMock()
    mock_command.download_url.return_value = cache_file

    # Calling unpack will create files
    mock_command.shutil.unpack_archive.side_effect = mock_unpack

    # Set up a side effect for accepting the license
    mock_command.subprocess.run.side_effect = accept_license(android_sdk_root_path)

    # Call `verify()`
    sdk = AndroidSDK.verify(mock_command, jdk=MagicMock())

    # Validate that the SDK was downloaded and unpacked
    mock_command.download_url.assert_called_once_with(
        url="https://dl.google.com/android/repository/commandlinetools-unknown-8092744_latest.zip",
        download_path=mock_command.tools_path,
    )

    mock_command.shutil.unpack_archive.assert_called_once_with(
        cache_file,
        extract_dir=cmdline_tools_base_path
    )

    # The cached file will be deleted
    cache_file.unlink.assert_called_once_with()

    # The license has been accepted
    assert (android_sdk_root_path / "licenses" / "android-sdk-license").exists()

    # The returned SDK has the expected root path.
    assert sdk.root_path == android_sdk_root_path


def test_raises_networkfailure_on_connectionerror(mock_command):
    "If an error occurs downloading the ZIP file, and error is raised."
    mock_command.download_url.side_effect = requests_exceptions.ConnectionError()

    with pytest.raises(NetworkFailure):
        AndroidSDK.verify(mock_command, jdk=MagicMock())

    # The download was attempted
    mock_command.download_url.assert_called_once_with(
        url="https://dl.google.com/android/repository/commandlinetools-unknown-8092744_latest.zip",
        download_path=mock_command.tools_path,
    )
    # But no unpack occurred
    assert mock_command.shutil.unpack_archive.call_count == 0


def test_detects_bad_zipfile(mock_command, tmp_path):
    "If the ZIP file is corrupted, an error is raised."
    android_sdk_root_path = tmp_path / "tools" / "android_sdk"

    cache_file = MagicMock()
    mock_command.download_url.return_value = cache_file

    # But the unpack will fail.
    mock_command.shutil.unpack_archive.side_effect = shutil.ReadError

    with pytest.raises(BriefcaseCommandError):
        AndroidSDK.verify(mock_command, jdk=MagicMock())

    # The download attempt was made.
    mock_command.download_url.assert_called_once_with(
        url="https://dl.google.com/android/repository/commandlinetools-unknown-8092744_latest.zip",
        download_path=mock_command.tools_path,
    )
    mock_command.shutil.unpack_archive.assert_called_once_with(
        cache_file,
        extract_dir=android_sdk_root_path / "cmdline-tools"
    )
