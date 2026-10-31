# safe_delete_files

A defensive, pure-Python utility for deleting files with optional root-directory containment enforcement.

## Overview

`safe_delete_files` deletes a batch of file paths while:

- **Skipping directories** (never recurses, never removes directories).
- **Tolerating missing paths** (reports `not_found` instead of raising).
- **Optionally enforcing a root boundary** so that only paths inside a trusted directory tree can be deleted.
- **Mitigating TOCTOU races** on POSIX using `dir_fd` + `O_NOFOLLOW` and inode re-validation.
- **Never aborting a batch** — every input path yields a status string.

The function is designed for scenarios where you have a list of candidate paths (e.g., from a manifest, cache index, or user input) and want to remove them safely without risking deletion outside a designated directory.

## Installation

This module is a single file with no third-party dependencies. Copy it into your project or import it directly:

```python
from safe_delete_files import safe_delete_files
```

Requires Python 3.6+ (uses `os.fspath`, `os.PathLike`).

## Quick Start

```python
from safe_delete_files import safe_delete_files

results = safe_delete_files([
    "/tmp/cache/a.bin",
    "/tmp/cache/missing.bin",
    "/tmp/cache/subdir",       # a directory — skipped
], root="/tmp/cache")

for path, status in results.items():
    print(f"{path}: {status}")
```

Output:

```
/tmp/cache/a.bin: deleted
/tmp/cache/missing.bin: not_found
/tmp/cache/subdir: is_directory
```

## API

### `safe_delete_files(file_paths, root=None) -> dict`

Delete each path in `file_paths`, returning a mapping from each input path to a status string.

#### Parameters

| Name         | Type                                   | Description |
|--------------|----------------------------------------|-------------|
| `file_paths` | `Iterable[str \| bytes \| os.PathLike]` | Paths to attempt to delete. |
| `root`       | `str \| bytes \| os.PathLike \| None`   | Optional containment root. If provided, only paths whose parent directory and entry location resolve inside `root` are deleted. |

#### Return value

A `dict` mapping each input path to one of:

| Status                | Meaning |
|-----------------------|---------|
| `'deleted'`           | The file was successfully unlinked. |
| `'not_found'`         | The path did not exist at the time of the check. |
| `'is_directory'`      | The path refers to a directory and was skipped. |
| `'error: <reason>'`   | The operation failed or was rejected. Common reasons: `invalid path`, `path outside root`, `reparse point`, or an OS error message. |

The function never raises for individual path failures; unexpected exceptions are caught defensively and reported as `'error: ...'`.

## Behavior Details

### Containment semantics

When `root` is provided, the boundary check is performed on:

1. The **resolved parent directory** of the target path.
2. The **final path component**, resolved *without* following it.

This matches `unlink` semantics: a symlink located inside `root` that points outside `root` is still safely deleted, because only the link itself is removed — not its target.

The final component must be a real entry name. Paths ending in `.` or `..` are rejected with `'error: invalid path'`, since they do not denote a removable directory entry.

### Symlinks

Symlinks are **unlinked, not followed**. This is intentional and matches POSIX `unlink` behavior. Callers should be aware that a symlink inside `root` pointing elsewhere will have the link removed, not the target.

### Platform guarantees

#### POSIX (with `O_DIRECTORY` and `O_NOFOLLOW`)

- The parent directory is opened with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`, preventing a symlinked parent from redirecting the operation.
- After `os.open`, the opened directory's `(st_dev, st_ino)` is compared against the validated parent to detect a swap between the containment check and the open.
- The final component is re-validated against `root` using the opened directory's resolved identity.
- `os.unlink(name, dir_fd=dfd)` performs the removal relative to the opened directory.

#### Windows / fallback

- The path is re-`lstat`'d immediately before `unlink`.
- Reparse points that are not symlinks (e.g., junctions) are rejected. If `st_file_attributes` is unavailable, the operation fails closed with `'error: reparse point'`.
- Containment is re-checked immediately before `os.unlink`.

This fallback is **best-effort** and not race-free.

### Input normalization

- `str`, `bytes`, and `os.PathLike` inputs are accepted.
- For containment checks, both `path` and `root` are normalized to `str` via `os.fsdecode` so mixed `str`/`bytes` inputs are handled consistently.
- `bytes` paths are rejected on Windows, where `os.fsdecode` may raise `UnicodeDecodeError` for arbitrary bytes.
- Empty paths and paths containing NUL bytes are rejected with `'error: invalid path'`.

## Limitations

This is a **pure-Python** utility and does not provide atomic guarantees:

- **TOCTOU**: A file may be replaced or removed between the `lstat`/`stat` and the `unlink` call. On POSIX the parent-directory race is mitigated via `dir_fd` + `O_NOFOLLOW` and inode re-validation, but the final component can still be swapped.
- **No secure deletion**: Files are unlinked, not overwritten. The filesystem may retain data.
- **No recursion**: Directories are reported as `is_directory` and never traversed.
- **Errors are reported, not raised**: Permission errors and other `OSError`s are returned as status strings.

## Examples

### Basic cleanup with containment

```python
from safe_delete_files import safe_delete_files

results = safe_delete_files(
    ["/var/app/cache/x.tmp", "/etc/passwd"],
    root="/var/app/cache",
)
# {
#   '/var/app/cache/x.tmp': 'deleted',
#   '/etc/passwd': 'error: path outside root',
# }
```

### Without a root (unrestricted, still skips directories)

```python
results = safe_delete_files(["/tmp/a", "/tmp/b"])
```

### Handling `bytes` paths

```python
results = safe_delete_files([b"/tmp/a.bin"], root=b"/tmp")
```

### Inspecting failures

```python
for path, status in safe_delete_files(paths, root=root).items():
    if status.startswith("error:"):
        print(f"failed to delete {path!r}: {status}")
```

## Design Notes

- **Fail closed**: When a safety attribute is unavailable (e.g., `st_file_attributes` on Windows), the operation is rejected rather than proceeding.
- **Batch resilience**: A single problematic path never aborts the batch.
- **Explicit status vocabulary**: Callers can distinguish "already gone" (`not_found`) from "refused" (`error: ...`) from "skipped" (`is_directory`).

## License

Add your project's license here.