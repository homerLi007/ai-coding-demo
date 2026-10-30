# safe_delete_files

A small, defensive utility for deleting files while guaranteeing that every deletion stays inside a designated root directory.

`safe_delete_files` resolves each candidate path to its real location (following symlinks) before checking containment, so it is resistant to path-traversal tricks (`../`) and symlink escapes. Files that fall outside the allowed root are refused; anything that is not a regular file is skipped.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  - [Basic Example](#basic-example)
  - [Result Codes](#result-codes)
- [API Reference](#api-reference)
  - [`safe_delete_files(file_paths, allowed_root)`](#safe_delete_filesfile_paths-allowed_root)
- [Security Model](#security-model)
- [Behavior Details](#behavior-details)
- [Limitations](#limitations)
- [License](#license)

---

## Features

- **Containment guarantee** — every path is resolved with `os.path.realpath` and validated against the allowed root using `os.path.commonpath`.
- **Symlink-safe** — symlinks are resolved before the containment check, preventing escapes via links.
- **Non-destructive on refusal** — out-of-root paths and non-regular files are reported, not deleted.
- **Per-file results** — returns a dictionary mapping each input path to a human-readable outcome, so callers can audit exactly what happened.
- **No exceptions on expected failures** — missing files and OS errors are captured in the result map rather than raised.

---

## Installation

This module has no third-party dependencies. Copy the function into your project or import it from its module:

```python
from safe_delete_files import safe_delete_files
```

Requires Python 3.5+ (uses `os.path.commonpath`).

---

## Usage

### Basic Example

```python
from safe_delete_files import safe_delete_files

results = safe_delete_files(
    file_paths=[
        "/var/app/uploads/report.pdf",
        "/var/app/uploads/../secrets.env",   # traversal attempt
        "/etc/passwd",                        # outside root
        "/var/app/uploads/missing.txt",       # does not exist
    ],
    allowed_root="/var/app/uploads",
)

for path, outcome in results.items():
    print(f"{path}: {outcome}")
```

Output:

```
/var/app/uploads/report.pdf: deleted
/var/app/uploads/../secrets.env: refused: outside allowed root
/etc/passwd: refused: outside allowed root
/var/app/uploads/missing.txt: skipped: not found
```

### Result Codes

Each value in the returned dictionary is one of the following strings:

| Result | Meaning |
| --- | --- |
| `"deleted"` | The file was a regular file inside the allowed root and was removed. |
| `"refused: outside allowed root"` | The resolved path is not contained within `allowed_root`. |
| `"skipped: not a regular file"` | The path exists but is a directory, device, FIFO, or other non-regular file. |
| `"skipped: not found"` | The path does not exist (raised `FileNotFoundError`). |
| `"error: <message>"` | An `OSError` occurred during deletion (e.g., permission denied). |

---

## API Reference

### `safe_delete_files(file_paths, allowed_root)`

Delete each file in `file_paths`, but only if it resolves to a path inside `allowed_root`.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `file_paths` | `Iterable[str]` | Paths to attempt to delete. Each path is processed independently. |
| `allowed_root` | `str` | The directory that all deletions must stay within. Resolved with `os.path.realpath` before use. |

#### Returns

`dict[str, str]` — a mapping from each input path to a result string (see [Result Codes](#result-codes)).

#### Raises

Does not raise for per-file failures; those are captured in the result map. Only programming errors (e.g., passing a non-iterable) will propagate.

---

## Security Model

The function defends against two common classes of attack:

1. **Path traversal** — inputs like `../../etc/passwd` are resolved to their real absolute path before the containment check, so they cannot escape the root.
2. **Symlink escapes** — a symlink inside the root that points outside it is resolved to its target, which then fails the containment check.

Containment is verified with:

```python
os.path.commonpath([root, real_path]) == root
```

Because both operands are already real paths, this comparison is reliable for the intended use case.

> **Note:** This function is designed for *file* deletion. It intentionally refuses directories and other non-regular files.

---

## Behavior Details

- **Root resolution** — `allowed_root` is resolved once at the start of the call. If the root itself does not exist, `realpath` still returns a normalized path, and containment checks proceed against it.
- **Per-file isolation** — an error on one path does not affect processing of the others.
- **Idempotent-ish** — deleting an already-missing file yields `"skipped: not found"` rather than an exception.
- **Returned keys** — the dictionary is keyed by the *original* input path, not the resolved path, so callers can correlate results with their inputs.

---

## Limitations

- **TOCTOU window** — there is a small time-of-check/time-of-use gap between `realpath`/`isfile` and `os.remove`. For high-security scenarios, consider `os.open` with `O_NOFOLLOW` and `dir_fd`-relative operations.
- **Case sensitivity** — on case-insensitive filesystems (e.g., macOS default, Windows), `commonpath` comparisons follow the platform's path semantics.
- **Root must be a directory** — passing a file as `allowed_root` is not meaningful and will cause all deletions to be refused.
- **No recursive deletion** — directories are always skipped.

---

## License

Specify your project's license here (e.g., MIT, Apache-2.0).