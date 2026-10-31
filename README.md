# safe_delete_files

A small, security-conscious Python utility for deleting files while refusing to follow symlinks or touch anything that is not a regular file.

---

## Overview

`safe_delete_files` deletes a batch of files and returns a per-path status report. It is designed for situations where the set of paths to delete may be influenced by untrusted input (logs, manifests, user-supplied lists) and where following a symlink, opening a FIFO, or unlinking a directory would be a security or correctness problem.

The implementation uses `O_NOFOLLOW`, `O_DIRECTORY`, `dir_fd`-relative `unlink`, and an inode identity re-check to close the common TOCTOU windows that naive `os.remove`-based code leaves open.

## Features

- **Never follows symlinks.** A symlink at the target path is reported as `not_file` and left untouched.
- **Only deletes regular files.** Directories, FIFOs, sockets, and device nodes are rejected.
- **TOCTOU-resistant.** The file is opened with `O_NOFOLLOW`, its inode is captured via `fstat`, and the unlink is performed relative to a directory file descriptor after re-verifying the inode identity.
- **Per-file error reporting.** Permission errors and other failures are returned in the result dict rather than raised, so one bad path does not abort the batch.
- **Dry-run mode.** Pass `dry_run=True` to report what would be deleted without unlinking anything.
- **No pre-checks.** `os.path.lexists` / `os.path.islink` are deliberately avoided — they are racy and add no security.

## Requirements

- Python 3.3+ (uses `dir_fd`-relative `os.stat` and `os.unlink`).
- POSIX platform. `O_NOFOLLOW`, `O_DIRECTORY`, and `dir_fd` support are not available on Windows.

## Installation

Copy the module into your project, or save it as `safe_delete.py` and import it:

```python
from safe_delete import safe_delete_files
```

## Usage

### Basic

```python
from safe_delete import safe_delete_files

results = safe_delete_files([
    '/var/tmp/report.csv',
    '/var/tmp/old.log',
    '/var/tmp/symlink-to-etc-passwd',   # will be skipped
    '/var/tmp/does-not-exist',
])

for path, status in results.items():
    print(f'{path}: {status}')
```

Example output:

```
/var/tmp/report.csv: deleted
/var/tmp/old.log: deleted
/var/tmp/symlink-to-etc-passwd: not_file
/var/tmp/does-not-exist: missing
```

### Dry run

```python
results = safe_delete_files(paths, dry_run=True)
# Every regular file that would be deleted is reported as 'deleted';
# nothing is actually unlinked.
```

### Handling results programmatically

```python
results = safe_delete_files(paths)

deleted  = [p for p, s in results.items() if s == 'deleted']
missing  = [p for p, s in results.items() if s == 'missing']
skipped  = [p for p, s in results.items() if s == 'not_file']
failed   = {p: s for p, s in results.items() if s.startswith('error:')}
```

## API

### `safe_delete_files(file_paths, *, dry_run=False)`

Delete the given files, skipping anything that is not a regular file.

**Parameters**

| Name         | Type                | Description                                                                 |
|--------------|---------------------|-----------------------------------------------------------------------------|
| `file_paths` | iterable of `str`   | Paths to delete. Must not be a bare string or `None`.                       |
| `dry_run`    | `bool` (keyword)    | If `True`, report what would be deleted without unlinking. Default `False`. |

**Returns**

A `dict` mapping each input path to a status string:

| Status         | Meaning                                                                 |
|----------------|-------------------------------------------------------------------------|
| `'deleted'`    | File was removed (or would be, in dry-run mode).                        |
| `'missing'`    | Path does not exist.                                                    |
| `'not_file'`   | Path exists but is not a regular file (directory, symlink, FIFO, etc.). |
| `'error: ...'` | Deletion failed; the message describes the underlying `OSError`.        |

**Raises**

- `TypeError` if `file_paths` is a `str`, `bytes`, or `None`.

## Security model

The function is built around three defensive layers:

1. **`O_NOFOLLOW` on open.** The target path is opened with `O_RDONLY | O_NOFOLLOW`. If the final component is a symlink, the open fails with `ELOOP` and the path is reported as `not_file`. The resulting file descriptor is then checked with `fstat` to confirm `S_ISREG`.

2. **`O_NOFOLLOW | O_DIRECTORY` on the parent.** The parent directory is opened with `O_NOFOLLOW | O_DIRECTORY`, so the final component of the parent path cannot be a symlink either.

3. **Inode identity re-check before unlink.** Before calling `os.unlink(name, dir_fd=dir_fd)`, the code re-stats the name relative to the parent directory fd and compares `(st_dev, st_ino)` against the values captured from the original `fstat`. If they differ, the path is reported as `not_file` and nothing is unlinked. This closes the window between the initial open and the unlink.

### Known limitations

- **Intermediate directory components are resolved by the kernel.** Only the final component of the parent path is `O_NOFOLLOW`-guarded. The inode identity check on the target file catches substitution via symlinked intermediates, but the parent directory itself is not pinned by an open fd from the start of the operation.
- **No cross-process locking.** Concurrent deleters may race; the inode check reduces but does not eliminate the resulting ambiguity.
- **Permission errors are surfaced per-file**, not raised.
- **POSIX only.** The `dir_fd` and `O_NOFOLLOW` semantics used here are not available on Windows.

## Internal helpers

The module exposes three private helpers used by `safe_delete_files`. They are documented here for maintainers; they are not part of the public API.

- `_open_regular_nofollow(path)` — Opens `path` with `O_NOFOLLOW` and verifies via `fstat` that it is a regular file. Returns `(fd, None)` on success or `(None, status)` on failure.
- `_split_path(path)` — Normalizes `path` and splits it into `(parent, basename)` for `dir_fd`-relative operations. Returns `(None, None)` if the path has no usable basename.
- `_open_parent_dir(parent)` — Opens the parent directory with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`.

## Testing recommendations

When writing tests for this module, cover at least:

- Regular file → `deleted`.
- Nonexistent path → `missing`.
- Symlink to a regular file → `not_file`, target untouched.
- Directory → `not_file`.
- FIFO → `not_file`.
- Path with no basename (`'/'`, `'.'`, `'foo/'`) → `not_file` or `missing`.
- `dry_run=True` → `deleted` reported, file still present.
- `file_paths` passed as a bare string → `TypeError`.
- Permission-denied on unlink → `error: ...` in the result dict.

## License

Add your project's license here.