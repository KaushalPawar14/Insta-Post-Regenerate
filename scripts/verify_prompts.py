"""
Guard test for the three protected prompts.  Run:  npm run verify:prompts

Two independent checks:

1. ALWAYS -- import `backend/_lib/prompts.py`, which self-verifies its
   contents against embedded SHA-256 checksums and refuses to load if any of
   the three prompts was edited.

2. WHEN AVAILABLE:
   - If the original pipeline is reachable (pass its path, or set
     ORIGINAL_PIPELINE_DIR), diff VISION_PROMPT, GENERATOR_PROMPT, and
     FACTSBYTES_GENERATOR_PROMPT against their sources and assert each
     differs by EXACTLY its own approved edit(s) -- see the EXPECTED_*
     lists below -- and nothing else.
"""

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

# VISION_PROMPT: 2 approved additions, appended after its last existing
# bullet. Nothing removed.
EXPECTED_VISION_ADDITIONS = [
    "* Always describe any diagram, hologram, X-ray, or anatomical/mechanical overlay in full detail as an essential part of the scene — never omit or shorten it.",
    "* Exclude ALL visible text in the scene, not just logos/captions — book titles, signs, labels, screens, clothing text included. Describe such objects by appearance only, never mentioning any words on them.",
]

# GENERATOR_PROMPT: 2 approved edits -- the gradient addition (pure addition)
# and the text-color "yellow" -> #f6ff02 swap (one line removed, one added).
# The thin BORDER line ("Preserve the thin yellow border.") is NOT part of
# either approved edit and must remain untouched.
EXPECTED_GENERATOR_REMOVALS = [
    "* Use only white and yellow text.",
]
EXPECTED_GENERATOR_ADDITIONS = [
    "* Use only white and #f6ff02 text.",
    '* The black gradient overlay must begin exactly at the vertical midpoint of the "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text sits above the gradient start and the lower half sits within it.',
    "* Do not begin the gradient any higher or lower than this point.",
]

# FACTSBYTES_GENERATOR_PROMPT: 1 approved edit -- the text-color bullet's two
# "yellow" mentions swapped to #f6ff02. The divider LINES bullets are NOT
# part of this approved edit and must remain untouched.
EXPECTED_FACTSBYTES_REMOVALS = [
    "• Use ONLY bright yellow and white text. Highlight important portions in yellow and keep remaining portions white.",
]
EXPECTED_FACTSBYTES_ADDITIONS = [
    "• Use ONLY bright #f6ff02 and white text. Highlight important portions in #f6ff02 and keep remaining portions white.",
]


