import os
import stat
import sys


_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_HAS_DIR_FD = os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd


class _SymlinkRefused(Exception):
    pass


class _NotRegular(Exception):
    pass


class _OutsideRoot(Exception):
    pass


class _InvalidName(Exception):
    pass


class _Unsupported(Exception):
    pass


# Sentinel returned by _walk_to_parent when the parent is the anchor itself.
_ANCHOR_IS_PARENT = object()


# Windows reserved device names (case-insensitive, with or without extension).
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _open_dir_nofollow(path):
    """Open a directory without following a final symlink."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
    return os.open(path, flags)


def _open_dir_at(dir_fd, name):
    """Open `name` relative to `dir_fd` as a directory, refusing symlinks."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
    return os.open(name, flags, dir_fd=dir_fd)


def _unlink_at(dir_fd, name):
    """Unlink `name` relative to `dir_fd`, refusing symlinks and non-regular
    files.

    The stat and unlink are two syscalls, so a concurrent attacker with
    write access to the parent directory could in principle swap the entry
    between them. We narrow the window as much as the POSIX API allows:

    - `os.unlink` never follows symlinks, so a symlink swap results in the
      symlink entry being removed, not its target. That is the safe
      outcome and matches the module's policy.
    - We re-`lstat` immediately before `unlink` and refuse if the entry is
      no longer a regular file. A swap to a FIFO/device/socket between
      the check and the unlink is a residual race that cannot be closed
      with pure POSIX unlink semantics (there is no unlink-if-regular
      primitive); we minimize it by keeping the check adjacent to the
      unlink and by anchoring both to the same directory fd.
    """
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        raise _SymlinkRefused()
    if not stat.S_ISREG(st.st_mode):
        raise _NotRegular()
    # Re-check immediately before unlink to narrow the TOCTOU window.
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        raise _SymlinkRefused()
    if not stat.S_ISREG(st.st_mode):
        raise _NotRegular()
    os.unlink(name, dir_fd=dir_fd)


def _walk_to_parent(root_fd, rel_components):
    """Walk `rel_components` from `root_fd`, opening each as a directory
    with O_NOFOLLOW|O_DIRECTORY. Returns an fd for the parent directory
    (the last component is NOT opened; it is the entry to unlink).

    Every intermediate component is opened with O_NOFOLLOW so a symlink
    swap of any component is refused. The returned fd is anchored to the
    actual directory inode, so subsequent unlink(name, dir_fd=...) cannot
    be redirected by further path manipulation.

    Every intermediate component is also validated with `_validate_name`
    so that NUL bytes, control characters, path separators, and (on
    Windows) reserved device names are rejected before any syscall is
    attempted. This prevents `ValueError` from `os.open` on embedded NUL
    from aborting the whole batch, and prevents Windows reserved-name
    aliasing of devices.

    Returns the sentinel `_ANCHOR_IS_PARENT` when the parent is `root_fd`
    itself (i.e. `rel_components` has exactly one element). Raises
    `_InvalidName` if `rel_components` is empty (no entry to unlink).
    """
    if not rel_components:
        raise _InvalidName()
    cur = root_fd
    opened = []
    try:
        for comp in rel_components[:-1]:
            if comp in ("", ".", ".."):
                raise _OutsideRoot()
            _validate_name(comp)
            nfd = _open_dir_at(cur, comp)
            opened.append(nfd)
            cur = nfd
        if not opened:
            return _ANCHOR_IS_PARENT
        # Detach the last opened fd so caller owns it; close the rest.
        result = opened[-1]
        opened = opened[:-1]
        return result
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


def _validate_name(name):
    """Reject names that are not a single, well-formed path component.

    Rejects empty names, `.`/`..`, path separators, NUL bytes, and
    control characters (including newlines) that have no business in a
    filename we are about to unlink. On Windows, also rejects reserved
    device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) and names with
    trailing dots or spaces, which the OS treats specially and which can
    alias other files.
    """
    if not isinstance(name, str) or name == "":
        raise _InvalidName()
    if name in (".", ".."):
        raise _InvalidName()
    if os.sep in name or (os.altsep and os.altsep in name):
        raise _InvalidName()
    if "\x00" in name:
        raise _InvalidName()
    for ch in name:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise _InvalidName()
    if sys.platform == "win32":
        if name.endswith(".") or name.endswith(" "):
            raise _InvalidName()
        stem = name.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise _InvalidName()


