import os
import stat
import errno


def _open_dir_nofollow(path, dir_fd=None):
    """Open a directory without following a final symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags, dir_fd=dir_fd)


def _safe_unlink_at(dir_fd, name):
    """Unlink a single path component relative to dir_fd, refusing symlinks.

    Uses unlinkat semantics via os.unlink(dir_fd=...) when available.
    Falls back to lstat + unlink with a re-check.
    """
    # Reject symlinks and non-regular files atomically-ish via lstat on dirfd.
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        raise OSError(errno.ELOOP, "symlink rejected", name)
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EINVAL, "not a regular file", name)
    try:
        os.unlink(name, dir_fd=dir_fd)
    except TypeError:
        # Platform without dir_fd support for unlink; best-effort fallback.
        os.unlink(name)


def _resolve_relative_parts(root_real, candidate):
    """Return the list of path components of candidate relative to root_real.

    Raises ValueError if candidate escapes root_real or contains '..'.
    """
    # Normalize without resolving symlinks; we will walk components ourselves.
    norm = os.path.normpath(candidate)
    if not os.path.isabs(norm):
        norm = os.path.join(root_real, norm)
    norm = os.path.normpath(norm)

    # Lexical containment check first (cheap, catches obvious escapes).
    try:
        common = os.path.commonpath([root_real, norm])
    except ValueError:
        raise ValueError("path outside root")
    if os.path.normcase(common) != os.path.normcase(root_real):
        raise ValueError("path outside root")

    rel = os.path.relpath(norm, root_real)
    if rel == os.curdir:
        raise ValueError("refusing to delete root itself")
    parts = rel.split(os.sep)
    if any(p in (os.curdir, os.pardir, "") for p in parts):
        raise ValueError("invalid path component")
    return parts


def secure_delete_files(file_paths, root=None):
    """Delete regular files confined to `root` (default: CWD).

    Security properties:
      - Rejects symlinks at every path component (O_NOFOLLOW traversal).
      - Uses directory file descriptors to avoid TOCTOU races on parents.
      - Refuses to operate on the filesystem root.
      - Returns a per-path status dict.
    """
    root_real = os.path.realpath(root or os.getcwd())
    if root_real == os.path.sep or (os.name == "nt" and root_real.endswith(":\\")):
        raise ValueError("refusing to operate on filesystem root")

    results = {}
    for path in file_paths:
        try:
            parts = _resolve_relative_parts(root_real, path)
        except ValueError as exc:
            results[path] = "outside_root" if "outside" in str(exc) else "invalid_path"
            continue

        # Walk from root down to the parent directory using O_NOFOLLOW.
        dir_fd = None
        try:
            dir_fd = _open_dir_nofollow(root_real)
            for comp in parts[:-1]:
                next_fd = _open_dir_nofollow(comp, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            _safe_unlink_at(dir_fd, parts[-1])
            results[path] = "deleted"
        except FileNotFoundError:
            results[path] = "not_found"
        except PermissionError:
            results[path] = "permission_denied"
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                results[path] = "symlink_rejected"
            elif exc.errno == errno.EINVAL:
                results[path] = "not_a_file"
            elif exc.errno == errno.ENOTDIR:
                results[path] = "not_a_directory"
            else:
                results[path] = "error"
        finally:
            if dir_fd is not None:
                try:
                    os.close(dir_fd)
                except OSError:
                    pass
    return results