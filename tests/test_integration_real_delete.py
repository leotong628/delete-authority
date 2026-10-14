"""Contained integration test for the REAL deletion call (claim into a private directory, then delete inside it).

It is skipped unless you opt in, because it really deletes files. It deletes only inside a disposable
temporary root that it creates itself, and it asserts that everything else is left alone.

    RUN_REAL_DELETE_TEST=1 python3 -m unittest -v tests/test_integration_real_delete.py
"""
import hashlib, io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "delete-authority" / "scripts"))
import safe_expiry as S


@unittest.skipUnless(os.environ.get("RUN_REAL_DELETE_TEST") == "1", "opt-in: set RUN_REAL_DELETE_TEST=1")
class RealDelete(unittest.TestCase):
    def test_only_the_proven_old_file_is_deleted_inside_the_temp_root(self):
        # required_uid=geteuid() because a test cannot create a root-owned config; production uses the default 0
        base = Path(tempfile.mkdtemp(prefix="safe_expiry_real_")).resolve()
        root, rdir, logs, outside = base / "bin", base / "records", base / "logs", base / "outside"
        for d in (root, rdir, logs, outside):
            d.mkdir()
        (root / "gone.wav").write_bytes(b"audio")                           # eligible
        (root / "nopeer.wav").write_bytes(b"no replacement")                 # not proven
        (root / "other.txt").write_text("not on the list")                   # wrong type
        (outside / "keep.wav").write_bytes(b"outside the root")
        (rdir / "gone.md").write_text("transcript")
        (rdir / "gone.md.sha256").write_text(
            hashlib.sha256(b"transcript").hexdigest() + " gone.md\nsource " + hashlib.sha256(b"audio").hexdigest() + "\n")
        cfg = {"root": str(root), "replacement_dir": str(rdir), "replacement_ext": ".md", "exts": [".wav"],
               "min_age_days": 0, "max_per_run": 10, "log": str(logs / "run.jsonl")}
        c = base / "config.json"
        c.write_text(json.dumps(cfg))
        c.chmod(0o600)
        with redirect_stdout(io.StringIO()):
            rc = S.main(["--apply"], config_path=str(c), required_uid=os.geteuid())   # default remover = real os.unlink
        self.assertEqual(rc, 0)
        self.assertFalse((root / "gone.wav").exists())
        self.assertEqual([n for n in os.listdir(root) if n.startswith(S.QPREFIX)], [])    # no claimed entry left behind
        for kept in (root / "nopeer.wav", root / "other.txt", outside / "keep.wav", rdir / "gone.md"):
            self.assertTrue(kept.exists(), kept)


if __name__ == "__main__":
    unittest.main()
