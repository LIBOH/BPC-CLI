"""BPC CLI 统一入口：提供 c / e 子命令，并保留 compress / extract 别名。"""

from __future__ import annotations

import click

from bpc_cli.compress import compress
from bpc_cli.extract import extract


class AliasedGroup(click.Group):
    """支持子命令别名的命令组。"""

    _ALIASES = {"compress": "c", "extract": "e"}

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd_name = self._ALIASES.get(cmd_name, cmd_name)
        return super().get_command(ctx, cmd_name)


@click.group(name="bpc", cls=AliasedGroup)
@click.version_option(version="1.0.0", prog_name="bpc")
def cli() -> None:
    """统一压缩与解压命令行工具。"""


cli.add_command(compress)
cli.add_command(extract)


def main() -> None:
    """兼容脚本入口。"""
    cli()
