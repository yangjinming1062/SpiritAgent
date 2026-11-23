"""为开场后持续的环境变化选择全景及同一画面区域。"""

from io import BytesIO
from itertools import combinations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

OPENING_SCENE_SAMPLE_OFFSETS = (1.0, 1.5, 2.0)


def opening_scene_evidence(frames: tuple[bytes, ...], times: tuple[float, ...]) -> bytes | None:
    early = [index for index, time in enumerate(times) if 0.35 <= time <= 2.25]
    if len(early) < 2:
        return None
    pixels = {}
    for index in (0, *early):
        with Image.open(BytesIO(frames[index])) as img:
            thumb = img.convert("RGB").resize((120, 120)).filter(ImageFilter.GaussianBlur(1))
            pixels[index] = np.asarray(thumb, dtype=np.int16)
    origin = pixels.pop(0)
    changed = {index: np.abs(rgb - origin).max(axis=2) > 24 for index, rgb in pixels.items()}
    sides: tuple[bool, ...] = ()
    for start, end in combinations(early, 2):
        if times[end] - times[start] < 0.18:
            continue
        persistent = changed[start] & changed[end]
        left, right = float(persistent[:, :38].mean()), float(persistent[:, -38:].mean())
        score = max(left, right)
        if score >= 0.22:
            sides = tuple(on_left for on_left, ratio in ((True, left), (False, right)) if ratio >= 0.75 * score)
            break
    if not sides:
        return None
    # 只选证据。机位、光影或经过的主体也会改变边缘像素。
    intermediate = (start - 1,) if start > 1 and times[start - 1] >= 0.15 else ()
    observations = (0, *intermediate, start, end)
    tile_width, tile_height = 1024 // len(observations), 316
    with Image.new("RGB", (len(observations) * tile_width, (1 + len(sides)) * tile_height + 25), "#eeeeeb") as sheet:
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default(size=18)
        for column, index in enumerate(observations):
            x = column * tile_width
            draw.text((x + 6, 4), f"{'ABCD'[column]}  {times[index]:.2f} s", fill="#252525", font=font)
            with (
                Image.open(BytesIO(frames[index])) as full,
                ImageOps.contain(full, (tile_width - 4, tile_height), Image.Resampling.LANCZOS) as upper,
            ):
                sheet.paste(upper, (x + (tile_width - upper.width) // 2, 25))
                width = round(full.width * 38 / 120)
                for row, on_left in enumerate(sides, start=1):
                    left = 0 if on_left else full.width - width
                    with (
                        full.crop((left, 0, left + width, full.height)) as region,
                        ImageOps.contain(region, (tile_width - 4, tile_height), Image.Resampling.LANCZOS) as lower,
                    ):
                        sheet.paste(lower, (x + (tile_width - lower.width) // 2, 25 + row * tile_height))
        with BytesIO() as output:
            sheet.save(output, format="WEBP", lossless=True)
            return output.getvalue()
