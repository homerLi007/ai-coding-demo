# safe_delete_files

A small, safety-focused utility for deleting files while guaranteeing that every deletion stays confined to a designated root directory. It protects against path traversal, symlink escapes, and accidental removal of directories or special files.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  - [Basic Example](#basic-example)
  - [Handling Results](#handling-results)
- [API Reference](#api-reference)
  - [`safe_delete_files(file_paths, allowed_root)`](#safe_delete_filesfile_paths-allowed_root)
- [Behavior Details](#behavior-details)
  - [Result Values](#result-values)
  - [Safety Guarantees](#safety-guarantees)
- [Examples](#examples)
  - [Mixed Outcomes](#mixed-outcomes)
  - [Rejecting Paths Outside the Root](#rejecting-paths-outside-the-root)
  - [Invalid Root](#invalid-root)
- [Limitations](#limitations)
- [License](#license)

---

## Overview

`safe_delete_files` deletes a collection of files, but only after verifying that each file — after resolving symlinks — resides inside a caller-supplied `allowed_root` directory. This makes it suitable for cleanup routines, cache eviction, or any scenario where file paths may originate from untrusted or partially trusted input.

The function never raises on a per-file basis. Instead, it returns a dictionary mapping each input path to a descriptive result string, allowing callers to inspect exactly what happened to every path.

---

## Features

- **Path traversal protection** — Resolved paths must be contained within `allowed_root`.
- **Symlink-safe** — Symlinks are resolved via `os.path.realpath` before the containment check, preventing escapes through links.
- **Type-restricted deletion** — Only regular files are removed; directories and special files (sockets, FIFOs, devices) are skipped.
- **Non-throwing per-file results** — Each path yields a status string rather than an exception.
- **Fail-fast root validation** — An invalid `allowed_root` raises `ValueError` immediately.

---

## Installation

This module has no third-party dependencies and requires only the Python standard library (`os`).

1. Save the code to a file, e.g. `safe_delete.py`.
2. Import it in your project:

```python
from safe_delete import safe_delete_files
```

Python 3.6+ is recommended.

---

## Usage

### Basic Example

```python
from safe_delete import safe_delete_files

results = safe_delete_files(
    file_paths=["/var/app/cache/a.tmp", "/var/app/cache/b.tmp"],
    allowed_root="/var/app/cache",
)

print(results)
# {'/var/app/cache/a.tmp': 'deleted', '/var/app/cache/b.tmp': 'deleted'}
```

### Handling Results

```python
results = safe_delete_files(paths, allowed_root="/srv/data")

for path, status in results.items():
    if status == "deleted":
        print(f"Removed: {path}")
    elif status == "outside_allowed_root":
        print(f"Refused (outside root): {path}")
    elif status == "not_found":
        print(f"Missing: {path}")
    elif status == "not_a_file":
        print(f"Skipped (not a regular file): {path}")
```

---

## API Reference

### `safe_delete_files(file_paths, allowed_root)`

Deletes each file in `file_paths`, but only if it resides within `allowed_root`.

#### Parameters

| Name | Type | Description |
|------|------|-------------|
| `file_paths` | `Iterable[str]` | File path strings to delete. |
| `allowed_root` | `str` | Directory that all deletions must be confined to. |

#### Returns

`dict[str, str]` — A mapping from each input path to a result string. See [Result Values](#result-values).

#### Raises

| Exception | Condition |
|-----------|-----------|
| `ValueError` | `allowed_root` does not exist or is not a directory. |

---

## Behavior Details

### Result Values

| Result | Meaning |
|--------|---------|
| `"deleted"` | The file existed, was a regular file, was inside the root, and was successfully removed. |
| `"not_found"` | The resolved path does not exist. |
| `"not_a_file"` | The resolved path exists but is not a regular file (e.g., a directory, symlink target that is a directory, socket, FIFO, or device). |
| `"outside_allowed_root"` | The resolved path is not contained within `allowed_root`. |

### Safety Guarantees

1. **Root validation first.** `allowed_root` is resolved with `os.path.realpath` and checked with `os.path.isdir`. If invalid, a `ValueError` is raised before any file is touched.
2. **Symlink resolution.** Each input path is resolved with `os.path.realpath` before any check, so a symlink pointing outside the root cannot be used to escape.
3. **Containment check.** `os.path.commonpath([root_real, real_path])` must equal `root_real`. This rejects both sibling directories and `..` traversal.
4. **Type check.** Only regular files are removed; directories and special files are reported as `"not_a_file"`.

---

## Examples

### Mixed Outcomes

```python
import os

os.makedirs("/tmp/root/sub", exist_ok=True)
open("/tmp/root/keep.txt", "w").close()
open("/tmp/root/sub/delete.txt", "w").close()
os.symlink("/etc/passwd", "/tmp/root/escape_link")

results = safe_delete_files(
    [
        "/tmp/root/keep.txt",        # regular file inside root
        "/tmp/root/sub/delete.txt",  # regular file inside root
        "/tmp/root/sub",             # directory
        "/tmp/root/missing.txt",     # does not exist
        "/tmp/root/escape_link",     # symlink to /etc/passwd
    ],
    allowed_root="/tmp/root",
)

print(results)
# {
#   '/tmp/root/keep.txt': 'deleted',
#   '/tmp/root/sub/delete.txt': 'deleted',
#   '/tmp/root/sub': 'not_a_file',
#   '/tmp/root/missing.txt': 'not_found',
#   '/tmp/root/escape_link': 'outside_allowed_root',
# }
```

### Rejecting Paths Outside the Root

```python
results = safe_delete_files(
    ["/tmp/root/../secret.txt", "/etc/hosts"],
    allowed_root="/tmp/root",
)
# {'/tmp/root/../secret.txt': 'outside_allowed_root',
#  '/etc/hosts': 'outside_allowed_root'}
```

### Invalid Root

```python
safe_delete_files(["a.txt"], allowed_root="/does/not/exist")
# raises ValueError: allowed_root is not a directory: '/does/not/exist'
```

---

## Limitations

- **TOCTOU window.** Between the containment check and `os.remove`, the filesystem could change (e.g., a path swapped for a symlink). For adversarial environments, consider `os.open` with `O_NOFOLLOW` and `dir_fd`-relative operations.
- **No recursive deletion.** Only the exact paths provided are considered; directories are never traversed.
- **No error capture on `os.remove`.** If `os.remove` fails (e.g., permission denied), the exception propagates. Wrap calls in `try/except` if you need per-file error handling.
- **Case sensitivity.** Containment relies on `os.path.commonpath`, which is case-sensitive on POSIX and case-insensitive on Windows — behavior follows the host OS.

---

## License

No license is specified in the source. Add a license header or `LICENSE` file before distributing.