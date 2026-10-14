# delete-authority

An Agent Skill that instructs an AI agent never to delete anything directly, and describes when a reviewed, scheduled program may.

> **An agent never deletes. A scheduled program may, if it was reviewed once, stays inside a fixed boundary and checks before it acts.**

A skill is instructions in a file. It guides an agent; it does not enforce anything. The enforcement is your permissions and your scheduled job.

## Why

"Agents never delete" on its own means nothing is ever deleted: bins fill up, raw files pile up, and storage pressure becomes its own risk. The fix is to split the decision. An agent decides what to delete at the moment it deletes, using reasoning nobody reviewed. A scheduled program applies a rule that was written once, reviewed once, and cannot change its mind.

## What the skill tells an agent

- **May:** move things to a bin, propose a scheduled deleter, run its dry run, ask the owner.
- **May not:** `rm`, `unlink`, `DELETE`, `DROP`, `TRUNCATE`, trash or empty-bin, on any surface, even with the power to do so. Sub-agents inherit the rule.
- Say **"move to bin"**, never "clean up" or "purge".

## Six conditions for a scheduled deleter

1. Scheduled, not invoked. 2. Bounded root. 3. Bounded type. 4. Proof of replacement (exists **and** hash matches; fail closed). 5. Age gate from change time. 6. Dry run by default; `--apply` to delete.

## Install

```
npx skills add leotong628/delete-authority
```

Or copy `skills/delete-authority/` into `~/.claude/skills/` (or `.claude/skills/` in a project). For Claude.ai, zip the `delete-authority` folder and upload it.

`npx` runs a separately fetched third-party command that this project has not audited. Prefer copying the files from a tagged release. Installing this skill does not authorize a scheduler or `--apply`; that is a separate administrator decision.

## Reference script

`skills/delete-authority/scripts/safe_expiry.py` illustrates conditions 2 to 6 in about 300 lines of standard-library Python. The boundary lives in one config file at a fixed installed path (`/etc/safe_expiry/config.json`, the constant `CONFIG_PATH` in the script). The command line has only `--apply`: no argument or environment variable selects another config, root, extension list, age gate or log.

```
{"root": "/data/audio_bin", "replacement_dir": "/data/transcripts", "replacement_ext": ".md",
 "exts": [".wav", ".m4a"], "min_age_days": 14, "max_per_run": 20, "log": "/var/log/safe_expiry.jsonl"}
```

Proof of replacement: `<replacement_dir>/<name><replacement_ext>` plus `<name><replacement_ext>.sha256` with exactly two lines:

```
<64 lowercase hex: sha256 of the replacement> <name><replacement_ext>
source <64 lowercase hex: sha256 of the original file>
```

This is consistency evidence: the pair matches each other and this exact original. It does not prove who made the replacement or that it keeps what matters; whoever can write that folder can write a matching pair, so keep it writable only by the trusted producer.

```
python3 safe_expiry.py            # dry run
```

Human/scheduler installation test only — never agent-run: `python3 safe_expiry.py --apply`

What it does: it refuses `/` and other exact system paths, a root that is a symlink, a config that is not owned by root or that group or others can write, a non-finite or negative age gate, and a log or replacement folder inside the root. A file is eligible only if it sits directly in the root (symlinks and sub-folders are skipped), its extension is on the list, it is old enough, and its proof matches. Right before each deletion it checks all of that again. It then **claims** the file: it creates a new private directory inside the root (mode 0700, a name that must not exist yet, so nothing is ever overwritten), renames the file into it, and checks the claimed file's device, inode, size, modification time and content hash there against the plan, and checks the proof of replacement one last time. Only then does it delete the file and the empty claim directory. If a difference is detected before the delete, the claimed entry is retained for a human and the batch stops. It opens the root without following symlinks and requires it to be the very directory (device and inode) that was approved. It writes a plan line to the log before deleting and a result line after; a dry run deletes nothing but still appends a log line.

What you must still provide: A deployment meets condition 2 only if that config cannot be changed at run time: the config file must be root-owned, and it and its parent directories must not be writable by the scheduled runtime identity or by agents. The log file's parent path, the installed script and the scheduler definition must also be administrator-owned and not writable by agents or by the scheduled runtime identity (`O_NOFOLLOW` protects only the final path component); agents must not be able to invoke the apply job. The replacement folder must be writable only by the trusted producer.

During an `--apply` run, only this scheduled job may mutate the deletion root or any claim entry. If any other process sharing write authority can rename, replace, or modify entries during that run, the claim protocol does not provide identity-safe deletion and the deployment is not approved. A difference detected before the delete leaves the claimed entry for review and stops the batch; a change after the final check is outside this portable reference implementation's guarantees.

During an `--apply` run, no other process may create, remove, rename, replace, or modify an eligible replacement or its proof sidecar. The trusted producer must be quiesced or must share a reviewed lock with the deleter for the whole apply run. If this cannot be guaranteed, the deployment is not approved. The checks after the claim are not an atomic cross-directory transaction; writer exclusion remains required.

## Tests

```
python3 -m unittest -v tests/test_safe_expiry.py
```

The unit tests inject a fake remover and do not exercise the real deletion call. A separate contained integration test verifies real deletion only inside a disposable temporary root; it is skipped unless you opt in:

```
RUN_REAL_DELETE_TEST=1 python3 -m unittest -v tests/test_integration_real_delete.py
```

Test folders are left behind in your temp directory; clear them yourself.

## How this was made

Designed and directed by Leo Tong, written with AI agents (Claude), reviewed before publication, and covered by the tests above. Licence: MIT.
