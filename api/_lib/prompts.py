"""
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

VISION_PROMPT = " \n\n        Analyze the uploaded image and provide:\n\n        1. A detailed prompt describing only the primary image/visual content shown in the post. Exclude the text section, borders, logos, watermarks, branding, layout structure, gradients, backgrounds outside the image area, and all design or formatting elements. Focus solely on the image itself and include sufficient detail to accurately recreate it.\n\n        2. An exact transcription of all visible text in the post, excluding the watermark. Preserve the original wording, capitalization, punctuation, line breaks, spacing hierarchy, and vertical arrangement as closely as possible.\n\n        Important:\n\n        * Prompt 1 must describe only the image content and not the overall post design.\n        * Do not include any details about borders, text placement, branding, formatting, typography, layout, gradients, or post structure in Prompt 1.\n        * If the image contains people, objects, environments, lighting, emotions, actions, clothing, colors, visual effects, camera angles, composition, or atmosphere, describe them comprehensively so the image can be recreated with high accuracy.\n        * Focus on the most important visual characteristics and avoid unnecessary details that do not meaningfully affect the final image.\n        * Optimize the image description for reliable image generation and accurate visual recreation.\n        * Use clear, practical, generation-friendly language that can be reproduced consistently by modern image-generation models.\n        * The final image prompt should maximize visual similarity to the original image while remaining concise, coherent, and highly usable for image generation.\n        * If any visual element is unclear, partially obscured, or ambiguous, describe the closest realistic and visually consistent interpretation while preserving the overall appearance and intent of the original image.\n        * If the image contains visual elements that may be difficult, restricted, overly explicit, graphic, or otherwise unsuitable for reliable image generation, describe the closest visually similar, professional, and non-explicit alternative that preserves the overall composition, mood, subject matter, and intent of the original image.\n        * When adaptation is necessary, preserve the key visual characteristics, scene composition, lighting, subjects, and overall appearance as closely as possible rather than omitting important elements.\n        * Prioritize producing a prompt that can be successfully and consistently rendered by modern image-generation models while maintaining maximum fidelity to the source image.\n\n        Text Extraction Rules:\n\n        * Transcribe all visible text exactly as shown, excluding the watermark.\n        * Preserve capitalization, punctuation, spacing hierarchy, line breaks, and vertical arrangement as closely as possible.\n        * Do not correct grammar, spelling, wording, or formatting.\n        * Do not summarize, paraphrase, or rewrite any text.\n        * Include every visible word, number, and symbol present in the image except the watermark.\n        * Smartly get to know the watermark or the name of the company, then don't include it in the text 2 which includes text.\n        "

# Placeholders {visual_prompt} and {text_transcription} were an f-string in the
# source; rendered via .format() here. Use render_generator_prompt() below.
GENERATOR_PROMPT = '\nThe image provided is for reference layout and formatting only.\n\nReplace:\n\n1. Detailed Image-Only Prompt (Visual Content Only):\n{visual_prompt}\n\n2. Exact Text Transcription (Excluding Watermark):\n{text_transcription}\n\nInstructions:\n* Replace the entire image section with Prompt 1.\n* Replace all text with Prompt 2.\n* Keep the overall layout, structure, proportions, and visual hierarchy identical to the reference.\n* Include the company name exactly as:\n  INSTAGRAM | FACTS4GENIUS\n* Place the company name centered above the main text section only.\n* Keep typography professional, clean, bold, and highly readable.\n* Adjust font size, spacing, and line breaks when necessary to improve balance and readability.\n* Use only white and yellow text.\n* Do not add extra text effects, decorations, outlines, shadows, or design elements not present in the reference.\n* Maintain the same overall style and appearance as the reference post.\n\nStrict Layout Rules:\n* Preserve the thin yellow border.\n* The border must follow the exact aspect ratio of the final post.\n* Maintain perfectly uniform spacing between the border and all content on every side.\n* The bottom border spacing must match the top, left, and right sides exactly.\n* Ensure the border never appears attached to, cropped by, or touching the bottom edge of the final image.\n* The border must remain fully visible and consistently inset from all four edges.\n* Ensure equal visual padding from the border to the image, text, and all design elements.\n* No edge, corner, top, bottom, left, or right side should appear closer to the border than any other side.\n* The image may extend behind the border if required, but the border must remain the dominant framing element.\n* Keep the composition dynamic while staying faithful to the reference layout.\n* Do not alter the fundamental structure of the design.\n\nImage-to-Text Transition:\n* Add a smooth gradient transition between the image section and the text section.\n* Apply a subtle fade to the lower edges and lower corners of the main image before the gradient begins.\n* The image should gradually blend into the gradient rather than ending abruptly.\n* Ensure the transition appears seamless, professional, and naturally integrated into the design.\n* Avoid any visible hard edges, sharp cutoffs, or unpolished image boundaries.\n* The black gradient overlay must begin exactly at the vertical midpoint of the "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text sits above the gradient start and the lower half sits within it.\n* Do not begin the gradient any higher or lower than this point.\n\nContent Safety & Reliability:\n* When generating people, clothing, poses, or visual scenarios, prioritize professional, platform-safe, non-explicit presentations.\n* Avoid unnecessary nudity, sexualized content, graphic elements, or other content that may prevent successful image generation.\n* If any requested visual element could create generation issues, use a visually similar, professional, policy-compliant alternative while preserving the intended message and style.\n* Prioritize successful image generation and visual quality over unnecessary sensitive details.\n\nGoal:\nCreate a professional, high-quality social-media post that closely matches the reference format while using the new image and text content provided in Prompts 1 and 2.\n'


def render_generator_prompt(visual_prompt: str, text_transcription: str) -> str:
    """Fill the generator prompt exactly as the original f-string did."""
    return GENERATOR_PROMPT.format(
        visual_prompt=visual_prompt,
        text_transcription=text_transcription,
    )


# --- integrity guard -------------------------------------------------------
_VISION_SHA256 = "afbc9025a9763b56bcc805f039a319776afa7fed4647bb16d0adaba06a214b00"
_GENERATOR_SHA256 = "70250669c94b24315a7fd94d1762b3fb1e39ceb25d250b561be44aee05be43ac"

if hashlib.sha256(VISION_PROMPT.encode()).hexdigest() != _VISION_SHA256:
    raise RuntimeError("VISION_PROMPT has been modified -- this prompt is protected IP.")
if hashlib.sha256(GENERATOR_PROMPT.encode()).hexdigest() != _GENERATOR_SHA256:
    raise RuntimeError("GENERATOR_PROMPT has been modified -- this prompt is protected IP.")
