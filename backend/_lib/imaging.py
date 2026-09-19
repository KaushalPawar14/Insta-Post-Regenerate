"""Pure image post-processing helpers -- no network, no OpenAI, no DB, no
Storage. Kept separate from generate.py so the actual pixel logic is
directly unit-testable without stubbing any of generate.py's heavier
dependencies.
"""

from PIL import Image


def cover_resize(img: "Image.Image", target_width: int, target_height: int) -> "Image.Image":
    """
    Scale `img` proportionally so it fully covers a `target_width` x
    `target_height` canvas, then center-crop the overflow -- the same
    "cover fit" CSS's `object-fit: cover` uses. Never distorts the aspect
    ratio, unlike a naive `.resize((target_width, target_height))`, which
    stretches non-uniformly whenever the source and target ratios differ.

    This is exactly the fix for gpt-image-2's native output (1024x1536, a
    2:3 ratio) not matching this app's Instagram delivery size (1080x1350,
    a 4:5 ratio) -- the previous direct resize-to-exact-dimensions silently
    stretched every generated image horizontally. See README "Fixing the
    generated-image aspect-ratio stretch" for the before/after this was
    visually verified against.

    Scales by whichever axis needs the LARGER factor to fully cover the
    target (the standard "cover" rule: `max(target_w/src_w,
    target_h/src_h)`), so the other axis ends up with excess that gets
    cropped, centered, rather than any axis falling short and leaving a gap.
    """
    src_width, src_height = img.size
    scale = max(target_width / src_width, target_height / src_height)
    scaled_width = round(src_width * scale)
    scaled_height = round(src_height * scale)
    scaled = img.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

    left = (scaled_width - target_width) // 2
    top = (scaled_height - target_height) // 2
    return scaled.crop((left, top, left + target_width, top + target_height))
