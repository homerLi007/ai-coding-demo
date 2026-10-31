import os
import stat
import errno


# NOTE: TOCTOU against an attacker with write access to the parent directory is
# explicitly out of scope per the task. The dir_fd machinery below pins the
# parent directory and unlinks relative to the opened fd, so the entry removed
# is the one validated against that fd. Hard links, Windows ADS, 8.3 short
# names, secure erasure, and permission enforcement are also out of scope.


def _strip_trailing_sep(path):
    """Strip exactly one trailing separator, preserving drive/root semantics.

    On Windows, 'C:\\' must not become 'C:' (which means CWD on C:).
    We therefore only strip when the result is still a non-empty path that
    is not a bare drive root. UNC roots ('\\\\server\\share\\') are preserved.
    """
    if not path:
        return path
    seps = os.sep + (os.altsep or "")
    if path[-1] not in seps:
        return path
    # Do not strip if the path is a root (e.g. '/', 'C:\\', '\\\\server\\share\\').
    drive, tail = os.path.splitdrive(path)
    # Normalize tail by collapsing separators to a single os.sep for the
    # root check, so double-trailing-sep UNC roots are handled correctly.
    tail_norm = tail
    for s in seps:
        tail_norm = tail_norm.replace(s, os.sep)
    if tail_norm in (os.sep, ""):
        return path
    # Strip exactly one trailing separator.
    return path[:-1]


def _containment_ok(parent_real, root_real):
    """Return True if parent_real is contained within root_real."""
    if root_real is None:
        return True
    root_cmp = os.path.normcase(os.path.abspath(root_real))
    parent_cmp = os.path.normcase(os.path.abspath(parent_real))
    try:
        return os.path.commonpath([root_cmp, parent_cmp]) == root_cmp
    except ValueError:
        return False


def _resolve_candidate(path, root_real):
    """Return the resolved candidate path for `path`, or raise ValueError.

    The parent directory is resolved with realpath so that `..` and symlinked
    intermediate components are handled physically, not lexically. The
    basename is then appended to the resolved parent. If `root_real` is set,
    the candidate must be contained within it.

    The basename is validated to be a plain name (not '.', '..', or empty)
    so that the caller's intent is preserved and the final component is not
    silently rewritten by normpath.
    """
    if not path:
        raise ValueError("empty path")
    stripped = _strip_trailing_sep(path)
    base = os.path.basename(stripped)
    if base in ("", ".", ".."):
        raise ValueError("invalid final component")

    parent = os.path.dirname(stripped)
    if not parent:
        parent = "."
    parent_real = os.path.realpath(parent)
    candidate = os.path.join(parent_real, base)

    if root_real is not None:
        root_cmp = os.path.normcase(os.path.abspath(root_real))
        cand_cmp = os.path.normcase(os.path.abspath(candidate))
        try:
            if os.path.commonpath([root_cmp, cand_cmp]) != root_cmp:
                raise ValueError("outside allowed_root")
        except ValueError:
            raise ValueError("outside allowed_root")
    return candidate


def _lstat_nofollow(name, dir_fd):
    """lstat `name` relative to `dir_fd`, never following symlinks.

    Prefer os.lstat with dir_fd (Python 3.10+); otherwise use os.stat with
    follow_symlinks=False. As a defensive cross-check, if the result does
    not report a symlink, verify via os.readlink that the entry is not a
    symlink. Any unexpected error from readlink is treated as a refusal
    (fail closed) rather than silently passing.

    If readlink is unsupported on this platform, we refuse unless the
    platform is known to honor follow_symlinks=False (checked via
    os.supports_follow_symlinks). This prevents a symlink from slipping
    through on platforms where follow_symlinks=False is silently ignored.
    """
    st = None
    try:
        st = os.lstat(name, dir_fd=dir_fd)
    except TypeError:
        st = None
    if st is None:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISLNK(st.st_mode):
        # Cross-check: if readlink succeeds, the entry is a symlink even if
        # the stat call did not report it (defensive against platforms that
        # ignore follow_symlinks=False). Fail closed on unexpected errors.
        try:
            os.readlink(name, dir_fd=dir_fd)
        except OSError as exc:
            # EINVAL/ENOSYS mean "not a symlink" / "unsupported" -> OK.
            if exc.errno in (errno.EINVAL, errno.ENOSYS):
                pass
            else:
                raise OSError("refused: readlink check failed: %s" % exc)
        except (TypeError, NotImplementedError, ValueError):
            # Platform does not support the cross-check. Only accept the
            # stat result if the platform is known to honor
            # follow_symlinks=False; otherwise fail closed.
            if os.stat not in os.supports_follow_symlinks:
                raise OSError(
                    "refused: cannot verify non-symlink on this platform"
                )
        else:
            raise OSError("refused: symlink")
    return st


