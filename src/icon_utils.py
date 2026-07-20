# -*- coding: utf-8 -*-
"""PaperPiggy 图标资产工具。

``web/PaperPiggy.png`` 是唯一真源；Windows 所需的多尺寸 ``.ico``
在构建期或启动时由这里生成，避免安装器、快捷方式与窗口各自维护一份图标。
"""
from __future__ import annotations

import os
from pathlib import Path


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _ico_sizes(path: Path) -> set[tuple[int, int]]:
    """读取 ICO 中实际包含的尺寸；损坏或不可读时返回空集合。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return {tuple(size) for size in image.info.get("sizes", set())}
    except Exception:
        return set()


def ico_is_valid(path: str | Path) -> bool:
    """是否是包含全部应用尺寸的有效多帧 ICO。"""
    target = Path(path)
    required = {(size, size) for size in ICON_SIZES}
    return target.is_file() and target.stat().st_size > 0 and required <= _ico_sizes(target)


def write_multi_size_ico(png_path: str | Path, ico_path: str | Path) -> Path:
    """从透明 PNG 原子生成 16–256px 的 Windows 多尺寸 ICO。"""
    from PIL import Image

    source = Path(png_path)
    target = Path(ico_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGBA")
            if image.width != image.height:
                raise ValueError(f"应用图标必须是正方形：{source} ({image.width}x{image.height})")
            image.save(
                temporary,
                format="ICO",
                sizes=[(size, size) for size in ICON_SIZES],
            )
        if not ico_is_valid(temporary):
            raise ValueError(f"生成的 ICO 缺少必要尺寸：{temporary}")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass
    return target


def ensure_multi_size_ico(
    png_path: str | Path,
    ico_path: str | Path,
    *,
    force: bool = False,
) -> tuple[Path, bool]:
    """按源图新旧与结构完整性生成 ICO，返回 ``(路径, 是否重建)``。"""
    source = Path(png_path)
    target = Path(ico_path)
    current = (
        not force
        and ico_is_valid(target)
        and target.stat().st_mtime_ns >= source.stat().st_mtime_ns
    )
    if current:
        return target, False
    return write_multi_size_ico(source, target), True
