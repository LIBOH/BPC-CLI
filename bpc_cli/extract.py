"""基于 click 的递归 7z 解压命令（支持分卷、嵌套压缩包、后缀修复）。"""

from __future__ import annotations

import logging
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from bpc_cli.common import collect_archives, find_7z, get_volume_group, run_7z_extract
from bpc_cli.ext_obfuscate import ObfuscateError, restore_directory
from bpc_cli.file_type import LOGGER_NAME as FILE_TYPE_LOGGER_NAME, repair_archive_extension


def _ensure_first_volume(path: Path) -> Path:
    """若输入为分卷文件，自动定位并返回首卷（如 .7z.001）。"""
    group = get_volume_group(path)
    return group[0] if group else path


def _repair_if_needed(path: Path, repair_enabled: bool, verbose: bool) -> Path:
    """在启用修复时检测并修正压缩包后缀名。"""
    if not repair_enabled:
        return path

    repaired, status = repair_archive_extension(path)
    if status == "repaired" and verbose:
        click.echo(f"修复后缀: {path.name} -> {repaired.name}")
    return repaired


def _repair_files_in_directory(root: Path, repair_enabled: bool, verbose: bool) -> None:
    """修复目录中所有潜在压缩包的错误后缀（原地重命名）。"""
    if not repair_enabled:
        return
    # 先收集为列表，避免在 rglob 迭代过程中重命名文件导致行为不确定
    for path in list(root.rglob("*")):
        if path.is_file():
            _repair_if_needed(path, repair_enabled, verbose)


def _collect_initial_archives(
    inputs: tuple[Path, ...],
    output: Path,
    recursive: bool,
    repair_enabled: bool,
    verbose: bool,
) -> list[tuple[Path, Path]]:
    """根据输入路径收集顶层压缩包及其输出基目录，可选修复错误后缀。"""
    archives: list[tuple[Path, Path]] = []
    for src in inputs:
        if not src.exists():
            raise click.BadParameter(f"输入不存在: {src}")
        if src.is_file():
            src_repaired = _repair_if_needed(src.resolve(), repair_enabled, verbose)
            first = _ensure_first_volume(src_repaired)
            archives.append((first, output.resolve()))
        elif src.is_dir():
            _repair_files_in_directory(src, repair_enabled, verbose)
            for archive in collect_archives(src, recursive=recursive):
                rel = archive.parent.relative_to(src)
                base_out = output.resolve() / rel
                archives.append((archive.resolve(), base_out))
    return archives


def _extract_one(
    sevenz: Path,
    archive: Path,
    base_out: Path,
    password: str | None,
    verbose: bool = False,
) -> tuple[bool, Path, str]:
    """解压单个压缩包并尝试恢复被混淆的后缀名。

    返回 (是否成功, 输出目录, 错误信息)。
    """
    output_dir = base_out / archive.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_7z_extract(sevenz, archive, output_dir, password)
    if result.returncode != 0:
        error_detail = result.stderr.strip() or result.stdout.strip() or "未知错误"
        if verbose:
            click.secho(
                f"7z 输出:\n{result.stdout}\n{result.stderr}", fg="red", err=True
            )
        return False, output_dir, error_detail

    try:
        restore_directory(output_dir, password, verbose=verbose)
    except ObfuscateError as exc:
        error_detail = str(exc)
        if verbose:
            click.secho(f"恢复后缀名失败: {exc}", fg="red", err=True)
        return False, output_dir, error_detail

    return True, output_dir, ""


def _default_workers() -> int:
    """根据 CPU 核心数计算默认并行解压线程数。"""
    return min(4, os.cpu_count() or 1)


