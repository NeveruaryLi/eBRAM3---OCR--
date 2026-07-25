"""Build the Case 6A Markdown knowledge base from official eBRAM sources.

The collector deliberately performs mechanical curation only: it removes website
chrome, preserves the official wording, resolves official PDF viewers, and records
source provenance for every emitted document.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import fitz
from lxml import etree, html


SITEMAP_LAST_MODIFIED = "2025-03-06"
USER_AGENT = "eBRAM-Case6A-KB-Curation/1.0 (+https://www.ebram.org/contact_us)"


@dataclass(frozen=True)
class Source:
    filename: str
    url: str
    category: str
    questions: str
    last_modified: str = SITEMAP_LAST_MODIFIED


SOURCES = (
    Source("about-profile.md", "https://www.ebram.org/overview.html", "about", "Q01"),
    Source("about-services.md", "https://www.ebram.org/aim_and_services.html", "about", "Q01"),
    Source("online-arbitration.md", "https://www.ebram.org/online_arbitration.html", "dispute_resolution", "Q01;Q02;Q06;Q09"),
    Source("online-mediation.md", "https://www.ebram.org/online_mediation.html", "dispute_resolution", "Q01;Q02;Q06;Q09"),
    Source("water-seepage-mediation.md", "https://www.ebram.org/Water_Seepage_POM_Scheme.html", "dispute_resolution", "Q01"),
    Source("apec-odr.md", "https://www.ebram.org/apec_odr.html", "dispute_resolution", "Q01;Q02;Q06;Q09"),
    Source("odr-services-platforms-guide.md", "https://www.ebram.org/odr/e_flyer/Online_Dispute_Resolution_ODR_Services_and_Platforms_Guide/", "guide", "Q02;Q03;Q06"),
    Source("rules-procedures-guide.md", "https://www.ebram.org/odr/e_flyer/Rules_and_Procedures_Guide/", "guide", "Q02;Q06"),
    Source("arbitration-rules.md", "https://www.ebram.org/arbitration_rules", "rules", "Q02;Q06;Q09"),
    Source("mediation-rules.md", "https://www.ebram.org/mediation_rules", "rules", "Q02;Q06;Q09"),
    Source("apec-rules.md", "https://www.ebram.org/apec_rules", "rules", "Q06;Q09"),
    Source("panel-application.md", "https://www.ebram.org/panel_application.html", "panels_training", "Q04"),
    Source("codes-and-guidelines.md", "https://www.ebram.org/codes_and_guidelines.html", "panels_training", "Q04"),
    Source("mediator-training.md", "https://www.ebram.org/mediator/training/", "panels_training", "Q04"),
    Source("model-clauses.md", "https://www.ebram.org/model_clauses", "model_clauses", "Q07;Q09"),
    Source("deal-making-overview.md", "https://www.ebram.org/deal_making/e_flyer/", "deal_making", "Q07"),
    Source("deal-making-quick-guide.md", "https://www.ebram.org/deal_making/quick_guide/", "deal_making", "Q07"),
    Source("deal-making-user-guide.md", "https://www.ebram.org/deal_making/user_guide/", "deal_making", "Q07"),
    Source("hearing-support.md", "https://www.ebram.org/hearing_support", "service", "Q01;Q09"),
    Source("electronic-signature.md", "https://www.ebram.org/lawtech/e_signature/", "lawtech", "Q01;Q07"),
    Source("video-conferencing.md", "https://www.ebram.org/lawtech/vc", "lawtech", "Q01;Q09"),
    Source("machine-document-translation.md", "https://www.ebram.org/machine_document_translation_service.html", "lawtech", "Q01;Q08"),
    Source("real-time-captions.md", "https://www.ebram.org/event-real-time-captions-transcription-service/", "lawtech", "Q01;Q08"),
    Source("support-and-contact.md", "https://www.ebram.org/contact_us", "support", "Q05"),
    Source("platform-terms-security.md", "https://www.ebram.org/terms_of_use.html", "policy", "Q03"),
)

FEE_EXTRACTS = (
    ("arbitration-fee-schedule.md", "arbitration-rules.md", "eBRAM Arbitration Rules — Fee Schedules", 36, 39),
    ("mediation-fee-schedule.md", "mediation-rules.md", "eBRAM Mediation Rules — Filing and Administrative Fees", 17, 18),
    ("apec-fee-schedule.md", "apec-rules.md", "eBRAM APEC Rules — Required Fees and Schedules of Fees", 41, 54),
)


def _slug_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _fetch(url: str, cache_dir: Path, delay: float, retries: int = 3) -> tuple[bytes, str, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".html"
    cache_path = cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"
    meta_path = cache_path.with_suffix(cache_path.suffix + ".json")
    if cache_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return cache_path.read_bytes(), meta["content_type"], meta["final_url"]

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Connection": "close"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                content_type = response.headers.get_content_type()
                final_url = response.geturl()
            cache_path.write_bytes(payload)
            meta_path.write_text(
                json.dumps(
                    {"source_url": url, "final_url": final_url, "content_type": content_type},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            time.sleep(delay)
            return payload, content_type, final_url
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay * attempt)
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def resolve_embedded_pdf(page_url: str, document: html.HtmlElement) -> str | None:
    """Return the official PDF URL embedded by eBRAM's PDF.js viewer."""
    for raw_src in document.xpath("//iframe/@src"):
        absolute = urllib.parse.urljoin(page_url, raw_src)
        parsed = urllib.parse.urlparse(absolute)
        query = urllib.parse.parse_qs(parsed.query)
        file_values = query.get("file")
        if file_values:
            resolved = urllib.parse.urljoin(page_url, file_values[0])
            parsed_resolved = urllib.parse.urlsplit(resolved)
            return urllib.parse.urlunsplit(
                (
                    parsed_resolved.scheme,
                    parsed_resolved.netloc,
                    urllib.parse.quote(parsed_resolved.path, safe="/%"),
                    urllib.parse.quote(parsed_resolved.query, safe="=&%"),
                    parsed_resolved.fragment,
                )
            )
        if parsed.path.lower().endswith(".pdf"):
            return absolute
    return None


