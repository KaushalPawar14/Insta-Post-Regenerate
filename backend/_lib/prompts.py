"""
PROTECTED INTELLECTUAL PROPERTY -- DO NOT EDIT.

These three prompts are the core IP of this project. They were extracted
byte-for-byte by a script (scripts/extract_prompts.py) rather than retyped,
to guarantee fidelity:

  VISION_PROMPT               <- nodes/agent_2_analyzer.py  :: system_prompt   (verbatim + approved edits)
  GENERATOR_PROMPT             <- nodes/agent_3_generator.py :: formatted_prompt
  FACTSBYTES_GENERATOR_PROMPT <- scripts/factsbytes_prompt_source.txt          (verbatim + approved edit)

VISION_PROMPT contains exactly TWO authorised additions versus its source,
both appended after its last existing bullet (nothing else moved or
reworded): a "diagram fidelity" bullet (never omit or shorten a diagram,
hologram, X-ray, or anatomical/mechanical overlay) and a "text exclusion"
bullet (exclude ALL visible text in the scene, not just logos/captions).

GENERATOR_PROMPT contains exactly TWO authorised modifications versus its
source: two bullets appended to the "Image-to-Text Transition" section pinning
the black gradient overlay's start to the vertical midpoint of the
"INSTAGRAM | FACTS4GENIUS" brand text line; and the text-color instruction's
"yellow" changed to the exact hex #f6ff02. The thin BORDER is still described
as plain "yellow" -- only the text-color instruction was approved for the hex
swap. Nothing else differs -- not the border rules, not the branding text,
not the layout instructions, not the wording of any other sentence.

FACTSBYTES_GENERATOR_PROMPT contains exactly ONE authorised modification
versus its source -- its first ever: the text-color bullet's two "yellow"
mentions changed to #f6ff02 (the base rule and the "highlight in yellow"
clause both describe the same text color). The divider LINES are still
described as plain "yellow" -- only the text-color bullet was approved for
the hex swap.

Do not rewrite, reformat, shorten, "improve", or reinterpret any of the three.
Integrity is enforced at import time by the checksums below; if you change a
prompt the module will refuse to load.
"""

import hashlib

VISION_PROMPT = " \n\n        Analyze the uploaded image and provide:\n\n        1. A detailed prompt describing only the primary image/visual content shown in the post. Exclude the text section, borders, logos, watermarks, branding, layout structure, gradients, backgrounds outside the image area, and all design or formatting elements. Focus solely on the image itself and include sufficient detail to accurately recreate it.\n\n        2. An exact transcription of all visible text in the post, excluding the watermark. Preserve the original wording, capitalization, punctuation, line breaks, spacing hierarchy, and vertical arrangement as closely as possible.\n\n        Important:\n\n        * Prompt 1 must describe only the image content and not the overall post design.\n        * Do not include any details about borders, text placement, branding, formatting, typography, layout, gradients, or post structure in Prompt 1.\n        * If the image contains people, objects, environments, lighting, emotions, actions, clothing, colors, visual effects, camera angles, composition, or atmosphere, describe them comprehensively so the image can be recreated with high accuracy.\n        * Focus on the most important visual characteristics and avoid unnecessary details that do not meaningfully affect the final image.\n        * Optimize the image description for reliable image generation and accurate visual recreation.\n        * Use clear, practical, generation-friendly language that can be reproduced consistently by modern image-generation models.\n        * The final image prompt should maximize visual similarity to the original image while remaining concise, coherent, and highly usable for image generation.\n        * If any visual element is unclear, partially obscured, or ambiguous, describe the closest realistic and visually consistent interpretation while preserving the overall appearance and intent of the original image.\n        * If the image contains visual elements that may be difficult, restricted, overly explicit, graphic, or otherwise unsuitable for reliable image generation, describe the closest visually similar, professional, and non-explicit alternative that preserves the overall composition, mood, subject matter, and intent of the original image.\n        * When adaptation is necessary, preserve the key visual characteristics, scene composition, lighting, subjects, and overall appearance as closely as possible rather than omitting important elements.\n        * Prioritize producing a prompt that can be successfully and consistently rendered by modern image-generation models while maintaining maximum fidelity to the source image.\n\n        Text Extraction Rules:\n\n        * Extract ONLY the main overlay caption/headline text intentionally added to the post for the viewer to read.\n        * Do NOT extract text that belongs to objects within the image itself, including book titles, product names, logos, labels, signs, screens, clothing text, packaging, posters, interface elements, watermarks, or text physically attached to any object.\n        * Preserve capitalization, punctuation, spacing hierarchy, line breaks, and vertical arrangement as closely as possible.\n        * Do not correct grammar, spelling, wording, or formatting.\n        * Do not summarize, paraphrase, or rewrite any text.\n        * When uncertain whether text is a caption or part of an object, exclude it.\n        * Smartly get to know the watermark or the name of the company, then don't include it in the text 2 which includes text.\n        * Always describe any diagram, hologram, X-ray, or anatomical/mechanical overlay in full detail as an essential part of the scene — never omit or shorten it.\n        * Exclude ALL visible text in the scene, not just logos/captions — book titles, signs, labels, screens, clothing text included. Describe such objects by appearance only, never mentioning any words on them.\n        "

