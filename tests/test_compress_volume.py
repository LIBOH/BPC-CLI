"""分卷压缩/解压功能测试。"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from bpc_cli.cli import cli
from bpc_cli.common import collect_archives, get_volume_group


def _run_compress(runner: CliRunner, src: Path, archive: Path, *extra: str):
    return runner.invoke(
        cli,
        ["compress", str(src), "-o", str(archive), "-s", "--no-obfuscate-ext", *extra],
    )


def _run_extract(runner: CliRunner, archive: Path, out: Path, *extra: str):
    return runner.invoke(
        cli,
        ["extract", str(archive), "-o", str(out), *extra],
    )


def _find_file(root: Path, name: str) -> Path | None:
    for path in root.rglob(name):
        if path.is_file():
            return path
    return None


def test_compress_single_file_to_volumes(tmp_path: Path) -> None:
    import random

    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    random.seed(42)
    data = bytes(random.randint(0, 255) for _ in range(50000))
    (src / "big.bin").write_bytes(data)

    archive = tmp_path / "packed.7z"
    result = _run_compress(runner, src, archive, "--volume-size", "1K")
    assert result.exit_code == 0, result.output

    volumes = get_volume_group(archive.with_name(f"{archive.name}.001"))
    assert len(volumes) >= 4, f"expected multiple volumes, got {len(volumes)}"

    out = tmp_path / "out"
    result = _run_extract(runner, volumes[0], out)
    assert result.exit_code == 0, result.output

    restored = _find_file(out, "big.bin")
    assert restored is not None
    assert restored.read_bytes() == data


def test_compress_directory_to_volumes_and_extract(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello", encoding="utf-8")
    (src / "b.txt").write_text("world", encoding="utf-8")
    sub = src / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("nested", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = _run_compress(runner, src, archive, "--volume-size", "100b")
    assert result.exit_code == 0, result.output

    volumes = get_volume_group(archive.with_name(f"{archive.name}.001"))
    assert len(volumes) >= 2

    out = tmp_path / "out"
    result = _run_extract(runner, volumes[0], out)
    assert result.exit_code == 0, result.output

    assert _find_file(out, "a.txt").read_text(encoding="utf-8") == "hello"
    assert _find_file(out, "b.txt").read_text(encoding="utf-8") == "world"
    assert _find_file(out, "c.txt").read_text(encoding="utf-8") == "nested"


def test_collect_archives_returns_only_first_volume(tmp_path: Path) -> None:
    (tmp_path / "a.7z.001").write_text("1", encoding="utf-8")
    (tmp_path / "a.7z.002").write_text("2", encoding="utf-8")
    (tmp_path / "b.zip").write_text("z", encoding="utf-8")

    found = list(collect_archives(tmp_path))
    assert len(found) == 2
    names = {p.name for p in found}
    assert "a.7z.001" in names
    assert "a.7z.002" not in names
    assert "b.zip" in names


def test_extract_from_non_first_volume_uses_first(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.txt").write_text("important", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = _run_compress(runner, src, archive, "--volume-size", "100b")
    assert result.exit_code == 0, result.output

    volumes = get_volume_group(archive.with_name(f"{archive.name}.001"))
    assert len(volumes) >= 2

    out = tmp_path / "out"
    # 直接传入非首卷 .002，应自动定位首卷
    result = _run_extract(runner, volumes[1], out)
    assert result.exit_code == 0, result.output
    assert _find_file(out, "data.txt").read_text(encoding="utf-8") == "important"


def test_volume_with_password_roundtrip(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "secret.txt").write_text("top secret", encoding="utf-8")

    archive = tmp_path / "secure.7z"
    result = _run_compress(runner, src, archive, "-p", "pw123", "--volume-size", "100b")
    assert result.exit_code == 0, result.output

    volumes = get_volume_group(archive.with_name(f"{archive.name}.001"))
    assert len(volumes) >= 2

    out = tmp_path / "out"
    result = _run_extract(runner, volumes[0], out, "-p", "pw123")
    assert result.exit_code == 0, result.output
    assert _find_file(out, "secret.txt").read_text(encoding="utf-8") == "top secret"


def test_compress_volumes_with_final_extension_obfuscation(tmp_path: Path) -> None:
    """默认开启：分卷压缩时同时混淆所有分卷后缀，解压仍正常。"""
    runner = CliRunner()
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.txt").write_text("volume obfuscation", encoding="utf-8")

    archive = tmp_path / "packed.7z"
    result = runner.invoke(
        cli,
        [
            "compress",
            str(src),
            "-o",
            str(archive),
            "-s",
            "--volume-size",
            "100b",
        ],
    )
    assert result.exit_code == 0, result.output

    # 原始 .7z.001 等应已不存在，目录中只剩混淆后的分卷
    assert not (archive.with_name(f"{archive.name}.001")).exists()
    files = [p for p in tmp_path.iterdir() if p.is_file()]
    assert len(files) >= 2
    for f in files:
        ext = "." + f.name.rsplit(".", 1)[-1]
        assert ext.lower() not in {".7z", ".zip", ".rar"}

    out = tmp_path / "out"
    result = _run_extract(runner, files[0], out)
    assert result.exit_code == 0, result.output
    assert (
        _find_file(out, "data.txt").read_text(encoding="utf-8") == "volume obfuscation"
    )
