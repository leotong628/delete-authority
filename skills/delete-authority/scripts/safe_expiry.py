#!/usr/bin/env python3
"""Reference scheduled deleter for the delete-authority skill. Dry run unless --apply.

The ONLY command-line argument is --apply. The boundary is read from one fixed, installed path
(CONFIG_PATH below). There is no argument, and no environment variable, that selects another config,
root, extension list, age gate or log file. The config must be owned by root and not writable by group or
others. The config file and its parent directories, the log file's parent directories, this script and its
scheduler entry must be administrator-owned and not writable by the scheduled runtime identity or by agents
(O_NOFOLLOW protects only the final path component).

During an --apply run, only this scheduled job may mutate the deletion root or any claim entry. If any other
process sharing write authority can rename, replace, or modify entries during that run, the claim protocol does
not provide identity-safe deletion and the deployment is not approved.

During an `--apply` run, no other process may create, remove, rename, replace, or modify an eligible replacement or
its proof sidecar. The trusted producer must be quiesced or must share a reviewed lock with the deleter for the
whole apply run. If this cannot be guaranteed, the deployment is not approved. The checks after the claim are not an
atomic cross-directory transaction; writer exclusion remains required.

Config (JSON): {"root": "/data/audio_bin", "replacement_dir": "/data/transcripts", "replacement_ext": ".md",
                "exts": [".wav", ".m4a"], "min_age_days": 14, "max_per_run": 20, "log": "/var/log/safe_expiry.jsonl"}

Proof of replacement: <replacement_dir>/<name><replacement_ext> and <name><replacement_ext>.sha256 containing
exactly two lines:
    <64 lowercase hex: sha256 of the replacement> <name><replacement_ext>
    source <64 lowercase hex: sha256 of the original file>
This is consistency evidence: the pair matches each other and this exact original. It does not prove who made the
replacement or that it keeps what matters; whoever can write that folder can write a matching pair. Keep the folder
writable only by the trusted producer.

Conditions: 2 bounded root (fixed config, unsafe roots refused, root opened without following symlinks and
              matched to the approved device/inode, top level only)
            3 extension allow-list   4 proof of replacement (above)
            5 age gate from ctime (finite number only)   6 dry run unless --apply
Condition 1 (scheduled, not invoked) is your scheduler. Each file is claimed before it is deleted: a new private
directory (mode 0700, a name that must not exist yet) is created inside the root, the file is renamed into it, and
the claimed file's device, inode, size, modification time and content hash are checked there against the plan. If a
difference is detected before the delete, the claimed entry is retained and the batch stops.
A dry run deletes nothing; it still appends a line to the log.
"""
import argparse, hashlib, json, math, os, re, secrets, stat, sys, time
from pathlib import Path

CONFIG_PATH = "/etc/safe_expiry/config.json"        # fixed at install time; tests pass config_path to main()
QPREFIX = ".deleting-"                               # claim directory: QPREFIX + 16 hex + "-" + original name
CLAIMED = "claimed"                                  # the file's name inside its claim directory
EXT = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
LINE1 = re.compile(r"([0-9a-f]{64}) (.+)")
LINE2 = re.compile(r"source ([0-9a-f]{64})")
SENTINELS = {"/", "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/opt", "/home", "/Users", "/root", "/System",
             "/Library", "/private", "/tmp", "/dev", "/proc", "/sys", "/mnt", "/media", "/boot", "/srv"}


class ConfigError(Exception):
    pass


class Changed(Exception):
    pass


def _reject_constant(_name):
    raise ValueError("NaN and Infinity are not allowed")


