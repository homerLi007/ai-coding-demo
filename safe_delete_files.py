import os
import stat


def safe_delete_files(file_paths, allowed_root):
    """Delete the given files, but only if they are regular files located
    beneath ``allowed_root``.

    Each path is resolved (following symlinks) and must remain inside the
    canonical ``allowed_root`` directory. Symlinks themselves are refused so
    that a link cannot redirect deletion outside the sandbox. Only regular
    files are removed; directories, devices, FIFOs, and sockets are skipped.

    Returns a dict mapping each input path to a result string:
    ``"deleted"``, ``"not_found"``, ``"outside_root"``, ``"symlink"``,
    ``"not_regular_file"``, or ``"error:<reason>"``.

    Known limitations (pure-Python scope):
    - TOCTOU races: between the checks and ``os.remove`` another process could
      swap the path (e.g. replace a file with a symlink). Fully closing this
      requires ``openat``/``unlinkat`` with ``O_NOFOLLOW`` and directory file
      descriptors, which are not exposed by the standard library.
    - ``allowed_root`` itself is trusted; callers must pass a canonical path.
    - No privilege dropping or capability checks are performed.
    """
    root = os.path.realpath(allowed_root)
    results = {}

    for path in file_paths:
        try:
            if os.path.islink(path):
                results[path] = "symlink"
                continue

            resolved = os.path.realpath(path)
            try:
                if os.path.commonpath([root, resolved]) != root:
                    results[path] = "outside_root"
                    continue
            except ValueError:
                # Mixed absolute/relative inputs or different drives: cannot
                # be proven inside root, so refuse.
                results[path] = "outside_root"
                continue

            try:
                st = os.lstat(resolved)
            except FileNotFoundError:
                results[path] = "not_found"
                continue

            if not stat.S_ISREG(st.st_mode):
                results[path] = "not_regular_file"
                continue

            # Re-verify containment immediately before deletion on the exact
            # path we are about to unlink, and refuse if the final component
            # has become a symlink. This narrows the window in which a
            # symlink swap could redirect the delete outside the root.
            # KNOWN LIMITATION: TOCTOU race between this re-check and
            # os.remove remains; an attacker controlling a directory inside
            # allowed_root could still swap an intermediate directory or the
            # final component after the check. Atomic unlink-by-fd (unlinkat
            # with O_NOFOLLOW) is not available in the standard library, so
            # this window cannot be fully closed here.
            if os.path.islink(resolved):
                results[path] = "symlink"
                continue
            try:
                recheck = os.path.realpath(resolved)
                if os.path.commonpath([root, recheck]) != root:
                    results[path] = "outside_root"
                    continue
            except ValueError:
                results[path] = "outside_root"
                continue

            # Re-resolve immediately before unlink and unlink the freshly
            # resolved path, so the containment check and the unlink operate
            # on the same value. This narrows (but does not eliminate) the
            # window in which an intermediate component could be swapped.
            # KNOWN LIMITATION: TOCTOU race between this final realpath and
            # os.remove remains; atomic unlink-by-fd (unlinkat with
            # O_NOFOLLOW) is not available in the standard library.
            final_path = os.path.realpath(resolved)
            try:
                if os.path.commonpath([root, final_path]) != root:
                    results[path] = "outside_root"
                    continue
            except ValueError:
                results[path] = "outside_root"
                continue
            if os.path.islink(final_path):
                results[path] = "symlink"
                continue

            os.remove(final_path)
            results[path] = "deleted"
        except OSError as exc:
            results[path] = f"error:{exc.strerror or exc}"

    return results