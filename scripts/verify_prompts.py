"""
Guard test for the two protected prompts.  Run:  npm run verify:prompts

Two independent checks:

1. ALWAYS -- import `api/_lib/prompts.py`, which self-verifies its contents
   against embedded SHA-256 checksums and refuses to load if either prompt was
   edited.

2. WHEN AVAILABLE -- if the original pipeline is reachable (pass its path, or
   set ORIGINAL_PIPELINE_DIR), diff both prompts against the source of truth
   and assert that:
       - VISION_PROMPT is byte-for-byte identical
       - GENERATOR_PROMPT differs by EXACTLY the two authorised gradient lines
"""

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

EXPECTED_ADDITIONS = [
    '* The black gradient overlay must begin exactly at the vertical midpoint of the "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text sits above the gradient start and the lower half sits within it.',
    "* Do not begin the gradient any higher or lower than this point.",
]


def main() -> int:
    # --- check 1: embedded integrity guard ---------------------------------
    try:
        from _lib.prompts import (  # noqa: PLC0415
            GENERATOR_PROMPT,
            VISION_PROMPT,
            render_generator_prompt,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    print("PASS  embedded checksums match (prompts are unmodified)")

    rendered = render_generator_prompt(visual_prompt="<VP>", text_transcription="<TT>")
    if "<VP>" not in rendered or "<TT>" not in rendered:
        print("FAIL: generator prompt placeholders did not render")
        return 1
    if "{" in rendered.replace("{", "", 0) and re.search(r"\{[a-z_]+\}", rendered):
        print("FAIL: unrendered placeholder left in the generator prompt")
        return 1
    print("PASS  generator prompt renders both placeholders")

    # --- check 2: diff against the original pipeline, if reachable ----------
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
        print("SKIP  original pipeline not reachable; checksum check only")
        print("      (pass its path as an argument to run the full diff)")
        return 0

    analyzer = (source / "nodes" / "agent_2_analyzer.py").read_text(encoding="utf-8")
    generator = (source / "nodes" / "agent_3_generator.py").read_text(encoding="utf-8")

    src_vision = re.search(r'system_prompt = """(.*?)"""', analyzer, re.DOTALL).group(1)
    src_generator = re.search(r'formatted_prompt = f"""(.*?)"""', generator, re.DOTALL).group(1)

    if VISION_PROMPT != src_vision:
        print("FAIL: VISION_PROMPT differs from the original source")
        return 1
    print(f"PASS  VISION_PROMPT byte-identical to source ({len(VISION_PROMPT)} chars)")

    ported = GENERATOR_PROMPT.splitlines()
    original = src_generator.splitlines()
    added = [line for line in ported if line not in original]
    removed = [line for line in original if line not in ported]

    if removed:
        print(f"FAIL: {len(removed)} line(s) removed from GENERATOR_PROMPT:")
        for line in removed:
            print(f"       - {line}")
        return 1

    if [line.strip() for line in added] != EXPECTED_ADDITIONS:
        print("FAIL: GENERATOR_PROMPT additions are not the two authorised lines:")
        for line in added:
            print(f"       + {line}")
        return 1

    print("PASS  GENERATOR_PROMPT differs by exactly the 2 authorised gradient lines")
    print("\nAll prompt integrity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
