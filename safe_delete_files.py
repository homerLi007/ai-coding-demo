import os


def safe_delete_files(file_paths, allowed_root):
    """Delete each file in file_paths, but only if it resides within allowed_root.

    This guards against path traversal and accidental deletion of files outside
    the intended directory. Symlinks are resolved before the containment check,
    and only regular files (not directories or special files) are removed.

    Args:
        file_paths: Iterable of file path strings to delete.
        allowed_root: Directory that all deletions must be confined to.

    Returns:
        A dict mapping each input path to a result string: "deleted",
        "not_found", "not_a_file", or "outside_allowed_root".

    Raises:
        ValueError: If allowed_root does not exist or is not a directory.
    """
    root_real = os.path.realpath(allowed_root)
    if not os.path.isdir(root_real):
        raise ValueError(f"allowed_root is not a directory: {allowed_root!r}")

    results = {}
    for path in file_paths:
        real_path = os.path.realpath(path)

        # Ensure the resolved path is inside the allowed root.
        if os.path.commonpath([root_real, real_path]) != root_real:
            results[path] = "outside_allowed_root"
            continue

        if not os.path.exists(real_path):
            results[path] = "not_found"
            continue

        if not os.path.isfile(real_path):
            results[path] = "not_a_file"
            continue

        os.remove(real_path)
        results[path] = "deleted"

    return results