# Secure File Deletion Utility

A small, safety-focused Python utility for deleting files while guaranteeing that every deletion is confined to a designated root directory.

---

## Overview

`secure_delete_files` accepts a collection of file paths and an `allowed_root` directory. It resolves every path (following symlinks and normalizing `..` segments) and refuses to delete anything that lies outside the allowed root. The function never raises on individual failures — instead it returns a per-path result dictionary so callers can inspect exactly what happened to each file.

This makes it suitable for cleanup routines, temporary-file purges, and any workflow where an attacker (or a bug) might otherwise trick the program into deleting arbitrary files.

---

## Features

- **Path confinement** — Every target is resolved with `Path.resolve()` and checked against the resolved `allowed_root` before any filesystem mutation occurs.
- **Symlink-safe** — Because resolution happens first, symlinks pointing outside the root are rejected.
- **Non-destructive on error** — Missing files, directories, and out-of-scope paths are reported rather than raising exceptions.
- **Detailed reporting** — Returns a mapping of every input path to a status string.
- **No external dependencies** — Uses only the Python standard library (`os`, `pathlib`).

---

## Requirements

- Python 3.6 or newer (uses `pathlib.Path` and f-string–compatible syntax).

---

## Installation

Copy the `secure_delete_files` function into your project, or save it as a module (e.g. `secure_delete.py`) and import it:

```python
from secure_delete import secure_delete_files
```

---

## Usage

### Basic example

```python
from secure_delete import secure_delete_files

results = secure_delete_files(
    file_paths=[
        "/tmp/workspace/cache/a.tmp",
        "/tmp/workspace/cache/b.tmp",
        "/etc/passwd",              # outside the allowed root
        "/tmp/workspace/cache/missing.tmp",
    ],
    allowed_root="/tmp/workspace",
)

for path, status in results.items():
    print(f"{path}: {status}")
```

### Example output

```
/tmp/workspace/cache/a.tmp: deleted
/tmp/workspace/cache/b.tmp: deleted
/etc/passwd: outside_allowed_root
/tmp/workspace/cache/missing.tmp: not_found
```

---

## API Reference

### `secure_delete_files(file_paths, allowed_root)`

Deletes each file in `file_paths` provided it resides within `allowed_root`.

#### Parameters

| Name | Type | Description |
|------|------|-------------|
| `file_paths` | `Iterable[str]` | File paths to attempt to delete. May be a list, tuple, generator, or any iterable of strings. |
| `allowed_root` | `str` | The directory that all deletions must be confined to. Both the root and each target are resolved to absolute, symlink-free paths before comparison. |

#### Returns

`dict[str, str]` — A mapping from each input path (as originally supplied) to one of the following status strings:

| Status | Meaning |
|--------|---------|
| `"deleted"` | The file existed, was inside the allowed root, and was successfully removed. |
| `"not_found"` | The path does not exist. |
| `"not_a_file"` | The path exists but is not a regular file (e.g. a directory or special file). |
| `"outside_allowed_root"` | The resolved path lies outside `allowed_root`. No deletion was attempted. |
| `"error"` | An `OSError` occurred during deletion (e.g. permission denied, file busy). |

#### Raises

This function does not raise on per-file failures. It may still raise if `allowed_root` itself is invalid in a way that `Path.resolve()` cannot handle (rare).

---

## Security Notes

- **Resolution before validation.** Both `allowed_root` and each target path are passed through `Path.resolve()`, which collapses `..` segments and follows symlinks. This prevents traversal attacks such as `allowed_root/../../etc/shadow`.
- **Root itself is protected.** The check `root not in path.parents and path != root` ensures that the root directory itself is never treated as a deletable file (and would be rejected as `not_a_file` anyway).
- **TOCTOU caveat.** As with any filesystem operation, there is a small window between the `resolve()`/`exists()` checks and the `os.remove()` call. If the filesystem is writable by untrusted users, consider running this in a directory you control, or use `os.open` with `O_NOFOLLOW` for stricter guarantees.
- **Not a shredder.** This performs a normal `unlink`. It does not overwrite file contents. For sensitive data, combine with disk-level encryption or a dedicated secure-erase tool.

---

## Behavior Details

- **Duplicate paths** in `file_paths` are processed independently; the returned dict will contain one entry per unique string key (later duplicates overwrite earlier results).
- **Relative paths** are resolved relative to the current working directory before the containment check.
- **Non-string inputs** are not explicitly validated; passing a `Path` object works because `Path(raw_path)` accepts it, but the returned dict will be keyed by that object rather than a string.

---

## Testing

A minimal sanity check:

```python
import tempfile, os
from pathlib import Path
from secure_delete import secure_delete_files

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    inside = root / "keep.txt"
    inside.write_text("hello")

    outside = Path(tempfile.gettempdir()) / "outside.txt"
    outside.write_text("nope")

    results = secure_delete_files([str(inside), str(outside)], str(root))

    assert results[str(inside)] == "deleted"
    assert results[str(outside)] == "outside_allowed_root"
    assert not inside.exists()
    assert outside.exists()

    outside.unlink()
```

---

## License

Add your preferred license here (e.g. MIT, Apache-2.0).