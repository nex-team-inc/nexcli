from typing import Any, Optional, Dict, Callable
from .transformer import Transformer
from functools import wraps

import click

_src_option = click.option(
    "--src",
    "-s",
    type=click.STRING,
    default=None,
    help="Specify the source image id",
)
_dst_option = click.option(
    "--dst",
    "-d",
    type=click.STRING,
    default=None,
    help="Specify the destination image id",
)


class AliasedGroup(click.Group):
    def __init__(self, *kargs, alias_map: Dict[str, str], **kwargs) -> None:
        super().__init__(*kargs, **kwargs)
        self._alias_map = alias_map

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd_name = self._alias_map.get(
            cmd_name, cmd_name
        )  # Try to see if there is already a defined lookup.
        return super().get_command(ctx, cmd_name)


@click.group(cls=AliasedGroup, chain=True, alias_map={"extract-alpha": "alpha"})
@_dst_option
@click.option(
    "--input",
    "-i",
    type=click.Path(exists=True),
    default=None,
    help="Specify the initial image path.",
)
@click.pass_context
def cli(
    ctx: click.Context, dst: Optional[str] = None, input: Optional[str] = None
) -> None:
    """Transform images using subcommands."""
    ctx.ensure_object(Transformer)
    transformer: Transformer = ctx.obj
    transformer.load(dst=dst, path=input)


def transformer_adaptor(func: Callable):
    def decorator(f: Callable):
        @click.pass_context
        @wraps(func)
        def wrapped(ctx: click.Context, *args, **kwargs):
            transformer: Transformer = ctx.obj
            func(transformer, *args, **kwargs)

        return wrapped

    return decorator


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@_dst_option
@transformer_adaptor(Transformer.load)
def load():
    pass


@cli.command()
@click.argument("path", type=click.Path())
@_src_option
@transformer_adaptor(Transformer.save)
def save():
    pass


@cli.command()
@_src_option
@_dst_option
@transformer_adaptor(Transformer.clone)
def clone():
    pass


@cli.command()
@_src_option
@_dst_option
@click.option(
    "--width",
    "-w",
    type=click.IntRange(min=-1),
    default=-1,
    help="Target width after resize.",
)
@click.option(
    "--height",
    "-h",
    type=click.IntRange(min=-1),
    default=-1,
    help="Target height after resize.",
)
@click.option(
    "--algorithm",
    "-a",
    type=click.Choice(
        ("auto", "linear", "nearest", "cubic", "area"), case_sensitive=False
    ),
    default="auto",
)
@transformer_adaptor(Transformer.resize)
def resize():
    pass


@cli.command()
@_src_option
@_dst_option
@click.option("--width", "-w", type=click.IntRange(min=-1), default=-1)
@click.option("--height", "-h", type=click.IntRange(min=-1), default=-1)
@click.option(
    "--pivot-x",
    "-px",
    "px",
    type=click.FloatRange(min=0, max=1, clamp=True),
    default=0.5,
)
@click.option(
    "--pivot-y",
    "-py",
    "py",
    type=click.FloatRange(min=0, max=1, clamp=True),
    default=0.5,
)
@click.option("--color", "-c", type=click.STRING, default="FFF0")
@transformer_adaptor(Transformer.pad)
def pad():
    pass


@cli.command("alpha")
@_src_option
@_dst_option
@click.option(
    "--color", "-c", type=click.STRING, default="FFF", help="RGB for non-alpha channel."
)
@transformer_adaptor(Transformer.extract_alpha)
def extract_alpha():
    pass


@cli.command()
@_src_option
@_dst_option
@click.option(
    "--radius", "-r", type=click.IntRange(min=1), default=1, help="Radius for dilation"
)
@transformer_adaptor(Transformer.dilate)
def dilate():
    pass


@cli.command()
@_src_option
@_dst_option
@click.option(
    "--radius", "-r", type=click.IntRange(min=1), default=1, help="Radius for erosion"
)
@transformer_adaptor(Transformer.erode)
def erode():
    pass


@cli.command()
@_src_option
@_dst_option
@click.option(
    "--radius",
    "-r",
    type=click.IntRange(min=1),
    default=1,
    help="Radius for Gaussian blur",
)
@transformer_adaptor(Transformer.blur)
def blur():
    pass


@cli.command()
@_src_option
@_dst_option
@click.option(
    "--by", "-b", type=click.STRING, default=None, help="The Subtrahend data."
)
@click.option(
    "--channel",
    "-c",
    type=click.IntRange(min=0, max=3),
    default=3,
    help="The channel subtraction happens.",
)
@transformer_adaptor(Transformer.subtract)
def subtract():
    pass
