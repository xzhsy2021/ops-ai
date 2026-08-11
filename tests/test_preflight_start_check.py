from pathlib import Path

from scripts.preflight_start_check import _writable_dir


def test_writable_dir_does_not_reuse_stale_probe_file(tmp_path, monkeypatch):
    target = tmp_path / "keys"
    target.mkdir()
    stale_probe = target / ".ops_preflight_write_test"
    stale_probe.write_text("old", encoding="utf-8")

    original_write_text = Path.write_text

    def fail_for_fixed_probe(self, *args, **kwargs):
        if self.name == stale_probe.name:
            raise PermissionError("stale probe is not writable")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_for_fixed_probe)

    ok, error = _writable_dir(target)

    assert ok is True
    assert error == ""
    assert stale_probe.read_text(encoding="utf-8") == "old"
