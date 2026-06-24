"""通过文件头（magic bytes）识别压缩包真实类型并修复后缀名。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from bpc_cli.common import (
    ARCHIVE_EXTENSIONS,
    _VOLUME_NUMBER_RE,
    _split_archive_ext,
    _split_volume,
)

LOGGER_NAME = "bpc_cli.file_type"

# (扩展名, 签名在文件中的偏移量, 签名字节串)
# 签名按优先级/特异性排序，越特殊的放越前面。
_ARCHIVE_MAGIC: tuple[tuple[str, int, bytes], ...] = (
    # 7z
    (".7z", 0, b"\x37\x7a\xbc\xaf\x27\x1c"),
    # RAR5
    (".rar", 0, b"Rar!\x1a\x07\x01\x00"),
    # RAR4
    (".rar", 0, b"Rar!\x1a\x07\x00"),
    # ZIP（包括空 ZIP 与分卷标记）
    (".zip", 0, b"PK\x03\x04"),
    (".zip", 0, b"PK\x05\x06"),
    (".zip", 0, b"PK\x07\x08"),
    # gzip
    (".gz", 0, b"\x1f\x8b\x08"),
    # bzip2
    (".bz2", 0, b"BZh"),
    # xz
    (".xz", 0, b"\xfd7zXZ\x00"),
    # cab
    (".cab", 0, b"MSCF"),
    # tar（POSIX ustar 标记位于 257 偏移）
    (".tar", 257, b"ustar"),
    # iso 9660（CD001 位于多个标准偏移）
    (".iso", 32769, b"CD001"),
    (".iso", 34817, b"CD001"),
    (".iso", 37633, b"CD001"),
    # cpio（odc 与 newc 两种常见变体）
    (".cpio", 0, b"070701"),
    (".cpio", 0, b"070702"),
    (".cpio", 0, b"070707"),
)

# 为了处理复合后缀（如 .tar.gz），建立主扩展名到复合扩展名的候选映射。
# detect_archive_type 返回基础扩展名后，外层可据此选择更合适的目标后缀。
_COMPOUND_CANDIDATES: dict[str, Iterable[str]] = {
    ".gz": (".tar.gz", ".tgz"),
    ".bz2": (".tar.bz2", ".tbz2"),
    ".xz": (".tar.xz", ".txz"),
}


def _read_header(path: Path, max_bytes: int = 65536) -> bytes | None:
    """安全读取文件头部字节；失败时返回 None 并记录日志。"""
    logger = logging.getLogger(LOGGER_NAME)
    try:
        with path.open("rb") as fh:
            return fh.read(max_bytes)
    except OSError as exc:
        logger.warning("无法读取文件头: %s -> %s", path, exc)
        return None
    except Exception as exc:  # pragma: no cover - 防御性捕获
        logger.warning("读取文件头时发生未知错误: %s -> %s", path, exc)
        return None


def _match_signature(header: bytes) -> str | None:
    """在文件头中匹配压缩包签名，返回识别到的扩展名。"""
    for ext, offset, signature in _ARCHIVE_MAGIC:
        end = offset + len(signature)
        if len(header) >= end and header[offset:end] == signature:
            return ext
    return None


def detect_archive_type(path: Path) -> str | None:
    """通过文件头识别压缩包真实类型，返回标准扩展名（含前导点）。

    当文件头损坏、文件过小或无法识别时返回 None。
    """
    if not path.is_file():
        return None

    header = _read_header(path)
    if header is None or len(header) < 2:
        return None

    return _match_signature(header)


def _current_archive_ext(name: str) -> str | None:
    """返回文件名中已知的压缩包扩展名；兼容分卷文件。"""
    vol = _split_volume(name)
    if vol is not None:
        return vol[1]
    single = _split_archive_ext(name)
    return single[1] if single else None


def _is_obfuscated_name(name: str) -> bool:
    """判断文件名是否符合本项目混淆后的命名格式。"""
    # 没有映射文件时无法 100% 确认；这里做快速启发式判断：
    # 存在 4 位字母数字 token + 合法压缩包扩展名。
    parts = name.split(".")
    if len(parts) < 3:
        return False
    # 倒数第二部分如果是 4 位字母数字，且最后一部分是合法扩展名
    token = parts[-2]
    ext = "." + parts[-1]
    if len(token) != 4 or not token.isalnum():
        return False
    return ext.lower() in {e.lower() for e in ARCHIVE_EXTENSIONS}


def _looks_like_obfuscated_volume(name: str) -> bool:
    """判断文件名是否为最终后缀混淆后的分卷文件（stem.obf_ext.001）。"""
    match = _VOLUME_NUMBER_RE.match(name)
    if not match:
        return False
    base, _vol = match.groups()
    if "." not in base:
        return False
    ext = "." + base.rsplit(".", 1)[-1]
    return ext.lower() not in {e.lower() for e in ARCHIVE_EXTENSIONS}


def _resolve_target_ext(stem: str, detected: str) -> str:
    """根据检测到的基础扩展名，选择最合适的目标后缀。"""
    # 如果当前文件名本身暗示了复合后缀（如 data.tar 但实际是 gz），
    # 优先保持 stem 中原有的复合扩展名部分。
    lower_stem = stem.lower()
    for base_ext, compound_exts in _COMPOUND_CANDIDATES.items():
        if detected.lower() == base_ext:
            for compound in compound_exts:
                if lower_stem.endswith(compound):
                    return compound
            return base_ext
    return detected


def _repair_stem(path: Path) -> str:
    """返回修复后缀时应使用的 stem。

    对普通单文件，使用 path.stem（去掉最后一个后缀）。
    对分卷文件，使用分卷 stem（如 data.7z.001 -> data）。
    """
    vol = _split_volume(path.name)
    if vol is not None:
        return vol[0]
    return path.stem


def repair_archive_extension(
    path: Path,
    detected: str | None = None,
    *,
    dry_run: bool = False,
) -> tuple[Path, str]:
    """检测并修复压缩包文件的后缀名。

    返回 (最终路径, 操作描述)。
    操作描述取值：
      - "ok"：无需修复（后缀已正确或是混淆格式）。
      - "repaired"：已修复为正确后缀。
      - "unknown"：无法识别真实类型，未改动。
      - "skipped_obfuscated"：疑似本项目混淆文件，未改动。
    """
    logger = logging.getLogger(LOGGER_NAME)

    if detected is None:
        detected = detect_archive_type(path)

    if detected is None:
        logger.info("无法识别文件类型: %s", path)
        return path, "unknown"

    current_ext = _current_archive_ext(path.name)

    # 已经是正确后缀，无需修复
    if current_ext is not None and current_ext.lower() == detected.lower():
        logger.debug("后缀已匹配真实类型: %s", path)
        return path, "ok"

    # 疑似本项目混淆文件，不主动修复，交给 restore_directory 处理
    if _is_obfuscated_name(path.name):
        logger.debug("跳过疑似混淆文件: %s", path)
        return path, "skipped_obfuscated"

    # 最终后缀混淆后的分卷文件：保持原样，避免破坏 7z 卷组命名一致性
    if _looks_like_obfuscated_volume(path.name):
        logger.debug("跳过混淆分卷文件: %s", path)
        return path, "skipped_obfuscated"

    # 选择目标后缀并生成新文件名
    stem = _repair_stem(path)
    target_ext = _resolve_target_ext(stem, detected)
    new_name = f"{stem}{target_ext}"
    new_path = path.with_name(new_name)

    # 处理目标文件名已存在的情况
    if new_path.exists() and new_path != path:
        logger.warning("修复目标文件已存在，尝试生成冲突后缀: %s", new_path)
        for attempt in range(1, 1000):
            alt_name = f"{stem}_repaired{attempt}{target_ext}"
            alt_path = path.with_name(alt_name)
            if not alt_path.exists():
                new_path = alt_path
                break
        else:
            logger.error("无法为修复文件生成唯一名称: %s", path)
            return path, "unknown"

    if dry_run:
        logger.info("[dry-run] 将修复后缀: %s -> %s", path.name, new_path.name)
        return new_path, "repaired"

    try:
        path.rename(new_path)
        logger.info("已修复后缀: %s -> %s", path, new_path)
        return new_path, "repaired"
    except OSError as exc:
        logger.error("修复后缀失败: %s -> %s: %s", path, new_path, exc)
        return path, "unknown"
