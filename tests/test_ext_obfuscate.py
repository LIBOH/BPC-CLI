"""ext_obfuscate 模块的单元测试。"""

from __future__ import annotations

import string
from pathlib import Path

import pytest

from bpc_cli.common import ARCHIVE_EXTENSIONS, _split_volume
from bpc_cli.ext_obfuscate import (
    MAP_FILENAME,
    MapCryptoError,
    ObfuscateError,
    decrypt_mapping,
    encrypt_mapping,
    generate_random_obfuscated_ext,
    generate_suffix,
    list_obfuscated_files,
    obfuscate_directory,
    obfuscate_final_archive,
    restore_directory,
    verify_final_obfuscation,
)


def test_generate_suffix_length_and_charset() -> None:
    for _ in range(100):
        suffix = generate_suffix()
        assert len(suffix) == 4
        assert all(c in string.ascii_letters + string.digits for c in suffix)


def test_encrypt_decrypt_roundtrip() -> None:
    plaintext = b'{"txt": ".pdf", "abc": ".doc"}'
    token = encrypt_mapping(plaintext, password="secret")
    assert decrypt_mapping(token, password="secret") == plaintext


def test_decrypt_with_wrong_password_fails() -> None:
    plaintext = b'{"txt": ".pdf"}'
    token = encrypt_mapping(plaintext, password="secret")
    with pytest.raises(MapCryptoError):
        decrypt_mapping(token, password="wrong")


def test_obfuscate_only_archives_and_restore(tmp_path: Path) -> None:
    original_files = {
        "doc1.txt": "hello",
        "doc2.pdf": "pdf content",
        "archive.zip": "zip",
        "image.png": "png",
        "archive2.7z": "7z",
        "noextension": "none",
    }
    archive_files = {"archive.zip", "archive2.7z"}
    for name, content in original_files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    mapping = obfuscate_directory(tmp_path)
    assert len(mapping) == len(archive_files)

    obfuscated = list(list_obfuscated_files(tmp_path))
    assert len(obfuscated) == len(archive_files)
    assert (tmp_path / MAP_FILENAME).exists()

    for path in obfuscated:
        # 混淆后的文件名仍应具有合法压缩包扩展名
        assert any(path.name.lower().endswith(ext) for ext in ARCHIVE_EXTENSIONS), (
            f"{path.name} 不是合法压缩包后缀"
        )

    # 非压缩包文件保持原样
    for name in set(original_files) - archive_files:
        assert (tmp_path / name).exists()

    # 恢复后所有文件名称与内容一致
    restore_directory(tmp_path)
    for name, content in original_files.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == content
    assert not (tmp_path / MAP_FILENAME).exists()


def test_obfuscate_volume_files(tmp_path: Path) -> None:
    (tmp_path / "data.7z.001").write_text("vol1", encoding="utf-8")
    (tmp_path / "data.7z.002").write_text("vol2", encoding="utf-8")

    mapping = obfuscate_directory(tmp_path)
    assert len(mapping) == 1

    obfuscated = list(list_obfuscated_files(tmp_path))
    assert len(obfuscated) == 2
    for path in obfuscated:
        split = _split_volume(path.name)
        assert split is not None
        # 仅压缩包扩展名被混淆，分卷序号保留
        assert split[2] in {".001", ".002"}

    restore_directory(tmp_path)
    assert (tmp_path / "data.7z.001").read_text(encoding="utf-8") == "vol1"
    assert (tmp_path / "data.7z.002").read_text(encoding="utf-8") == "vol2"


def test_obfuscate_preserves_multi_dot_archive_name(tmp_path: Path) -> None:
    (tmp_path / "my.file.zip").write_text("a", encoding="utf-8")
    mapping = obfuscate_directory(tmp_path)
    obfuscated = list(list_obfuscated_files(tmp_path))
    assert len(obfuscated) == 1
    assert obfuscated[0].name.startswith("my.file.")
    token = list(mapping.keys())[0]
    assert token in obfuscated[0].name

    restore_directory(tmp_path)
    assert (tmp_path / "my.file.zip").exists()


def test_obfuscate_skips_non_archives(tmp_path: Path) -> None:
    (tmp_path / "regular.txt").write_text("a", encoding="utf-8")
    (tmp_path / "noextension").write_text("b", encoding="utf-8")
    (tmp_path / ".hidden").write_text("c", encoding="utf-8")
    (tmp_path / ".hidden2.txt").write_text("d", encoding="utf-8")

    mapping = obfuscate_directory(tmp_path)
    assert len(mapping) == 0
    assert len(list(list_obfuscated_files(tmp_path))) == 0


def test_obfuscate_with_password(tmp_path: Path) -> None:
    (tmp_path / "secret.zip").write_text("x", encoding="utf-8")
    obfuscate_directory(tmp_path, password="pw123")

    with pytest.raises(ObfuscateError):
        restore_directory(tmp_path, password="wrong")

    restore_directory(tmp_path, password="pw123")
    assert (tmp_path / "secret.zip").exists()


