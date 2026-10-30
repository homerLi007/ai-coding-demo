import os
from pathlib import Path


def secure_delete_files(file_paths, allowed_root):
    """Delete the given files, but only if they reside within allowed_root.

    Args:
        file_paths: Iterable of file path strings to delete.
        allowed_root: Directory that all deletions must be confined to.

    Returns:
        A dict mapping each input path to a result string:
        "deleted", "not_found", "not_a_file", or "outside_allowed_root".
    """
    root = Path(allowed_root).resolve()
    results = {}

    for raw_path in file_paths:
        path = Path(raw_path).resolve()

        if root not in path.parents and path != root:
            results[raw_path] = "outside_allowed_root"
            continue

        if not path.exists():
            results[raw_path] = "not_found"
            continue

        if not path.is_file():
            results[raw_path] = "not_a_file"
            continue

        try:
            os.remove(path)
            results[raw_path] = "deleted"
        except OSError:
            results[raw_path] = "error"

    return results