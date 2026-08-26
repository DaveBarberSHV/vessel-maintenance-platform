"""
Page images: renders selected PDF pages to images and uploads them to
Supabase Storage, so citations can link to "see the actual page" —
including diagrams, exploded views, and tables that plain text extraction
can't fully capture.

Why this exists: a real, motivating case — a 1415-page parts manual where
each part is documented as a table on one page with its exploded-view
diagram either on the same page or a following "1 of 2 / 2 of 2" page.
The diagram often carries information (which callout number points to
which part, how pieces connect) that text extraction alone can't convey.
See BACKLOG.md.

Why images are rendered at ingestion time, not on-demand at query time:
the deployed app has no access to the original PDF files — it only ever
receives extracted text (now in Postgres, see the pgvector migration).
Images have to be produced once, up front, wherever the real PDF bytes
are (Dave's machine, during scan_folder.py), and uploaded somewhere the
deployed app can reach.

Which pages get rendered: not blindly every page, since a large manual
would mean a lot of storage for pages nobody needs. A page is rendered if
IT has real extracted text, OR either adjacent page does — this protects
exactly the "picture continues on the next page, with little or no text
of its own" pattern from being skipped by a naive "only pages with text"
rule, while still skipping genuinely blank/separator pages.
"""

import os
from pathlib import Path

import pdfplumber
import requests

STORAGE_BUCKET = "tm-page-images"
RENDER_RESOLUTION = 150  # tested: good balance of legibility vs. file size (~70KB/page at this setting)


def get_supabase_storage_config() -> tuple[str, str]:
    """Returns (project_url, service_role_key). Deliberately separate
    credentials from SUPABASE_DB_URL (the Postgres connection string) —
    Storage is a different Supabase product with its own REST API, not
    reachable over the Postgres wire protocol."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise ValueError(
            "No SUPABASE_URL / SUPABASE_SERVICE_KEY available. These are "
            "different from SUPABASE_DB_URL — get them from Supabase's "
            "Settings -> API page (Project URL, and the service_role key, "
            "not the anon key). See docs/architecture.md."
        )
    return url, key


def ensure_storage_bucket():
    """Creates the storage bucket if it doesn't already exist — safe to
    call every time, mirrors the ensure_pg_schema() pattern used for the
    Postgres table. Bucket is public: consistent with the app's current
    security posture (no password gate yet either — see BACKLOG.md), and
    page images aren't sensitive on their own."""
    url, key = get_supabase_storage_config()
    resp = requests.post(
        f"{url}/storage/v1/bucket",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"id": STORAGE_BUCKET, "name": STORAGE_BUCKET, "public": True},
    )
    # 409 = bucket already exists, which is fine and expected on every run after the first.
    if resp.status_code not in (200, 201, 409):
        raise RuntimeError(f"Failed to create/verify storage bucket: {resp.status_code} {resp.text}")


def should_render_page(page_number: int, pages_with_text: set[int]) -> bool:
    """A page is worth rendering if it has real text, or either
    neighboring page does (protects picture-only continuation pages —
    see module docstring)."""
    return (
        page_number in pages_with_text
        or (page_number - 1) in pages_with_text
        or (page_number + 1) in pages_with_text
    )


def render_page_image(pdf_path: Path, page_number: int) -> bytes:
    """Renders one page to PNG bytes. Requires genuine PDF bytes (same
    requirement as table extraction elsewhere in this pipeline) — will
    raise if given the platform-preprocessed preview format instead."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]  # pdfplumber is 0-indexed; our page numbers are 1-indexed
        im = page.to_image(resolution=RENDER_RESOLUTION)
        from io import BytesIO
        buf = BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def storage_path(source_file: str, page_number: int) -> str:
    return f"{source_file}/p{page_number}.png"


def public_url(source_file: str, page_number: int) -> str:
    url, _ = get_supabase_storage_config()
    return f"{url}/storage/v1/object/public/{STORAGE_BUCKET}/{storage_path(source_file, page_number)}"


def upload_page_image(image_bytes: bytes, source_file: str, page_number: int) -> str:
    """Uploads one page image, returns its public URL. Idempotent (safe
    to re-run for the same page — overwrites rather than erroring) via
    the x-upsert header."""
    url, key = get_supabase_storage_config()
    path = storage_path(source_file, page_number)
    resp = requests.post(
        f"{url}/storage/v1/object/{STORAGE_BUCKET}/{path}",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "image/png",
            "x-upsert": "true",
        },
        data=image_bytes,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to upload page image {path}: {resp.status_code} {resp.text}")
    return public_url(source_file, page_number)


def render_and_upload_selected_pages(pdf_path: Path, source_file: str,
                                      pages_with_text: set[int], total_pages: int) -> dict[int, str]:
    """Main entry point — renders and uploads every page that
    should_render_page() selects. Returns {page_number: public_url} for
    successfully-uploaded pages. Failures on individual pages are caught
    and skipped (printed as a warning) rather than aborting the whole
    file — consistent with this pipeline's existing resilience pattern
    (one bad chunk/page shouldn't sink the whole file's ingestion)."""
    urls = {}
    for page_number in range(1, total_pages + 1):
        if not should_render_page(page_number, pages_with_text):
            continue
        try:
            image_bytes = render_page_image(pdf_path, page_number)
            urls[page_number] = upload_page_image(image_bytes, source_file, page_number)
        except Exception as e:
            print(f"  WARNING: could not render/upload page {page_number}: {e}")
    return urls
