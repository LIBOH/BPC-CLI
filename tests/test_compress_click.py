"""bpc compress / extract 子命令的集成测试。"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from bpc_cli.cli import cli
from bpc_cli.common import find_7z, run_7z_list


def _create_samples(src: Path) -> dict[str, str]:
    """创建包含普通文件与压缩包文件的测试样本。"""
    samples = {
        "readme.txt": "hello",
        "report.pdf": "%PDF-1.4",
        "data.json": '{"k": 1}',
        "script.py": "print('ok')",
        "image.png": "PNG\x89",
        "image.jpg": "JFIF",
        "sheet.xls": "xls",
        "slide.pptx": "pptx",
        "archive.zip": "zip",
        "video.mp4": "mp4",
        "audio.mp3": "mp3",
        "notes.md": "# notes",
        "multi.dot.name.txt": "multi",
    }
    for name, content in samples.items():
        (src / name).write_text(content, encoding="utf-8")
    (src / "no_extension").write_text("none", encoding="utf-8")
    samples["no_extension"] = "none"
    return samples


def _run_compress(
    runner: CliRunner, src: Path, archive: Path, password: str = "", single: bool = True
):
    cmd = [
        "compress",
        str(src),
        "-o",
        str(archive),
        "-p",
        password,
        "--no-obfuscate-ext",
    ]
    if single:
        cmd.append("--single")
    return runner.invoke(cli, cmd)


def _run_extract(runner: CliRunner, archive: Path, out: Path, password: str = ""):
    # max-depth 0 避免测试样本中的 .zip 被当作嵌套压缩包处理
    return runner.invoke(
        cli,
        [
            "extract",
            str(archive),
            "-o",
            str(out),
            "-p",
            password,
            "--max-depth",
            "0",
        ],
    )


def test_compress_extract_roundtrip(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    expected = _create_samples(src)

    archive = tmp_path / "packed.7z"
    result = _run_compress(runner, src, archive)
    assert result.exit_code == 0, result.output

    # 压缩包中：普通文件保持原扩展名，压缩包文件扩展名被混淆
    sevenz = find_7z()
    listing = run_7z_list(sevenz, archive)
    assert listing.returncode == 0
    listing_text = listing.stdout

    archive_samples = {"archive.zip"}
    for name in expected:
        if name in archive_samples:
            assert name not in listing_text
        elif "." in name and not name.startswith("."):
            assert name in listing_text

    assert ".bpc_extmap" in listing_text

    out = tmp_path / "out"
    result = _run_extract(runner, archive, out)
    assert result.exit_code == 0, result.output

    extract_root = out / "packed" / "src"
    for name, content in expected.items():
        path = extract_root / name
        assert path.exists(), f"missing {path}"
        assert path.read_text(encoding="utf-8") == content


def test_compress_extract_with_password(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "secret.txt").write_text("top secret", encoding="utf-8")

    archive = tmp_path / "secure.7z"
    result = _run_compress(runner, src, archive, password="p@ssw0rd")
    assert result.exit_code == 0, result.output

    out = tmp_path / "out"
    result = _run_extract(runner, archive, out, password="p@ssw0rd")
    assert result.exit_code == 0, result.output
    assert (out / "secure" / "src" / "secret.txt").read_text(
        encoding="utf-8"
    ) == "top secret"


def test_compress_extract_network_transfer(tmp_path: Path) -> None:
    """模拟网络传输/存储后再解压仍能 100% 恢复。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    expected = _create_samples(src)

    archive = tmp_path / "packed.7z"
    result = _run_compress(runner, src, archive)
    assert result.exit_code == 0, result.output

    # 模拟传输：复制到新的位置
    transferred = tmp_path / "remote" / "packed.7z"
    transferred.parent.mkdir(parents=True, exist_ok=True)
    transferred.write_bytes(archive.read_bytes())

    out = tmp_path / "out"
    result = _run_extract(runner, transferred, out)
    assert result.exit_code == 0, result.output

    extract_root = out / "packed" / "src"
    restored_names = {
        p.relative_to(extract_root).as_posix()
        for p in extract_root.rglob("*")
        if p.is_file()
    }
    assert set(expected.keys()) == restored_names