# Placeholders {visual_prompt} and {text_transcription} were an f-string in the
# source; rendered via .format() here. Use render_generator_prompt() below.
GENERATOR_PROMPT = '\nThe image provided is for reference layout and formatting only.\n\nReplace:\n\n1. Detailed Image-Only Prompt (Visual Content Only):\n{visual_prompt}\n\n2. Exact Text Transcription (Excluding Watermark):\n{text_transcription}\n\nInstructions:\n* Replace the entire image section with Prompt 1.\n* Replace all text with Prompt 2.\n* Keep the overall layout, structure, proportions, and visual hierarchy identical to the reference.\n* Include the company name exactly as:\n  INSTAGRAM | FACTS4GENIUS\n* Place the company name centered above the main text section only.\n* Keep typography professional, clean, bold, and highly readable.\n* Adjust font size, spacing, and line breaks when necessary to improve balance and readability.\n* Use only white and #f6ff02 text.\n* Do not add extra text effects, decorations, outlines, shadows, or design elements not present in the reference.\n* Maintain the same overall style and appearance as the reference post.\n\nStrict Layout Rules:\n* Preserve the thin yellow border.\n* The border must follow the exact aspect ratio of the final post.\n* Maintain perfectly uniform spacing between the border and all content on every side.\n* The bottom border spacing must match the top, left, and right sides exactly.\n* Ensure the border never appears attached to, cropped by, or touching the bottom edge of the final image.\n* The border must remain fully visible and consistently inset from all four edges.\n* Ensure equal visual padding from the border to the image, text, and all design elements.\n* No edge, corner, top, bottom, left, or right side should appear closer to the border than any other side.\n* The image may extend behind the border if required, but the border must remain the dominant framing element.\n* Keep the composition dynamic while staying faithful to the reference layout.\n* Do not alter the fundamental structure of the design.\n\nImage-to-Text Transition:\n* Add a smooth gradient transition between the image section and the text section.\n* Apply a subtle fade to the lower edges and lower corners of the main image before the gradient begins.\n* The image should gradually blend into the gradient rather than ending abruptly.\n* Ensure the transition appears seamless, professional, and naturally integrated into the design.\n* Avoid any visible hard edges, sharp cutoffs, or unpolished image boundaries.\n* The black gradient overlay must begin exactly at the vertical midpoint of the "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text sits above the gradient start and the lower half sits within it.\n* Do not begin the gradient any higher or lower than this point.\n\nContent Safety & Reliability:\n* When generating people, clothing, poses, or visual scenarios, prioritize professional, platform-safe, non-explicit presentations.\n* Avoid unnecessary nudity, sexualized content, graphic elements, or other content that may prevent successful image generation.\n* If any requested visual element could create generation issues, use a visually similar, professional, policy-compliant alternative while preserving the intended message and style.\n* Prioritize successful image generation and visual quality over unnecessary sensitive details.\n\nGoal:\nCreate a professional, high-quality social-media post that closely matches the reference format while using the new image and text content provided in Prompts 1 and 2.\n'


