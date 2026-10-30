# safe_delete_files

A defensive file-deletion utility for Python that refuses to follow symlinks, rejects non-regular files, and (optionally) enforces containment within an allowed root directory.

---

## Overview

`safe_delete_files` deletes a batch of files while defending against the common hazards of naive `os.remove` / `os.unlink` usage:

- **Symlink attacks** — a caller authorizes deletion of `path`, but an attacker swaps in a symlink so the code deletes a *different* file. This module always deletes the *named entry*, never a symlink target.
- **TOCTOU on intermediate path components** — an attacker swaps a directory in the path for a symlink between validation and deletion. On POSIX, every intermediate component is opened with `O_NOFOLLOW | O_DIRECTORY` and the final `unlink` is anchored to the resulting directory file descriptor, closing this window.
- **Confused-deputy / containment escapes** — when `allowed_root` is supplied, the target must lexically reside under that root, and the root itself is opened by walking from the filesystem root with `O_NOFOLLOW` at every step so a symlinked ancestor cannot redirect the anchor.
- **Non-regular files** — directories, FIFOs, sockets, and device nodes are refused.
- **Malformed names** — empty names, `.`/`..`, path separators, NUL bytes, control characters, and (on Windows) reserved device names and trailing dots/spaces are rejected before any syscall.

The function never raises for per-path failures; it returns a mapping of each input path to either `"deleted"` or a human-readable error string.

---

## Requirements

- Python 3.6+
- POSIX platforms (Linux, macOS, BSD) for the full security guarantees.
- Windows and other platforms without `dir_fd` support are handled via a best-effort fallback (see [Platform Behavior](#platform-behavior)).

---

## Installation

Copy the module into your project (e.g. as `safe_delete.py`) and import it:

```python
from safe_delete import safe_delete_files
```

---

## API

### `safe_delete_files(file_paths, *, allowed_root=None)`

Delete each path in `file_paths`, refusing anything that looks unsafe.

#### Parameters

| Name | Type | Description |
| --- | --- | --- |
| `file_paths` | `Iterable[str]` | Paths to delete. Each is processed independently. |
| `allowed_root` | `str \| None` | Optional. If provided, each path must lexically reside under this directory. The root is opened with `O_NOFOLLOW` at every component. |

#### Returns

`dict[str, str]` — maps each input path to one of:

| Result | Meaning |
| --- | --- |
| `"deleted"` | The entry was successfully unlinked. |
| `"not found"` | The path does not exist. |
| `"refusing to delete symlink"` | The final entry is a symlink. |
| `"not a regular file"` | The entry is a directory, FIFO, socket, or device. |
| `"outside allowed_root"` | The path is not lexically contained under `allowed_root`. |
| `"invalid filename"` | The name is empty, `.`/`..`, contains a separator, NUL, control character, or (on Windows) is a reserved device name or ends with `.`/space. |
| `"unsupported: no dir_fd"` | `allowed_root` was supplied on a platform without `dir_fd` support. |
| `"error: <message>"` | Any other `OSError` (e.g. permission denied). |

#### Example

```python
from safe_delete import safe_delete_files

results = safe_delete_files(
    ["/var/tmp/app/cache/a.bin", "/var/tmp/app/cache/b.bin"],
    allowed_root="/var/tmp/app",
)

for path, status in results.items():
    print(f"{path}: {status}")
```

Output:

```
/var/tmp/app/cache/a.bin: deleted
/var/tmp/app/cache/b.bin: deleted
```

A symlink at the target is refused rather than followed:

```python
results = safe_delete_files(["/var/tmp/app/link"], allowed_root="/var/tmp/app")
# {'/var/tmp/app/link': 'refusing to delete symlink'}
```

---

## Security Model

### Symlink refusal

The function deletes the *named directory entry*, never a symlink target. This is enforced at two levels:

1. The final entry is `lstat`-checked and refused if it is a symlink.
2. On POSIX, `os.unlink(name, dir_fd=...)` never follows symlinks, so even a swap between the check and the unlink results in the symlink entry being removed — the safe outcome.

### Intermediate-component TOCTOU

On POSIX, the path is walked one component at a time from an anchor directory fd:

- Each intermediate component is opened with `O_NOFOLLOW | O_DIRECTORY`, so a symlink swap is refused.
- The final `unlink` is anchored to the resulting directory fd, so subsequent path manipulation cannot redirect it.

### Anchor opening

When `allowed_root` is provided, the anchor is opened by walking from the filesystem root (`/`) with `O_NOFOLLOW` at every step. A symlink anywhere in the root's ancestry is refused rather than followed, preventing an attacker-controlled `allowed_root` from pointing outside the intended tree.

### Lexical containment

Containment under `allowed_root` is checked lexically via `os.path.commonpath`. The components validated are exactly the components walked, so validation and execution operate on the same path — no `realpath` resolution is involved that could diverge from the walk.

---

## Platform Behavior

| Platform | `allowed_root` | Behavior |
| --- | --- | --- |
| POSIX with `dir_fd` | Not set | Full guarantee: walk from `/` with `O_NOFOLLOW`, unlink anchored to fd. |
| POSIX with `dir_fd` | Set | Full guarantee: anchor opened via `O_NOFOLLOW` walk from `/`, containment enforced lexically, unlink anchored to fd. |
| No `dir_fd` support | Not set | Best-effort `lstat`-then-`unlink` on the caller-supplied path. Symlinks and non-regular files are refused; the TOCTOU window is narrowed by re-checking immediately before removal. |
| No `dir_fd` support | Set | **Refused** (`"unsupported: no dir_fd"`). Containment cannot be enforced, so the function refuses rather than silently degrading. |

---

## Known Limitations

These are inherent to the pure-Python / POSIX API surface and are documented here so callers can reason about residual risk:

- **No secure erasure.** Deletion only unlinks the directory entry; file contents are not overwritten.
- **Permission checks are best-effort.** The OS enforces the real access policy; this module does not attempt to replicate it.
- **Residual final-entry race.** The `stat`/`unlink` pair on the final entry is not a single atomic operation. A concurrent attacker with write access to the parent directory can still swap a regular file for a non-regular entry in the residual window. POSIX provides no `unlink-if-regular` primitive, so this cannot be fully closed. The window is minimized by re-checking immediately before `unlink` and by anchoring both operations to the same directory fd.
- **Windows reserved names.** On Windows, names like `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, and `LPT1`–`LPT9` (with or without extension) are rejected, as are names ending in `.` or space, because the OS treats them specially and they can alias other files.

---

## Design Notes

- **Per-path error isolation.** Every path is processed independently; a failure on one path does not abort the batch. All exceptions are mapped to per-path status strings.
- **Defensive `ValueError` handling.** Embedded NUL bytes and similar malformed names are rejected by `_validate_name` before any syscall, and any residual `ValueError` from the OS layer is caught and mapped to `"invalid filename"`.
- **Uniform exception mapping.** The POSIX and fallback paths raise the same internal exceptions (`_SymlinkRefused`, `_NotRegular`, `_OutsideRoot`, `_InvalidName`, `_Unsupported`), so the caller's status mapping is identical across platforms.
- **File-descriptor hygiene.** Every opened fd is closed in a `finally` block, including on the error path.

---

## License

Add your project's license here.