def _inline(node: etree._Element, base_url: str) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node:
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        child_text = _inline(child, base_url)
        if tag == "a":
            href = urllib.parse.urljoin(base_url, child.get("href", ""))
            if href.startswith(("http://", "https://")) and child_text.strip():
                parts.append(f"[{child_text.strip()}]({href})")
            else:
                parts.append(child_text)
        elif tag in {"strong", "b"} and child_text.strip():
            parts.append(f"**{child_text.strip()}**")
        elif tag in {"em", "i"} and child_text.strip():
            parts.append(f"*{child_text.strip()}*")
        elif tag == "br":
            parts.append("\n")
        elif tag == "img":
            src = urllib.parse.urljoin(base_url, child.get("src", ""))
            alt = _slug_text(child.get("alt", "")) or "Official eBRAM image"
            if src.startswith(("http://", "https://")):
                parts.append(f"![{alt}]({src})")
        else:
            parts.append(child_text)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _table_markdown(table: etree._Element, base_url: str) -> str:
    rows: list[list[str]] = []
    for tr in table.xpath(".//tr"):
        cells = tr.xpath("./th|./td")
        if cells:
            rows.append([_slug_text(_inline(cell, base_url)).replace("|", "\\|") for cell in cells])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    return "\n".join(
        ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows[1:]]
    )