def render_generator_prompt(visual_prompt: str, text_transcription: str) -> str:
    """Fill the generator prompt exactly as the original f-string did."""
    return GENERATOR_PROMPT.format(
        visual_prompt=visual_prompt,
        text_transcription=text_transcription,
    )


# Bracketed tokens, not .format() braces -- substituted with plain .replace()
# so a stray { or } anywhere in the analyzer output can never break rendering
# the way it could with .format().
FACTSBYTES_GENERATOR_PROMPT = 'Create a professional vertical social-media fact post using the exact formatting structure below.\nFIXED FORMAT:\n• Upper section: Prompt 1 is the ONLY source for the main visual. Make it realistic, detailed, and visually engaging. The image fills the entire available width and may extend fully to the left and right edges of the post.\n• Lower section: A smooth black gradient begins EXACTLY around the central logo divider and progressively becomes completely black behind the text. The image must fade naturally into this gradient with no hard boundary.\n• At the exact horizontal center, place a small glowing light-bulb-style logo with "FACT BYTES" directly underneath it.\n• Place two thin horizontal yellow lines on the same level as the logo: one extending left and one extending right. The lines must be perfectly symmetrical and centered.\n• IMPORTANT: The yellow lines MUST NOT touch the left or right edges. Keep equal, clearly visible horizontal padding between each line endpoint and the post boundaries.\n• The logo/divider and the entire text block must share the same narrower horizontal safe area. Maintain consistent left and right padding.\n• Below the divider, place Prompt 2 as the ONLY text content.\n• IMPORTANT: The text MUST NOT touch either side of the post. Keep equal horizontal padding on both sides. Never allow any word or line to reach the boundaries. Automatically adjust font size and line breaks to remain comfortably inside this safe area.\n• Typography: extremely bold, clean, condensed sans-serif, uppercase, highly readable, matching the reference style and proportions. Center-align all text.\n• Use ONLY bright #f6ff02 and white text. Highlight important portions in #f6ff02 and keep remaining portions white.\n• The entire area behind the text must be deep solid black.\n• Maintain generous and consistent spacing between the divider, text lines, and bottom edge.\n• No additional logos, icons, borders, decorations, text, shadows, outlines, or graphic elements.\n• Preserve the exact visual hierarchy, proportions, spacing, typography weight, divider treatment, and overall appearance of the reference.\nSPACING RULE:\nThe main IMAGE uses the full available width. The YELLOW DIVIDER and TEXT use a narrower centered content area with equal left/right padding. The divider and text must never touch the post boundaries.\nVARIABLE INPUTS:\nPrompt 1:\n[Prompt 1 : Image description]\nPrompt 2:\n[Prompt 2 : Text as it is]'


def render_factsbytes_prompt(image_description: str, text_as_is: str) -> str:
    """Fill the Facts Bytes prompt by replacing its two bracketed tokens."""
    return (
        FACTSBYTES_GENERATOR_PROMPT
        .replace('[Prompt 1 : Image description]', image_description)
        .replace('[Prompt 2 : Text as it is]', text_as_is)
    )


# --- integrity guard -------------------------------------------------------
_VISION_SHA256 = "e1456378d83b845eddcd8127ff9b6a595d201524a71eac5f8fe77778fa38b62d"
_GENERATOR_SHA256 = "26bfa19bf05d12c3d14cfb3e56bb2ed37ef1c7ca9b4602c8fd6f29f0c6f42d54"
_FACTSBYTES_SHA256 = "7d88aede192a92cf32a4d2b087a932b55cddef45a9e5dd53d6d8c8208b4962fd"

if hashlib.sha256(VISION_PROMPT.encode()).hexdigest() != _VISION_SHA256:
    raise RuntimeError("VISION_PROMPT has been modified -- this prompt is protected IP.")
if hashlib.sha256(GENERATOR_PROMPT.encode()).hexdigest() != _GENERATOR_SHA256:
    raise RuntimeError("GENERATOR_PROMPT has been modified -- this prompt is protected IP.")
if hashlib.sha256(FACTSBYTES_GENERATOR_PROMPT.encode()).hexdigest() != _FACTSBYTES_SHA256:
    raise RuntimeError("FACTSBYTES_GENERATOR_PROMPT has been modified -- this prompt is protected IP.")
