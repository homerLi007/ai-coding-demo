# Safe File Deletion Utility

A small Python utility for safely deleting files while guarding against path traversal attacks and accidental deletion of files outside an intended directory.

---

## Overview

`safe_delete_files` deletes a list of files, but only after verifying that each resolved path lies inside a designated "allowed root" directory. Every path is checked against a series of safety conditions before deletion, and a per-path status is returned so callers can inspect exactly what happened.

This is useful in scenarios such as:

- Cleaning up temporary or cache files within a sandboxed directory
- Processing user-supplied file lists where paths may be untrusted
- Batch deletion jobs where you want a full audit trail of outcomes

---

## Features

- **Path traversal protection** — Uses `os.path.realpath` and `os.path.commonpath` to ensure targets resolve inside the allowed root, defeating `..` tricks and symlink escapes.
- **Existence and type checks** — Skips paths that don't exist or aren't regular files (e.g., directories, sockets, devices).
- **Graceful error handling** — Catches `OSError` per file so one failure doesn't abort the batch.
- **Detailed results** — Returns a dictionary mapping each input path to a human-readable status string.

---

## Installation

No external dependencies. Requires Python 3.6+ (uses `os.path.commonpath`).

Simply copy the function into your project or import it from the module where it lives.

```python
from safe_delete import safe_delete_files
```

---

## Usage

### Basic example

```python
from safe_delete import safe_delete_files

results = safe_delete_files(
    file_paths=["/tmp/work/a.txt", "/tmp/work/b.log"],
    allowed_root="/tmp/work",
)

for path, status in results.items():
    print(f"{path}: {status}")
```

### Example output

```python
{
    "/tmp/work/a.txt": "deleted",
    "/tmp/work/b.log": "deleted",
    "/etc/passwd":    "skipped: outside allowed root",
    "/tmp/work/missing.txt": "skipped: does not exist",
    "/tmp/work/subdir":      "skipped: not a regular file",
    "/tmp/work/locked.txt":  "error: [Errno 13] Permission denied: '/tmp/work/locked.txt'",
}
```

---

## API Reference

### `safe_delete_files(file_paths, allowed_root)`

Deletes each file in `file_paths`, but only if it resolves to a path inside `allowed_root`.

#### Parameters

| Name           | Type                | Description                                                                 |
| -------------- | ------------------- | --------------------------------------------------------------------------- |
| `file_paths`   | `Iterable[str]`     | Paths to attempt to delete.                                                 |
| `allowed_root` | `str`               | The directory that all deletions must be confined to. Resolved via `realpath`. |

#### Returns

`dict[str, str]` — A mapping from each input path to a status string. Possible statuses:

| Status                          | Meaning                                                          |
| ------------------------------- | ---------------------------------------------------------------- |
| `"deleted"`                     | The file was successfully removed.                               |
| `"skipped: outside allowed root"` | The resolved path is not inside `allowed_root`.                |
| `"skipped: does not exist"`     | The resolved path does not exist on disk.                        |
| `"skipped: not a regular file"` | The resolved path exists but is not a regular file (e.g., a directory). |
| `"error: <message>"`            | An `OSError` occurred during deletion (e.g., permission denied). |

---

## Safety Guarantees

The function performs the following checks, in order, for each path:

1. **Resolve the path** with `os.path.realpath`, which follows symlinks and normalizes `..` segments.
2. **Verify containment** by comparing `os.path.commonpath([root, real_path])` against `root`. If they differ, the path is outside the allowed root and is skipped.
3. **Verify existence** with `os.path.exists`.
4. **Verify file type** with `os.path.isfile` (rejects directories, FIFOs, sockets, etc.).
5. **Attempt deletion** with `os.remove`, catching `OSError`.

Because containment is checked *after* `realpath` resolution, symlinks pointing outside the allowed root are correctly rejected.

---

## Limitations & Notes

- **Symlink targets are followed.** A symlink *inside* the allowed root that points *outside* it will be rejected (the resolved target is outside). To delete the symlink itself rather than its target, use `os.path.islink` and `os.unlink` explicitly.
- **Race conditions (TOCTOU).** Between the containment check and `os.remove`, the filesystem could change. For high-security use cases, consider `os.open` with `O_NOFOLLOW` and directory file descriptors.
- **Empty directories are not removed.** Only regular files are deleted.
- **`commonpath` requires both paths to be absolute or both relative.** Since `realpath` returns absolute paths, this is satisfied automatically.

---

## License

Add your preferred license here (e.g., MIT, Apache-2.0).