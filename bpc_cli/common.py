"""7z 调用与压缩包识别的共享工具。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# 7z 支持的常见扩展名；优先匹配复合后缀如 .tar.gz
ARCHIVE_EXTENSIONS: tuple[str, ...] = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.lz",
    ".tar.zst",
    ".tar.7z",
    ".tar.Z",
    ".7z",
    ".zip",
    ".rar",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".tbz2",
    ".xz",
    ".txz",
    ".lz",
    ".tlz",
    ".zst",
    ".tzst",
    ".iso",
    ".wim",
    ".swm",
    ".esd",
    ".cab",
    ".arj",
    ".lzh",
    ".chm",
    ".z",
    ".taz",
    ".cpio",
    ".rpm",
    ".deb",
    ".img",
    ".vhd",
    ".vhdx",
    ".dmg",
    ".hfs",
    ".xar",
    ".pkg",
)

# 7z 风格分卷序号：name.7z.001、name.7z.002 ...
_VOLUME_NUMBER_RE = re.compile(r"^(.+)(\.\d{3,})$")


def _split_archive_ext(name: str) -> tuple[str, str] | None:
    """从文件名中分离出已知的压缩包扩展名。

    返回 (stem, ext)，ext 包含前导点；未匹配返回 None。
    """
    lower = name.lower()
    for ext in ARCHIVE_EXTENSIONS:
        if lower.endswith(ext):
            return name[: -len(ext)], ext
    return None


def _split_volume(name: str) -> tuple[str, str, str] | None:
    """解析 7z 风格分卷文件名。

    例如 name.7z.001 -> (stem, '.7z', '.001')；不是分卷返回 None。
    """
    match = _VOLUME_NUMBER_RE.match(name)
    if not match:
        return None
    base, vol = match.groups()
    split = _split_archive_ext(base)
    if split is None:
        return None
    stem, archive_ext = split
    return stem, archive_ext, vol


def looks_like_archive(path: Path) -> bool:
    """通过后缀名快速判断是否为单文件压缩包。"""
    return path.is_file() and _split_archive_ext(path.name) is not None


def is_volume_file(path: Path) -> bool:
    """判断是否为 7z 风格分卷文件（如 .7z.001）。"""
    return path.is_file() and _split_volume(path.name) is not None


def looks_like_archive_or_volume(path: Path) -> bool:
    """判断是否为压缩包或分卷文件。"""
    return looks_like_archive(path) or is_volume_file(path)


def get_volume_group(first_volume: Path) -> list[Path]:
    """根据首卷查找同目录下同一分卷组的所有卷，按序号排序返回。"""
    split = _split_volume(first_volume.name)
    if split is None:
        return [first_volume]
    stem, archive_ext, _ = split
    volumes: list[tuple[int, Path]] = []
    for candidate in first_volume.parent.glob(f"{stem}{archive_ext}.*"):
        if not candidate.is_file():
            continue
        cand_split = _split_volume(candidate.name)
        if cand_split is None or cand_split[0] != stem or cand_split[1] != archive_ext:
            continue
        num = int(cand_split[2][1:])
        volumes.append((num, candidate))
    volumes.sort(key=lambda item: item[0])
    return [p for _, p in volumes]


def collect_archives(root: Path, recursive: bool = True) -> Iterable[Path]:
    """收集目录中的压缩包文件；分卷包只返回首卷。"""
    paths = list(root.rglob("*") if recursive else root.iterdir())

    volume_paths: list[Path] = []
    for p in paths:
        if not p.is_file():
            continue
        if is_volume_file(p):
            volume_paths.append(p)
        elif looks_like_archive(p):
            yield p

    # 按 (stem, 压缩扩展名, 父目录) 分组，只返回每组的首卷
    groups: dict[tuple[str, str, Path], list[tuple[int, Path]]] = {}
    for p in volume_paths:
        split = _split_volume(p.name)
        if split is None:
            continue
        stem, archive_ext, vol = split
        key = (stem, archive_ext, p.parent)
        groups.setdefault(key, []).append((int(vol[1:]), p))

    for members in groups.values():
        members.sort(key=lambda item: item[0])
        yield members[0][1]


def find_7z(cli_path: str | None = None) -> Path:
    """定位 7z 可执行文件。"""
    if cli_path:
        path = Path(cli_path)
        if not path.exists():
            raise FileNotFoundError(f"指定的 7z 不存在: {path}")
        return path

    env_path = os.environ.get("SEVENZ_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    exe = "7z.exe" if sys.platform == "win32" else "7z"
    found = shutil.which(exe)
    if found:
        return Path(found)

    if sys.platform == "win32":
        candidates = [
            Path(r"C:\Program Files\7-Zip\7z.exe"),
            Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        ]
        for cand in candidates:
            if cand.exists():
                return cand

    raise FileNotFoundError(
        "找不到 7z 可执行文件。请安装 7-Zip 并将其加入 PATH，"
        "或通过 --sevenz / 环境变量 SEVENZ_PATH 指定。"
    )


def run_7z_extract(
    sevenz: Path,
    archive: Path,
    output_dir: Path,
    password: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """调用 7z x 解压。"""
    cmd: list[str] = [
        str(sevenz),
        "x",
        str(archive),
        f"-o{output_dir}",
        "-y",
        "-aoa",
    ]
    if password is not None:
        cmd.append(f"-p{password}")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
    )


def run_7z_create(
    sevenz: Path,
    archive: Path,
    listfile: Path,
    cwd: Path,
    password: str | None = None,
    volume_size: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """调用 7z a 通过列表文件创建压缩包，可选分卷。"""
    cmd: list[str] = [
        str(sevenz),
        "a",
        "-y",
        "-scsUTF-8",
        str(archive),
        f"@{listfile}",
    ]
    if volume_size is not None:
        cmd.append(f"-v{volume_size}")
    if password is not None:
        cmd.append(f"-p{password}")
        if archive.suffix.lower() == ".7z":
            cmd.append("-mhe=on")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        encoding="utf-8",
        errors="replace",
    )


def run_7z_list(
    sevenz: Path,
    archive: Path,
    password: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """调用 7z l 列出压缩包内容。"""
    cmd: list[str] = [str(sevenz), "l", "-y", str(archive)]
    if password is not None:
        cmd.append(f"-p{password}")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
