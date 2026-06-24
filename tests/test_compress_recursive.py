"""递归压缩/解压功能测试。"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from bpc_cli.cli import cli


def _create_source(src: Path) -> dict[str, str]:
    samples = {
        "readme.txt": "hello",
        "data.json": '{"k": 1}',
        "sub/nested.txt": "nested content",
        "sub/deep/still_here.txt": "deep content",
    }
    for name, content in samples.items():
        path = src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return samples


def _run_compress(runner: CliRunner, src: Path, archive: Path, *extra: str):
    return runner.invoke(
        cli,
        ["c", str(src), "-o", str(archive), "--no-obfuscate-ext", *extra],
    )


def _run_extract(runner: CliRunner, archive: Path, out: Path, *extra: str):
    return runner.invoke(
        cli,
        ["e", str(archive), "-o", str(out), *extra],
    )


def _find_file(root: Path, name: str) -> Path | None:
    for path in root.rglob(name):
        if path.is_file():
            return path
    return None


def _assert_restored(out: Path, archive_stem: str, expected: dict[str, str]) -> None:
    """校验解压后的文件结构与内容。"""
    for name, content in expected.items():
        found = _find_file(out / archive_stem, name)
        assert found is not None, f"未找到文件 {name}"
        assert found.read_text(encoding="utf-8") == content, f"文件 {name} 内容不匹配"


def test_recursive_compress_default_depth(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    expected = _create_source(src)

    archive = tmp_path / "recursive.7z"
    result = _run_compress(runner, src, archive)
    assert result.exit_code == 0, result.output

    # 最终输出必须是单个压缩包
    assert archive.exists()
    assert archive.is_file()

    out = tmp_path / "out"
    result = _run_extract(runner, archive, out, "--max-depth", "10")
    assert result.exit_code == 0, result.output
    _assert_restored(out, "recursive", expected)


def test_recursive_compress_explicit_levels(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    expected = _create_source(src)

    for levels in (3, 4):
        archive = tmp_path / f"level{levels}.7z"
        result = _run_compress(
            runner,
            src,
            archive,
            "--recursive-levels",
            str(levels),
        )
        assert result.exit_code == 0, result.output
        assert archive.exists()

        out = tmp_path / f"out{levels}"
        result = _run_extract(runner, archive, out, "--max-depth", "10")
        assert result.exit_code == 0, result.output
        _assert_restored(out, f"level{levels}", expected)


def test_recursive_compress_with_volume_size(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    expected = _create_source(src)
    # 增大源数据量，确保某些层会产生分卷
    (src / "big.bin").write_bytes(b"X" * 10000)
    expected["big.bin"] = ""

    archive = tmp_path / "recursive_vol.7z"
    result = _run_compress(
        runner,
        src,
        archive,
        "--recursive-levels",
        "3",
        "--volume-size",
        "1K",
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    out = tmp_path / "out"
    result = _run_extract(runner, archive, out, "--max-depth", "10")
    assert result.exit_code == 0, result.output

    # 校验 big.bin 内容
    root = out / "recursive_vol"
    big_files = [p for p in root.rglob("big.bin")]
    assert len(big_files) == 1
    assert big_files[0].read_bytes() == b"X" * 10000


def test_recursive_compress_password_applied_uniformly(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "secret.txt").write_text("top secret", encoding="utf-8")

    archive = tmp_path / "recursive_pw.7z"
    result = _run_compress(
        runner,
        src,
        archive,
        "--recursive-levels",
        "3",
        "-p",
        "pw123",
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    # 错误密码应解压失败
    out_bad = tmp_path / "out_bad"
    result = _run_extract(runner, archive, out_bad, "-p", "wrong", "--max-depth", "10")
    assert result.exit_code != 0

    # 正确密码完整恢复
    out = tmp_path / "out"
    result = _run_extract(runner, archive, out, "-p", "pw123", "--max-depth", "10")
    assert result.exit_code == 0, result.output

    secret_files = [p for p in (out / "recursive_pw").rglob("secret.txt")]
    assert len(secret_files) == 1
    assert secret_files[0].read_text(encoding="utf-8") == "top secret"


def test_recursive_compress_no_password_uses_empty(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "recursive_empty.7z"
    result = _run_compress(
        runner,
        src,
        archive,
        "--recursive-levels",
        "3",
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    out = tmp_path / "out"
    result = _run_extract(runner, archive, out, "--max-depth", "10")
    assert result.exit_code == 0, result.output

    plain_files = [p for p in (out / "recursive_empty").rglob("plain.txt")]
    assert len(plain_files) == 1
    assert plain_files[0].read_text(encoding="utf-8") == "plain"


def test_recursive_compress_uses_non_standard_extensions(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    expected = _create_source(src)

    archive = tmp_path / "recursive_ext.7z"
    result = _run_compress(
        runner,
        src,
        archive,
        "--recursive-levels",
        "3",
        "-v",
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    out = tmp_path / "out"
    result = _run_extract(runner, archive, out, "--max-depth", "10")
    assert result.exit_code == 0, result.output
    _assert_restored(out, "recursive_ext", expected)


def test_recursive_compress_verbose_report(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    _create_source(src)

    archive = tmp_path / "recursive_report.7z"
    result = _run_compress(
        runner,
        src,
        archive,
        "--recursive-levels",
        "4",
        "-v",
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    output = result.output
    assert "递归压缩过程报告" in output
    assert "原始文件总大小" in output
    assert "最终文件大小" in output
    assert "大小变化比例" in output
    for i in range(1, 5):
        assert f"第 {i} 层" in output
