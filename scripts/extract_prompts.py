"""
Extracts the two protected prompts byte-for-byte from the original pipeline
source files and emits `backend/_lib/prompts.py` for the new project.

The ONLY modification applied is the single approved edit to the Generator
prompt: two bullets appended to the "Image-to-Text Transition" section that
pin the black gradient overlay's start to the vertical midpoint of the
"INSTAGRAM | FACTS4GENIUS" brand text line.

Nothing else in either prompt is altered. A hash check at the end proves the
vision prompt is untouched and that the generator prompt differs ONLY by the
two inserted lines.
"""

import hashlib
import re
import sys
from pathlib import Path

SRC = Path(r"D:\Testing")
DEST = Path(r"D:\Testing\Instagram application")

analyzer_src = (SRC / "nodes" / "agent_2_analyzer.py").read_text(encoding="utf-8")
generator_src = (SRC / "nodes" / "agent_3_generator.py").read_text(encoding="utf-8")

# ---------------------------------------------------------------- vision prompt
# In agent_2_analyzer.py:  system_prompt = """ ... """
m = re.search(r'system_prompt = """(.*?)"""', analyzer_src, re.DOTALL)
if not m:
    sys.exit("FATAL: could not locate system_prompt in agent_2_analyzer.py")
VISION_PROMPT = m.group(1)

# ------------------------------------------------------------- generator prompt
# In agent_3_generator.py:  formatted_prompt = f""" ... """
m = re.search(r'formatted_prompt = f"""(.*?)"""', generator_src, re.DOTALL)
if not m:
    sys.exit("FATAL: could not locate formatted_prompt in agent_3_generator.py")
GENERATOR_PROMPT_RAW = m.group(1)

# The f-string interpolates two locals. Convert to a .format() template by
# leaving those two placeholders exactly as they appear. There are no other
# braces in the prompt, so this is a faithful 1:1 mapping.
brace_tokens = set(re.findall(r"\{([^}]*)\}", GENERATOR_PROMPT_RAW))
expected = {"visual_prompt", "text_transcription"}
if brace_tokens != expected:
    sys.exit(f"FATAL: unexpected brace tokens in generator prompt: {brace_tokens}")

# ------------------------------------------------- the ONE approved edit
ANCHOR = "* Avoid any visible hard edges, sharp cutoffs, or unpolished image boundaries.\n"
ADDITION = (
    '* The black gradient overlay must begin exactly at the vertical midpoint of the "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text sits above the gradient start and the lower half sits within it.\n'
    "* Do not begin the gradient any higher or lower than this point.\n"
)

if GENERATOR_PROMPT_RAW.count(ANCHOR) != 1:
    sys.exit(
        f"FATAL: anchor line found {GENERATOR_PROMPT_RAW.count(ANCHOR)} times; expected exactly 1"
    )

GENERATOR_PROMPT = GENERATOR_PROMPT_RAW.replace(ANCHOR, ANCHOR + ADDITION)

# ------------------------------------------------------------------ verify diff
before = GENERATOR_PROMPT_RAW.splitlines(keepends=True)
after = GENERATOR_PROMPT.splitlines(keepends=True)
added = [ln for ln in after if ln not in before]
removed = [ln for ln in before if ln not in after]
if len(after) - len(before) != 2 or removed:
    sys.exit(f"FATAL: diff is not exactly +2/-0. added={len(added)} removed={len(removed)}")

print("=== VERIFICATION ===")
print(f"vision prompt      : {len(VISION_PROMPT)} chars, sha256={hashlib.sha256(VISION_PROMPT.encode()).hexdigest()[:16]}")
print(f"generator (source) : {len(GENERATOR_PROMPT_RAW)} chars, sha256={hashlib.sha256(GENERATOR_PROMPT_RAW.encode()).hexdigest()[:16]}")
print(f"generator (ported) : {len(GENERATOR_PROMPT)} chars  (+{len(GENERATOR_PROMPT) - len(GENERATOR_PROMPT_RAW)} chars, +2 lines, -0 lines)")
print("\nLines added (the one approved edit):")
for ln in added:
    print("  + " + ln.rstrip("\n"))

# ------------------------------------------------------------------ emit module
header = '''"""
PROTECTED INTELLECTUAL PROPERTY -- DO NOT EDIT.

These two prompts are the core IP of this project. They were extracted
byte-for-byte from the original pipeline by a script
(scripts/extract_prompts.py) rather than retyped, to guarantee fidelity:

  VISION_PROMPT     <- nodes/agent_2_analyzer.py  :: system_prompt   (verbatim)
  GENERATOR_PROMPT  <- nodes/agent_3_generator.py :: formatted_prompt

GENERATOR_PROMPT contains exactly ONE authorised modification versus the
source: two bullets appended to the "Image-to-Text Transition" section pinning
the black gradient overlay's start to the vertical midpoint of the
"INSTAGRAM | FACTS4GENIUS" brand text line. Nothing else differs -- not the
border rules, not the branding text, not the layout instructions, not the
wording of any other sentence.

Do not rewrite, reformat, shorten, "improve", or reinterpret either prompt.
Integrity is enforced at import time by the checksums below; if you change a
prompt the module will refuse to load.
"""

import hashlib

'''

body = (
    header
    + "VISION_PROMPT = "
    + repr(VISION_PROMPT)
    + "\n\n"
    + "# Placeholders {visual_prompt} and {text_transcription} were an f-string in the\n"
    + "# source; rendered via .format() here. Use render_generator_prompt() below.\n"
    + "GENERATOR_PROMPT = "
    + repr(GENERATOR_PROMPT)
    + "\n\n\n"
    + "def render_generator_prompt(visual_prompt: str, text_transcription: str) -> str:\n"
    + '    """Fill the generator prompt exactly as the original f-string did."""\n'
    + "    return GENERATOR_PROMPT.format(\n"
    + "        visual_prompt=visual_prompt,\n"
    + "        text_transcription=text_transcription,\n"
    + "    )\n\n\n"
    + "# --- integrity guard -------------------------------------------------------\n"
    + f'_VISION_SHA256 = "{hashlib.sha256(VISION_PROMPT.encode()).hexdigest()}"\n'
    + f'_GENERATOR_SHA256 = "{hashlib.sha256(GENERATOR_PROMPT.encode()).hexdigest()}"\n\n'
    + "if hashlib.sha256(VISION_PROMPT.encode()).hexdigest() != _VISION_SHA256:\n"
    + '    raise RuntimeError("VISION_PROMPT has been modified -- this prompt is protected IP.")\n'
    + "if hashlib.sha256(GENERATOR_PROMPT.encode()).hexdigest() != _GENERATOR_SHA256:\n"
    + '    raise RuntimeError("GENERATOR_PROMPT has been modified -- this prompt is protected IP.")\n'
)

out = DEST / "backend" / "_lib" / "prompts.py"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(body, encoding="utf-8")
print(f"\nWrote {out}")

# round-trip check: import it back and compare
sys.path.insert(0, str(out.parent.parent))
import importlib.util

spec = importlib.util.spec_from_file_location("prompts", out)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert mod.VISION_PROMPT == VISION_PROMPT, "round-trip mismatch: VISION_PROMPT"
assert mod.GENERATOR_PROMPT == GENERATOR_PROMPT, "round-trip mismatch: GENERATOR_PROMPT"
rendered = mod.render_generator_prompt("VP", "TT")
assert "VP" in rendered and "TT" in rendered, "placeholder render failed"
print("Round-trip check: PASS (module re-imports to identical strings)")
