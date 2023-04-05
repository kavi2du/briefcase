import pytest

from briefcase.integrations.windows_sdk import WindowsSDK

from .test_WindowsSDK__verify import setup_winsdk_path


@pytest.mark.parametrize(
    "versions, expected",
    [
        ([], []),
        (["85.0.1"], ["85.0.1.0"]),
        # ensure the newest version is first
        (["85.0.1", "85.0.2", "85.0.3"], ["85.0.3.0", "85.0.2.0", "85.0.1.0"]),
        # ensure invalid versions are ignored
        (["85.0.1", "85.0.2", "86.0.3"], ["85.0.2.0", "85.0.1.0"]),
        # ensure no versions if all versions are invalid
        (["86.0.1", "86.0.2", "86.0.3"], []),
    ],
)
def test_sdk_versions_from_bin(tmp_path, versions, expected):
    """Versions from SDK bin directory are properly found and sorted."""
    WindowsSDK.SDK_VERSION = "85.0"
    sdk_path = tmp_path / "win_sdk"
    for version in versions:
        setup_winsdk_path(sdk_path.parent, version)

    assert WindowsSDK._sdk_versions_from_bin(sdk_path) == expected
