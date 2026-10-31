# `safe_delete` — Symlink-Safe File Deletion

A small, dependency-free Python utility for deleting regular files while refusing to follow symlinks, escape a configured root directory, or remove non-regular filesystem entries.

---

## Overview

`safe_delete` provides a single public function, `delete_files(paths, allowed_root=None)`, that removes a list of files with the following guarantees:

- **Symlinks are never followed.** The named entry is inspected and unlinked directly; a symlink is refused rather than resolved to its target.
- **Containment is enforced.** When `allowed_root` is supplied, any path whose resolved parent lies outside that root is refused.
- **Only regular files are removed.** Directories, FIFOs, sockets, block/character devices, and other special entries are refused.
- **Best-effort TOCTOU narrowing.** On POSIX systems with `dir_fd` support, the parent directory is opened once and the unlink is performed relative to that descriptor, so the entry removed is the one that was validated against the same fd.

The module is intentionally conservative: on platforms where the safety checks cannot be verified, it **fails closed** rather than proceeding.

---

## Requirements

- **Python 3.8+** (tested against the `dir_fd` / `follow_symlinks` feature-detection paths).
- No third-party dependencies.
- Standard library only: `os`, `stat`, `errno`.

---

## Installation

Copy the module into your project (e.g. as `safe_delete.py`) and import it:

```python
from safe_delete import delete_files
```

---

## Usage

### Basic deletion

```python
from safe_delete import delete_files

failures = delete_files(["build/tmp.txt", "build/cache.bin"])
for path, reason in failures:
    print(f"could not delete {path}: {reason}")
```

### Constrained to a root directory

```python
failures = delete_files(
    ["/srv/app/uploads/a.png", "/srv/app/uploads/b.png"],
    allowed_root="/srv/app/uploads",
)
```

Any path whose resolved parent is not contained within `allowed_root` is refused with `"refused: outside allowed_root"`.

### Handling results

`delete_files` never raises for per-path failures. It returns a list of `(path, error)` tuples for entries that were **not** deleted. An empty list means every path was successfully removed.

```python
failures = delete_files(paths, allowed_root=ROOT)
if failures:
    for path, err in failures:
        log.warning("delete failed: %s (%s)", path, err)
```

---

## API

### `delete_files(paths, allowed_root=None) -> list[tuple[str, str]]`

Delete each path in `paths`, subject to the safety rules described above.

| Parameter      | Type                     | Description                                                                 |
|----------------|--------------------------|-----------------------------------------------------------------------------|
| `paths`        | `Iterable[str]`          | Paths to delete. Each is processed independently.                           |
| `allowed_root` | `str \| None`            | If provided, paths whose resolved parent is outside this directory are refused. |

**Returns:** a list of `(path, error_message)` tuples for paths that were not deleted. Errors are human-readable strings such as `"not found"`, `"refused: symlink"`, `"refused: outside allowed_root"`, or `"refused: not a regular file"`.

**Raises:** nothing for per-path failures. Programming errors (e.g. passing a non-iterable) propagate normally.

---

## Security Model

### Guarantees

| Guarantee | Mechanism |
|-----------|-----------|
| Symlinks are refused | `lstat` with `follow_symlinks=False`, cross-checked with `readlink`; the parent directory is opened with `O_NOFOLLOW`. |
| Containment within `allowed_root` | Parent is resolved with `realpath`; the opened directory's real path is re-verified via `/proc/self/fd` on Linux. |
| Only regular files are removed | `stat.S_ISREG` check immediately before unlink. |
| Inode stability | `(st_dev, st_ino)` is re-checked immediately before unlink to narrow the swap window. |
| Fail-closed on unsupported platforms | If `readlink` is unavailable and `follow_symlinks=False` is not honored, the operation is refused. |

### Known limitations (explicitly out of scope)

- **TOCTOU against a writer in the parent directory.** The window is narrowed but not eliminated. An attacker with write access to the parent directory can still race the operation.
- **Hard links.** A hard link to a file outside `allowed_root` is indistinguishable from a regular file inside it.
- **Windows alternate data streams** and **8.3 short names** are not considered.
- **Secure erasure.** File contents are not overwritten; deletion is a normal `unlink`.
- **Permission enforcement.** The module does not attempt to change or verify file permissions.

---

## Platform Notes

### POSIX (Linux, macOS, BSD)

When `os.unlink` supports `dir_fd` (i.e. `os.unlink in os.supports_dir_fd`), the module:

1. Opens the parent directory with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`.
2. Re-verifies containment against the *opened* directory's real path (via `/proc/self/fd/<fd>` on Linux, or `realpath(parent)` elsewhere).
3. Performs `lstat` and `unlink` relative to the directory fd.

This is the strongest path and is the recommended deployment target.

### Windows

`dir_fd` support is not available, so the module falls back to a path-based flow that:

1. Re-verifies containment against `realpath(parent)`.
2. Re-`lstat`s the candidate immediately before `unlink`.
3. Refuses if the entry is no longer a regular file or has changed inode.

The fallback is weaker than the POSIX path but still refuses symlinks and non-regular files.

### Unsupported platforms

If neither `dir_fd` nor a reliable `follow_symlinks=False` is available, the module **refuses** rather than proceeding. This is intentional.

---

## Examples

### Refusing a symlink

```python
import os
os.symlink("/etc/passwd", "/tmp/link")

failures = delete_files(["/tmp/link"])
# [('/tmp/link', 'refused: symlink')]
# /etc/passwd is untouched.
```

### Refusing an out-of-root path

```python
failures = delete_files(["/etc/hosts"], allowed_root="/srv/app")
# [('/etc/hosts', 'refused: outside allowed_root')]
```

### Refusing a directory

```python
failures = delete_files(["/srv/app/uploads"])
# [('/srv/app/uploads', 'refused: not a regular file')]
```

### Mixed batch

```python
failures = delete_files(
    ["/srv/app/a.txt", "/srv/app/link", "/srv/app/missing.txt"],
    allowed_root="/srv/app",
)
# [('/srv/app/link', 'refused: symlink'),
#  ('/srv/app/missing.txt', 'not found')]
```

---

## Design Notes

- **`_strip_trailing_sep`** removes exactly one trailing separator while preserving drive roots (`C:\`) and UNC roots (`\\server\share\`), so `C:\` is not silently reinterpreted as `C:` (the current directory on drive C).
- **`_resolve_candidate`** resolves the *parent* with `realpath` (handling `..` and symlinked intermediate components physically) and then appends the basename. The basename is validated to be a plain name — `.`, `..`, and empty are rejected — so the caller's intent is preserved.
- **`_lstat_nofollow`** prefers `os.lstat(..., dir_fd=...)` (Python 3.10+) and falls back to `os.stat(..., follow_symlinks=False)`. It then cross-checks with `readlink` and fails closed on unexpected errors.
- **`_unlink_nofollow`** is the authoritative validation point. The pre-check in `delete_files` is a fast-fail only and is not relied upon for security.

---

## License

Add your license here (e.g. MIT, Apache-2.0).