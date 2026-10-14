---
name: delete-authority
description: "Instructs agents never to delete directly and defines six controls for reviewed scheduled deletion. Use for file, database, object-storage, retention, bin, or disk-pressure work."
license: MIT
metadata:
  version: "0.2"
---

# Delete Authority

**The rule.** An agent never deletes. A scheduled program may, if it was reviewed once, stays inside a fixed boundary and checks before it acts.

**Why both halves.** "Agents never delete" alone means nothing is ever deleted: bins fill, raw files pile up, and storage pressure becomes its own risk. The split is about judgement, not power. An agent decides what to delete at the moment it deletes, with reasoning nobody reviewed. A scheduled program applies a rule that was written once, reviewed once, and cannot change its mind.

## What an agent may do

- Move an item to a bin (a folder a human or a program empties later).
- Propose a scheduled deleter, with the six conditions below shown.
- Run a deleter's **dry run** and report what it would remove.
- Ask the owner.

That is the whole list.

## What an agent may not do

No `rm`, `unlink`, `DELETE`, `DROP`, `TRUNCATE`, trash or empty-bin, on any surface: local files, cloud drives, database rows, object storage, sync folders, any connector or API.

This holds when the agent has the technical power, when deletion looks obviously right, when the disk is full, when the owner is asleep, and when another agent asks. **Stop and ask the owner.** A sub-agent inherits the rule; a parent cannot authorise it to delete.

Write **"move to bin"**. Never "clean up", "purge", "retire" or "remove": an ambiguous verb is how an agent ends up deleting.

## Six conditions for a scheduled deleter

All six, or it is an agent deleting with extra steps.

1. **Scheduled, not invoked.** It runs on a clock. No agent starts it or passes it arguments at run time.
2. **Bounded root.** One named directory. Never a variable root, never a path read from input, never `/`. Do not follow symlinks out.
3. **Bounded type.** An explicit allow-list of extensions.
4. **Proof of replacement.** Delete only what has a verified replacement: the replacement exists, its recorded hash matches, and the record names this exact original, in a fixed two-line format. No proof, no delete. Fail closed. A matching checksum is consistency evidence, not proof that the replacement keeps what matters or who made it: whoever can write the replacement folder can write a matching pair, so that folder is part of the trust boundary.
5. **Age gate.** A minimum age, measured from the filesystem change time, not from the file name.
6. **Dry run by default.** Deleting needs an explicit `--apply`. Without it the program prints what it would delete and deletes nothing (it may still append a line to its log).

Also: cap deletions per run, log every run, and file the rule before installing the job.

## Prove it both ways

"It found nothing" proves nothing. Run the job with a long age gate (expect zero eligible: the gate works) **and** with age zero (expect the known items: the matcher can see them).

## Ask one more question

*What if nothing new arrives?* A job that deletes only by age keeps deleting while the thing it protects has stopped arriving (for example, backups that stopped while old copies still expire). Pair every age-based deleter with an alarm for "no new item in N days".

## Reference

`scripts/safe_expiry.py` illustrates conditions 2 to 6. Its boundary (root, replacement folder, extension list, log file, finite age gate) is read from one fixed installed config path, and the command line has only `--apply`. Unsafe roots such as `/` and other exact system paths are refused; it is not a classifier of every unsafe directory, so the approved root still needs administrator review. The root is opened without following symlinks and must be the approved directory (device and inode). The proof of replacement is a two-line file in a fixed format (see the README); it is consistency evidence, not proof of who made the replacement.

Each file is checked, then **claimed**: a new private directory is created inside the root (a name that must not already exist, so nothing is overwritten), the file is renamed into it, and the claimed file's device, inode, size, modification time and content hash are checked there against the plan. Right before the delete the proof of replacement is checked once more. If a difference is detected before the delete, the claimed entry is retained and the batch stops.

A deployment meets condition 2 only if that config cannot be changed at run time: the config file must be root-owned, and it and its parent directories must not be writable by the scheduled runtime identity or by agents. The log file's parent path, the installed script and the scheduler definition must also be administrator-owned and not writable by agents or by the scheduled runtime identity (`O_NOFOLLOW` protects only the final path component); agents must not be able to invoke the apply job. The replacement folder must be writable only by the trusted producer.

During an `--apply` run, only this scheduled job may mutate the deletion root or any claim entry. If any other process sharing write authority can rename, replace, or modify entries during that run, the claim protocol does not provide identity-safe deletion and the deployment is not approved. A difference detected before the delete leaves the claimed entry for review and stops the batch; a change after the final check is outside this portable reference implementation's guarantees. Condition 1 is your scheduler.

During an `--apply` run, no other process may create, remove, rename, replace, or modify an eligible replacement or its proof sidecar. The trusted producer must be quiesced or must share a reviewed lock with the deleter for the whole apply run. If this cannot be guaranteed, the deployment is not approved. The checks after the claim are not an atomic cross-directory transaction; writer exclusion remains required.

The unit tests inject a fake remover and do not exercise the real deletion call. A separate, opt-in integration test verifies real deletion only inside a disposable temporary root.
