# secure_delete

A defensive, race-resistant file deletion utility for Python.

`secure_delete` removes regular files beneath a trusted root directory while
resisting symlink attacks, path-traversal attempts, and stat/unlink
time-of-check-to-time-of-use (TOCTOU) races. It is designed for scenarios where
the contents of a directory may be attacker-controlled — for example, cleaning
up user-uploaded files, cache directories, or sandboxed workspaces.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Python API](#python-api)
  - [Command Line](#command-line)
- [Return Values](#return-values)
- [Security Model](#security-model)
- [Platform Support](#platform-support)
- [Examples](#examples)
- [Limitations](#limitations)
- [License](#license)

---

## Overview

Deleting a file safely is harder than calling `os.unlink`. Between the moment
you check that a path is safe and the moment you delete it, an attacker with
write access to the directory can swap a regular file for a symlink, replace a
directory with a symlink, or otherwise redirect your deletion outside the
intended root.

`secure_delete` mitigates these risks by:

- Refusing to follow symlinks at **any** path component.
- Opening every directory with `O_NOFOLLOW | O_DIRECTORY`.
- Opening the target file with `O_NOFOLLOW` and verifying its inode before
  unlinking.
- On Linux, unlinking via `/proc/self/fd/<fd>` so the deletion is bound to the
  exact inode that was opened.
- Failing closed on platforms that lack the required primitives.

---

## Features

- **Path traversal protection** — rejects absolute paths, drive-qualified
  paths, and any component that is empty, `.`, or `..`.
- **Symlink rejection** — never follows symlinks, whether at the root, at an
  intermediate directory, or at the final component.
- **TOCTOU mitigation** — compares `(st_dev, st_ino)` between `lstat` and
  `fstat` of the opened file descriptor, and again immediately before unlink.
- **Type restriction** — only deletes regular files; directories, devices,
  FIFOs, and sockets are refused.
- **Per-path results** — returns a dictionary mapping each input path to a
  status string, so a single failure does not abort the batch.
- **Fail-closed design** — if the platform lacks `O_NOFOLLOW`, `O_DIRECTORY`,
  `dir_fd`, or `follow_symlinks` support, every path is reported as `'error'`.
- **Zero dependencies** — standard library only.

---

## Requirements

- Python 3.6 or newer (uses `os.supports_dir_fd`, `os.supports_follow_symlinks`,
  and `dir_fd=` keyword arguments).
- A POSIX-like platform that provides:
  - `os.O_NOFOLLOW`
  - `os.O_DIRECTORY`
  - `dir_fd` support for `os.open`, `os.stat`, and `os.unlink`
  - `follow_symlinks` support for `os.stat`

Linux is the primary supported platform and receives the strongest guarantee
(via `/proc/self/fd`). Other POSIX systems (e.g. macOS, BSD) are supported with
a slightly weaker but still hardened fallback.

---

## Installation

Copy `secure_delete.py` into your project, or install it as a module:

```bash
pip install .
```

There are no third-party dependencies.

---

## Usage

### Python API

```python
from secure_delete import secure_delete

results = secure_delete('/var/app/uploads', [
    'user1/avatar.png',
    'user2/report.pdf',
    'user3/../etc/passwd',   # rejected
])

for path, status in results.items():
    print(f'{path}: {status}')
```

The `root` argument is the trusted base directory. Every path in `paths` is
interpreted as **relative to** `root`. Absolute paths are rejected.

### Command Line

```bash
python secure_delete.py ROOT PATH [PATH ...]
```

Example:

```bash
$ python secure_delete.py /var/app/uploads user1/avatar.png user2/report.pdf
user1/avatar.png: deleted
user2/report.pdf: deleted
```

Exit codes:

| Code | Meaning                                    |
|------|--------------------------------------------|
| `0`  | All paths processed (see per-path output). |
| `2`  | Incorrect usage (missing arguments).       |

---

## Return Values

`secure_delete()` returns a `dict` mapping each input path to one of the
following status strings:

| Status              | Meaning                                                                 |
|---------------------|-------------------------------------------------------------------------|
| `'deleted'`         | The file was successfully removed.                                      |
| `'error'`           | The path was rejected, the file was missing, or an I/O error occurred.  |
| `'race_detected'`   | The entry changed between `stat` and `unlink`; deletion was aborted.    |
| `'symlink_rejected'`| The final component is a symlink; it was not followed or deleted.       |

---

## Security Model

`secure_delete` is built around the following invariants:

1. **No path escapes the root.** Paths are split into components and validated
   before any filesystem operation. Absolute paths, drive letters, UNC
   prefixes, and `.`/`..` components are rejected outright.

2. **No symlink is ever followed.** The root directory, every intermediate
   directory, and the final component are all opened with `O_NOFOLLOW`. A
   symlink swap at any level causes the operation to fail rather than redirect.

3. **The deleted inode is the inspected inode.** The final component is opened
   with `O_NOFOLLOW` and its `(st_dev, st_ino)` is compared against the
   preceding `lstat`. On Linux, the unlink is performed through
   `/proc/self/fd/<fd>`, binding the deletion to the opened inode rather than
   to the path.

4. **Only regular files are deleted.** Directories, block/character devices,
   FIFOs, and sockets are refused.

5. **Fail closed.** If the platform cannot provide the primitives required for
   the above guarantees, no deletion is attempted.

### What this does *not* protect against

- **Concurrent writers with `CAP_DAC_OVERRIDE` or root privileges.** If an
  attacker can modify the directory concurrently with the deletion, the
  portable fallback path still contains a narrow TOCTOU window. On Linux the
  `/proc/self/fd` path closes this window; on other platforms it is narrowed
  but not eliminated.
- **Hard links.** A hard link to a file outside the root will be deleted if it
  is reachable via a path inside the root. This is inherent to how hard links
  work and is not considered an escape.
- **Filesystem-level attacks** (e.g. FUSE filesystems that lie about inodes).

---

## Platform Support

| Platform | Support | Notes                                                       |
|----------|---------|-------------------------------------------------------------|
| Linux    | Full    | Uses `O_PATH` and `/proc/self/fd` for the strongest guarantee. |
| macOS    | Partial | Uses `O_RDONLY` fallback; TOCTOU window narrowed, not closed. |
| BSD      | Partial | Same as macOS.                                              |
| Windows  | None    | Fails closed; every path returns `'error'`.                 |

---

## Examples

### Basic batch deletion

```python
from secure_delete import secure_delete

results = secure_delete('/srv/data', ['a.txt', 'b.txt', 'c.txt'])
assert results == {
    'a.txt': 'deleted',
    'b.txt': 'deleted',
    'c.txt': 'deleted',
}
```

### Rejecting traversal attempts

```python
results = secure_delete('/srv/data', [
    '../etc/passwd',
    '/etc/passwd',
    'a/../../b',
    'a//b',
])
# All four entries map to 'error'.
```

### Rejecting symlinks

Given `/srv/data/link -> /etc/passwd`:

```python
results = secure_delete('/srv/data', ['link'])
# {'link': 'symlink_rejected'}
```

### Detecting a race

If a file is replaced between the initial `lstat` and the `unlink`, the result
is `'race_detected'` and the file is left untouched.

---

## Limitations

- **POSIX only.** Windows is not supported; the function fails closed.
- **No recursive deletion.** `secure_delete` deletes individual files. To
  remove a directory tree, walk it yourself and pass each file path.
- **No "unlink this exact inode" primitive on non-Linux systems.** POSIX does
  not provide one; the portable fallback narrows but does not eliminate the
  stat/unlink race.
- **Requires write permission on the parent directory**, not on the file
  itself, as with any `unlink`.

---

## License

This code is provided as-is for use in your own projects. Add your preferred
license header before distribution.
