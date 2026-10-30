# Secure File Deletion Utility

A small, security-focused Python utility for deleting files while confining all operations to a caller-specified root directory.

## Overview

`secure_delete_files` deletes a list of files, but only if they reside inside an explicitly allowed root directory. The implementation is hardened against common filesystem attack classes — path traversal, symlink swaps, and time-of-check/time-of-use (TOCTOU) races — by anchoring every operation to a directory file descriptor opened on the resolved root.

## Features

- **Root confinement** — every operation is performed relative to a file descriptor opened on the resolved `allowed_root`.
- **TOCTOU-resistant** — the target is opened, verified via `fstat`, and unlinked using the same directory file descriptor, so the verified entry is the one that gets removed.
- **Symlink-safe** — `O_NOFOLLOW` prevents the final path component from being swapped for a symlink between check and unlink.
- **Traversal rejection** — absolute paths and any path containing `..` or empty components are rejected up front.
- **Regular-file-only** — directories, devices, FIFOs, and other non-regular files are never unlinked.
- **Per-file status reporting** — returns a dictionary mapping each input path to a clear outcome.

## Requirements

- Python 3.x
- A POSIX-like operating system that supports `O_DIRECTORY`, `O_NOFOLLOW`, and the `dir_fd` parameter of `os.open` / `os.unlink` (Linux, macOS, and other modern Unixes).

## Installation

Copy the function into your project, or save it as a module (e.g., `secure_delete.py`) and import it:

```python
from secure_delete import secure_delete_files
```

## Usage

```python
from secure_delete import secure_delete_files

results = secure_delete_files(
    file_paths=["logs/app.log", "cache/tmp.bin", "missing.txt"],
    allowed_root="/var/app/data",
)

for path, status in results.items():
    print(f"{path}: {status}")
```

### Example output

```
logs/app.log: deleted
cache/tmp.bin: deleted
missing.txt: not_found
```

## API

### `secure_delete_files(file_paths, allowed_root)`

#### Parameters

| Name | Type | Description |
|------|------|-------------|
| `file_paths` | `list[str]` or `tuple[str, ...]` | Relative paths to delete. Each path is interpreted relative to `allowed_root`. |
| `allowed_root` | `str` | Directory that all deletions must be confined to. It is resolved with `os.path.realpath` before use. |

#### Returns

A `dict[str, str]` mapping each input path to one of the following status strings:

| Status | Meaning |
|--------|---------|
| `"deleted"` | The file was successfully unlinked. |
| `"not_found"` | The path did not exist at open or unlink time. |
| `"not_a_file"` | The path exists but is not a regular file (e.g., a directory, symlink, or device). |
| `"rejected"` | The path was rejected for security or permission reasons (absolute path, contains `..`, permission denied, or another OS error). |

#### Raises

| Exception | Condition |
|-----------|-----------|
| `TypeError` | `file_paths` is not a `list`/`tuple`, `allowed_root` is not a `str`, or an individual path is not a `str`. |
| `ValueError` | `allowed_root` cannot be resolved, is not a directory, or cannot be opened. |

## Security Model

The function is designed to be safe even when the filesystem is being modified concurrently by an untrusted party.

1. **Root anchoring.** `allowed_root` is resolved with `os.path.realpath` and opened once with `O_RDONLY | O_DIRECTORY`. All subsequent operations use this descriptor via `dir_fd`, so parent-directory swaps cannot redirect the operation outside the root.

2. **Input validation.** Absolute paths and any path containing `..` or empty components are rejected before any filesystem access. This blocks trivial traversal attempts.

3. **Symlink protection.** Files are opened with `O_NOFOLLOW`, so if the final path component is a symlink, the open fails and the path is rejected rather than followed.

4. **Type verification.** After opening, `os.fstat` is used to confirm the target is a regular file (`stat.S_ISREG`). Non-regular files are reported as `not_a_file` and left untouched.

5. **Atomic unlink.** `os.unlink(path, dir_fd=root_fd)` removes the entry relative to the same directory descriptor used for verification, closing the TOCTOU window between check and delete.

6. **Resource hygiene.** File descriptors are always closed in `finally` blocks, even on error paths.

## Limitations

- **POSIX only.** The function relies on `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`, which are not available on Windows.
- **Not a shredder.** This function unlinks files; it does not overwrite their contents. On journaling or copy-on-write filesystems, data may persist after unlinking. Use a dedicated secure-erase tool if you need to overwrite data.
- **Single-component paths only in practice.** Because `..` and empty components are rejected, paths must be clean relative paths. Nested paths like `a/b/c.txt` are accepted, but intermediate components are not individually re-validated against symlink swaps — only the final component is protected by `O_NOFOLLOW`.
- **Permissions are not escalated.** If the process lacks permission to unlink a file, the status is `"rejected"`.

## License

Add your preferred license here (e.g., MIT, Apache-2.0).