@click.command(name="e")
@click.argument(
    "input",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=Path.cwd,
    show_default="当前目录",
    help="解压输出根目录。",
)
@click.option(
    "-p",
    "--password",
    default="",
    hide_input=True,
    show_default=False,
    help="解压密码；未指定则默认空密码。",
)
@click.option(
    "--recursive/--no-recursive",
    default=True,
    show_default=True,
    help="是否递归扫描输入目录中的压缩包。",
)
@click.option(
    "--max-depth",
    type=int,
    default=10,
    show_default=True,
    help="嵌套压缩包最大递归深度。",
)
@click.option(
    "--sevenz",
    type=click.Path(exists=True, path_type=Path),
    help="7z 可执行文件路径（默认自动查找）。",
)
@click.option(
    "-j",
    "--jobs",
    type=int,
    default=_default_workers(),
    show_default=True,
    help="并行解压线程数。",
)
@click.option(
    "--repair/--no-repair",
    default=True,
    show_default=True,
    help="是否根据文件头自动修复错误的后缀名。",
)
@click.option(
    "--repair-log",
    type=click.Path(path_type=Path),
    help="后缀修复操作日志文件路径（默认不写入文件）。",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="输出详细日志。",
)
def extract(
    input: tuple[Path, ...],
    output: Path,
    password: str,
    recursive: bool,
    max_depth: int,
    sevenz: Path | None,
    jobs: int,
    repair: bool,
    repair_log: Path | None,
    verbose: bool,
) -> None:
    """调用系统 7z 递归解压压缩包（e / extract），自动合并分卷、恢复混淆后缀、修复错误后缀。"""
    try:
        sevenz_path = find_7z(str(sevenz) if sevenz else None)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    effective_password = password or None
    output.mkdir(parents=True, exist_ok=True)

    # 配置后缀修复日志
    repair_logger = logging.getLogger(FILE_TYPE_LOGGER_NAME)
    repair_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    repair_logger.handlers.clear()
    if repair_log:
        try:
            repair_log.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(repair_log, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            repair_logger.addHandler(handler)
        except OSError as exc:
            click.secho(f"警告：无法创建修复日志文件: {exc}", fg="yellow", err=True)

    try:
        initial = _collect_initial_archives(
            input, output, recursive, repair_enabled=repair, verbose=verbose
        )
    except click.BadParameter:
        raise
    except Exception as exc:
        raise click.ClickException(f"收集压缩包失败: {exc}") from exc

    if not initial:
        click.secho("未找到任何压缩包。", fg="yellow")
        return

    queue: deque[tuple[Path, Path, int]] = deque(
        (archive, base_out, 0) for archive, base_out in initial
    )
    processed: set[Path] = set()
    success_count = 0
    fail_count = 0
    failures: list[tuple[Path, str]] = []

    label = "正在解压..."
    with click.progressbar(
        length=len(initial),
        label=label,
        show_pos=True,
        item_show_func=lambda _: f"成功 {success_count} / 失败 {fail_count}",
    ) as bar:
        while queue:
            current_depth = queue[0][2]
            current_batch: list[tuple[Path, Path, int]] = []
            while queue and queue[0][2] == current_depth:
                current_batch.append(queue.popleft())

            workers = max(1, min(jobs, len(current_batch)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _extract_one,
                        sevenz_path,
                        archive,
                        base_out,
                        effective_password,
                        verbose,
                    ): (archive, base_out, current_depth)
                    for archive, base_out, _ in current_batch
                }

                for future in as_completed(futures):
                    archive, base_out, depth = futures[future]
                    try:
                        ok, extracted_dir, error_detail = future.result()
                    except Exception as exc:
                        ok = False
                        error_detail = str(exc)
                        extracted_dir = base_out / archive.stem

                    if archive in processed:
                        continue
                    processed.add(archive)

                    if ok:
                        success_count += 1
                    else:
                        fail_count += 1
                        failures.append((archive, error_detail))
                        click.secho(
                            f"失败: {archive}\n  原因: {error_detail}",
                            fg="red",
                            err=True,
                        )

                    bar.update(1)

                    if not ok or depth >= max_depth:
                        continue

                    if extracted_dir.exists():
                        _repair_files_in_directory(extracted_dir, repair, verbose)
                        nested = list(collect_archives(extracted_dir, recursive=True))
                        for nested_archive in nested:
                            if nested_archive not in processed:
                                queue.append(
                                    (nested_archive, nested_archive.parent, depth + 1)
                                )
                        if nested:
                            bar.length += len(nested)

    click.echo()
    if fail_count == 0:
        click.secho(
            f"完成：成功 {success_count} 个，失败 {fail_count} 个。输出目录: {output.resolve()}",
            fg="green",
        )
    else:
        click.secho(
            f"完成：成功 {success_count} 个，失败 {fail_count} 个。输出目录: {output.resolve()}",
            fg="yellow",
        )
    raise SystemExit(1 if fail_count else 0)