def _lexical_parts_under_root(abs_path, root):
    """Return the lexical components of `abs_path` relative to `root`.

    Both arguments must be absolute, normalized paths (no `..`, no `.`).
    Containment is checked lexically via commonpath so that the components
    we validate are exactly the components we will walk with O_NOFOLLOW.
    Symlink refusal is enforced solely by O_NOFOLLOW during the walk.
    """
    try:
        if os.path.commonpath([root, abs_path]) != root:
            raise _OutsideRoot()
    except ValueError:
        raise _OutsideRoot()
    rel = os.path.relpath(abs_path, root)
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise _OutsideRoot()
    parts = rel.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        raise _OutsideRoot()
    return parts


def _open_root_anchor(root):
    """Open `root` as a directory fd, refusing symlinks at *every*
    component of the path, not just the final one.

    `os.open(root, O_NOFOLLOW|O_DIRECTORY)` only refuses a symlink at the
    final component; intermediate symlinked ancestors are silently
    followed. If `allowed_root` (or any ancestor) is attacker-influenced,
    that would let the anchor fd point at a directory outside the
    intended root, defeating the containment guarantee.

    To close this, we walk `root` from the filesystem root one component
    at a time, opening each with O_NOFOLLOW|O_DIRECTORY. Any symlink in
    the chain is refused. The returned fd is anchored to the actual
    directory inode, so subsequent unlinks cannot be redirected.
    """
    if not os.path.isabs(root):
        raise _OutsideRoot()
    parts = [p for p in root.split(os.sep) if p]
    if not parts:
        # root is the filesystem root itself.
        return _open_dir_nofollow(os.sep)
    cur = _open_dir_nofollow(os.sep)
    opened = [cur]
    try:
        for comp in parts:
            if comp in ("", ".", ".."):
                raise _OutsideRoot()
            _validate_name(comp)
            nfd = _open_dir_at(cur, comp)
            opened.append(nfd)
            cur = nfd
        result = opened[-1]
        opened = opened[:-1]
        return result
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


def safe_delete_files(file_paths, *, allowed_root=None):
    """Delete the given files, refusing anything that looks unsafe.

    Each path is deleted only if it is a regular file (not a directory,
    device, FIFO, or symlink) and, when allowed_root is provided, resolves
    to a location inside that root.

    Symlinks are always refused: the function deletes the *named entry*,
    never a symlink target. This avoids the confused-deputy / data-loss
    hazard where a caller authorizes deletion of `path` but the code
    deletes a different file reached via symlink resolution.

    Returns a dict mapping each input path to either "deleted" or an
    error string describing why it was skipped.

    On POSIX, deletion is performed by opening the allowed_root directory
    (or the filesystem root when no root is given) and walking each path
    component with O_NOFOLLOW|O_DIRECTORY, then unlinking the final entry
    via os.unlink(name, dir_fd=...). Because every component is opened
    with O_NOFOLLOW and the unlink is anchored to the resulting fd, an
    attacker cannot redirect the operation by swapping an intermediate
    path component for a symlink (TOCTOU on intermediate components is
    closed). The anchor for `allowed_root` itself is opened by walking
    from the filesystem root with O_NOFOLLOW at every step, so a symlink
    anywhere in the root's ancestry is refused rather than followed.

    When `allowed_root` is provided on a platform without dir_fd support,
    the containment guarantee cannot be enforced and the function refuses
    to operate rather than silently degrading.

    Known limitations (pure Python, no kernel primitives):
    - We cannot guarantee secure erasure of file contents; deletion only
      unlinks the directory entry.
    - Permission checks are best-effort; the OS enforces the real policy.
    - The final-entry stat/unlink pair is not a single atomic operation;
      a concurrent attacker with write access to the parent directory can
      still swap a regular file for a non-regular entry in the residual
      window. POSIX provides no unlink-if-regular primitive, so this is
      inherent to the API surface.
    """
    results = {}
    root = os.path.abspath(allowed_root) if allowed_root is not None else None

    for path in file_paths:
        try:
            if _HAS_DIR_FD:
                results[path] = _delete_posix(path, root)
            else:
                results[path] = _delete_fallback(path, root)
        except FileNotFoundError:
            results[path] = "not found"
        except _SymlinkRefused:
            results[path] = "refusing to delete symlink"
        except _NotRegular:
            results[path] = "not a regular file"
        except _OutsideRoot:
            results[path] = "outside allowed_root"
        except _InvalidName:
            results[path] = "invalid filename"
        except _Unsupported:
            results[path] = "unsupported: no dir_fd"
        except ValueError:
            # Defensive: embedded NUL or similar rejected by the OS layer.
            results[path] = "invalid filename"
        except OSError as exc:
            results[path] = f"error: {exc.strerror or exc}"

    return results


