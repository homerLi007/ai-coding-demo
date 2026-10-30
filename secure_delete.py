import os
import stat


def secure_delete_files(file_paths, allowed_root):
    """Securely delete files confined to allowed_root.

    Returns a dict mapping each input path to one of:
    'deleted', 'not_found', 'not_a_file', 'rejected'.

    Security properties:
    - All operations are anchored to a directory file descriptor opened on
      the resolved allowed_root, eliminating TOCTOU races on parent dirs.
    - O_NOFOLLOW prevents final-component symlink swaps.
    - fstat on the opened fd verifies the target is a regular file before
      unlinking, and unlink is performed via dir_fd so it targets the same
      directory entry that was verified.
    - Absolute paths and '..' components are rejected up front.
    """
    if not isinstance(file_paths, (list, tuple)):
        raise TypeError(
            f"file_paths must be a list or tuple, got {type(file_paths).__name__}"
        )
    if not isinstance(allowed_root, str):
        raise TypeError(
            f"allowed_root must be a string, got {type(allowed_root).__name__}"
        )

    try:
        resolved_root = os.path.realpath(allowed_root)
    except OSError:
        raise ValueError("allowed_root could not be resolved")

    if not os.path.isdir(resolved_root):
        raise ValueError("allowed_root is not a directory")

    try:
        root_fd = os.open(
            resolved_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
    except OSError as exc:
        raise ValueError(f"could not open allowed_root: {exc.strerror}")

    results = {}
    try:
        for path in file_paths:
            if not isinstance(path, str):
                raise TypeError(
                    f"file path must be a string, got {type(path).__name__}"
                )

            # Reject absolute paths and any traversal components up front.
            if os.path.isabs(path):
                results[path] = "rejected"
                continue
            parts = path.split(os.sep)
            if any(part in ("..", "") for part in parts):
                results[path] = "rejected"
                continue

            fd = None
            try:
                try:
                    fd = os.open(
                        path,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=root_fd,
                    )
                except FileNotFoundError:
                    results[path] = "not_found"
                    continue
                except PermissionError:
                    results[path] = "rejected"
                    continue
                except OSError:
                    results[path] = "rejected"
                    continue

                try:
                    st = os.fstat(fd)
                except OSError:
                    results[path] = "rejected"
                    continue

                if not stat.S_ISREG(st.st_mode):
                    results[path] = "not_a_file"
                    continue

                try:
                    os.unlink(path, dir_fd=root_fd)
                except FileNotFoundError:
                    results[path] = "not_found"
                except PermissionError:
                    results[path] = "rejected"
                except OSError:
                    results[path] = "rejected"
                else:
                    results[path] = "deleted"
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
    finally:
        try:
            os.close(root_fd)
        except OSError:
            pass

    return results