"""基于 click 的 7z 压缩命令（集成防和谐后缀混淆、分卷、递归压缩）。"""

from __future__ import annotations

import datetime
import random
import re
import secrets
import shutil
import string
import tempfile
import time
from pathlib import Path

import click

from bpc_cli.common import (
    find_7z,
    is_volume_file,
    run_7z_create,
)
from bpc_cli.ext_obfuscate import (
    ObfuscateError,
    generate_random_obfuscated_ext,
    obfuscate_directory,
    obfuscate_final_archive,
    verify_final_obfuscation,
)


def _collect_source_paths(
    inputs: tuple[Path, ...], recursive: bool
) -> tuple[list[Path], list[Path]]:
    """区分文件与目录输入，并校验存在性。"""
    files: list[Path] = []
    dirs: list[Path] = []
    for src in inputs:
        if not src.exists():
            raise click.BadParameter(f"输入不存在: {src}")
        if src.is_file():
            files.append(src.resolve())
        elif src.is_dir():
            dirs.append(src.resolve())
    if not files and not dirs:
        raise click.BadParameter("至少提供一个文件或目录输入")
    return files, dirs


def _copy_inputs_to_staging(
    staging: Path,
    files: list[Path],
    dirs: list[Path],
    recursive: bool,
    verbose: bool,
) -> None:
    """将输入复制到临时暂存目录，保留原始文件名。"""
    copied_names: set[str] = set()

    for src in files:
        dest = staging / src.name
        if src.name in copied_names:
            raise click.ClickException(f"存在同名输入文件: {src.name}")
        copied_names.add(src.name)
        if src.is_file():
            shutil.copy2(src, dest)
        else:
            shutil.copytree(src, dest, dirs_exist_ok=True)
        if verbose:
            click.echo(f"复制文件: {src} -> {dest}")

    for src in dirs:
        dest = staging / src.name
        if src.name in copied_names:
            raise click.ClickException(f"存在同名输入目录: {src.name}")
        copied_names.add(src.name)
        if recursive:
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            for child in src.iterdir():
                if child.is_file():
                    shutil.copy2(child, dest / child.name)
                elif child.is_dir():
                    shutil.copytree(child, dest / child.name, dirs_exist_ok=True)
        if verbose:
            click.echo(f"复制目录: {src} -> {dest}")


def _build_listfile(staging: Path) -> Path:
    """生成 7z 列表文件，包含暂存目录下所有文件。"""
    listfile = staging.parent / f"{staging.name}.lst"
    lines: list[str] = []
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            rel = path.relative_to(staging).as_posix()
            lines.append(rel)
    listfile.write_text("\n".join(lines), encoding="utf-8")
    return listfile


def _collect_created_files(archive: Path, volume_size: str | None) -> list[Path]:
    """根据 7z 输出路径收集实际创建的压缩包文件（含分卷）。"""
    if volume_size is None:
        return [archive] if archive.exists() else []

    # 分卷模式：archive.7z.001、archive.7z.002 ...
    pattern = f"{archive.name}.*"
    volumes = sorted(p for p in archive.parent.glob(pattern) if is_volume_file(p))
    return volumes


def _compress_one_layer(
    sevenz_path: Path,
    inputs: tuple[Path, ...],
    output: Path,
    password: str | None,
    include_subdirs: bool,
    volume_size: str | None,
    verbose: bool,
    use_random_ext: bool = False,
) -> list[Path]:
    """执行单层压缩，返回实际创建的压缩包文件路径列表。"""
    files, dirs = _collect_source_paths(inputs, include_subdirs)

    staging = Path(tempfile.mkdtemp(prefix="bpc_compress_"))
    listfile: Path | None = None
    try:
        _copy_inputs_to_staging(staging, files, dirs, include_subdirs, verbose)

        if verbose:
            click.echo("开始混淆文件后缀名...")
        ext_factory = None
        if use_random_ext:
            ext_factory = lambda _original: generate_random_obfuscated_ext()
        try:
            obfuscate_directory(
                staging, password, verbose=verbose, custom_ext_factory=ext_factory
            )
        except ObfuscateError as exc:
            raise click.ClickException(str(exc)) from exc

        listfile = _build_listfile(staging)
        if verbose:
            click.echo(f"列表文件: {listfile}")
            click.echo(f"创建压缩包: {output}")
            if volume_size:
                click.echo(f"分卷大小: {volume_size}")

        result = run_7z_create(
            sevenz_path, output, listfile, staging, password, volume_size
        )
        if result.returncode != 0:
            raise click.ClickException(
                f"7z 压缩失败:\n{result.stdout}\n{result.stderr}"
            )

        created = _collect_created_files(output, volume_size)
        if not created:
            raise click.ClickException("7z 未生成任何压缩包文件")
        return created
    finally:
        if listfile is not None and listfile.exists():
            listfile.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _apply_final_obfuscation(path: Path, verbose: bool) -> list[Path]:
    """对最终压缩包执行后缀名混淆并验证，返回所有新生成的文件路径。"""
    if verbose:
        click.echo(f"混淆最终压缩包后缀: {path}")
    try:
        new_paths = obfuscate_final_archive(path)
        verify_final_obfuscation(new_paths[0])
    except ObfuscateError as exc:
        raise click.ClickException(str(exc)) from exc
    if verbose:
        for p in new_paths:
            click.echo(f"混淆完成: {p}")
    return new_paths


