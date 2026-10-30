# safe_delete_files

A defensive, pure-Python file deletion utility that restricts deletions to regular files located beneath a trusted root directory. It refuses symlinks, skips non-regular files, and reports a per-path outcome for every input.

---

## Overview

`safe_delete_files` provides a sandboxed deletion primitive for scripts that need to remove files supplied by untrusted or semi-trusted input. Every path is resolved through `os.path.realpath`, checked for containment within a canonical `allowed_root`, and verified to be a regular file before `os.remove` is called.

The function never raises for individual path failures — instead it returns a dictionary mapping each input path to a result string, so callers can inspect and log outcomes without wrapping each call in `try/except`.

---

## Function Signature

```python
def safe_delete_files(file_paths, allowed_root):
    ...
```

### Parameters

| Name | Type | Description |
|------|------|-------------|
| `file_paths` | `Iterable[str]` | Paths to attempt to delete. Each entry is processed independently. |
| `allowed_root` | `str` | The trusted root directory. Must be a canonical path (see [Security Notes](#security-notes)). All deletions must resolve to a location strictly inside this directory. |

### Returns

`dict[str, str]` — a mapping from each input path to one of the following result strings:

| Result | Meaning |
|--------|---------|
| `"deleted"` | The file was a regular file inside `allowed_root` and was successfully removed. |
| `"not_found"` | The resolved path does not exist. |
| `"outside_root"` | The resolved path is not contained within `allowed_root` (including cross-drive or mixed absolute/relative cases). |
| `"symlink"` | The path (or its final component at check time) is a symbolic link. |
| `"not_regular_file"` | The path exists but is a directory, device, FIFO, socket, or other non-regular file. |
| `"error:<reason>"` | An `OSError` occurred during deletion; `<reason>` is the `strerror` or the exception string. |

---

## Behavior

For each input path, the function performs the following checks in order:

1. **Symlink check (input path)** — If `os.path.islink(path)` is true, the path is refused with `"symlink"`.
2. **Containment check** — The path is resolved via `os.path.realpath` and compared against the canonical root using `os.path.commonpath`. If the resolved path is not inside the root, the result is `"outside_root"`.
3. **Existence check** — `os.lstat` is called on the resolved path. A missing file yields `"not_found"`.
4. **Regular-file check** — `stat.S_ISREG` is used to confirm the target is a regular file. Directories, devices, FIFOs, and sockets are skipped with `"not_regular_file"`.
5. **Re-verification before unlink** — The path is re-resolved and re-checked for containment and symlink status immediately before deletion, narrowing the window for a symlink-swap attack.
6. **Deletion** — `os.remove` is called on the freshly resolved path.

Any `OSError` raised during the process is caught and reported as `"error:<reason>"`.

---

## Usage Example

```python
from safe_delete_files import safe_delete_files

results = safe_delete_files(
    file_paths=[
        "/srv/uploads/report.pdf",
        "/srv/uploads/link_to_etc_passwd",
        "/etc/passwd",
        "/srv/uploads/subdir",
        "/srv/uploads/missing.txt",
    ],
    allowed_root="/srv/uploads",
)

for path, outcome in results.items():
    print(f"{path}: {outcome}")
```

Example output:

```
/srv/uploads/report.pdf: deleted
/srv/uploads/link_to_etc_passwd: symlink
/etc/passwd: outside_root
/srv/uploads/subdir: not_regular_file
/srv/uploads/missing.txt: not_found
```

---

## Security Notes

This function is designed to be conservative, but it is **not** a complete sandbox. Callers should understand the following guarantees and limitations.

### Guarantees

- **Symlinks are refused.** Both the input path and the final resolved component are checked with `os.path.islink`. A symlink cannot be used to redirect deletion outside the root.
- **Containment is enforced via `realpath` + `commonpath`.** Paths that resolve outside `allowed_root` — including via `..` traversal or intermediate symlinks — are rejected.
- **Only regular files are deleted.** Directories, block/character devices, FIFOs, and sockets are skipped.
- **Mixed absolute/relative or cross-drive inputs are refused.** A `ValueError` from `os.path.commonpath` is treated as `"outside_root"`.

### Known Limitations

- **TOCTOU races remain.** Between the final containment check and `os.remove`, another process with write access to a directory inside `allowed_root` could swap an intermediate directory or the final component. Fully closing this window requires `openat`/`unlinkat` with `O_NOFOLLOW` and directory file descriptors, which are not exposed by the Python standard library. The function performs multiple re-checks to narrow — but not eliminate — this window.
- **`allowed_root` is trusted.** Callers must pass a canonical, absolute path. The function does not validate that `allowed_root` itself is safe or that the caller has appropriate privileges.
- **No privilege dropping or capability checks.** The function runs with the caller's privileges and performs no `setuid`, `setgid`, or capability manipulation.
- **No audit logging.** Results are returned to the caller; persistent logging is the caller's responsibility.

### Recommendations

- Run the calling process with the minimum privileges required to delete files under `allowed_root`.
- Ensure `allowed_root` is not writable by untrusted users, or accept that TOCTOU races may redirect deletions within that directory tree.
- Log the returned result dictionary for auditability.

---

## Requirements

- Python 3.6+ (uses `os.path.commonpath`, `os.path.realpath`, `stat.S_ISREG`).
- Standard library only — no third-party dependencies.

---

## License

Specify your project's license here (e.g., MIT, Apache-2.0).