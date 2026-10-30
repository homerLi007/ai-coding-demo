# Secure File Deletion Utility

A small, dependency-free Python utility for safely deleting files while enforcing a trusted root directory boundary. It is designed for scenarios where file paths may come from untrusted sources (user input, configuration files, job queues) and must not be allowed to escape a sandbox.

---

## Overview

`secure_delete_files` deletes a batch of files, but only after verifying that each path:

1. Is **not a symlink** (prevents symlink-based escapes).
2. Resolves (via `realpath`) to a location **inside** a caller-supplied `allowed_root`.
3. Exists and is a **regular file** (directories and special files are skipped).

Every input path receives a per-path result string, so callers can audit exactly what happened without exceptions interrupting the batch.

---

## Requirements

- Python 3.6+
- Standard library only (`os`)

---

## Installation

Copy the module into your project, or save it as `secure_delete.py` and import it:

```python
from secure_delete import secure_delete_files
```

---

## API

### `secure_delete_files(file_paths, allowed_root)`

Deletes files from an iterable, restricted to a trusted root directory.

#### Parameters

| Name | Type | Description |
|------|------|-------------|
| `file_paths` | `Iterable[str]` | File paths to delete. May be relative or absolute. |
| `allowed_root` | `str` | Directory that all deletions must stay within. Resolved with `os.path.realpath`. |

#### Returns

A `dict` mapping each input path (as provided) to a result string:

| Result | Meaning |
|--------|---------|
| `"deleted"` | File was successfully removed. |
| `"not_found"` | Path does not exist. |
| `"not_a_file"` | Path exists but is not a regular file (e.g., a directory). |
| `"outside_root"` | Resolved path lies outside `allowed_root`. |
| `"symlink_rejected"` | Path is a symlink and was refused. |
| `"error: <message>"` | An `OSError` or `ValueError` occurred (e.g., permission denied). |

#### Behavior notes

- **Symlinks are rejected outright**, even if they point inside `allowed_root`. This is a deliberate conservative choice to eliminate a class of TOCTOU-style attacks.
- **Containment is checked with `os.path.commonpath`** on fully resolved paths, so `..` traversal and relative paths are handled correctly.
- **Directories are never deleted**, even if empty.
- **Failures are isolated per path** — one bad entry does not abort the batch.

---

## Usage

### Basic example

```python
from secure_delete import secure_delete_files

results = secure_delete_files(
    file_paths=[
        "/srv/uploads/report.pdf",
        "/srv/uploads/../etc/passwd",       # outside root
        "/srv/uploads/link_to_secret",      # symlink
        "/srv/uploads/missing.txt",         # not found
        "/srv/uploads/subdir",              # directory
    ],
    allowed_root="/srv/uploads",
)

for path, status in results.items():
    print(f"{path}: {status}")
```

**Output:**

```
/srv/uploads/report.pdf: deleted
/srv/uploads/../etc/passwd: outside_root
/srv/uploads/link_to_secret: symlink_rejected
/srv/uploads/missing.txt: not_found
/srv/uploads/subdir: not_a_file
```

### Filtering successful deletions

```python
results = secure_delete_files(paths, allowed_root="/data/tmp")
deleted = [p for p, status in results.items() if status == "deleted"]
print(f"Removed {len(deleted)} files")
```

### Logging failures

```python
for path, status in results.items():
    if status.startswith("error:"):
        logger.warning("Failed to delete %s: %s", path, status)
```

---

## Security Model

This function is intended to be a **defense-in-depth** helper, not a complete sandbox. It mitigates:

- **Path traversal** (`../`) via `realpath` + `commonpath` containment check.
- **Symlink escapes** by rejecting any path that is itself a symlink.
- **Accidental directory deletion** by requiring `os.path.isfile`.

It does **not** protect against:

- **Race conditions** between the checks and `os.remove` (TOCTOU). For high-assurance use cases, consider `os.open` with `O_NOFOLLOW` and `dir_fd`-relative operations.
- **Hard links** pointing to files outside the root — the file content is still deleted, but the link itself lives inside the root.
- **Privilege escalation** — the caller must already have permission to delete the target files.

---

## Limitations

- Symlinks are rejected even when they resolve inside `allowed_root`. If you need to follow in-root symlinks, this function is not suitable as-is.
- `allowed_root` must exist; `os.path.realpath` will not raise if it does not, but containment checks will then behave unexpectedly. Validate the root before calling.
- The function is synchronous and single-threaded; for large batches, parallelize at the caller level.

---

## License

Add your preferred license here (e.g., MIT, Apache-2.0).