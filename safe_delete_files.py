import os


def safe_delete_files(file_paths, allowed_root):
    """Delete each file in file_paths, but only if it resolves to a path
    inside allowed_root. Returns a dict mapping each path to a status string.

    This guards against path traversal and accidental deletion of files
    outside the intended directory.
    """
    root = os.path.realpath(allowed_root)
    results = {}

    for path in file_paths:
        real_path = os.path.realpath(path)

        # Ensure the target is within the allowed root directory.
        if os.path.commonpath([root, real_path]) != root:
            results[path] = "skipped: outside allowed root"
            continue

        if not os.path.exists(real_path):
            results[path] = "skipped: does not exist"
            continue

        if not os.path.isfile(real_path):
            results[path] = "skipped: not a regular file"
            continue

        try:
            os.remove(real_path)
            results[path] = "deleted"
        except OSError as exc:
            results[path] = f"error: {exc}"

    return results