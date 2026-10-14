"""Unit tests for safe_expiry.py. They inject a fake remover and do NOT exercise the real deletion call.
Real deletion is covered by test_integration_real_delete.py (opt-in, disposable temp root only).
Fixtures come from mkdtemp and are left in place; clear them yourself."""
import hashlib, inspect, io, json, os, sys, tempfile, time, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "delete-authority" / "scripts"))
import safe_expiry as S

DAY = 86400
H = lambda b: hashlib.sha256(b).hexdigest()
orig = lambda q: q[len(S.QPREFIX) + 17:]                      # claimed name = prefix + 16 hex + "-" + original name


def sidecar(rep_hash, src_hash, name="a.md"):
    return f"{rep_hash} {name}\nsource {src_hash}\n"


GOOD_REP, GOOD_SRC = H(b"transcript a"), H(b"audio-a")


def make_case(proven=True, hash_ok=True, source_ok=True, one_line=False, sidecar_text=None, extra_files=(), **cfg_over):
    base = Path(tempfile.mkdtemp(prefix="safe_expiry_")).resolve()
    root, rdir, logs = base / "bin", base / "records", base / "logs"
    for d in (root, rdir, logs):
        d.mkdir()
    (root / "a.wav").write_bytes(b"audio-a")
    (rdir / "a.md").write_text("transcript a")
    if proven:
        text = sidecar(GOOD_REP if hash_ok else "0" * 64, GOOD_SRC if source_ok else "1" * 64)
        text = text.split("\n")[0] + "\n" if one_line else text
        (rdir / "a.md.sha256").write_bytes((text if sidecar_text is None else sidecar_text).encode())
    for n in extra_files:
        (root / f"{n}.wav").write_bytes(n.encode())
        (rdir / f"{n}.md").write_text(n)
        (rdir / f"{n}.md.sha256").write_text(sidecar(H(n.encode()), H(n.encode()), f"{n}.md"))
    cfg = {"root": str(root), "replacement_dir": str(rdir), "replacement_ext": ".md", "exts": [".wav"],
           "min_age_days": 14, "max_per_run": 20, "log": str(logs / "run.jsonl")}
    cfg.update(cfg_over)
    cpath = base / "config.json"
    cpath.write_text(json.dumps(cfg))
    cpath.chmod(0o600)
    return base, root, rdir, cpath


def rewrite(cpath, **over):
    cfg = json.loads(cpath.read_text())
    cfg.update(over)
    cpath.write_text(json.dumps(cfg))


def run(cpath, apply=True, days=30, remover=None, after_plan=None, after_check=None, required_uid=None,
        before_claim=None, raw=None, after_claim=None):
    raw = [] if raw is None else raw
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = S.main(["--apply"] if apply else [], remover=remover or (lambda q, fd: raw.append(q)),
                    now=time.time() + days * DAY, after_plan=after_plan, config_path=str(cpath), after_check=after_check,
                    required_uid=os.geteuid() if required_uid is None else required_uid,    # tests cannot make root-owned files
                    before_claim=before_claim, after_claim=after_claim)
    return rc, [orig(q) for q in raw], json.loads(buf.getvalue())


