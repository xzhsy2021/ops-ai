from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = [ROOT / "app", ROOT / "config_manager.py", ROOT / "main.py", ROOT / "scripts"]
ALLOWED = {
    ROOT / "app" / "db" / "migrations" / "runner.py",
}
FORBIDDEN = (
    "config_kv",
    "ConfigKV",
    "ConfigRepository",
    "load_config(",
    "save_config(",
    "load_config_cached(",
    "app.config.repository",
    "app.config.cache",
)


def test_runtime_has_no_generic_config_kv_dependencies():
    violations = []
    for root in RUNTIME_ROOTS:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if path in ALLOWED:
                continue
            content = path.read_text(encoding="utf-8")
            hits = [token for token in FORBIDDEN if token in content]
            if hits:
                violations.append(f"{path.relative_to(ROOT)}: {', '.join(hits)}")

    assert violations == [], "\n" + "\n".join(violations)
