import os
import stat


# Anchor for walking the root path component-by-component. We start from
# the filesystem root ("/") and open each component with
# O_NOFOLLOW|O_DIRECTORY, so no intermediate component of the caller-
# supplied root can be a symlink that redirects the operation.
_ROOT_ANCHOR = os.sep


def _open_dir_nofollow(path, dir_fd=None):
    """Open a directory without following a final symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if dir_fd is not None:
        return os.open(path, flags, dir_fd=dir_fd)
    return os.open(path, flags)


def _split_components(path):
    """Split an absolute path into components, rejecting `..` and NULs.

    Returns (parts, error_string_or_None). `parts` is a list of path
    components with empty and `.` entries removed. Any `..` component
    is rejected outright (rather than being collapsed by normpath),
    so callers cannot smuggle traversal through lexical normalization.

    Embedded NUL bytes (and other control characters) are rejected
    here so that downstream os.open/os.unlink calls never raise
    ValueError (which is not an OSError and would otherwise escape
    the per-path error handling).
    """
    if not isinstance(path, str):
        return None, "error: path must be a string"
    if "\x00" in path:
        return None, "error: invalid path"
    # Reject other control characters that have no business in a path
    # component; this also guards against odd inputs that some
    # platforms may treat specially.
    for ch in path:
        if ord(ch) < 0x20 or ord(ch) == 0x7f:
            return None, "error: invalid path"
    if not os.path.isabs(path):
        return None, "error: path must be absolute"
    parts = []
    for p in path.split(os.sep):
        if p == "" or p == os.curdir:
            continue
        if p == os.pardir:
            return None, "error: invalid path"
        parts.append(p)
    return parts, None


def _open_root_nofollow(root):
    """Open `root` as a directory fd, refusing symlinks in ANY component.

    The caller-supplied `root` is treated as an absolute path (relative
    paths are rejected). We walk it component-by-component starting from
    the filesystem root, opening each component with
    O_NOFOLLOW|O_DIRECTORY. This eliminates the race where an
    intermediate component of the root path is (or becomes) a symlink
    between resolution and open.

    `..` components are rejected outright rather than collapsed by
    normpath, so a root like `/safe/root/../etc` is refused instead of
    silently being reinterpreted as `/etc`.

    Returns (fd, error_string_or_None).
    """
    parts, err = _split_components(root)
    if err is not None:
        return None, err

    try:
        fd = _open_dir_nofollow(os.sep)
    except OSError as exc:
        return None, f"error: {exc}"

    opened = [fd]
    success = False
    try:
        for comp in parts:
            try:
                next_fd = _open_dir_nofollow(comp, dir_fd=fd)
            except FileNotFoundError:
                return None, "missing"
            except NotADirectoryError:
                return None, "error: root is not a directory"
            except OSError as exc:
                if exc.errno == getattr(os, "ELOOP", -1):
                    return None, "error: root contains symlink"
                return None, f"error: {exc}"
            opened.append(next_fd)
            fd = next_fd
        # Transfer ownership of the final fd to the caller; close the
        # intermediate ones.
        result_fd = opened[-1]
        for extra in opened[:-1]:
            try:
                os.close(extra)
            except OSError:
                pass
        success = True
        return result_fd, None
    finally:
        # On any non-success exit (early return or exception), close
        # every fd we opened. On success the final fd has been handed
        # off and the intermediates already closed, so there is nothing
        # left to close here.
        if not success:
            for extra in opened:
                try:
                    os.close(extra)
                except OSError:
                    pass


def _safe_unlink_at(root_fd, rel_parts):
    """Delete the file identified by `rel_parts` under `root_fd`.

    `rel_parts` is an already-split list of path components relative to
    `root_fd` (as produced by `_path_relative_to_root`). It must not be
    empty, must not contain `..`, and must not contain absolute or
    otherwise invalid components; those invariants are enforced here so
    the confinement property is locally verifiable rather than relying
    on the caller.

    Every intermediate component is opened with
    O_NOFOLLOW|O_DIRECTORY relative to the previous fd, so no symlink in
    any component can redirect the operation outside the root. The
    final unlink is done relative to the parent directory fd, so there
    is no TOCTOU window between the confinement check and the unlink.

    The final component is unlinked directly with os.unlink(name,
    dir_fd=parent_fd). We deliberately do NOT stat-then-unlink: that
    would introduce a race where the target could be swapped between
    the check and the unlink. os.unlink does not follow symlinks, so a
    symlink at the final component is removed as a link (its target is
    untouched). The "regular file only" contract is therefore
    best-effort at the final component: a symlink or other non-regular
    entry that appears at the final name will be unlinked as itself.

    Returns one of: "deleted", "missing", "not_a_file",
    "error: outside root", or "error: <reason>".
    """
    if not isinstance(rel_parts, (list, tuple)) or not rel_parts:
        return "not_a_file"
    for comp in rel_parts:
        if not isinstance(comp, str) or comp in ("", os.curdir, os.pardir):
            return "error: outside root"
        if os.sep in comp or (os.altsep and os.altsep in comp):
            return "error: outside root"
        if "\x00" in comp:
            return "error: outside root"

    dir_fd = root_fd
    opened_fds = []
    try:
        for comp in rel_parts[:-1]:
            try:
                next_fd = _open_dir_nofollow(comp, dir_fd=dir_fd)
            except FileNotFoundError:
                return "missing"
            except NotADirectoryError:
                return "not_a_file"
            except OSError as exc:
                if exc.errno == getattr(os, "ELOOP", -1):
                    return "not_a_file"
                return f"error: {exc}"
            opened_fds.append(next_fd)
            dir_fd = next_fd

        name = rel_parts[-1]

        # Reject directories explicitly so we never rmdir-by-accident;
        # os.unlink on a directory raises IsADirectoryError anyway, but
        # we want a stable "not_a_file" result. We use lstat (no
        # follow) purely to classify the entry; the unlink itself is
        # the authoritative operation and is not gated on this check.
        try:
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "missing"
        except OSError as exc:
            return f"error: {exc}"

        if stat.S_ISDIR(st.st_mode):
            return "not_a_file"

        try:
            os.unlink(name, dir_fd=dir_fd)
        except FileNotFoundError:
            return "missing"
        except (IsADirectoryError, NotADirectoryError):
            return "not_a_file"
        except OSError as exc:
            return f"error: {exc}"
        return "deleted"
    finally:
        for fd in reversed(opened_fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _path_relative_to_root(root, path):
    """Return `path` expressed relative to `root` as a component list.

    Returns (rel_parts, error_string_or_None). `rel_parts` is a list of
    path components (possibly empty if `path` equals `root`).

    Both `root` and `path` are treated lexically (no symlink resolution).
    Relative `path` values are interpreted relative to `root`, NOT the
    process CWD. `..` components are rejected outright rather than
    collapsed by normpath, so traversal cannot be smuggled through
    lexical normalization.
    """
    if not isinstance(root, str) or not isinstance(path, str):
        return None, "error: outside root"
    if not os.path.isabs(root):
        return None, "error: outside root"

    root_parts, err = _split_components(root)
    if err is not None:
        return None, "error: outside root"

    if os.path.isabs(path):
        path_parts, err = _split_components(path)
        if err is not None:
            return None, "error: outside root"
    else:
        # Interpret relative paths relative to root, not CWD.
        rel_parts, err = _split_components(os.sep + path)
        if err is not None:
            return None, "error: outside root"
        path_parts = root_parts + rel_parts

    if len(path_parts) < len(root_parts):
        return None, "error: outside root"
    if path_parts[: len(root_parts)] != root_parts:
        return None, "error: outside root"

    return path_parts[len(root_parts):], None


def safe_delete_files(file_paths, root):
    """Delete each file in file_paths, confined to the given root directory.

    The root is opened by walking it component-by-component from the
    filesystem root with O_NOFOLLOW|O_DIRECTORY, so no symlink in any
    component of `root` (including intermediate components) can redirect
    the operation. Deletion of each target is performed relative to that
    root fd using O_NOFOLLOW|O_DIRECTORY opens for every path component,
    followed by os.unlink(name, dir_fd=parent_fd). This eliminates the
    TOCTOU window between the confinement check and the unlink.

    Relative `path` values are interpreted relative to `root`, not the
    process CWD. Absolute paths must lie under `root` lexically. `..`
    components in either `root` or `path` are rejected outright rather
    than collapsed by normpath.

    Only regular files are unlinked; directories are skipped. A symlink
    at the final component is unlinked as a link (its target is not
    touched); the "regular file only" check at the final component is
    best-effort to avoid a stat-then-unlink race.

    Returns a dict mapping each path to one of:
      "deleted", "missing", "not_a_file", "error: outside root",
      or "error: <reason>".

    Limitations (pure-Python scope):
    - No secure erasure: data may persist on disk after unlink.
    - No permission elevation; permission errors are reported, not retried.
    - On platforms lacking O_NOFOLLOW/O_DIRECTORY/dir_fd support (e.g.
      Windows), this function is not available; callers should use a
      platform-specific implementation.
    """
    results = {}

    root_fd, root_err = _open_root_nofollow(root)
    if root_fd is None:
        for path in file_paths:
            results[path] = root_err if root_err else "error: cannot open root"
        return results

    try:
        for path in file_paths:
            try:
                rel_parts, err = _path_relative_to_root(root, path)
                if err is not None:
                    results[path] = err
                    continue
                if not rel_parts:
                    results[path] = "not_a_file"
                    continue
                results[path] = _safe_unlink_at(root_fd, rel_parts)
            except OSError as exc:
                results[path] = f"error: {exc}"
            except ValueError as exc:
                # Defensive: _split_components already rejects NULs, but
                # keep per-path isolation if any ValueError slips through.
                results[path] = f"error: {exc}"
    finally:
        try:
            os.close(root_fd)
        except OSError:
            pass
    return results