def main() -> int:
    # --- check 1: embedded integrity guard ---------------------------------
    try:
        from _lib.prompts import (  # noqa: PLC0415
            FACTSBYTES_GENERATOR_PROMPT,
            GENERATOR_PROMPT,
            VISION_PROMPT,
            render_factsbytes_prompt,
            render_generator_prompt,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    print("PASS  embedded checksums match (all three prompts are unmodified)")

    rendered = render_generator_prompt(visual_prompt="<VP>", text_transcription="<TT>")
    if "<VP>" not in rendered or "<TT>" not in rendered:
        print("FAIL: generator prompt placeholders did not render")
        return 1
    if "{" in rendered.replace("{", "", 0) and re.search(r"\{[a-z_]+\}", rendered):
        print("FAIL: unrendered placeholder left in the generator prompt")
        return 1
    print("PASS  generator prompt renders both placeholders")

    fb_rendered = render_factsbytes_prompt(image_description="<IMGDESC>", text_as_is="<TEXTASIS>")
    if "<IMGDESC>" not in fb_rendered or "<TEXTASIS>" not in fb_rendered:
        print("FAIL: Facts Bytes prompt tokens did not render")
        return 1
    if "[Prompt 1" in fb_rendered or "[Prompt 2" in fb_rendered:
        print("FAIL: unsubstituted [Prompt N : ...] token left in the Facts Bytes prompt")
        return 1
    print("PASS  Facts Bytes prompt renders both bracketed tokens")

    # --- check 2a: Facts Bytes prompt vs its own source file (always) -------
    fb_source_path = REPO / "scripts" / "factsbytes_prompt_source.txt"
    if not fb_source_path.exists():
        print(f"FAIL: {fb_source_path} not found -- cannot verify Facts Bytes prompt fidelity")
        return 1
    fb_source = fb_source_path.read_text(encoding="utf-8")

    fb_ported_lines = FACTSBYTES_GENERATOR_PROMPT.splitlines()
    fb_source_lines = fb_source.splitlines()
    fb_added = [line for line in fb_ported_lines if line not in fb_source_lines]
    fb_removed = [line for line in fb_source_lines if line not in fb_ported_lines]

    if fb_removed != EXPECTED_FACTSBYTES_REMOVALS or fb_added != EXPECTED_FACTSBYTES_ADDITIONS:
        print("FAIL: FACTSBYTES_GENERATOR_PROMPT differs from scripts/factsbytes_prompt_source.txt")
        print("      by more (or other) than its one approved text-color edit:")
        for line in fb_removed:
            print(f"       - {line}")
        for line in fb_added:
            print(f"       + {line}")
        return 1
    print(f"PASS  FACTSBYTES_GENERATOR_PROMPT differs from its source by exactly the 1 approved text-color edit ({len(FACTSBYTES_GENERATOR_PROMPT)} chars)")

    # --- check 2b: diff against the original pipeline, if reachable --------
    candidates = []
    if len(sys.argv) > 1:
        candidates.append(Path(sys.argv[1]))
    if os.environ.get("ORIGINAL_PIPELINE_DIR"):
        candidates.append(Path(os.environ["ORIGINAL_PIPELINE_DIR"]))
    candidates.append(REPO.parent)  # the folder this project was created in

    source = None
    for candidate in candidates:
        if (candidate / "nodes" / "agent_2_analyzer.py").exists():
            source = candidate
            break

    if source is None:
        print("SKIP  original pipeline not reachable; checksum + Facts Bytes checks only")
        print("      (pass its path as an argument to run the full diff)")
        return 0

    analyzer = (source / "nodes" / "agent_2_analyzer.py").read_text(encoding="utf-8")
    generator = (source / "nodes" / "agent_3_generator.py").read_text(encoding="utf-8")

    src_vision = re.search(r'system_prompt = """(.*?)"""', analyzer, re.DOTALL).group(1)
    src_generator = re.search(r'formatted_prompt = f"""(.*?)"""', generator, re.DOTALL).group(1)

    vision_ported = VISION_PROMPT.splitlines()
    vision_original = src_vision.splitlines()
    vision_added = [line.strip() for line in vision_ported if line not in vision_original]
    vision_removed = [line for line in vision_original if line not in vision_ported]

    if vision_removed or vision_added != EXPECTED_VISION_ADDITIONS:
        print("FAIL: VISION_PROMPT differs from the original source by more (or other) than its 2 approved additions:")
        for line in vision_removed:
            print(f"       - {line}")
        for line in vision_added:
            print(f"       + {line}")
        return 1
    print(f"PASS  VISION_PROMPT differs from source by exactly the 2 approved additions ({len(VISION_PROMPT)} chars)")

    ported = GENERATOR_PROMPT.splitlines()
    original = src_generator.splitlines()
    added = [line for line in ported if line not in original]
    removed = [line for line in original if line not in ported]

    if removed != EXPECTED_GENERATOR_REMOVALS or [line.strip() for line in added] != EXPECTED_GENERATOR_ADDITIONS:
        print("FAIL: GENERATOR_PROMPT differs from the original source by more (or other) than its 2 approved edits:")
        for line in removed:
            print(f"       - {line}")
        for line in added:
            print(f"       + {line}")
        return 1

    print("PASS  GENERATOR_PROMPT differs by exactly the 2 authorised edits (gradient lines + text-color hex)")
    print("\nAll prompt integrity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