def _open_parent_dir(parent):
    """Open `parent` as a directory fd, refusing symlinks on the final comp."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    return os.open(parent, flags)


def _opened_dir_real(dir_fd, parent):
    """Best-effort real path of the directory referenced by `dir_fd`.

    On Linux, /proc/self/fd gives the true path of the opened fd. On other
    POSIX platforms we fall back to realpath(parent); the dir_fd itself is
    still used for the unlink, so the entry removed is the one validated
    against the fd even if the path string is stale.
    """
    try:
        return os.readlink("/proc/self/fd/%d" % dir_fd)
    except OSError:
        return os.path.realpath(parent)


def _unlink_nofollow(candidate, root_real):
    """Unlink `candidate` after re-validating it via a directory fd.

    Opens the parent directory with O_DIRECTORY|O_NOFOLLOW and unlinks the
    basename relative to that fd, so the entry removed is the one validated
    against the opened fd. Containment against `root_real` is re-verified
    against the opened directory's real path. The inode is re-checked
    immediately before unlink to narrow the race window. On platforms
    lacking dir_fd support, the fallback re-verifies containment and
    re-lstats immediately before unlink.
    """
    parent = os.path.dirname(candidate) or "."
    name = os.path.basename(candidate)

    if hasattr(os, "O_DIRECTORY") and os.unlink in os.supports_dir_fd:
        dir_fd = _open_parent_dir(parent)
        try:
            # Re-verify containment against the directory we actually opened.
            opened_real = _opened_dir_real(dir_fd, parent)
            if not _containment_ok(opened_real, root_real):
                raise OSError("refused: outside allowed_root")

            st = _lstat_nofollow(name, dir_fd)
            if stat.S_ISLNK(st.st_mode):
                raise OSError("refused: symlink")
            if not stat.S_ISREG(st.st_mode):
                raise OSError("refused: not a regular file")

            # Re-check the inode immediately before unlink to narrow the
            # window in which the entry could be swapped for a different
            # regular file.
            st2 = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISLNK(st2.st_mode):
                raise OSError("refused: symlink")
            if not stat.S_ISREG(st2.st_mode):
                raise OSError("refused: not a regular file")
            if (st.st_dev, st.st_ino) != (st2.st_dev, st2.st_ino):
                raise OSError("refused: entry changed")

            os.unlink(name, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
    else:
        # No dir_fd support (e.g. Windows). Re-verify containment against the
        # real parent path, then re-lstat immediately before unlink and
        # refuse if the entry is no longer a regular file.
        parent_real = os.path.realpath(parent)
        if not _containment_ok(parent_real, root_real):
            raise OSError("refused: outside allowed_root")

        st = os.lstat(candidate)
        if stat.S_ISLNK(st.st_mode):
            raise OSError("refused: symlink")
        if not stat.S_ISREG(st.st_mode):
            raise OSError("refused: not a regular file")

        # Re-check containment and inode immediately before unlink.
        parent_real2 = os.path.realpath(parent)
        if not _containment_ok(parent_real2, root_real):
            raise OSError("refused: outside allowed_root")
        st2 = os.lstat(candidate)
        if stat.S_ISLNK(st2.st_mode):
            raise OSError("refused: symlink")
        if not stat.S_ISREG(st2.st_mode):
            raise OSError("refused: not a regular file")
        if (st.st_dev, st.st_ino) != (st2.st_dev, st2.st_ino):
            raise OSError("refused: entry changed")

        os.unlink(candidate)


def delete_files(paths, allowed_root=None):
    """Delete the given regular files, refusing symlinks and out-of-root paths.

    Security guarantees:
      (a) Symlinks are refused (the named entry itself, never a target).
      (b) If allowed_root is provided, paths outside it are refused.
      (c) Non-regular files (directories, FIFOs, devices, sockets) are refused.

    Known limitations (out of scope):
      - TOCTOU races against an attacker with write access to the parent
        directory are not fully defended against (the window is narrowed).
      - Hard links, Windows alternate data streams, and 8.3 short names are
        not considered.
      - File contents are not securely erased.

    Returns a list of (path, error) tuples for paths that were not deleted.
    """
    root_real = None
    if allowed_root is not None:
        root_real = os.path.realpath(allowed_root)

    failures = []
    for path in paths:
        try:
            try:
                candidate = _resolve_candidate(path, root_real)
            except ValueError as exc:
                failures.append((path, "refused: %s" % exc))
                continue

            # The authoritative validation happens inside _unlink_nofollow
            # against the opened parent dir_fd. The pre-check here is only a
            # fast-fail for obviously bad entries; it is not relied upon for
            # security.
            try:
                st = os.lstat(candidate)
            except FileNotFoundError:
                failures.append((path, "not found"))
                continue
            if stat.S_ISLNK(st.st_mode):
                failures.append((path, "refused: symlink"))
                continue
            if not stat.S_ISREG(st.st_mode):
                failures.append((path, "refused: not a regular file"))
                continue

            _unlink_nofollow(candidate, root_real)
        except FileNotFoundError:
            failures.append((path, "not found"))
        except OSError as exc:
            failures.append((path, str(exc)))

    return failures