def _blocks(node: etree._Element, base_url: str) -> Iterable[str]:
    for child in node:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag in {"script", "style", "noscript", "svg", "form", "button"}:
            continue
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = _slug_text(_inline(child, base_url))
            if text:
                yield f"{'#' * int(tag[1])} {text}"
        elif tag == "p":
            text = _slug_text(_inline(child, base_url))
            if text:
                yield text
        elif tag in {"ul", "ol"}:
            ordered = tag == "ol"
            lines = []
            for index, item in enumerate(child.xpath("./li"), 1):
                text = _slug_text(_inline(item, base_url))
                if text:
                    lines.append(f"{index}. {text}" if ordered else f"- {text}")
            if lines:
                yield "\n".join(lines)
        elif tag == "table":
            table_md = _table_markdown(child, base_url)
            if table_md:
                yield table_md
        elif tag == "img":
            src = urllib.parse.urljoin(base_url, child.get("src", ""))
            alt = _slug_text(child.get("alt", "")) or "Official eBRAM image"
            if src.startswith(("http://", "https://")):
                yield f"![{alt}]({src})"
        else:
            block_children = child.xpath(
                "./h1|./h2|./h3|./h4|./h5|./h6|./p|./ul|./ol|./table|./div|./section|./article"
            )
            if block_children:
                yield from _blocks(child, base_url)
            else:
                text = _slug_text(_inline(child, base_url))
                if text:
                    yield text


def html_to_markdown(payload: bytes, source_url: str) -> tuple[str, str]:
    document = html.fromstring(payload, base_url=source_url)
    title_nodes = document.xpath("//title")
    title = _slug_text(title_nodes[0].text_content()) if title_nodes else source_url
    roots = document.xpath("//main") or document.xpath("//body")
    if not roots:
        raise ValueError(f"No readable HTML body found at {source_url}")
    root = roots[0]
    for selector in (
        ".//header",
        ".//footer",
        ".//nav",
        ".//*[contains(concat(' ', normalize-space(@class), ' '), ' year-nav ')]",
        ".//*[@id='snackbar']",
    ):
        for unwanted in root.xpath(selector):
            parent = unwanted.getparent()
            if parent is not None:
                parent.remove(unwanted)
    blocks = list(dict.fromkeys(block.strip() for block in _blocks(root, source_url) if block.strip()))
    markdown = "\n\n".join(blocks).strip()
    if len(re.sub(r"\W", "", markdown)) < 80:
        raise ValueError(f"Official page produced too little readable content: {source_url}")
    return title, markdown


