# secure_delete.py

A race-resistant, symlink-safe file deletion utility for Linux.

## Overview

`secure_delete.py` provides a hardened alternative to `os.unlink()` for deleting files in potentially hostile environments. It defends against common filesystem race conditions (TOCTOU attacks) by using `openat2(2)` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS`, `renameat2(2)` with `RENAME_NOREPLACE`, and inode identity verification.

The module is designed for scenarios where an attacker may have write access to a directory (e.g., `/tmp`, shared caches, or user-writable directories) and could attempt to swap files, insert symlinks, or otherwise subvert a naive deletion.

## Features

- **Symlink-safe** — Refuses to follow or delete symlinks (`O_NOFOLLOW`, `RESOLVE_NO_SYMLINKS`).
- **Race-resistant** — Uses `renameat2(RENAME_NOREPLACE)` to atomically move the target to a private random name, then verifies inode identity before unlinking.
- **Path traversal protection** — Rejects `..`, absolute paths, and empty components.
- **Directory safety checks** — Refuses to operate in directories that are group/other-writable or not owned by the current effective user.
- **Graceful fallback** — Uses `ctypes` to call `openat2` and `renameat2` syscalls directly on Python versions that don't expose them natively.
- **Durability** — `fsync`s the parent directory after deletion to ensure the unlink is persisted.

## Requirements

- **Linux kernel** ≥ 5.6 (for `openat2`) and ≥ 3.15 (for `renameat2`).
- **Python** 3.8+ (tested behavior; `os.openat2` / `os.renameat2` are used if available, otherwise `ctypes` fallback).
- **Filesystem support** for `RENAME_NOREPLACE` (ext4, xfs, btrfs, tmpfs, and most modern Linux filesystems).

## Installation

Copy `secure_delete.py` into your project. There are no external dependencies.

```bash
cp secure_delete.py /path/to/your/project/
```

## Usage

### Basic

```python
from secure_delete import secure_delete

# Delete files relative to the current working directory
secure_delete(["cache.tmp", "logs/old.log"])
```

### With an explicit root

```python
from secure_delete import secure_delete

# Delete files relative to /var/app/data
secure_delete(
    ["uploads/expired.bin", "tmp/scratch.dat"],
    root="/var/app/data",
)
```

### Error handling

```python
from secure_delete import secure_delete, SecureDeleteError

try:
    secure_delete(["suspicious.file"], root="/tmp/workdir")
except SecureDeleteError as e:
    print(f"Refused to delete: {e}")
```

## API

### `secure_delete(paths, root=None)`

Deletes each path in `paths` relative to `root`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `paths` | `Iterable[str \| bytes]` | Iterable of relative paths to delete. Must **not** be a single `str`/`bytes`. |
| `root` | `str \| bytes \| None` | Base directory. Defaults to the current working directory (`"."`). Must not contain `..`. |

**Raises:**

- `TypeError` — if `paths` is a single string/bytes instead of an iterable.
- `SecureDeleteError` — if any safety check fails (symlink, unsafe directory, unsupported syscall, inode mismatch, etc.).
- `OSError` — for underlying filesystem errors.

### `SecureDeleteError`

Exception raised for all safety-related refusals. Subclasses `Exception`.

## Security Model

`secure_delete` enforces the following invariants:

1. **No symlink traversal.** Every path component is opened with `O_NOFOLLOW` and `RESOLVE_NO_SYMLINKS`. If any component is a symlink, deletion is refused.
2. **No path escapes.** `RESOLVE_BENEATH` ensures resolution stays under the root directory. Absolute paths and `..` components are rejected before resolution.
3. **Trusted parent directory.** The immediate parent of the target must be:
   - A directory,
   - Not group- or other-writable,
   - Owned by the current effective UID.
4. **Atomic rename.** The target is renamed to a random private name (`.secure-delete-<32 hex chars>`) using `RENAME_NOREPLACE`, which fails if the destination already exists — preventing an attacker from pre-creating a name.
5. **Inode identity verification.** After rename, the file is re-opened and its `(st_dev, st_ino)` compared against the originally pinned inode. If they differ, the rename is rolled back and deletion is refused.
6. **Durability.** The parent directory is `fsync`'d after unlink.

## Limitations

- **Linux-only.** Relies on `openat2`, `renameat2`, `O_PATH`, and `RESOLVE_*` flags.
- **Filesystem support required.** `RENAME_NOREPLACE` must be supported; otherwise deletion is refused (fail-closed).
- **Orphaned private names.** If the process crashes between rename and unlink, a `.secure-delete-*` file may remain. This is a documented trade-off; it is safe but requires manual cleanup.
- **Not a "shredder".** This module deletes files safely from a *filesystem race* perspective. It does **not** overwrite file contents. On modern filesystems (CoW, journaling, SSD wear leveling), secure erasure of data is not achievable at the application layer.
- **Directory ownership check.** Requires the parent directory to be owned by the current EUID. This may be too strict for some legitimate use cases (e.g., deleting from a shared directory owned by another trusted user).

## Example: Cleaning a user-writable temp directory

```python
import os
import tempfile
from secure_delete import secure_delete, SecureDeleteError

with tempfile.TemporaryDirectory() as tmp:
    # Create some files
    for name in ("a.txt", "b.txt", "c.txt"):
        with open(os.path.join(tmp, name), "w") as f:
            f.write("data")

    # Delete them safely
    try:
        secure_delete(["a.txt", "b.txt", "c.txt"], root=tmp)
    except SecureDeleteError as e:
        print(f"Refused: {e}")

    assert os.listdir(tmp) == []
```

## Implementation Notes

- **`_openat2` fallback** — On Python builds without `os.openat2`, the module invokes syscall 437 directly via `ctypes`. The `open_how` struct is constructed manually.
- **`_renameat2` fallback** — Similarly, syscall 316 is invoked directly when `os.renameat2` is unavailable.
- **`_fsync_dir`** — `O_PATH` file descriptors cannot be `fsync`'d, so a fresh `O_RDONLY` descriptor is opened for the directory.
- **`_restore`** — Best-effort rollback of the rename if inode verification fails. If rollback fails, the file remains under its private name (safe, but orphaned).

## License

Specify your license here (e.g., MIT, Apache-2.0).