def test_obfuscate_archive_suffixes_are_unique(tmp_path: Path) -> None:
    exts = [".zip", ".7z", ".rar", ".tar.gz", ".tar.bz2"]
    for i, ext in enumerate(exts * 20):
        (tmp_path / f"file{i}{ext}").write_text(str(i), encoding="utf-8")
    mapping = obfuscate_directory(tmp_path)
    assert len(mapping) == len(exts) * 20
    assert len(set(mapping.keys())) == len(mapping)

    restore_directory(tmp_path)
    for i, ext in enumerate(exts * 20):
        assert (tmp_path / f"file{i}{ext}").exists()


def test_generate_random_obfuscated_ext_is_non_standard() -> None:
    for _ in range(100):
        ext = generate_random_obfuscated_ext()
        assert ext.startswith(".")
        assert ext[1:].isalnum()
        assert ext.lower() not in {e.lower() for e in ARCHIVE_EXTENSIONS}


def test_obfuscate_with_random_ext_factory(tmp_path: Path) -> None:
    (tmp_path / "data.7z").write_text("archive", encoding="utf-8")
    (tmp_path / "data.zip").write_text("archive2", encoding="utf-8")

    mapping = obfuscate_directory(
        tmp_path, custom_ext_factory=lambda _ext: generate_random_obfuscated_ext()
    )
    assert len(mapping) == 2

    obfuscated = list(list_obfuscated_files(tmp_path))
    assert len(obfuscated) == 2
    for path in obfuscated:
        # 混淆后的后缀不应再是任何已知压缩扩展名
        assert not any(path.name.lower().endswith(ext) for ext in ARCHIVE_EXTENSIONS), (
            f"{path.name} 仍是标准压缩后缀"
        )

    restore_directory(tmp_path)
    assert (tmp_path / "data.7z").read_text(encoding="utf-8") == "archive"
    assert (tmp_path / "data.zip").read_text(encoding="utf-8") == "archive2"


def test_obfuscate_v3_volume_with_random_ext_factory(tmp_path: Path) -> None:
    (tmp_path / "data.7z.001").write_text("vol1", encoding="utf-8")
    (tmp_path / "data.7z.002").write_text("vol2", encoding="utf-8")

    obfuscate_directory(
        tmp_path, custom_ext_factory=lambda _ext: generate_random_obfuscated_ext()
    )
    obfuscated = list(list_obfuscated_files(tmp_path))
    assert len(obfuscated) == 2

    restore_directory(tmp_path)
    assert (tmp_path / "data.7z.001").read_text(encoding="utf-8") == "vol1"
    assert (tmp_path / "data.7z.002").read_text(encoding="utf-8") == "vol2"


# ---------- 最终压缩包后缀混淆测试 ----------


def _make_fake_7z(path: Path, content: bytes = b"fake archive payload") -> None:
    """写入一个可被 detect_archive_type 识别为 7z 的假文件。"""
    path.write_bytes(b"\x37\x7a\xbc\xaf\x27\x1c" + content)


def test_obfuscate_final_archive_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "packed.7z"
    _make_fake_7z(archive)
    original_hash = archive.read_bytes()

    obfuscated = obfuscate_final_archive(archive)

    assert len(obfuscated) == 1
    assert not archive.exists()
    assert obfuscated[0].exists()
    assert obfuscated[0].read_bytes() == original_hash
    assert "." in obfuscated[0].name
    ext = "." + obfuscated[0].name.rsplit(".", 1)[-1]
    assert ext.lower() not in {e.lower() for e in ARCHIVE_EXTENSIONS}
    verify_final_obfuscation(obfuscated[0])


def test_obfuscate_final_archive_volume_group(tmp_path: Path) -> None:
    vol1 = tmp_path / "packed.7z.001"
    vol2 = tmp_path / "packed.7z.002"
    _make_fake_7z(vol1, b"vol1")
    vol2.write_bytes(b"\x37\x7a\xbc\xaf\x27\x1c" + b"vol2")
    original_contents = sorted(p.read_bytes() for p in (vol1, vol2))

    obfuscated = obfuscate_final_archive(vol1)

    assert len(obfuscated) == 2
    assert not vol1.exists()
    assert not vol2.exists()
    primary = obfuscated[0]
    assert primary.exists()
    new_contents = sorted(v.read_bytes() for v in obfuscated)
    assert new_contents == original_contents
    verify_final_obfuscation(primary)


def test_verify_final_obfuscation_rejects_standard_ext(tmp_path: Path) -> None:
    archive = tmp_path / "packed.7z"
    _make_fake_7z(archive)
    with pytest.raises(ObfuscateError):
        # 标准 .7z 扩展名不应通过“已混淆”验证
        verify_final_obfuscation(archive)
