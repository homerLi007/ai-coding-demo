import os
import stat


def _validate_path(path):
    """Return (ok, reason) for a candidate path value."""
    if isinstance(path, bool):
        return False, 'invalid path'
    if not isinstance(path, (str, bytes, os.PathLike)):
        return False, 'invalid path'
    try:
        p = os.fspath(path)
    except TypeError:
        return False, 'invalid path'
    if isinstance(p, bytes):
        if not p:
            return False, 'invalid path'
        if b'\x00' in p:
            return False, 'invalid path'
        # Reject bytes paths on Windows: os.fsdecode uses mbcs/utf-8 there
        # and can raise UnicodeDecodeError for arbitrary bytes, making
        # containment checks platform-dependent and fragile.
        if os.name == 'nt':
            return False, 'invalid path'
    else:
        if not p:
            return False, 'invalid path'
        if '\x00' in p:
            return False, 'invalid path'
    return True, None


def _is_within_root(resolved, root_resolved):
    """Return True if resolved path is root itself or strictly beneath it."""
    try:
        common = os.path.commonpath([resolved, root_resolved])
    except ValueError:
        # Different drives on Windows, or mixed abs/rel.
        return False
    return common == root_resolved


def _normalize_pair(path, root):
    """Return (path_str, root_str) both as str, or raise ValueError."""
    p = os.fspath(path)
    r = os.fspath(root)
    # Normalize to str to avoid mixed-type commonpath failures.
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if isinstance(r, bytes):
        r = os.fsdecode(r)
    return p, r


def _split_final_component(p_str):
    """Return (parent_str, name) for a path, rejecting '.' and '..' names.

    Returns (None, None) if the final component is empty, '.', or '..'.
    These are not valid unlink targets: they refer to the parent directory
    itself or its parent, not to a directory entry we can safely remove.
    """
    parent = os.path.dirname(p_str) or os.curdir
    name = os.path.basename(p_str)
    if not name or name in ('.', '..'):
        return None, None
    return parent, name


def _check_containment(path, root):
    """Return None if path is within root, else an error string.

    The boundary test is performed on the *parent* directory (resolved)
    and on the final component *without* following it. This matches the
    actual unlink semantics: we remove the directory entry itself, so a
    symlink inside ``root`` pointing outside ``root`` is still a safe
    deletion (we unlink the link, not the target).

    The final component must be a real entry name; '.' and '..' are
    rejected explicitly because they do not denote a removable entry and
    would make the containment check validate a different path than the
    one actually unlinked.
    """
    if root is None:
        return None
    try:
        p_str, r_str = _normalize_pair(path, root)
    except (TypeError, ValueError) as exc:
        return f'error: {exc}'
    try:
        root_resolved = os.path.realpath(r_str)
        parent, name = _split_final_component(p_str)
        if parent is None:
            return 'error: invalid path'
        parent_resolved = os.path.realpath(parent)
        if not _is_within_root(parent_resolved, root_resolved):
            return 'error: path outside root'
        # Resolve the final component *without* following it, so that a
        # symlink inside root pointing outside root is still accepted
        # (we unlink the link itself). We only need to ensure the entry
        # itself lives within root.
        entry_resolved = os.path.join(parent_resolved, name)
        if not _is_within_root(entry_resolved, root_resolved):
            return 'error: path outside root'
    except (OSError, ValueError, TypeError) as exc:
        return f'error: {exc}'
    return None