def _random_layer_name() -> str:
    """为递归压缩的中间层生成随机目录/文件名。"""
    return "layer_" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))


DEFAULT_RECURSIVE_VOLUME_SIZE = "1G"


def _total_size(paths: tuple[Path, ...]) -> int:
    """计算给定路径列表中所有文件的总字节数。"""
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total


def _format_size(size: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def _recursive_compress(
    sevenz_path: Path,
    inputs: tuple[Path, ...],
    final_output: Path,
    password: str | None,
    include_subdirs: bool,
    volume_size: str | None,
    recursive_levels: int,
    verbose: bool,
) -> None:
    """执行多层递归压缩，最终输出为单个压缩包，并记录每层日志。"""
    if recursive_levels < 2:
        raise click.ClickException("递归层数至少为 2")

    effective_volume_size = (
        volume_size if volume_size is not None else DEFAULT_RECURSIVE_VOLUME_SIZE
    )
    original_size = _total_size(inputs)
    layer_logs: list[dict] = []

    work_dir = Path(tempfile.mkdtemp(prefix="bpc_recursive_"))
    try:
        current_inputs = inputs
        for layer in range(1, recursive_levels + 1):
            is_last = layer == recursive_levels
            use_volume = (not is_last) and random.choice([True, False])
            layer_size = effective_volume_size if use_volume else None
            layer_name = _random_layer_name()
            layer_output = work_dir / layer_name / f"{layer_name}{final_output.suffix}"
            layer_output.parent.mkdir(parents=True, exist_ok=True)

            input_size = _total_size(current_inputs)
            start_time = time.perf_counter()
            start_dt = datetime.datetime.now().isoformat(timespec="seconds")

            if verbose:
                click.echo(
                    f"[{start_dt}] 递归压缩第 {layer}/{recursive_levels} 层 "
                    f"模式={'分卷' if use_volume else '单文件'} "
                    f"输入大小={_format_size(input_size)}"
                )

            created = _compress_one_layer(
                sevenz_path,
                current_inputs,
                layer_output,
                password,
                include_subdirs=False,
                volume_size=layer_size,
                verbose=verbose,
                use_random_ext=True,
            )
            output_size = sum(p.stat().st_size for p in created)
            elapsed = time.perf_counter() - start_time

            layer_logs.append(
                {
                    "layer": layer,
                    "mode": "volume" if use_volume else "single",
                    "input_size": input_size,
                    "output_size": output_size,
                    "duration_seconds": elapsed,
                    "file_count": len(created),
                    "files": [p.name for p in created],
                }
            )

            if verbose:
                click.echo(
                    f"  -> 输出大小={_format_size(output_size)} "
                    f"文件数={len(created)} 耗时={elapsed:.2f}s"
                )

            current_inputs = tuple(created)

        # 最后一层必定为单文件，直接移动到最终输出路径
        if len(current_inputs) != 1:
            raise click.ClickException(
                f"最终层异常：期望单个文件，实际得到 {len(current_inputs)} 个"
            )
        final_archive = current_inputs[0]
        final_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final_archive), str(final_output))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    final_size = final_output.stat().st_size
    ratio = final_size / original_size if original_size > 0 else 0.0

    if verbose:
        click.echo("\n========== 递归压缩过程报告 ==========")
        click.echo(f"原始文件总大小: {_format_size(original_size)}")
        click.echo(f"最终文件大小:   {_format_size(final_size)}")
        click.echo(f"大小变化比例:   {ratio:.2%}")
        click.echo(f"递归压缩层数:   {recursive_levels}")
        for log in layer_logs:
            click.echo(
                f"  第 {log['layer']} 层: {log['mode']:6} "
                f"输入={_format_size(log['input_size'])} "
                f"输出={_format_size(log['output_size'])} "
                f"文件数={log['file_count']} "
                f"耗时={log['duration_seconds']:.2f}s"
            )
        click.echo("======================================\n")


