import os
import stat


def _open_regular_nofollow(path):
    """Open path without following symlinks; return (fd, None) if it is a regular file.

    O_NOFOLLOW ensures we never open a symlink target. The S_ISREG check on the
    resulting fd is the actual defense against FIFOs/devices/sockets; O_NONBLOCK
    is intentionally omitted since it has no effect on regular files and would
    only mislead maintainers into thinking it mitigates blocking opens.

    Returns (fd, None) on success, or (None, status) where status is one of
    'missing', 'not_file', or 'error: ...'.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, 'missing'
    except OSError as exc:
        # ELOOP (symlink), EISDIR, ENXIO, etc. -> not a regular file we can open.
        if exc.errno in (getattr(os, 'ELOOP', None), getattr(os, 'EISDIR', None)):
            return None, 'not_file'
        return None, f'error: {exc}'
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None, 'not_file'
    except OSError as exc:
        os.close(fd)
        return None, f'error: {exc}'
    return fd, None


def _split_path(path):
    """Split a path into (parent_dir, basename) for dir_fd-relative operations.

    Returns (parent, name) or (None, None) if the path has no usable basename
    (empty, '.', '..', or trailing-slash-only forms).
    """
    # Normalize to strip trailing slashes and redundant separators. normpath
    # also collapses 'a/./b' and 'a//b'. We deliberately do NOT resolve
    # symlinks here (that would defeat O_NOFOLLOW semantics).
    norm = os.path.normpath(path)
    name = os.path.basename(norm)
    if name in ('', '.', '..'):
        return None, None
    parent = os.path.dirname(norm)
    if parent == '':
        parent = '.'
    return parent, name


def _open_parent_dir(parent):
    """Open the parent directory without following a symlink at the final component.

    Intermediate components of `parent` are still resolved by the kernel; the
    subsequent inode identity check on the target file catches substitution
    via symlinked intermediates. Only the final component is O_NOFOLLOW-guarded.
    """
    return os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def safe_delete_files(file_paths, *, dry_run=False):
    """Delete the given files, skipping anything that is not a regular file.

    Returns a dict mapping each path to one of:
      'deleted'   - file was removed (or would be, if dry_run)
      'missing'   - path does not exist
      'not_file'  - path exists but is not a regular file (dir, symlink, etc.)
      'error: ...' - deletion failed with the given message

    Limitations (pure-Python scope):
      - Symlinks are never followed or deleted; they are reported as 'not_file'.
      - No cross-process locking; concurrent deleters may race.
      - Permission errors are surfaced per-file rather than raised.
      - Intermediate directory components of the parent path are resolved by
        the kernel (only the final component is O_NOFOLLOW-guarded); the
        inode identity check on the target file catches substitution.
    """
    if isinstance(file_paths, (str, bytes)) or file_paths is None:
        raise TypeError('file_paths must be an iterable of paths, not a string/None')

    results = {}
    for path in file_paths:
        # No lexists/islink pre-checks: they are racy and add no security.
        # The authoritative checks are the O_NOFOLLOW open + inode re-verify.
        parent, name = _split_path(path)
        if name is None:
            # Path has no usable basename (e.g. '/', '.', 'foo/').
            # Distinguish missing vs not_file without following symlinks.
            try:
                st = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                results[path] = 'missing'
                continue
            except OSError as exc:
                results[path] = f'error: {exc}'
                continue
            results[path] = 'not_file' if not stat.S_ISREG(st.st_mode) else 'not_file'
            continue

        fd, status = _open_regular_nofollow(path)
        if fd is None:
            results[path] = status
            continue

        try:
            try:
                st_fd = os.fstat(fd)

                if dry_run:
                    results[path] = 'deleted'
                    continue

                # Perform the unlink relative to the parent directory fd so the
                # final component is resolved against the same directory we
                # verified, closing the TOCTOU window between stat and remove.
                try:
                    dir_fd = _open_parent_dir(parent)
                except FileNotFoundError:
                    results[path] = 'missing'
                    continue
                except OSError as exc:
                    results[path] = f'error: {exc}'
                    continue
                try:
                    # Re-verify identity via the directory fd before unlinking.
                    try:
                        st_dir = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        results[path] = 'missing'
                        continue
                    except OSError as exc:
                        results[path] = f'error: {exc}'
                        continue
                    if (st_fd.st_dev, st_fd.st_ino) != (st_dir.st_dev, st_dir.st_ino):
                        results[path] = 'not_file'
                        continue
                    if stat.S_ISLNK(st_dir.st_mode) or not stat.S_ISREG(st_dir.st_mode):
                        results[path] = 'not_file'
                        continue
                    try:
                        os.unlink(name, dir_fd=dir_fd)
                        results[path] = 'deleted'
                    except FileNotFoundError:
                        results[path] = 'missing'
                    except OSError as exc:
                        results[path] = f'error: {exc}'
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                results[path] = f'error: {exc}'
        finally:
            os.close(fd)
    return results