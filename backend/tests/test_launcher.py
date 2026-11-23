from app.launcher import frontend_hash


def test_frontend_hash_changes_with_sources_only(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.tsx").write_text("a", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    first = frontend_hash(tmp_path)
    assert frontend_hash(tmp_path) == first
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("built", encoding="utf-8")  # output, ignored
    assert frontend_hash(tmp_path) == first
    (tmp_path / "src" / "main.tsx").write_text("b", encoding="utf-8")
    assert frontend_hash(tmp_path) != first