def _parse_volume_size(value: str | None) -> str | None:
    """校验并归一化分卷大小参数（如 100M、1G）。"""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not re.match(r"^\d+[kmgtpezy]?b?$", value, re.IGNORECASE):
        raise click.BadParameter(f"无效的分卷大小: {value}（示例: 100M, 1G）")
    return value


def _default_output_path(inputs: tuple[Path, ...]) -> Path:
    """根据首个输入路径计算默认输出压缩包路径（与输入同级）。"""
    first = inputs[0].resolve()
    if first.is_dir():
        return first.parent / f"{first.name}.7z"
    return first.parent / f"{first.stem}.7z"


@click.command(name="c")
@click.argument(
    "input",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    required=False,
    default=None,
    type=click.Path(path_type=Path),
    help="输出压缩包路径（默认与输入同级，文件名同输入）。",
)
@click.option(
    "-p",
    "--password",
    default="",
    hide_input=True,
    show_default=False,
    help="压缩包密码；同时用于加密后缀映射文件。未指定则默认空密码。",
)
@click.option(
    "--recursive/--no-recursive",
    default=True,
    show_default=True,
    help="是否递归包含输入目录中的子目录（单层压缩时）。",
)
@click.option(
    "--volume-size",
    callback=lambda _ctx, _param, value: _parse_volume_size(value),
    help="分卷大小（如 100M、1G）；递归压缩时未指定则默认 1G。",
)
@click.option(
    "--obfuscate-ext/--no-obfuscate-ext",
    default=True,
    show_default=True,
    help="对最终生成的压缩包后缀名进行混淆（仅重命名，不修改内容）。",
)
@click.option(
    "-s",
    "--single",
    is_flag=True,
    help="仅执行单层压缩，禁用默认的递归压缩。",
)
@click.option(
    "--recursive-compress",
    is_flag=True,
    hidden=True,
    help="已废弃，现等价于 --single。",
)
@click.option(
    "--recursive-levels",
    type=click.IntRange(2, 10),
    help="显式指定递归压缩层数（覆盖随机层数）。",
)
@click.option(
    "--sevenz",
    type=click.Path(exists=True, path_type=Path),
    help="7z 可执行文件路径（默认自动查找）。",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="输出详细日志。",
)
def compress(
    input: tuple[Path, ...],
    output: Path | None,
    password: str,
    recursive: bool,
    volume_size: str | None,
    obfuscate_ext: bool,
    single: bool,
    recursive_compress: bool,
    recursive_levels: int | None,
    sevenz: Path | None,
    verbose: bool,
) -> None:
    """调用系统 7z 压缩文件/目录（c / compress），默认递归多层压缩并自动混淆后缀名。"""
    try:
        sevenz_path = find_7z(str(sevenz) if sevenz else None)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    effective_password = password or None

    if output is None:
        output = _default_output_path(input)
    else:
        output = output.resolve()
        if output.is_dir():
            first_name = input[0].name
            suffix = output.suffix or ".7z"
            output = output / f"{first_name}{suffix}"

    use_recursive = not single
    if recursive_compress:
        # 旧参数语义已反转，现在等价于 --single
        if not single:
            click.secho(
                "警告：--recursive-compress 已废弃，现等价于 --single。"
                "请使用 --single 指定单层压缩。",
                fg="yellow",
                err=True,
            )
        use_recursive = False

    if use_recursive:
        levels = (
            recursive_levels if recursive_levels is not None else random.randint(3, 4)
        )
        _recursive_compress(
            sevenz_path,
            input,
            output,
            effective_password,
            recursive,
            volume_size,
            levels,
            verbose,
        )
        if obfuscate_ext:
            created = _apply_final_obfuscation(output, verbose)
            output = created[0]
    else:
        created = _compress_one_layer(
            sevenz_path,
            input,
            output,
            effective_password,
            recursive,
            volume_size,
            verbose,
        )
        if obfuscate_ext:
            created = _apply_final_obfuscation(created[0], verbose)
            output = created[0]
        if verbose:
            for p in created:
                click.echo(f"生成文件: {p}")

    extra_msg = "（后缀名已混淆，映射文件已加密）"
    if obfuscate_ext:
        extra_msg += "，最终压缩包后缀名已混淆"
    click.secho(
        f"完成：已创建 {output}{extra_msg}。",
        fg="green",
    )
    raise SystemExit(0)
