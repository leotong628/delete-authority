# Changelog

All notable changes to `delete-authority`. Versions follow semantic versioning (`MAJOR.MINOR.PATCH`).

## 0.2.0
Changes after two independent security reviews of 0.1.0 (the first public release).
- `safe_expiry.py`: each file is **claimed** before it is deleted: a new private directory (mode 0700, a name that must not exist, so nothing is overwritten) is created inside the root, the file is renamed into it, and its device, inode, size, modification time and content hash are checked there against the plan. A difference detected before the delete retains the claimed entry and stops the batch.
- `safe_expiry.py`: the proof of replacement is checked again after the claim and right before the delete; if it no longer holds, nothing is deleted, the claimed entry is retained and the batch stops.
- `safe_expiry.py`: `min_age_days` must be a finite number; `NaN`, infinity, booleans and strings are refused, and a literal `NaN` in the JSON is rejected.
- `safe_expiry.py`: the proof of replacement is a strict two-line file: `<64 lowercase hex> <name><replacement_ext>` then `source <64 lowercase hex>` naming the original's hash; the file name is enforced; replacement and sidecar files are opened without following symlinks and must not be group- or world-writable.
- `safe_expiry.py`: the config is checked and read through one file descriptor; the log must be a regular file owned by the job and not writable by group or others; a plan line is written before deleting and a result line after.
- Documentation: a matching checksum is described as consistency evidence, not proof of provenance; deployment requirements for the root, the replacement folder, the log's parent path, the script and the scheduler are stated, together with the single-writer condition for an apply run; a dry run is described as "deletes nothing" (it still appends a log line); the short description no longer says "prevents".
- README: an install note about the third-party `npx` command.
- Tests: 37 (36 run, 1 opt-in real-delete test), including same-inode rewrite, proof-file format, claim-name collision, a changed proof after the claim, and the earlier race cases.

## 0.1.0
First public release.