def pdf_to_markdown(payload: bytes, source_url: str) -> tuple[str, str]:
    document = fitz.open(stream=payload, filetype="pdf")
    if document.needs_pass:
        raise ValueError(f"Encrypted PDF is not importable: {source_url}")
    metadata = document.metadata or {}
    title = _slug_text(metadata.get("title") or Path(urllib.parse.urlparse(source_url).path).stem)
    pages: list[str] = []
    for index, page in enumerate(document, 1):
        text = page.get_text("text", sort=True).replace("\x00", "")
        lines = [_slug_text(line) for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        if text:
            pages.append(f"## Page {index}\n\n{text}")
    document.close()
    body = "\n\n".join(pages).strip()
    if len(re.sub(r"\W", "", body)) < 80:
        raise ValueError(f"Official PDF contains no usable text layer: {source_url}")
    return title or source_url, body


def _document_text(source: Source, title: str, resolved_url: str, body: str) -> tuple[str, str]:
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    header = (
        f"# {title}\n\n"
        f"- Source: {resolved_url}\n"
        f"- Canonical page: {source.url}\n"
        f"- Retrieved: {date.today().isoformat()}\n"
        f"- Last modified: {source.last_modified}\n"
        f"- Category: {source.category}\n"
        f"- Content SHA-256: `{body_hash}`\n\n"
    )
    if body.startswith("# "):
        body = body.split("\n", 1)[1].lstrip() if "\n" in body else ""
    return header + body.rstrip() + "\n", body_hash


def extract_page_range(markdown: str, first_page: int, last_page: int) -> str:
    """Extract complete PDF page sections without rewriting their wording."""
    sections = re.split(r"(?=^## Page \d+\s*$)", markdown, flags=re.MULTILINE)
    selected = []
    for section in sections:
        match = re.match(r"^## Page (\d+)\s*$", section, flags=re.MULTILINE)
        if match and first_page <= int(match.group(1)) <= last_page:
            selected.append(section.strip())
    if not selected:
        raise ValueError(f"No PDF pages found in range {first_page}-{last_page}")
    return "\n\n".join(selected)


def build(output_dir: Path, *, delay: float = 1.0, refresh: bool = False) -> list[dict[str, str]]:
    docs_dir = output_dir / "docs"
    cache_dir = output_dir / ".cache"
    docs_dir.mkdir(parents=True, exist_ok=True)
    if refresh and cache_dir.exists():
        for path in cache_dir.iterdir():
            if path.is_file():
                path.unlink()

    rows: list[dict[str, str]] = []
    for source in SOURCES:
        row = {
            "filename": source.filename,
            "canonical_url": source.url,
            "resolved_url": "",
            "last_modified": source.last_modified,
            "category": source.category,
            "coverage_questions": source.questions,
            "content_sha256": "",
            "characters": "0",
            "status": "failed",
            "error": "",
        }
        try:
            payload, content_type, final_url = _fetch(source.url, cache_dir, delay)
            resolved_url = final_url
            if content_type == "application/pdf" or final_url.lower().split("?")[0].endswith(".pdf"):
                title, body = pdf_to_markdown(payload, resolved_url)
            else:
                tree = html.fromstring(payload, base_url=final_url)
                embedded_pdf = resolve_embedded_pdf(final_url, tree)
                if embedded_pdf:
                    payload, _, resolved_url = _fetch(embedded_pdf, cache_dir, delay)
                    title_nodes = tree.xpath("//title")
                    wrapper_title = _slug_text(title_nodes[0].text_content()) if title_nodes else ""
                    pdf_title, body = pdf_to_markdown(payload, resolved_url)
                    title = wrapper_title or pdf_title
                else:
                    title, body = html_to_markdown(payload, final_url)
            document, digest = _document_text(source, title, resolved_url, body)
            (docs_dir / source.filename).write_text(document, encoding="utf-8")
            row.update(
                {
                    "resolved_url": resolved_url,
                    "content_sha256": digest,
                    "characters": str(len(body)),
                    "status": "ready",
                }
            )
            print(f"READY {source.filename} ({len(body):,} chars)")
        except Exception as exc:  # Each source is reported independently.
            row["error"] = str(exc)
            print(f"FAILED {source.filename}: {exc}")
        rows.append(row)

    primary_rows = {row["filename"]: row for row in rows}
    source_by_filename = {source.filename: source for source in SOURCES}
    for filename, parent_filename, title, first_page, last_page in FEE_EXTRACTS:
        parent_row = primary_rows[parent_filename]
        parent_source = source_by_filename[parent_filename]
        body = extract_page_range(
            (docs_dir / parent_filename).read_text(encoding="utf-8"),
            first_page,
            last_page,
        )
        derived_source = Source(
            filename,
            parent_source.url,
            "fees",
            "Q06",
            parent_source.last_modified,
        )
        document, digest = _document_text(
            derived_source,
            title,
            parent_row["resolved_url"],
            body,
        )
        (docs_dir / filename).write_text(document, encoding="utf-8")
        rows.append(
            {
                "filename": filename,
                "canonical_url": parent_source.url,
                "resolved_url": parent_row["resolved_url"],
                "last_modified": parent_source.last_modified,
                "category": "fees",
                "coverage_questions": "Q06",
                "content_sha256": digest,
                "characters": str(len(body)),
                "status": "ready",
                "error": "",
            }
        )
        print(f"READY {filename} ({len(body):,} chars)")

    manifest_path = output_dir / "source_manifest.csv"
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    rows = build(args.output_dir, delay=max(args.delay, 0.2), refresh=args.refresh)
    failed = [row for row in rows if row["status"] != "ready"]
    print(f"Built {len(rows) - len(failed)}/{len(rows)} knowledge documents")
    if failed and not args.allow_partial:
        raise SystemExit("Knowledge build is incomplete; inspect source_manifest.csv")


if __name__ == "__main__":
    main()