def _delete_posix(path, root):
    """POSIX deletion anchored to a directory fd, walking components with
    O_NOFOLLOW so intermediate symlink swaps cannot redirect the unlink.

    The final entry is always unlinked as-is (never followed), so a
    symlink at `path` is refused rather than having its target deleted.

    When `root` is None, we walk the *lexical* components of the absolute
    path from the filesystem root with O_NOFOLLOW at every step. This
    refuses intermediate symlinks rather than silently resolving them
    (which would be a confused-deputy hazard: a caller authorizing
    deletion of `/tmp/userdir/file` where `/tmp/userdir` is a symlink to
    `/etc` must not cause `/etc/file` to be deleted).
    """
    abs_path = os.path.abspath(path)

    if root is not None:
        # Lexical containment check: the components we validate are the
        # exact components we walk. Symlink refusal is enforced by
        # O_NOFOLLOW at each step, not by realpath resolution.
        parts = _lexical_parts_under_root(abs_path, root)
        name = parts[-1]
        _validate_name(name)

        # Open the anchor by walking from the filesystem root with
        # O_NOFOLLOW at every component, so a symlinked ancestor of
        # `allowed_root` is refused rather than followed.
        anchor_fd = _open_root_anchor(root)
        try:
            parent_fd = _walk_to_parent(anchor_fd, parts)
            if parent_fd is _ANCHOR_IS_PARENT:
                _unlink_at(anchor_fd, name)
            else:
                try:
                    _unlink_at(parent_fd, name)
                finally:
                    os.close(parent_fd)
        finally:
            os.close(anchor_fd)
        return "deleted"

    # No allowed_root: walk the lexical components of the absolute path
    # from the filesystem root with O_NOFOLLOW at every step. Intermediate
    # symlinks are refused (not resolved), so the deletion cannot be
    # redirected to a different location than the caller named.
    name = os.path.basename(abs_path)
    _validate_name(name)
    parts = [p for p in abs_path.split(os.sep) if p]
    if not parts:
        raise _InvalidName()

    anchor_fd = _open_dir_nofollow(os.sep)
    try:
        parent_fd = _walk_to_parent(anchor_fd, parts)
        if parent_fd is _ANCHOR_IS_PARENT:
            _unlink_at(anchor_fd, name)
        else:
            try:
                _unlink_at(parent_fd, name)
            finally:
                os.close(parent_fd)
    finally:
        os.close(anchor_fd)
    return "deleted"


def _delete_fallback(path, root):
    """Best-effort path-based deletion for platforms without dir_fd.

    When `allowed_root` is set, containment cannot be enforced without
    dir_fd support, so we refuse rather than silently degrade. When no
    root is given, we perform a best-effort lstat-then-unlink on the
    caller-supplied path, refusing symlinks and non-regular files.

    Validation is performed *before* any filesystem call so that an
    embedded NUL or other malformed name raises `_InvalidName` (mapped
    to a per-path error) rather than `ValueError` aborting the batch.
    Status is reported by raising the same exceptions as the POSIX path
    so the caller's exception mapping is uniform.

    The TOCTOU window between check and unlink is inherent to the OS API
    surface on these platforms; we narrow it by re-checking immediately
    before removal.
    """
    if root is not None:
        raise _Unsupported()

    name = os.path.basename(path)
    _validate_name(name)

    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise _SymlinkRefused()
    if not stat.S_ISREG(st.st_mode):
        raise _NotRegular()

    # Re-check the entry immediately before removal.
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise _SymlinkRefused()
    if not stat.S_ISREG(st.st_mode):
        raise _NotRegular()

    os.remove(path)
    return "deleted"