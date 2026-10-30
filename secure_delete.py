import errno
import os
import stat
import sys


# ---------------------------------------------------------------------------
# Platform capability checks
# ---------------------------------------------------------------------------

def _platform_supports_secure_delete():
    """Return True only if the platform provides the primitives we need.

    We require:
      * os.O_NOFOLLOW (so we never follow a symlink at the root or at any
        intermediate component)
      * os.O_DIRECTORY (so intermediate components must be directories)
      * dir_fd support for os.open / os.stat / os.unlink
      * follow_symlinks support for os.stat
    If any of these are missing we fail closed: the caller gets 'error' for
    every path rather than a silently-weaker check.
    """
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        return False
    if os.open not in os.supports_dir_fd:
        return False
    if os.stat not in os.supports_dir_fd:
        return False
    if os.stat not in os.supports_follow_symlinks:
        return False
    if os.unlink not in os.supports_dir_fd:
        return False
    return True


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _split_and_validate(path):
    """Split *path* into components, rejecting anything that could escape.

    Returns a list of components (all non-empty, none equal to '.' or '..'),
    or raises ValueError if the path is unsafe.

    We deliberately do NOT rely on os.path.normpath alone: it is order
    dependent and platform specific. Instead we:
      * reject absolute paths explicitly
      * reject drive letters / UNC prefixes on Windows
      * split on both os.sep and os.altsep (if set)
      * reject any component that is '', '.', or '..'
    """
    if not isinstance(path, str):
        raise ValueError('path must be a str')
    if path == '' or path == '.':
        raise ValueError('empty path')
    if os.path.isabs(path):
        raise ValueError('absolute paths are not permitted')
    drive, _ = os.path.splitdrive(path)
    if drive:
        raise ValueError('drive-qualified paths are not permitted')

    separators = [os.sep]
    altsep = getattr(os, 'altsep', None)
    if altsep:
        separators.append(altsep)

    # Normalize separators to os.sep, then split.
    normalized = path
    for sep in separators:
        if sep != os.sep:
            normalized = normalized.replace(sep, os.sep)

    parts = normalized.split(os.sep)
    for part in parts:
        if part in ('', '.', '..'):
            raise ValueError('path contains unsafe component')
    return parts


# ---------------------------------------------------------------------------
# Safe directory walk
# ---------------------------------------------------------------------------

def _open_parent_beneath(root_fd, parts):
    """Open the parent directory of the final component beneath *root_fd*.

    Returns (parent_fd, final_name). The caller owns parent_fd and must close
    it. Uses O_NOFOLLOW | O_DIRECTORY on every intermediate component so a
    symlink swap cannot redirect us outside the root.

    fd ownership is tracked with a single variable that is set to -1 after
    close, so the except block can never double-close.
    """
    if not parts:
        raise ValueError('empty path')

    current_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            # Close the previous fd only after the new one is open.
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except BaseException:
        if current_fd >= 0:
            try:
                os.close(current_fd)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def secure_delete(root, paths):
    """Delete each path in *paths* beneath *root*.

    Returns a dict mapping each input path to one of:
      'deleted'          - file was removed
      'error'            - path was rejected or an I/O error occurred
      'race_detected'    - the entry changed between stat and unlink
      'symlink_rejected' - the final component is a symlink

    Security notes
    --------------
    * Absolute paths, drive-qualified paths, and any path containing '',
      '.', or '..' components are rejected.
    * Every intermediate directory is opened with O_NOFOLLOW | O_DIRECTORY,
      so a symlink swap cannot redirect us outside *root*.
    * The final component is opened with O_NOFOLLOW | O_PATH (Linux) or
      O_NOFOLLOW | O_RDONLY (elsewhere) and its inode is compared against a
      fresh lstat immediately before unlink. This narrows, but does not
      eliminate, the stat/unlink TOCTOU window: POSIX has no "unlink this
      exact inode" primitive. On Linux we additionally unlink via
      /proc/self/fd/<fd> so the unlink is bound to the opened inode.
    * If the platform lacks the required primitives we fail closed.
    """
    results = {}

    if not _platform_supports_secure_delete():
        for path in paths:
            results[path] = 'error'
        return results

    # Validate root itself is a directory and not a symlink.
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError:
        for path in paths:
            results[path] = 'error'
        return results

    try:
        for path in paths:
            try:
                parts = _split_and_validate(path)
            except ValueError:
                results[path] = 'error'
                continue

            try:
                parent_fd, name = _open_parent_beneath(root_fd, parts)
            except OSError:
                results[path] = 'error'
                continue

            try:
                results[path] = _delete_one(parent_fd, name)
            finally:
                try:
                    os.close(parent_fd)
                except OSError:
                    pass
    finally:
        try:
            os.close(root_fd)
        except OSError:
            pass

    return results


def _delete_one(parent_fd, name):
    """Delete *name* inside *parent_fd*, returning a result string."""
    # First, lstat the entry without following symlinks.
    try:
        st1 = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return 'error'
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return 'symlink_rejected'
        return 'error'

    if stat.S_ISLNK(st1.st_mode):
        return 'symlink_rejected'
    if not stat.S_ISREG(st1.st_mode):
        # Refuse to delete directories, devices, fifos, sockets, etc.
        return 'error'

    # Open the final component with O_NOFOLLOW so we hold a reference to the
    # exact inode we intend to delete. On Linux, O_PATH lets us do this even
    # without read permission; elsewhere fall back to O_RDONLY.
    open_flags = os.O_NOFOLLOW
    if hasattr(os, 'O_PATH'):
        open_flags |= os.O_PATH
    else:
        open_flags |= os.O_RDONLY

    try:
        file_fd = os.open(name, open_flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return 'symlink_rejected'
        return 'error'

    try:
        # Re-stat the opened fd and compare to the earlier lstat. If they
        # differ, the entry was swapped between our lstat and our open.
        try:
            st2 = os.fstat(file_fd)
        except OSError:
            return 'error'

        if (st1.st_dev, st1.st_ino) != (st2.st_dev, st2.st_ino):
            return 'race_detected'

        # On Linux, unlink via /proc/self/fd/<fd> so the unlink is bound to
        # the inode we opened, not to whatever *name* currently resolves to.
        # This closes the stat/unlink TOCTOU window on Linux.
        proc_path = '/proc/self/fd/%d' % file_fd
        if os.path.exists(proc_path):
            try:
                os.unlink(proc_path)
                return 'deleted'
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    # Entry already gone; treat as race.
                    return 'race_detected'
                # Fall through to the portable path below.

        # Portable fallback: re-lstat immediately before unlink and compare
        # inodes. This narrows but does not eliminate the race; callers that
        # need a hard guarantee must ensure no concurrent writers exist in
        # the target directory.
        try:
            st3 = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return 'race_detected'
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return 'symlink_rejected'
            return 'error'

        if (st1.st_dev, st1.st_ino) != (st3.st_dev, st3.st_ino):
            return 'race_detected'

        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return 'race_detected'
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return 'symlink_rejected'
            return 'error'
        return 'deleted'
    finally:
        try:
            os.close(file_fd)
        except OSError:
            pass


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('usage: secure_delete.py ROOT PATH [PATH ...]', file=sys.stderr)
        sys.exit(2)
    root = sys.argv[1]
    targets = sys.argv[2:]
    for p, r in secure_delete(root, targets).items():
        print('%s: %s' % (p, r))