class Selection(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        _, root, _, c = make_case()
        rc, calls, out = run(c, apply=False)
        self.assertEqual((rc, calls, out["listed"]), (0, [], ["a.wav"]))
        self.assertEqual(os.listdir(root), ["a.wav"])

    def test_apply_removes_only_the_proven_old_file(self):
        _, root, _, c = make_case()
        (root / "b.wav").write_bytes(b"no replacement")
        rc, calls, _ = run(c)
        self.assertEqual((rc, calls), (0, ["a.wav"]))

    def test_remover_gets_the_claimed_hidden_name_not_the_original(self):
        _, _, _, c = make_case()
        raw = []
        run(c, raw=raw)
        self.assertTrue(raw[0].startswith(S.QPREFIX) and orig(raw[0]) == "a.wav")

    def test_age_gate_both_ways(self):
        _, _, _, c = make_case()
        rc, calls, out = run(c, days=1)
        self.assertEqual((calls, out["skipped"]["age"]), ([], 1))
        _, _, _, c0 = make_case(min_age_days=0)
        rc, calls, _ = run(c0, days=1)
        self.assertEqual(calls, ["a.wav"])

    def test_missing_or_wrong_proof_fails_closed(self):
        for kw in ({"proven": False}, {"hash_ok": False}, {"source_ok": False}, {"one_line": True}):
            _, _, _, c = make_case(**kw)
            rc, calls, out = run(c)
            self.assertEqual((calls, out["skipped"]["unproven"]), ([], 1), kw)

    def test_the_proof_file_must_have_exactly_the_documented_two_lines(self):
        """Review finding: a sidecar with the right digest but another file name was once accepted."""
        bad = {
            "wrong file name": f"{GOOD_REP} WRONG.md\nsource {GOOD_SRC}\n",
            "extra token on line one": f"{GOOD_REP} a.md extra\nsource {GOOD_SRC}\n",
            "extra third line": f"{GOOD_REP} a.md\nsource {GOOD_SRC}\nmore\n",
            "empty middle line": f"{GOOD_REP} a.md\n\nsource {GOOD_SRC}\n",
            "non-hex digest": f"{'g' * 64} a.md\nsource {GOOD_SRC}\n",
            "uppercase digest": f"{GOOD_REP.upper()} a.md\nsource {GOOD_SRC}\n",
            "short digest": f"{GOOD_REP[:63]} a.md\nsource {GOOD_SRC}\n",
            "windows line endings": f"{GOOD_REP} a.md\r\nsource {GOOD_SRC}\r\n",
            "two spaces": f"{GOOD_REP}  a.md\nsource {GOOD_SRC}\n",
            "source line not hex": f"{GOOD_REP} a.md\nsource {'z' * 64}\n",
            "empty file": "",
        }
        for why, text in bad.items():
            _, _, _, c = make_case(sidecar_text=text)
            rc, calls, out = run(c)
            self.assertEqual((calls, out["skipped"]["unproven"]), ([], 1), why)
        _, _, _, c = make_case(sidecar_text=sidecar(GOOD_REP, GOOD_SRC).rstrip("\n"))      # no final newline is fine
        self.assertEqual(run(c)[1], ["a.wav"])

    def test_a_symlinked_sidecar_is_not_accepted(self):
        base, _, rdir, c = make_case(proven=False)                          # no sidecar in the folder
        real = base / "elsewhere.sha256"
        real.write_text(sidecar(GOOD_REP, GOOD_SRC))                        # a perfectly valid sidecar, elsewhere
        os.symlink(real, rdir / "a.md.sha256")                              # reached only through a symlink
        rc, calls, out = run(c)
        self.assertEqual((calls, out["skipped"]["unproven"]), ([], 1))

    def test_extension_not_on_list_is_skipped(self):
        _, root, _, c = make_case()
        (root / "c.txt").write_text("x")
        rc, calls, out = run(c)
        self.assertEqual((calls, out["skipped"]["type"]), (["a.wav"], 1))

    def test_symlink_and_subfolder_are_never_followed(self):
        base, root, _, c = make_case()
        outside = base / "keep.wav"
        outside.write_bytes(b"outside")
        os.symlink(outside, root / "link.wav")
        (root / "sub").mkdir()
        (root / "sub" / "z.wav").write_bytes(b"z")
        rc, calls, out = run(c)
        self.assertEqual(calls, ["a.wav"])
        self.assertEqual(out["skipped"]["unsafe"], 2)

    def test_earlier_claimed_entries_are_skipped(self):
        _, root, _, c = make_case()
        (root / (S.QPREFIX + "0" * 16 + "-x.wav")).write_bytes(b"left over")
        rc, calls, out = run(c)
        self.assertEqual((calls, out["skipped"]["quarantined"]), (["a.wav"], 1))

    def test_max_per_run_caps_the_batch(self):
        _, _, _, c = make_case(extra_files=("d", "e"), max_per_run=2)
        rc, calls, out = run(c)
        self.assertEqual((len(calls), out["eligible"]), (2, 3))


class Boundary(unittest.TestCase):
    def refused(self, cpath):
        rc, calls, out = run(cpath)
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("refused", out)
        return out["refused"]

    def test_unsafe_roots_are_refused(self):
        for bad in ("/", str(Path.home()), "/tmp", "/etc"):
            _, _, _, c = make_case(root=bad)
            self.refused(c)

    def test_root_must_be_canonical(self):
        base, root, _, _ = make_case()
        os.symlink(root, base / "lnk")
        _, _, _, c = make_case(root=str(base / "lnk"))
        self.assertIn("canonical", self.refused(c))

    def test_replacement_dir_and_log_must_be_outside_root(self):
        _, root, _, c = make_case()
        rewrite(c, replacement_dir=str(root))
        self.refused(c)
        _, root, _, c = make_case()
        rewrite(c, log=str(root / "run.jsonl"))
        self.refused(c)

    def test_group_writable_config_is_refused(self):
        _, _, _, c = make_case()
        c.chmod(0o660)
        self.assertIn("config", self.refused(c))

    def test_a_symlinked_config_is_refused(self):
        base, _, _, c = make_case()
        os.symlink(c, base / "config_link.json")
        self.refused(base / "config_link.json")

    def test_command_line_has_no_way_to_select_a_boundary(self):
        _, _, _, c = make_case()
        for flag in (["--config", str(c)], ["--root", "/"], ["--log", "/tmp/x"], ["--ext", ".txt"], ["--min-age-days", "0"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                S.main(flag, config_path=str(c))

    def test_the_installed_defaults_are_fixed(self):
        params = inspect.signature(S.main).parameters
        self.assertEqual(S.CONFIG_PATH, "/etc/safe_expiry/config.json")
        self.assertEqual(params["config_path"].default, S.CONFIG_PATH)
        self.assertEqual(params["required_uid"].default, 0)                # the config must be root-owned by default

    def test_a_config_owned_by_the_runtime_user_is_refused_by_default(self):
        _, _, _, c = make_case()                                           # mode 0600, owned by the test user, not root
        self.assertNotEqual(os.lstat(c).st_uid, 0)
        rc, calls, out = run(c, required_uid=0)
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("root-owned", out["refused"])

    def test_malformed_extension_lists_are_refused(self):
        for bad in ([], ["wav"], [""], ["*"], [".w av"], ["."], [".wav/../x"]):
            _, _, _, c = make_case(exts=bad)
            self.refused(c)

    def test_non_finite_or_badly_typed_numbers_are_refused(self):
        for bad in (float("nan"), float("inf"), -1, "nan", "14", True, None):
            _, _, _, c = make_case(min_age_days=bad)
            self.refused(c)
        for bad in (True, 2.5):
            _, _, _, c = make_case(max_per_run=bad)
            self.refused(c)

    def test_a_literal_nan_in_the_config_cannot_switch_the_age_gate_off(self):
        _, _, _, c = make_case()
        c.write_text(c.read_text().replace('"min_age_days": 14', '"min_age_days": NaN'))
        self.assertIn("config", self.refused(c))


class Logging(unittest.TestCase):
    def test_log_goes_only_to_the_configured_path_and_records_plan_then_result(self):
        base, _, _, c = make_case()
        run(c)
        lines = [json.loads(x) for x in (base / "logs" / "run.jsonl").read_text().strip().split("\n")]
        self.assertEqual([x["event"] for x in lines], ["plan", "result"])
        self.assertEqual(lines[0]["listed"], ["a.wav"])
        self.assertEqual(lines[1]["deleted"], ["a.wav"])

    def test_a_plan_record_exists_even_if_the_first_deletion_fails(self):
        base, _, _, c = make_case()

        def boom(q, fd):
            raise OSError("disk error")
        rc, _, out = run(c, remover=boom)
        lines = [json.loads(x) for x in (base / "logs" / "run.jsonl").read_text().strip().split("\n")]
        self.assertEqual((rc, [x["event"] for x in lines]), (2, ["plan", "result"]))
        self.assertTrue(lines[1]["error"].startswith("OSError"))

    def test_symlinked_log_is_refused_before_anything_is_deleted(self):
        base, _, _, _ = make_case()
        target = base / "target.jsonl"
        target.write_text("")
        os.symlink(target, base / "logs" / "link.jsonl")
        _, _, _, c = make_case(log=str(base / "logs" / "link.jsonl"))
        rc, calls, _ = run(c)
        self.assertEqual((rc, calls), (2, []))

    def test_an_existing_group_writable_log_is_refused(self):
        base, _, _, _ = make_case()
        log = base / "logs" / "open.jsonl"
        log.write_text("")
        log.chmod(0o666)
        _, _, _, c = make_case(log=str(log))
        rc, calls, out = run(c)
        self.assertEqual((rc, calls), (2, []))


class PlanToApply(unittest.TestCase):
    def test_candidate_replaced_between_plan_and_final_check_is_not_deleted(self):
        base, root, _, c = make_case()

        def swap(_root):
            fresh = base / "fresh.wav"
            fresh.write_bytes(b"a different file with the same name")
            os.replace(fresh, root / "a.wav")                              # new inode, same name
        rc, calls, out = run(c, after_plan=swap)
        self.assertEqual((rc, calls), (2, []))
        self.assertTrue(out["error"].startswith("Changed"))

    def test_candidate_replaced_after_the_final_check_is_claimed_but_never_deleted(self):
        """Review finding: the window between the last check and the delete."""
        base, root, _, c = make_case()

        def swap(fd, name):
            fresh = base / "fresh.wav"
            fresh.write_bytes(b"a different file with the same name")
            os.replace(fresh, root / name)                                 # swapped after the final check
        raw = []
        rc, calls, out = run(c, before_claim=swap, raw=raw)
        self.assertEqual((rc, raw), (2, []))                               # the remover was never called
        self.assertTrue(out["error"].startswith("Changed"))
        held = [n for n in os.listdir(root) if n.startswith(S.QPREFIX)]
        self.assertEqual(len(held), 1)                                     # retained in a claim directory for a human
        self.assertEqual((root / held[0] / S.CLAIMED).read_bytes(), b"a different file with the same name")
        self.assertFalse((root / "a.wav").exists())

    def test_same_inode_rewrite_after_the_final_check_is_caught_by_the_content_hash(self):
        """Review finding: rewrite the source in place with the same length and restore the modification time."""
        _, root, _, c = make_case()
        target = root / "a.wav"

        def rewrite_in_place(fd, name):
            st = os.stat(target)
            with open(target, "r+b") as f:
                f.write(b"AUDIO-X")                                        # same length as b"audio-a"
            os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))          # same size, same mtime, same inode
        raw = []
        rc, calls, out = run(c, before_claim=rewrite_in_place, raw=raw)
        self.assertEqual((rc, raw), (2, []))
        self.assertTrue(out["error"].startswith("Changed"))
        held = [n for n in os.listdir(root) if n.startswith(S.QPREFIX)]
        self.assertEqual((root / held[0] / S.CLAIMED).read_bytes(), b"AUDIO-X")

    def test_the_proof_is_checked_again_after_the_claim_and_before_the_delete(self):
        """Review finding: the proof could stop being true between the last check and the remover."""
        for what in ("replacement moved", "replacement rewritten", "sidecar moved"):
            _, root, rdir, c = make_case()

            def change(fd, qdir, what=what, rdir=rdir):
                if what == "replacement moved":
                    os.rename(rdir / "a.md", rdir / "a.md.moved")
                elif what == "replacement rewritten":
                    (rdir / "a.md").write_text("rewritten after the claim")
                else:
                    os.rename(rdir / "a.md.sha256", rdir / "a.md.sha256.moved")
            raw = []
            rc, calls, out = run(c, after_claim=change, raw=raw)
            self.assertEqual((rc, raw), (2, []), what)                     # remover never called
            self.assertTrue(out["error"].startswith("Changed"), what)      # error reported
            held = [n for n in os.listdir(root) if n.startswith(S.QPREFIX)]
            self.assertEqual(len(held), 1, what)                           # claimed source retained
            self.assertEqual((root / held[0] / S.CLAIMED).read_bytes(), b"audio-a", what)
            self.assertEqual(out["deleted"], [], what)

    def test_a_claim_name_collision_never_overwrites_an_existing_entry(self):
        for kind in ("directory", "file"):
            _, root, _, c = make_case()
            clash = root / (S.QPREFIX + "0" * 16 + "-a.wav")
            if kind == "directory":
                clash.mkdir()
                (clash / S.CLAIMED).write_bytes(b"old claim")
            else:
                clash.write_bytes(b"old claim")
            raw = []
            with mock.patch.object(S.secrets, "token_hex", return_value="0" * 16):
                rc, calls, out = run(c, raw=raw)
            self.assertEqual((rc, raw), (2, []), kind)
            self.assertTrue(out["error"].startswith("Changed"), kind)
            self.assertEqual((clash / S.CLAIMED if kind == "directory" else clash).read_bytes(), b"old claim")
            self.assertEqual((root / "a.wav").read_bytes(), b"audio-a")      # nothing was moved

    def test_replacement_changed_between_plan_and_apply_is_not_deleted(self):
        _, _, rdir, c = make_case()
        rc, calls, out = run(c, after_plan=lambda _r: (rdir / "a.md").write_text("tampered"))
        self.assertEqual((rc, calls), (2, []))

    def test_original_changed_after_the_proof_was_made_is_not_deleted(self):
        _, root, _, c = make_case()
        rc, calls, out = run(c, after_plan=lambda _r: (root / "a.wav").write_bytes(b"audio changed after the transcript"))
        self.assertEqual((rc, calls), (2, []))

    def test_root_swapped_to_a_symlink_after_the_check_is_not_followed(self):
        base, root, _, c = make_case()
        elsewhere = base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "a.wav").write_bytes(b"decoy")

        def swap(_root):
            os.rename(root, base / "bin_moved")
            os.symlink(elsewhere, root)
        rc, calls, out = run(c, after_check=swap)
        self.assertEqual((rc, calls), (2, []))
        self.assertTrue(out["error"])

    def test_root_replaced_by_another_directory_after_the_check_is_refused(self):
        base, root, rdir, c = make_case()

        def swap(_root):
            os.rename(root, base / "bin_moved")
            root.mkdir()
            (root / "a.wav").write_bytes(b"audio-a")                       # same name, different directory
        rc, calls, out = run(c, after_check=swap)
        self.assertEqual((rc, calls), (2, []))
        self.assertTrue(out["error"].startswith("Changed"))

    def test_failure_partway_stops_the_batch_and_reports(self):
        _, _, _, c = make_case(extra_files=("d", "e"))
        seen = []

        def flaky(q, fd):
            seen.append(q)
            if len(seen) == 2:
                raise OSError("disk error")
        rc, _, out = run(c, remover=flaky)
        self.assertEqual((rc, out["deleted"], len(seen)), (2, ["a.wav"], 2))
        self.assertTrue(out["error"].startswith("OSError"))


if __name__ == "__main__":
    unittest.main()