def test_compress_without_explicit_password_uses_empty(tmp_path: Path) -> None:
    """未显式指定 -p 时，默认使用空密码，不应挂起等待输入。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(
        cli,
        ["compress", str(src), "-o", str(archive), "-s", "--no-obfuscate-ext"],
    )
    assert result.exit_code == 0, result.output

    out = tmp_path / "out"
    result = runner.invoke(
        cli, ["extract", str(archive), "-o", str(out), "--max-depth", "0"]
    )
    assert result.exit_code == 0, result.output
    assert (out / "packed" / "src" / "plain.txt").read_text(encoding="utf-8") == "plain"


def test_group_help_shows_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "c" in result.output
    assert "e" in result.output


def test_legacy_subcommand_names_still_work(tmp_path: Path) -> None:
    """旧子命令名 compress / extract 仍可用。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(
        cli, ["compress", str(src), "-o", str(archive), "-s", "--no-obfuscate-ext"]
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    out = tmp_path / "out"
    result = runner.invoke(
        cli, ["extract", str(archive), "-o", str(out), "--max-depth", "0"]
    )
    assert result.exit_code == 0, result.output
    assert (out / "packed" / "src" / "plain.txt").read_text(encoding="utf-8") == "plain"


def test_compress_output_as_directory_uses_input_name(tmp_path: Path) -> None:
    """当 -o 指向已存在目录时，自动使用输入名作为输出文件名。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    out_dir = tmp_path / "archives"
    out_dir.mkdir()
    result = runner.invoke(
        cli, ["compress", str(src), "-o", str(out_dir), "-s", "--no-obfuscate-ext"]
    )
    assert result.exit_code == 0, result.output

    expected_archive = out_dir / "src.7z"
    assert expected_archive.exists(), f"未生成 {expected_archive}"

    out = tmp_path / "out"
    result = _run_extract(runner, expected_archive, out)
    assert result.exit_code == 0, result.output
    assert (out / "src" / "src" / "plain.txt").read_text(encoding="utf-8") == "plain"


def test_compress_default_output_path(tmp_path: Path) -> None:
    """未指定 -o 时，输出文件与输入目录同级且同名。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    result = runner.invoke(cli, ["c", str(src), "-s", "--no-obfuscate-ext"])
    assert result.exit_code == 0, result.output

    expected_archive = tmp_path / "src.7z"
    assert expected_archive.exists(), f"未生成 {expected_archive}"

    out = tmp_path / "out"
    result = _run_extract(runner, expected_archive, out)
    assert result.exit_code == 0, result.output
    assert (out / "src" / "src" / "plain.txt").read_text(encoding="utf-8") == "plain"


def test_compress_default_recursive_compress(tmp_path: Path) -> None:
    """不指定 --single 时默认启用递归压缩。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(
        cli, ["c", str(src), "-o", str(archive), "--no-obfuscate-ext"]
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()

    out = tmp_path / "out"
    result = runner.invoke(
        cli, ["e", str(archive), "-o", str(out), "--max-depth", "10"]
    )
    assert result.exit_code == 0, result.output

    files = [p for p in (out / "packed").rglob("plain.txt")]
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "plain"


def test_compress_single_flag(tmp_path: Path) -> None:
    """--single / -s 显式禁用递归压缩，仅单层压缩。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    for flag in ("--single", "-s"):
        result = runner.invoke(
            cli, ["c", str(src), "-o", str(archive), flag, "--no-obfuscate-ext"]
        )
        assert result.exit_code == 0, result.output
        assert archive.exists()

        sevenz = find_7z()
        listing = run_7z_list(sevenz, archive)
        assert listing.returncode == 0
        # 单层压缩应直接包含源文件
        assert "plain.txt" in listing.stdout


def test_compress_extract_aliases(tmp_path: Path) -> None:
    """c / e 与 compress / extract 子命令别名均可用。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    for compress_name in ("c", "compress"):
        result = runner.invoke(
            cli,
            [compress_name, str(src), "-o", str(archive), "-s", "--no-obfuscate-ext"],
        )
        assert result.exit_code == 0, result.output
        assert archive.exists()

        out = tmp_path / f"out_{compress_name}"
        extract_name = "e" if compress_name == "c" else "extract"
        result = runner.invoke(
            cli,
            [extract_name, str(archive), "-o", str(out), "--max-depth", "0"],
        )
        assert result.exit_code == 0, result.output
        assert (out / "packed" / "src" / "plain.txt").read_text(
            encoding="utf-8"
        ) == "plain"


def test_compress_final_extension_obfuscation_default_single(tmp_path: Path) -> None:
    """默认开启：单层压缩时仅修改最终压缩包后缀，不影响内容与解压。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(cli, ["compress", str(src), "-o", str(archive), "-s"])
    assert result.exit_code == 0, result.output
    assert not archive.exists()

    obfuscated = next(p for p in archive.parent.iterdir() if p.is_file())
    assert obfuscated != archive
    ext = "." + obfuscated.name.rsplit(".", 1)[-1]
    assert ext.lower() not in {".7z", ".zip", ".rar", ".tar.gz"}

    out = tmp_path / "out"
    result = runner.invoke(
        cli, ["extract", str(obfuscated), "-o", str(out), "--max-depth", "0"]
    )
    assert result.exit_code == 0, result.output
    assert (out / "packed" / "src" / "plain.txt").read_text(encoding="utf-8") == "plain"


def test_compress_final_extension_obfuscation_default_recursive(tmp_path: Path) -> None:
    """默认开启：递归压缩时仅修改最终压缩包后缀，不影响内容与解压。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(cli, ["compress", str(src), "-o", str(archive)])
    assert result.exit_code == 0, result.output
    assert not archive.exists()

    obfuscated = next(p for p in archive.parent.iterdir() if p.is_file())
    ext = "." + obfuscated.name.rsplit(".", 1)[-1]
    assert ext.lower() not in {".7z", ".zip", ".rar", ".tar.gz"}

    out = tmp_path / "out"
    result = runner.invoke(
        cli, ["extract", str(obfuscated), "-o", str(out), "--max-depth", "10"]
    )
    assert result.exit_code == 0, result.output

    files = [p for p in (out / "packed").rglob("plain.txt")]
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "plain"


def test_compress_no_obfuscate_ext_opt_out(tmp_path: Path) -> None:
    """--no-obfuscate-ext 可显式关闭最终压缩包后缀混淆。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "plain.txt").write_text("plain", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(
        cli, ["compress", str(src), "-o", str(archive), "-s", "--no-obfuscate-ext"]
    )
    assert result.exit_code == 0, result.output
    assert archive.exists()
