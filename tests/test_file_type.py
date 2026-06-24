"""file_type 模块与 extract 后缀修复功能的测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from bpc_cli.cli import cli
from bpc_cli.file_type import (
    _current_archive_ext,
    _is_obfuscated_name,
    _looks_like_obfuscated_volume,
    detect_archive_type,
    repair_archive_extension,
)


# ---------- 真实压缩包样本创建 ----------


def _make_zip_archive(src: Path) -> Path:
    """用 7z 创建一个 zip 测试压缩包。"""
    archive = src.with_suffix(".zip")
    subprocess.run(
        ["7z", "a", "-y", "-tzip", str(archive), str(src)],
        check=True,
        capture_output=True,
    )
    return archive


def _make_7z_archive(src: Path) -> Path:
    """用 7z 创建一个 7z 测试压缩包。"""
    archive = src.with_suffix(".7z")
    subprocess.run(
        ["7z", "a", "-y", "-t7z", str(archive), str(src)],
        check=True,
        capture_output=True,
    )
    return archive


# ---------- detect_archive_type 单元测试 ----------


def test_detect_zip_from_header(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_zip_archive(src)

    # 将正确后缀改为错误后缀
    wrong = archive.with_name("archive.dat")
    archive.rename(wrong)

    assert detect_archive_type(wrong) == ".zip"


def test_detect_7z_from_header(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_7z_archive(src)

    wrong = archive.with_name("data.bin")
    archive.rename(wrong)

    assert detect_archive_type(wrong) == ".7z"


def test_detect_unknown_file_returns_none(tmp_path: Path) -> None:
    txt = tmp_path / "plain.txt"
    txt.write_text("not an archive", encoding="utf-8")
    assert detect_archive_type(txt) is None


def test_detect_empty_file_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert detect_archive_type(empty) is None


def test_detect_tiny_file_returns_none(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny"
    tiny.write_bytes(b"PK")
    assert detect_archive_type(tiny) is None


# ---------- repair_archive_extension 单元测试 ----------


def test_repair_wrong_extension_to_zip(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_zip_archive(src)

    wrong = archive.with_name("wrong_ext.txt")
    archive.rename(wrong)

    repaired, status = repair_archive_extension(wrong)
    assert status == "repaired"
    assert repaired.name == "wrong_ext.zip"
    assert repaired.exists()


def test_repair_correct_extension_returns_ok(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_zip_archive(src)

    repaired, status = repair_archive_extension(archive)
    assert status == "ok"
    assert repaired == archive


def test_repair_unknown_file_returns_unknown(tmp_path: Path) -> None:
    txt = tmp_path / "plain.txt"
    txt.write_text("not an archive", encoding="utf-8")

    repaired, status = repair_archive_extension(txt)
    assert status == "unknown"
    assert repaired == txt


def test_repair_skips_obfuscated_name(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_zip_archive(src)

    # 模拟本项目混淆命名但扩展名不匹配真实类型：stem.token.<压缩后缀>
    obfuscated = archive.with_name("data.a1b2.7z")
    archive.rename(obfuscated)

    repaired, status = repair_archive_extension(obfuscated)
    assert status == "skipped_obfuscated"
    assert repaired == obfuscated


def test_repair_resolves_compound_extension(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    # 创建一个 .tar.gz 压缩包
    tar_gz = tmp_path / "bundle.tar.gz"
    subprocess.run(
        ["7z", "a", "-y", "-tgzip", str(tar_gz), str(src)],
        check=True,
        capture_output=True,
    )

    wrong = tar_gz.with_name("bundle.tar.dat")
    tar_gz.rename(wrong)

    repaired, status = repair_archive_extension(wrong)
    assert status == "repaired"
    # 因为 stem 以 .tar 结尾，应恢复为 .tar.gz
    assert repaired.name == "bundle.tar.gz"


def test_repair_conflict_generates_unique_name(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_zip_archive(src)

    wrong = archive.with_name("wrong_ext.txt")
    archive.rename(wrong)

    # 预先创建目标文件，触发冲突重命名
    (tmp_path / "wrong_ext.zip").write_text("occupied", encoding="utf-8")

    repaired, status = repair_archive_extension(wrong)
    assert status == "repaired"
    assert repaired.name.startswith("wrong_ext_repaired")
    assert repaired.suffix == ".zip"


# ---------- 辅助函数测试 ----------


def test_current_archive_ext(tmp_path: Path) -> None:
    assert _current_archive_ext("data.zip") == ".zip"
    assert _current_archive_ext("data.7z.001") == ".7z"
    assert _current_archive_ext("plain.txt") is None


def test_is_obfuscated_name() -> None:
    assert _is_obfuscated_name("data.a1b2.zip") is True
    assert _is_obfuscated_name("data.1234.7z") is True
    assert _is_obfuscated_name("archive.zip") is False
    assert _is_obfuscated_name("data.a1b2.txt") is False


def test_looks_like_obfuscated_volume() -> None:
    assert _looks_like_obfuscated_volume("packed.xxx.001") is True
    assert _looks_like_obfuscated_volume("packed.7z.001") is False
    assert _looks_like_obfuscated_volume("packed.zip") is False
    assert _looks_like_obfuscated_volume("packed.001") is False


def test_repair_skips_obfuscated_volume(tmp_path: Path) -> None:
    """最终后缀混淆后的分卷文件不应被修复，避免破坏卷组。"""
    src = tmp_path / "hello.txt"
    src.write_text("hello", encoding="utf-8")
    archive = _make_7z_archive(src)

    obfuscated_vol = archive.with_name("packed.xxx.001")
    archive.rename(obfuscated_vol)

    repaired, status = repair_archive_extension(obfuscated_vol)
    assert status == "skipped_obfuscated"
    assert repaired == obfuscated_vol


# ---------- extract 集成测试 ----------


def _run_extract(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["extract", *args])


def test_extract_auto_repair_wrong_extension(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("hello", encoding="utf-8")

    archive = _make_zip_archive(src / "file.txt")
    wrong = archive.with_name("packed.dat")
    archive.rename(wrong)

    out = tmp_path / "out"
    result = _run_extract(runner, [str(wrong), "-o", str(out), "-p", ""])
    assert result.exit_code == 0, result.output
    assert (out / "packed" / "file.txt").read_text(encoding="utf-8") == "hello"


def test_extract_repair_logs_to_file(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("hello", encoding="utf-8")

    archive = _make_zip_archive(src / "file.txt")
    wrong = archive.with_name("packed.dat")
    archive.rename(wrong)

    out = tmp_path / "out"
    log = tmp_path / "repair.log"
    result = _run_extract(
        runner,
        [str(wrong), "-o", str(out), "-p", "", "--repair-log", str(log)],
    )
    assert result.exit_code == 0, result.output
    assert log.exists()
    log_text = log.read_text(encoding="utf-8")
    assert "修复后缀" in log_text or "repaired" in log_text or "已修复后缀" in log_text


def test_extract_repair_nested_archive(tmp_path: Path) -> None:
    """嵌套压缩包具有错误后缀时也能被修复并解压。"""
    runner = CliRunner()

    inner_src = tmp_path / "inner_src"
    inner_src.mkdir()
    (inner_src / "deep.txt").write_text("deep", encoding="utf-8")
    inner = _make_zip_archive(inner_src / "deep.txt")
    inner_wrong = inner.with_name("inner.dat")
    inner.rename(inner_wrong)

    outer_src = tmp_path / "outer_src"
    outer_src.mkdir()
    (outer_src / "top.txt").write_text("top", encoding="utf-8")
    (outer_src / "inner.dat").write_bytes(inner_wrong.read_bytes())
    outer = _make_zip_archive(outer_src / "top.txt")
    # 将 outer 内的 top.txt 与 inner.dat 一起重新打包成 outer.zip
    subprocess.run(
        [
            "7z",
            "a",
            "-y",
            "-tzip",
            str(outer),
            str(outer_src / "top.txt"),
            str(outer_src / "inner.dat"),
        ],
        check=True,
        capture_output=True,
    )

    out = tmp_path / "out"
    result = _run_extract(runner, [str(outer), "-o", str(out), "-p", ""])
    assert result.exit_code == 0, result.output
    # 调试：列出所有解压出的文件
    all_files = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    print("extracted files:", all_files)
    assert (out / "top" / "top.txt").read_text(encoding="utf-8") == "top"
    deep_file = out / "top" / "inner" / "deep.txt"
    assert deep_file.exists(), f"missing {deep_file}; files: {all_files}"
    assert deep_file.read_text(encoding="utf-8") == "deep"
