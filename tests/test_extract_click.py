"""bpc extract 子命令的单元与集成测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from bpc_cli.cli import cli


def _make_archive(src: Path, name: str, password: str | None = None) -> Path:
    """用 7z 创建一个测试压缩包。"""
    archive = src / name
    cmd = ["7z", "a", "-y", str(archive), str(src / "file.txt")]
    if password:
        cmd.extend([f"-p{password}", "-mhe=on"])
    subprocess.run(cmd, check=True, capture_output=True)
    return archive


def _make_nested_archive(tmp: Path, password: str | None = None) -> Path:
    """创建 outer.zip -> inner.7z(加密) -> file.txt 的嵌套结构。"""
    inner_src = tmp / "inner_src"
    inner_src.mkdir()
    (inner_src / "file.txt").write_text("deep", encoding="utf-8")
    inner = inner_src / "inner.7z"
    cmd = ["7z", "a", "-y", str(inner), str(inner_src / "file.txt")]
    if password:
        cmd.extend([f"-p{password}", "-mhe=on"])
    subprocess.run(cmd, check=True, capture_output=True)

    outer_src = tmp / "outer_src"
    outer_src.mkdir()
    (outer_src / "file.txt").write_text("top", encoding="utf-8")
    (outer_src / "inner.7z").write_bytes(inner.read_bytes())
    outer = outer_src / "outer.zip"
    subprocess.run(
        [
            "7z",
            "a",
            "-y",
            str(outer),
            str(outer_src / "file.txt"),
            str(outer_src / "inner.7z"),
        ],
        check=True,
        capture_output=True,
    )
    return outer


def _run_extract(runner: CliRunner, args: list[str]) -> None:
    """通过 bpc extract 子命令执行解压。"""
    return runner.invoke(cli, ["extract", *args])


def test_extract_single_archive(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("hello", encoding="utf-8")
    archive = _make_archive(src, "test.zip")
    out = tmp_path / "out"

    result = _run_extract(runner, [str(archive), "-o", str(out), "-p", ""])
    assert result.exit_code == 0, result.output
    assert (out / "test" / "file.txt").read_text(encoding="utf-8") == "hello"


def test_extract_with_password(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("secret", encoding="utf-8")
    archive = _make_archive(src, "test.7z", password="pwd123")
    out = tmp_path / "out"

    result = _run_extract(runner, [str(archive), "-o", str(out), "-p", "pwd123"])
    assert result.exit_code == 0, result.output
    assert (out / "test" / "file.txt").read_text(encoding="utf-8") == "secret"


def test_extract_wrong_password(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("secret", encoding="utf-8")
    archive = _make_archive(src, "test.7z", password="pwd123")
    out = tmp_path / "out"

    result = _run_extract(runner, [str(archive), "-o", str(out), "-p", "wrong"])
    assert result.exit_code != 0
    assert "失败" in result.output or "错误" in result.output


def test_extract_directory_recursive(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("a", encoding="utf-8")
    _make_archive(src, "first.zip")
    (src / "file.txt").write_text("b", encoding="utf-8")
    _make_archive(src, "second.7z")
    out = tmp_path / "out"

    result = _run_extract(runner, [str(src), "-o", str(out), "-p", ""])
    assert result.exit_code == 0, result.output
    assert (out / "first" / "file.txt").read_text(encoding="utf-8") == "a"
    assert (out / "second" / "file.txt").read_text(encoding="utf-8") == "b"


def test_extract_nested_archive(tmp_path: Path) -> None:
    runner = CliRunner()
    archive = _make_nested_archive(tmp_path, password="inner-pwd")
    out = tmp_path / "out"

    result = _run_extract(runner, [str(archive), "-o", str(out), "-p", "inner-pwd"])
    assert result.exit_code == 0, result.output
    assert (out / "outer" / "file.txt").read_text(encoding="utf-8") == "top"
    assert (out / "outer" / "inner" / "file.txt").read_text(encoding="utf-8") == "deep"


def test_extract_nonexistent_input() -> None:
    runner = CliRunner()
    result = _run_extract(runner, ["does-not-exist.zip", "-p", ""])
    assert result.exit_code != 0
    assert "不存在" in result.output or "Invalid" in result.output


def test_no_archives_found(tmp_path: Path) -> None:
    runner = CliRunner()
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_extract(runner, [str(empty), "-p", ""])
    assert result.exit_code == 0
    assert "未找到" in result.output