def _delete_one(path, root):
    """Attempt to delete a single path. Returns a status string."""
    ok, reason = _validate_path(path)
    if not ok:
        return f'error: {reason}'

    containment_err = _check_containment(path, root)
    if containment_err is not None:
        return containment_err

    try:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return 'not_found'
        except (ValueError, TypeError) as exc:
            return f'error: {exc}'

        if stat.S_ISDIR(st.st_mode):
            return 'is_directory'

        # POSIX: use dir_fd + O_NOFOLLOW to avoid following a symlinked
        # parent directory component between lstat and unlink.
        if os.name == 'posix' and hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_NOFOLLOW'):
            p = os.fspath(path)
            parent = os.path.dirname(p) or (b'.' if isinstance(p, bytes) else '.')
            name = os.path.basename(p)
            if not name or name in (b'.', b'..', '.', '..'):
                return 'error: invalid path'
            try:
                dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError as exc:
                return f'error: {exc.strerror or exc}'
            try:
                # Re-validate that the opened directory is the one we
                # validated in _check_containment (device + inode match).
                # This closes the window where the parent path could be
                # swapped between the containment check and os.open.
                if root is not None:
                    try:
                        p_str, r_str = _normalize_pair(path, root)
                        root_resolved = os.path.realpath(r_str)
                        parent_str, name_str = _split_final_component(p_str)
                        if parent_str is None:
                            return 'error: invalid path'
                        parent_resolved = os.path.realpath(parent_str)
                        expected = os.stat(parent_resolved)
                        opened = os.fstat(dfd)
                        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                            return 'error: path outside root'
                        if not _is_within_root(parent_resolved, root_resolved):
                            return 'error: path outside root'
                        # Re-validate the final component against root using
                        # the *opened* directory's resolved identity, so the
                        # entry check is anchored to the same directory we
                        # will unlink from (not a possibly-swapped realpath).
                        entry_resolved = os.path.join(parent_resolved, name_str)
                        if not _is_within_root(entry_resolved, root_resolved):
                            return 'error: path outside root'
                    except (OSError, ValueError, TypeError) as exc:
                        return f'error: {exc}'
                try:
                    st2 = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                except FileNotFoundError:
                    return 'not_found'
                if stat.S_ISDIR(st2.st_mode):
                    return 'is_directory'
                os.unlink(name, dir_fd=dfd)
                return 'deleted'
            finally:
                os.close(dfd)

        # Fallback (e.g. Windows): re-validate immediately before unlink and
        # refuse to operate on reparse points / symlinks to avoid following
        # a swapped-in link. This is best-effort; Windows lacks dir_fd.
        try:
            st3 = os.lstat(path)
        except FileNotFoundError:
            return 'not_found'
        if stat.S_ISDIR(st3.st_mode):
            return 'is_directory'
        # On Windows, reject reparse points that are not symlinks (e.g.
        # junctions) to avoid traversing them. Fail closed if the attribute
        # is unavailable.
        if os.name == 'nt':
            attrs = getattr(st3, 'st_file_attributes', None)
            if attrs is None:
                return 'error: reparse point'
            reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
            if attrs & reparse_flag and not stat.S_ISLNK(st3.st_mode):
                return 'error: reparse point'
        # Re-run containment immediately before unlink to narrow the
        # parent-swap window on platforms without dir_fd.
        if root is not None:
            recheck = _check_containment(path, root)
            if recheck is not None:
                return recheck
        os.unlink(path)
        return 'deleted'
    except OSError as exc:
        return f'error: {exc.strerror or exc}'
    except (ValueError, TypeError) as exc:
        return f'error: {exc}'


def safe_delete_files(file_paths, root=None):
    """Delete each file in file_paths, skipping directories and missing paths.

    Returns a dict mapping each input path to one of:
      'deleted', 'not_found', 'is_directory', 'error: <reason>'

    If ``root`` is provided, paths whose parent directory or entry location
    falls outside ``root`` are rejected with 'error: path outside root'.
    The final component is *not* followed for the boundary test, matching
    the unlink semantics: a symlink inside ``root`` pointing outside
    ``root`` is still safely deleted (the link itself is removed).
    Both ``path`` and ``root`` are normalized to ``str`` for the boundary
    check, so mixed ``str``/``bytes`` inputs are handled consistently.

    Paths whose final component is '.' or '..' are rejected with
    'error: invalid path': they do not denote a removable directory entry
    and would otherwise make the containment check validate a different
    path than the one actually unlinked.

    Platform guarantees:
    - POSIX with O_DIRECTORY/O_NOFOLLOW: the parent directory is opened with
      O_NOFOLLOW and the unlink is performed via dir_fd, preventing a
      symlinked parent from redirecting the operation outside ``root``.
      After ``os.open``, the opened directory's (st_dev, st_ino) is compared
      against the validated parent to detect a swap between the containment
      check and the open, and the final component is re-validated against
      ``root`` using the opened directory's resolved identity.
    - Windows / other fallback: the path is re-lstat'd immediately before
      unlink; reparse points that are not symlinks are rejected (failing
      closed if ``st_file_attributes`` is unavailable), and containment is
      re-checked immediately before ``os.unlink``. This is best-effort and
      not race-free.

    Limitations (pure-Python scope):
    - Not fully atomic: a file may be replaced or removed between the
      lstat/stat and the unlink call (TOCTOU). On POSIX we mitigate the
      parent-directory race by opening the parent with O_DIRECTORY|O_NOFOLLOW
      and unlinking via dir_fd, but the final component can still be swapped.
    - Symlinks are unlinked (the link itself), not followed; this is
      intentional but callers should be aware.
    - No secure/overwrite deletion; filesystem may retain data.
    - Permission errors and other OSErrors are reported, not raised.
    """
    results = {}
    for path in file_paths:
        try:
            results[path] = _delete_one(path, root)
        except Exception as exc:  # defensive: never abort the batch
            results[path] = f'error: {exc}'
    return results