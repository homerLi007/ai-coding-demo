# secure_delete_files

A hardened, race-resistant file deletion utility for Python.

`secure_delete_files` removes regular files while confining all operations to a trusted root directory. It defends against symlink-based path traversal and TOCTOU (time-of-check-to-time-of-use) attacks by walking the path component-by-component using directory file descriptors and `O_NOFOLLOW` semantics.

---

## Table of Contents

- [Features](#features)
- [Security Model](#security-model)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Basic Example](#basic-example)
  - [Custom Root Directory](#custom-root-directory)
  - [Interpreting Results](#interpreting-results)
- [API Reference](#api-reference)
  - [`secure_delete_files(file_paths, root=None)`](#secure_delete_filesfile_paths-rootnone)
- [Result Codes](#result-codes)
- [Platform Notes](#platform-notes)
- [Limitations](#limitations)
- [License](#license)

---

## Features

- **Symlink-safe traversal** — Every path component is opened with `O_NOFOLLOW` (where supported), so a symlinked directory or file cannot redirect deletion outside the root.
- **TOCTOU-resistant** — Uses directory file descriptors (`dir_fd`) to anchor operations, preventing parent-directory swaps between validation and deletion.
- **Root confinement** — Lexical containment checks reject any path that resolves outside the configured root.
- **Filesystem-root guard** — Refuses to operate when the root is `/` (or a drive root on Windows).
- **Regular-file-only** — Only regular files are unlinked; directories, devices, FIFOs, and symlinks are rejected.
- **Per-path status reporting** — Returns a dictionary mapping each input path to a descriptive outcome string instead of raising on the first failure.
- **Graceful degradation** — Falls back to best-effort behavior on platforms lacking `dir_fd` support for `unlink`.

---

## Security Model

`secure_delete_files` is designed for scenarios where an attacker may control filesystem state (e.g., a shared temp directory, an upload staging area, or a user-writable cache). It mitigates:

| Threat | Mitigation |
|--------|------------|
| Symlink swap on the target file | `O_NOFOLLOW` on the final open + `lstat` re-check before unlink |
| Symlink swap on a parent directory | Each intermediate directory is opened with `O_NOFOLLOW` relative to the previous `dir_fd` |
| TOCTOU between validation and deletion | All operations are anchored to open directory file descriptors |
| Path traversal via `..` or absolute paths | Lexical normalization + `commonpath` containment check |
| Deleting the filesystem root | Explicit guard against `/` and Windows drive roots |
| Deleting non-regular files | `stat.S_ISREG` check before unlink |

> **Note:** This utility is not a substitute for OS-level sandboxing. It hardens a specific class of filesystem operations but does not protect against a fully compromised process or kernel.

---

## Requirements

- **Python:** 3.6+ (uses `os.stat(..., dir_fd=...)` and `os.unlink(..., dir_fd=...)`)
- **Platform:** POSIX systems (Linux, macOS, BSD) provide full functionality. Windows support is limited — see [Platform Notes](#platform-notes).

---

## Installation

Copy the module into your project, or save it as `secure_delete.py` and import it:

```python
from secure_delete import secure_delete_files
```

No third-party dependencies are required.

---

## Usage

### Basic Example

```python
from secure_delete import secure_delete_files

results = secure_delete_files([
    "uploads/tmp/report.pdf",
    "uploads/tmp/avatar.png",
])

print(results)
# {'uploads/tmp/report.pdf': 'deleted', 'uploads/tmp/avatar.png': 'deleted'}
```

### Custom Root Directory

All deletions are confined to `root`. Paths may be absolute or relative; relative paths are resolved against `root`.

```python
results = secure_delete_files(
    ["cache/2024/data.bin", "/var/app/cache/old.log"],
    root="/var/app",
)
```

### Interpreting Results

The function never raises for individual path failures — it returns a status per path:

```python
for path, status in results.items():
    if status != "deleted":
        print(f"Could not delete {path}: {status}")
```

---

## API Reference

### `secure_delete_files(file_paths, root=None)`

Delete regular files confined to `root`.

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `file_paths` | `Iterable[str]` | Paths to delete. May be absolute or relative to `root`. |
| `root` | `str \| None` | Trusted root directory. Defaults to the current working directory. |

**Returns**

`dict[str, str]` — A mapping from each input path to a status code (see [Result Codes](#result-codes)).

**Raises**

| Exception | Condition |
|-----------|-----------|
| `ValueError` | `root` resolves to the filesystem root (`/` or a Windows drive root). |

**Behavior**

1. Resolves `root` to a real path and validates it is not the filesystem root.
2. For each input path:
   - Normalizes and lexically verifies containment within `root`.
   - Rejects paths containing `..`, `.`, or empty components.
   - Opens `root` with `O_NOFOLLOW | O_DIRECTORY`.
   - Walks each intermediate component with `O_NOFOLLOW`, closing the previous descriptor.
   - Rejects symlinks and non-regular files at the leaf via `lstat`.
   - Unlinks the leaf using `dir_fd`-anchored `os.unlink`.
3. Records the outcome and continues to the next path.

---

## Result Codes

| Code | Meaning |
|------|---------|
| `deleted` | File was successfully removed. |
| `not_found` | A component of the path does not exist. |
| `permission_denied` | Insufficient permissions to open or unlink. |
| `symlink_rejected` | A symlink was encountered at the leaf or an intermediate component. |
| `not_a_file` | The leaf exists but is not a regular file (e.g., directory, FIFO, device). |
| `not_a_directory` | An intermediate path component is not a directory. |
| `outside_root` | The path resolves outside the configured root. |
| `invalid_path` | The path contains invalid components (`.`, `..`, empty) or refers to the root itself. |
| `error` | Any other `OSError` occurred. |

---

## Platform Notes

- **POSIX (Linux, macOS, BSD):** Full functionality. `O_NOFOLLOW`, `O_DIRECTORY`, and `dir_fd`-relative operations are all available.
- **Windows:** `O_NOFOLLOW` and `dir_fd` support are limited or absent. The code falls back to a best-effort `os.unlink(name)` when `dir_fd` is unsupported. **Do not rely on this utility for security guarantees on Windows** — use it only as a convenience wrapper.
- **Filesystems without `O_NOFOLLOW`:** If the flag is unavailable, the final-component symlink check relies on `lstat` alone, which is subject to a narrow TOCTOU window.

---

## Limitations

- **Not a shredder.** This function performs a standard `unlink`; it does not overwrite file contents. Data may remain recoverable on the underlying storage.
- **No recursive deletion.** Only individual files are removed. Directories must be emptied separately.
- **No glob expansion.** Pass explicit paths; wildcards are not interpreted.
- **Root must exist.** The `root` directory itself must be openable; otherwise every path will report `not_found` or `error`.
- **Best-effort on Windows.** See [Platform Notes](#platform-notes).

---

## License

Specify your project's license here (e.g., MIT, Apache-2.0).