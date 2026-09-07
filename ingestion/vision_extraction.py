"""
Vision-based text extraction for image-only pages (Aug 2026).

Real motivating case: drawings, wiring diagrams, and dense control-panel
screenshots often have NO real text layer (or only a caption/page-number
worth of one), so they're currently just skipped by ingestion entirely —
none of their content is searchable at all. Real, concrete example: a
wiring diagram means an engineer asking "what is the wiring for the
Forward Bridge Control Panel?" gets nothing back, even though that exact
label is annotated right on the drawing.

This module sends a page's rendered image (already produced by
page_images.py at ingestion time — no new rendering step needed) to
Claude's vision-capable API and asks it to transcribe every visible
label/text verbatim, rather than skipping the page. The result is treated
as that page's chunk text, going through the exact same
chunk -> embed -> store -> retrieve pipeline as every other document —
no parallel system.

Deliberately scoped to TRANSCRIPTION, not interpretation (Aug 2026, see
BACKLOG.md for the full two-tier reasoning): reading printed labels off a
drawing is something a vision model does reliably; interpreting arrows,
symbols, or spatial relationships is a meaningfully harder, less-proven
problem, and getting it wrong on something like rotation direction could
matter for a real maintenance decision. A "tier 2" (interpretation) mode
is intentionally not built here — this only ever asks for what's
literally written on the page.

Real, concrete testing (Aug 2026): tried against a real Berg Propulsion
control panel screenshot and a real azimuth thruster drawing — caught
extensive real labels the existing text-layer extraction completely
missed (e.g. "PROPELLER IN SERVICE", "MAIN CLUTCH ENGAGED", "AZIMUTH HULL
FOUNDATION FLANGE"). Also surfaced a real, separate finding: one small,
dense table on the thruster drawing was illegible even zoomed in 6x on
the source image — confirmed as a source-resolution problem, not a
vision-model weakness, which is why render_page_image() (page_images.py)
now supports a higher resolution specifically for this use case; see
scan_folder.py's vision-extraction call site.
"""

import base64
import os


VISION_PROMPT = """This image is a page from a technical/marine engineering \
manual — possibly a wiring diagram, schematic, control panel, or a dense \
technical drawing with embedded labels.

Transcribe EVERY piece of text visible in the image, exactly as written — \
every label, equipment name, panel name, button/indicator text, table \
value, figure caption, and any other visible text. Include ALL of it, not \
a summary. Preserve exact spelling, capitalization, and abbreviations \
(e.g. "FWD BRIDGE CONTROL PANEL" stays exactly that, not "Forward Bridge \
Control Panel"). Group related labels together with brief spatial context \
if it's clear (e.g. "labeled near the top-left gauge"), but do not \
interpret meaning, infer relationships, or describe what the diagram as a \
whole depicts — this must be a literal transcription of visible text, not \
an explanation of the drawing.

If there is genuinely no legible text anywhere in the image, respond with \
exactly: NO_TEXT_FOUND"""


def extract_text_from_image(image_bytes: bytes, media_type: str = "image/png",
                             api_key: str | None = None) -> str:
    """Sends one page image to Claude's vision API, returns the transcribed
    text (or "" if none was found). Raises ValueError if no API key is
    available — same pattern as answer_query.get_answer(), so a missing
    key fails loudly and specifically rather than silently producing
    empty/wrong ingestion data.

    Default media_type is "image/png" (Aug 2026, real bug fix) — not
    "image/jpeg" as originally written. The real production source of
    images for this function is always page_images.render_page_image(),
    which hard-codes PNG output; the original JPEG default was based on
    early test images from an unrelated source and caused a real,
    reproducible 400 error ("specified using the image/jpeg media type,
    but the image appears to be a image/png image") the first time this
    was tried against a real production image."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "No ANTHROPIC_API_KEY available. Set the ANTHROPIC_API_KEY "
            "environment variable, or pass api_key= explicitly."
        )

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64_image},
                },
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    )
    text = response.content[0].text.strip()
    if text == "NO_TEXT_FOUND":
        return ""
    return text


VISION_TRANSCRIPT_MARKER = "[AI-transcribed from a drawing/image — no native text layer on this page]"


def extract_text_from_tiled_page(tile_bytes_list: list[bytes],
                                   api_key: str | None = None) -> str:
    """Extracts text from a large-format page rendered as overlapping tiles
    (Sept 2026, see page_images.tile_page_for_vision() and BACKLOG.md).

    Calls extract_text_from_image() on each tile, then combines the results
    into a single transcription. Duplicate text in overlap zones is acceptable
    — it's better to have minor redundancy than to miss text at a tile boundary,
    and the embedding treats the combined text as one chunk anyway.

    A single-tile list (normal-format page) passes through to
    extract_text_from_image() unchanged — same behavior as before tiling.

    Tile order is preserved (left-to-right, top-to-bottom) so the combined
    text flows in roughly the same spatial order as the drawing itself,
    which helps retrieval since related labels appear near each other."""
    if not tile_bytes_list:
        return ""

    if len(tile_bytes_list) == 1:
        return extract_text_from_image(tile_bytes_list[0], api_key=api_key)

    tile_texts = []
    for i, tile_bytes in enumerate(tile_bytes_list, start=1):
        text = extract_text_from_image(tile_bytes, api_key=api_key)
        if text:
            tile_texts.append(f"[Tile {i} of {len(tile_bytes_list)}]\n{text}")

    return "\n\n".join(tile_texts) if tile_texts else ""





def format_vision_chunk_text(transcribed_text: str) -> str:
    """Wraps vision-transcribed text with a clear marker before it enters
    the normal chunk/embed/retrieve pipeline (Aug 2026). Deliberately a
    plain text prefix rather than a new database column — flows through
    every existing part of the pipeline (chunking, embedding, citation
    display, and Claude's own answer prompt) with zero schema or code
    changes needed elsewhere, and means an answer built from this content
    naturally reflects that it came from a transcribed drawing, not a
    typed manual page, the same way it already reflects any other source
    context given to it."""
    return f"{VISION_TRANSCRIPT_MARKER}\n\n{transcribed_text}"
