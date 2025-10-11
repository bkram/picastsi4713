"""Guard against accidentally committing binary artifacts.

The repository previously included several binary datasheets and mock
screenshots.  This test ensures we keep the tree free from similar
artifacts by verifying that every tracked file looks like plain text.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


# Historical commits added the Si4713 datasheet PDF before the binary guard
# existed.  We keep an allow-list of those legacy blobs so the regression test
# focuses on preventing new binary content from being introduced again.
KNOWN_LEGACY_BINARY_PATHS = {
    "docs/._Si4712-13-B30.pdf",
    "docs/Si4712-13-B30.pdf",
}


def is_probably_binary(data: bytes) -> bool:
    """Heuristically determine whether *data* represents binary content."""

    if not data:
        return False

    if b"\0" in data:
        return True

    non_ascii_ratio = sum(byte >= 0x80 for byte in data) / len(data)
    return non_ascii_ratio > 0.30


def test_repository_contains_no_binary_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tracked_files = subprocess.check_output(
        ["git", "ls-files"], cwd=repo_root, text=True
    ).splitlines()

    binary_files: list[str] = []
    for relative_path in tracked_files:
        file_path = repo_root / relative_path
        if not file_path.is_file():
            continue
        data = file_path.read_bytes()
        if is_probably_binary(data):
            binary_files.append(relative_path)

    assert not binary_files, f"Binary files tracked by git: {binary_files}"


def test_git_history_contains_no_binary_blobs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rev_list = subprocess.check_output(
        ["git", "rev-list", "--objects", "HEAD"], cwd=repo_root, text=True
    )

    seen: set[str] = set()
    binary_blobs: list[str] = []
    for line in rev_list.splitlines():
        try:
            object_id, path = line.split(" ", 1)
        except ValueError:
            continue

        if object_id in seen:
            continue
        seen.add(object_id)

        obj_type = subprocess.check_output(
            ["git", "cat-file", "-t", object_id], cwd=repo_root, text=True
        ).strip()
        if obj_type != "blob":
            continue

        data = subprocess.check_output(
            ["git", "cat-file", "blob", object_id], cwd=repo_root
        )
        if is_probably_binary(data) and path not in KNOWN_LEGACY_BINARY_PATHS:
            binary_blobs.append(path)

    assert not binary_blobs, f"Binary blobs in git history: {binary_blobs}"
