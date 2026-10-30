import os


def safe_delete_files(file_paths, allowed_root):
    """Delete each file in file_paths, but only if it resolves to a path
    inside allowed_root. Returns a dict mapping each path to a result.

    This guards against path traversal and symlink escapes by resolving
    the real path before checking containment.
    """
    root = os.path.realpath(allowed_root)
    results = {}

    for path in file_paths:
        try:
            real_path = os.path.realpath(path)

            if os.path.commonpath([root, real_path]) != root:
                results[path] = "refused: outside allowed root"
                continue

            if not os.path.isfile(real_path):
                results[path] = "skipped: not a regular file"
                continue

            os.remove(real_path)
            results[path] = "deleted"
        except FileNotFoundError:
            results[path] = "skipped: not found"
        except OSError as exc:
            results[path] = f"error: {exc}"

    return results