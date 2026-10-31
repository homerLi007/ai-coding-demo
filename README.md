# `safe_delete_files` — Symlink-Safe, Root-Confined File Deletion

A small, dependency-free Python utility for deleting files **without ever following symlinks** and **without ever escaping a caller-supplied root directory**.

The implementation is designed around two hard guarantees:

1. **Confinement** — every operation is performed relative to a directory file descriptor opened from the filesystem root, walking each path component with `O_NOFOLLOW | O_DIRECTORY`. No symlink in any component (including intermediate components of `root` itself) can redirect the operation outside the intended tree.
2. **No TOCTOU window** — the final `unlink` is performed relative to the parent directory fd, so there is no gap between the confinement check and the destructive operation.

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
  - [`safe_delete_files(file_paths, root)`](#safe_delete_filesfile_paths-root)
- [Return Values](#return-values)
- [Path Semantics](#path-semantics)
- [Security Model](#security-model)
- [Limitations](#limitations)
- [Platform Support](#platform-support)
- [Examples](#examples)
- [Internal Helpers](#internal-helpers)

---

## Features

- **Symlink-safe traversal** — every directory component is opened with `O_NOFOLLOW | O_DIRECTORY`.
- **Root confinement** — paths are resolved lexically against `root`; anything outside is rejected.
- **No `..` smuggling** — `..` components are rejected outright rather than collapsed by `normpath`.
- **No TOCTOU race** — `unlink` is performed via `dir_fd` on the parent directory.
- **Per-path error isolation** — a failure on one path never aborts the batch.
- **Deterministic result codes** — every input path maps to a stable string result.
- **Pure standard library** — no third-party dependencies.

---

## Installation

Copy the module into your project (e.g. as `safe_delete.py`). It requires **Python 3.3+** on a POSIX platform that supports `O_NOFOLLOW`, `O_DIRECTORY`, and `dir_fd` arguments to `os.open` / `os.unlink` (Linux, macOS, and other Unix-likes).

---

## Quick Start

```python
from safe_delete import safe_delete_files

results = safe_delete_files(
    ["logs/app.log", "logs/old.log", "/var/data/tmp/cache.bin"],
    root="/var/data",
)

for path, status in results.items():
    print(f"{path}: {status}")
```

---

## API Reference

### `safe_delete_files(file_paths, root)`

Delete each file in `file_paths`, confined to `root`.

#### Parameters

| Name         | Type                | Description                                                                 |
|--------------|---------------------|-----------------------------------------------------------------------------|
| `file_paths` | `Iterable[str]`     | Paths to delete. Each may be absolute (must lie under `root`) or relative (interpreted relative to `root`, **not** the process CWD). |
| `root`       | `str`               | Absolute path to the confinement root. Must not contain `..` or symlinks in any component. |

#### Returns

A `dict` mapping each input path to a status string. See [Return Values](#return-values).

#### Behavior

- The `root` directory is opened by walking it component-by-component from `/` using `O_NOFOLLOW | O_DIRECTORY`. If any component is a symlink, a non-directory, or missing, **every** path in the batch receives the corresponding error status.
- Each path is resolved lexically against `root`. Absolute paths must be lexically under `root`; relative paths are joined to `root`.
- Each intermediate directory component is opened with `O_NOFOLLOW | O_DIRECTORY` relative to the previous fd.
- The final component is unlinked with `os.unlink(name, dir_fd=parent_fd)`.

---

## Return Values

Each path maps to exactly one of the following strings:

| Status                  | Meaning                                                                 |
|-------------------------|-------------------------------------------------------------------------|
| `"deleted"`             | The file was successfully unlinked.                                     |
| `"missing"`             | The file (or an intermediate directory) does not exist.                 |
| `"not_a_file"`          | The target is a directory, or an intermediate component is not a directory, or a symlink was encountered where a directory was expected. |
| `"error: outside root"` | The path is not lexically under `root`, contains `..`, or is otherwise invalid. |
| `"error: <reason>"`     | Any other OS-level error (permission denied, I/O error, etc.).          |

---

## Path Semantics

- **Absolute paths** must be lexically under `root`. For example, with `root="/var/data"`, the path `/var/data/logs/a.log` is accepted; `/var/data/../etc/passwd` is rejected.
- **Relative paths** are interpreted relative to `root`, **not** the process working directory. With `root="/var/data"`, the path `"logs/a.log"` refers to `/var/data/logs/a.log`.
- **`..` components** are rejected outright in both `root` and `path`. They are never collapsed by `normpath`, so traversal cannot be smuggled through lexical normalization.
- **Empty components** and `.` components are silently dropped.
- **NUL bytes and control characters** are rejected.

---

## Security Model

The module is built to defend against the following classes of attack:

### 1. Symlink redirection of `root`

A naive implementation might call `os.chdir(root)` or `os.open(root)` and trust that `root` is a real directory. If an attacker can replace an intermediate component of `root` with a symlink (e.g. `/safe/root` → `/etc`), the operation would escape.

**Mitigation:** `_open_root_nofollow` walks `root` component-by-component starting from `/`, opening each with `O_NOFOLLOW | O_DIRECTORY`. Any symlink in any component causes the whole batch to fail with `"error: root contains symlink"`.

### 2. Symlink redirection of intermediate path components

If an attacker can replace a subdirectory of `root` with a symlink pointing outside the tree, a naive `os.unlink(os.path.join(root, path))` would follow it.

**Mitigation:** `_safe_unlink_at` opens each intermediate component with `O_NOFOLLOW | O_DIRECTORY` relative to the previous fd. A symlink at any intermediate component yields `"not_a_file"`.

### 3. TOCTOU between check and unlink

A naive implementation might `stat` the target to confirm it is a regular file, then `unlink` it. Between those two calls, an attacker can swap the target.

**Mitigation:** The final `unlink` is performed directly via `dir_fd` on the parent directory. The `lstat` used to classify the entry is **not** used to gate the unlink — it only affects the reported status. `os.unlink` does not follow symlinks, so a symlink at the final component is removed as a link (its target is untouched).

### 4. Traversal via `..`

`os.path.normpath` would collapse `..` lexically, which can be surprising when combined with symlinks. This module rejects `..` outright.

---

## Limitations

This is a **pure-Python** utility with a deliberately narrow scope:

- **No secure erasure.** `unlink` removes the directory entry; the underlying data may persist on disk until overwritten by the filesystem.
- **No privilege elevation.** Permission errors are reported, not retried.
- **Best-effort "regular file only" contract at the final component.** A symlink or other non-regular entry that appears at the final name will be unlinked as itself. This is intentional — it avoids a `stat`-then-`unlink` race.
- **No recursive deletion.** Only individual files are unlinked; directories are reported as `"not_a_file"`.
- **No cross-platform fallback.** See below.

---

## Platform Support

This module requires:

- `os.O_NOFOLLOW`
- `os.O_DIRECTORY`
- `dir_fd` support in `os.open`, `os.stat`, and `os.unlink`

These are available on Linux, macOS, and most other POSIX systems. **Windows is not supported** — callers on Windows should use a platform-specific implementation.

---

## Examples

### Basic batch deletion

```python
results = safe_delete_files(
    ["a.txt", "b.txt", "c.txt"],
    root="/srv/data",
)
# {'a.txt': 'deleted', 'b.txt': 'deleted', 'c.txt': 'missing'}
```

### Absolute paths under root

```python
results = safe_delete_files(
    ["/srv/data/logs/app.log"],
    root="/srv/data",
)
# {'/srv/data/logs/app.log': 'deleted'}
```

### Rejected traversal

```python
results = safe_delete_files(
    ["../etc/passwd", "/etc/passwd", "logs/../../etc/passwd"],
    root="/srv/data",
)
# {
#   '../etc/passwd': 'error: outside root',
#   '/etc/passwd': 'error: outside root',
#   'logs/../../etc/passwd': 'error: outside root',
# }
```

### Symlink in root

```python
# /srv/data is a symlink to /etc
results = safe_delete_files(["passwd"], root="/srv/data")
# {'passwd': 'error: root contains symlink'}
```

### Symlink at final component

```python
# /srv/data/link -> /etc/passwd
results = safe_delete_files(["link"], root="/srv/data")
# {'link': 'deleted'}  # the link is removed; /etc/passwd is untouched
```

### Directory target

```python
results = safe_delete_files(["logs"], root="/srv/data")
# {'logs': 'not_a_file'}
```

---

## Internal Helpers

The public API is a thin wrapper around a set of small, individually-testable helpers. They are documented here for maintainers; they are not part of the stable public interface.

| Helper | Purpose |
|--------|---------|
| `_open_dir_nofollow(path, dir_fd=None)` | Open a directory with `O_NOFOLLOW \| O_DIRECTORY`, optionally relative to `dir_fd`. |
| `_split_components(path)` | Split an absolute path into components, rejecting `..`, NULs, and control characters. |
| `_open_root_nofollow(root)` | Walk `root` from `/` component-by-component, refusing symlinks in any component. |
| `_safe_unlink_at(root_fd, rel_parts)` | Unlink a file identified by `rel_parts` under `root_fd`, refusing symlinks in intermediate components. |
| `_path_relative_to_root(root, path)` | Express `path` as a component list relative to `root`, rejecting anything outside. |

---

## License

Add your license here.