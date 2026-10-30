import os


def secure_delete_files(file_paths, allowed_root):
    """Delete files from a list, restricted to a trusted root directory.

    Each path is resolved to its real absolute location and must reside
    within ``allowed_root``. Symlinks are rejected to prevent escaping the
    sandbox. Only regular files are deleted; directories are skipped.

    Args:
        file_paths: Iterable of file path strings to delete.
        allowed_root: Directory that all deletions must stay within.

    Returns:
        A dict mapping each input path to a result string:
        "deleted", "not_found", "not_a_file", "outside_root",
        "symlink_rejected", or "error: <message>".
    """
    root = os.path.realpath(allowed_root)
    results = {}

    for path in file_paths:
        try:
            if os.path.islink(path):
                results[path] = "symlink_rejected"
                continue

            real_path = os.path.realpath(path)

            if os.path.commonpath([root, real_path]) != root:
                results[path] = "outside_root"
                continue

            if not os.path.exists(real_path):
                results[path] = "not_found"
                continue

            if not os.path.isfile(real_path):
                results[path] = "not_a_file"
                continue

            os.remove(real_path)
            results[path] = "deleted"
        except (OSError, ValueError) as exc:
            results[path] = f"error: {exc}"

    return results