def sha256_fd(fd):
    h = hashlib.sha256()
    with os.fdopen(os.dup(fd), "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def open_regular(name, dir_fd):
    """Open a regular file by name inside a directory descriptor, never following a symlink."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return None, None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None, None
    return fd, st


def load_config(path, required_uid=0):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)             # one descriptor: the checks and the read agree
    except OSError:
        raise ConfigError("config not readable")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o022 or st.st_uid != required_uid:
            raise ConfigError("config must be a root-owned regular file, not writable by group or others")
        raw = os.read(fd, 1 << 20)
    finally:
        os.close(fd)
    try:
        c = json.loads(raw.decode(), parse_constant=_reject_constant)
        exts = [e.lower() for e in c["exts"]]
        age, cap = c["min_age_days"], c["max_per_run"]
        if isinstance(age, bool) or not isinstance(age, (int, float)) or isinstance(cap, bool) or not isinstance(cap, int):
            raise ValueError("wrong type")
        cfg = {"root": c["root"], "replacement_dir": c["replacement_dir"], "replacement_ext": c["replacement_ext"],
               "exts": exts, "min_age_days": float(age), "max_per_run": cap, "log": c["log"]}
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
        raise ConfigError("config is not valid JSON with all required keys")
    if not exts or not all(isinstance(e, str) and EXT.match(e) for e in exts) or not EXT.match(str(cfg["replacement_ext"])):
        raise ConfigError("exts and replacement_ext must be non-empty lists of plain extensions like .wav")
    if not math.isfinite(cfg["min_age_days"]) or cfg["min_age_days"] < 0 or not 1 <= cfg["max_per_run"] <= 1000:
        raise ConfigError("min_age_days must be a finite number >= 0 and max_per_run between 1 and 1000")
    for k in ("root", "replacement_dir", "log"):
        if not isinstance(cfg[k], str) or not os.path.isabs(cfg[k]):
            raise ConfigError(k + " must be an absolute path")
    return cfg


def check_boundary(cfg):
    """Approve the root. Returns (path, (device, inode)); run() must open exactly that directory.
    It refuses exact system paths and very shallow paths. It is not a classifier of every unsafe directory:
    the approved root still needs administrator review."""
    p = Path(cfg["root"])
    try:
        real = p.resolve(strict=True)
    except OSError:
        raise ConfigError("root does not exist")
    if real != p:
        raise ConfigError("root is not canonical (symlink or '..' in the path)")
    if not real.is_dir() or str(real) in SENTINELS or real == Path.home().resolve() or len(real.parts) < 3:
        raise ConfigError("unsafe root refused")
    for other in (Path(cfg["replacement_dir"]).resolve(), Path(cfg["log"]).resolve()):
        if other == real or real in other.parents:
            raise ConfigError("replacement_dir and log must be outside the root")
    st = os.stat(real)
    return real, (st.st_dev, st.st_ino)


def replacement_ok(stem, original_sha, cfg, rfd):
    """Condition 4. Descriptor-bound, no symlinks, exactly two strictly formatted lines. Fail closed."""
    ext = cfg["replacement_ext"]
    sfd = rep = None
    try:
        sfd, sst = open_regular(stem + ext + ".sha256", rfd)
        if sfd is None or sst.st_mode & 0o022 or sst.st_size > 4096:
            return False
        text = os.read(sfd, 4096).decode()
        lines = (text[:-1] if text.endswith("\n") else text).split("\n")
        if len(lines) != 2:
            return False
        m1, m2 = LINE1.fullmatch(lines[0]), LINE2.fullmatch(lines[1])
        if not m1 or not m2 or m1.group(2) != stem + ext:
            return False
        rep, rst = open_regular(stem + ext, rfd)
        if rep is None or rst.st_mode & 0o022:
            return False
        return sha256_fd(rep) == m1.group(1) and m2.group(1) == original_sha
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    finally:
        for f in (sfd, rep):
            if f is not None:
                os.close(f)


def inspect(cfg, fd, rfd, name, now):
    """Returns (reason_to_skip or None, plan identity). Used for the plan AND again right before claiming."""
    if name.startswith(QPREFIX):
        return "quarantined", None
    ofd, st = open_regular(name, fd)
    if ofd is None:
        return "unsafe", None
    try:
        if Path(name).suffix.lower() not in cfg["exts"]:
            return "type", None
        if (now - st.st_ctime) / 86400 < cfg["min_age_days"]:
            return "age", None
        original_sha = sha256_fd(ofd)
        if not replacement_ok(Path(name).stem, original_sha, cfg, rfd):
            return "unproven", None
        return None, (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, original_sha)
    finally:
        os.close(ofd)


def claim(fd, name, ident):
    """Move the file into a brand-new private directory and check it THERE. Never overwrites, never deletes.
    ident = (device, inode, size, mtime_ns, ctime_ns, original sha256). The rename changes ctime, so ctime is not compared;
    the content hash is."""
    qdir = QPREFIX + secrets.token_hex(8) + "-" + name
    try:
        os.mkdir(qdir, 0o700, dir_fd=fd)                       # atomic: fails if anything has this name
    except FileExistsError:
        raise Changed("claim name already exists, nothing was moved")
    qfd = os.open(qdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        os.rename(name, CLAIMED, src_dir_fd=fd, dst_dir_fd=qfd)    # the destination directory is new and empty
        cfd, st = open_regular(CLAIMED, qfd)
        if cfd is None:
            raise Changed("claimed entry is not a regular file, retained in " + qdir)
        try:
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != ident[:4] or sha256_fd(cfd) != ident[5]:
                raise Changed("claimed entry differs from the planned file, retained in " + qdir)
        finally:
            os.close(cfd)
    finally:
        os.close(qfd)
    return qdir


def default_remover(qdir, fd):
    """Delete the claimed file by name inside its claim directory, then the empty claim directory."""
    qfd = os.open(qdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        os.unlink(CLAIMED, dir_fd=qfd)
    finally:
        os.close(qfd)
    os.rmdir(qdir, dir_fd=fd)


def run(cfg, root, approved, logfd, apply=False, remover=None, now=None, after_plan=None, before_claim=None,
        after_claim=None):
    now = time.time() if now is None else now
    remover = remover or default_remover
    out = {"mode": "apply" if apply else "dry-run", "scanned": 0, "eligible": 0, "listed": [], "deleted": [],
           "skipped": {"unsafe": 0, "type": 0, "age": 0, "unproven": 0, "quarantined": 0}, "error": None}
    fd = rfd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)   # never follow a symlink at the root
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != approved:                              # must be the directory that was approved
            raise Changed("root")
        rfd = os.open(cfg["replacement_dir"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        plan = []
        for name in sorted(os.listdir(fd)):
            out["scanned"] += 1
            why, ident = inspect(cfg, fd, rfd, name, now)
            if why:
                out["skipped"][why] += 1
            else:
                plan.append((name, ident))
        out["eligible"] = len(plan)
        batch = plan[: cfg["max_per_run"]]
        out["listed"] = [n for n, _ in batch]
        if after_plan:
            after_plan(root)
        if apply:
            os.write(logfd, (json.dumps({"event": "plan", "at": time.time(), "listed": out["listed"]}) + "\n").encode())
            for name, ident in batch:
                why, again = inspect(cfg, fd, rfd, name, now)
                if why or again != ident:
                    raise Changed(name)
                if before_claim:
                    before_claim(fd, name)
                qdir = claim(fd, name, ident)
                if after_claim:
                    after_claim(fd, qdir)
                if not replacement_ok(Path(name).stem, ident[5], cfg, rfd):    # the proof must still hold right now
                    raise Changed("replacement proof no longer valid after the claim, claimed entry retained in " + qdir)
                remover(qdir, fd)
                out["deleted"].append(name)
    except Exception as e:                                                 # fail closed: stop the batch, report
        out["error"] = type(e).__name__ + (": " + str(e) if isinstance(e, Changed) else "")
    finally:
        for f in (fd, rfd):
            if f is not None:
                os.close(f)
    return out


def open_log(path):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_mode & 0o022 or st.st_uid != os.geteuid():
        os.close(fd)
        raise ConfigError("log must be a regular file owned by the job, not writable by group or others")
    return fd


def main(argv=None, remover=None, now=None, after_plan=None, config_path=CONFIG_PATH, after_check=None,
         required_uid=0, before_claim=None, after_claim=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really delete (condition 6)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(config_path, required_uid)
        root, approved = check_boundary(cfg)
        logfd = open_log(cfg["log"])
    except (ConfigError, OSError) as e:
        print(json.dumps({"refused": str(e) if isinstance(e, ConfigError) else "config or log not usable"}))
        return 2
    if after_check:
        after_check(root)
    out = run(cfg, root, approved, logfd, args.apply, remover, now, after_plan, before_claim, after_claim)
    os.write(logfd, (json.dumps({"event": "result", "at": time.time(), **out}) + "\n").encode())
    os.close(logfd)
    print(json.dumps(out))
    return 2 if out["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
