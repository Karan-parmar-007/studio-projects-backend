"""Turn a document into ordered blocks (text, table, figure) with their heading path.

Docling does the real work in the worker image (layout, reading order, tables,
figure crops, figure classification). Without Docling (API image, tests) plain
text, Markdown and text-only PDFs (pypdf) still work.
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Figures that carry no meaning for a reader: never stored, never shown.
SKIP_FIGURE_LABELS = {"logo", "icon", "signature", "qr_code", "bar_code", "stamp", "page_thumbnail"}
MIN_FIGURE_SIDE = 96

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".html", ".htm"}


class ParseError(Exception):
    pass


@dataclass
class FigureDraft:
    number: int  # reading order, 1-based
    image_png: bytes
    width: int
    height: int
    caption: str = ""
    label: str = ""
    page: int | None = None
    # The document's own label from the caption: "3", or "11.6.1" in books like D2L.
    doc_number: str | None = None


@dataclass
class Block:
    kind: str  # text | table | figure
    text: str
    headings: list[str] = field(default_factory=list)
    page: int | None = None
    figure: FigureDraft | None = None


@dataclass
class ParsedDoc:
    title: str
    blocks: list[Block]
    pages: int = 0

    @property
    def figures(self) -> list[FigureDraft]:
        return [block.figure for block in self.blocks if block.figure is not None]


_FIG_NUMBER = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|chart|diagram|image|plate)\s*(\d+(?:\.\d+)*)", re.IGNORECASE
)

# Sections that only list other places in the document. Their entries ("2.3.2 Vectors .... 42")
# match almost any keyword search and push real passages out, so they are never indexed.
SKIP_SECTIONS = {"contents", "table of contents", "index", "bibliography", "references", "list of figures", "list of tables"}


def caption_number(caption: str) -> str | None:
    """"Fig. 11.6.1: Attention" -> "11.6.1"; "Figure 3. Results" -> "3"."""
    match = _FIG_NUMBER.match(caption or "")
    return match.group(1) if match else None


_GLYPH = re.compile(r"glyph\[[^\]]*\]\s*")


def clean_text(text: str) -> str:
    """Drop PDF font artefacts like "glyph[a116]" that some LaTeX books leave in the text."""
    return _GLYPH.sub("", text).strip()


def attach_captions(blocks: list[Block]) -> list[Block]:
    """Some PDFs (D2L) put "Fig. 11.6.1 ..." as an ordinary paragraph right after the picture
    instead of a marked caption. Give it to the figure and drop it from the running text."""
    out: list[Block] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        nxt = blocks[index + 1] if index + 1 < len(blocks) else None
        figure = block.figure
        if (
            block.kind == "figure"
            and figure is not None
            and figure.doc_number is None
            and nxt is not None
            and nxt.kind == "text"
            and caption_number(nxt.text) is not None
        ):
            caption = nxt.text[:1000]
            figure.caption = caption
            figure.doc_number = caption_number(caption)
            block.text = caption
            out.append(block)
            index += 2
            continue
        out.append(block)
        index += 1
    return out


def skipped_heading(name: str) -> bool:
    return re.sub(r"[^a-z ]+", "", name.lower()).strip() in SKIP_SECTIONS


def docling_available() -> bool:
    try:
        import docling  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def parse_file(path: str | Path, filename: str, *, ocr: bool | None = None) -> ParsedDoc:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError("Upload a PDF, Word, PowerPoint, Markdown, HTML or text file.")
    if ext in {".txt"}:
        return parse_text(Path(path).read_text(encoding="utf-8", errors="replace"), filename)
    if ext in {".md", ".markdown"}:
        return parse_markdown(Path(path).read_text(encoding="utf-8", errors="replace"), filename)
    if docling_available():
        return parse_with_docling(path, filename, ocr=ocr)
    if ext == ".pdf":
        return parse_pdf_text(path, filename)
    raise ParseError("This file type needs the document parser, which is not installed here.")


# ---------------------------------------------------------------- plain formats


def _title_from(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return stem[:300] or "Document"


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def parse_text(text: str, filename: str) -> ParsedDoc:
    blocks = [Block(kind="text", text=para) for para in _paragraphs(text)]
    return ParsedDoc(title=_title_from(filename), blocks=blocks)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def parse_markdown(text: str, filename: str) -> ParsedDoc:
    headings: list[str] = []
    blocks: list[Block] = []
    buffer: list[str] = []
    title = ""

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if headings and skipped_heading(headings[-1]):
            return
        for para in _paragraphs(body):
            kind = "table" if para.lstrip().startswith("|") else "text"
            blocks.append(Block(kind=kind, text=para, headings=list(headings)))

    for line in text.splitlines():
        match = _MD_HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            name = match.group(2).strip()
            if level == 1 and not title:
                title = name
            headings[:] = headings[: level - 1] + [name]
            continue
        buffer.append(line)
    flush()
    return ParsedDoc(title=title or _title_from(filename), blocks=blocks)


def parse_pdf_text(path: str | Path, filename: str) -> ParsedDoc:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ParseError("That PDF could not be read.") from exc
    blocks: list[Block] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        for para in _paragraphs(text):
            blocks.append(Block(kind="text", text=re.sub(r"[ \t]+", " ", para), page=number))
    title = ""
    try:
        title = str((reader.metadata or {}).get("/Title") or "").strip()
    except Exception:  # noqa: BLE001
        title = ""
    return ParsedDoc(title=title or _title_from(filename), blocks=blocks, pages=len(reader.pages))


# ---------------------------------------------------------------- docling

_converter = None


def release_models() -> None:
    """Drop Docling's models (~4 GB) between documents so entity linking does not stack on top.
    A parse still running in another task keeps its own reference until it finishes."""
    global _converter
    _converter = None
    import gc

    gc.collect()


def _get_converter(ocr: bool):
    global _converter
    if _converter is not None and _converter[0] == ocr:
        return _converter[1]
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    options.do_table_structure = True
    options.generate_picture_images = True
    options.images_scale = 2.0
    options.do_picture_classification = True
    options.document_timeout = float(os.environ.get("RAG_PARSE_TIMEOUT_SECONDS", "7200"))
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    _converter = (ocr, converter)
    return converter


def _picture_label(item) -> str:
    """The classifier's top label; the attribute moved between Docling versions."""
    try:
        meta = getattr(item, "meta", None)
        classification = getattr(meta, "classification", None) if meta is not None else None
        predictions = getattr(classification, "predictions", None) if classification is not None else None
        if predictions:
            return str(getattr(predictions[0], "class_name", "") or "").lower()
    except Exception:  # noqa: BLE001
        pass
    try:
        for annotation in getattr(item, "annotations", None) or []:
            classes = getattr(annotation, "predicted_classes", None)
            if classes:
                return str(getattr(classes[0], "class_name", "") or "").lower()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _page_of(item) -> int | None:
    prov = getattr(item, "prov", None) or []
    return int(prov[0].page_no) if prov else None


@dataclass
class _DoclingState:
    """Carried across page batches so headings and figure numbers continue."""

    headings: list[str] = field(default_factory=list)
    title: str = ""
    figure_number: int = 0
    # Many PDFs (papers, books) mark the title as an ordinary heading.
    first_heading: str = ""
    # Inside a contents / index / bibliography section: drop everything until the next heading.
    skipping: bool = False

    def best_title(self, filename: str) -> str:
        return (self.title or self.first_heading or _title_from(filename))[:300]


def _docling_blocks(doc, state: _DoclingState) -> list[Block]:
    from docling_core.types.doc import DocItemLabel, PictureItem, SectionHeaderItem, TableItem, TextItem

    skip_labels = {
        DocItemLabel.CAPTION,
        DocItemLabel.PAGE_HEADER,
        DocItemLabel.PAGE_FOOTER,
        DocItemLabel.DOCUMENT_INDEX,
    }
    headings = state.headings
    blocks: list[Block] = []
    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        if isinstance(item, SectionHeaderItem) or label == DocItemLabel.TITLE:
            name = clean_text(getattr(item, "text", "") or "")
            if not name:
                continue
            if label == DocItemLabel.TITLE:
                state.title = state.title or name
                headings[:] = [name]
                continue
            state.skipping = skipped_heading(name)
            if state.skipping:
                continue
            if not state.first_heading and not blocks:
                state.first_heading = name
            level = max(1, int(getattr(item, "level", 1) or 1))
            # The title sits at depth 0 when present; section levels start below it.
            base = 1 if state.title and headings and headings[0] == state.title else 0
            headings[:] = headings[: base + level - 1] + [name]
            continue
        if state.skipping or label == DocItemLabel.DOCUMENT_INDEX:
            continue
        if isinstance(item, TableItem):
            try:
                markdown = item.export_to_markdown(doc=doc).strip()
            except Exception:  # noqa: BLE001
                markdown = ""
            caption = ""
            try:
                caption = item.caption_text(doc).strip()
            except Exception:  # noqa: BLE001
                pass
            if markdown:
                text = f"{caption}\n{markdown}" if caption else markdown
                blocks.append(Block(kind="table", text=text, headings=list(headings), page=_page_of(item)))
            continue
        if isinstance(item, PictureItem):
            try:
                image = item.get_image(doc)
            except Exception:  # noqa: BLE001
                image = None
            if image is None:
                continue
            width, height = image.size
            kind = _picture_label(item)
            if kind in SKIP_FIGURE_LABELS or min(width, height) < MIN_FIGURE_SIDE:
                continue
            caption = ""
            try:
                caption = clean_text(item.caption_text(doc))
            except Exception:  # noqa: BLE001
                pass
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            state.figure_number += 1
            figure = FigureDraft(
                number=state.figure_number,
                image_png=buffer.getvalue(),
                width=width,
                height=height,
                caption=caption[:1000],
                label=kind[:40],
                page=_page_of(item),
                doc_number=caption_number(caption),
            )
            blocks.append(
                Block(kind="figure", text=caption, headings=list(headings), page=figure.page, figure=figure)
            )
            continue
        if isinstance(item, TextItem):
            if label in skip_labels:
                continue
            text = clean_text(item.text or "")
            if not text:
                continue
            if label == DocItemLabel.LIST_ITEM:
                text = f"- {text}"
            blocks.append(Block(kind="text", text=text, headings=list(headings), page=_page_of(item)))
    return attach_captions(blocks)


def _ocr_default(ocr: bool | None) -> bool:
    if ocr is not None:
        return ocr
    return os.environ.get("RAG_PARSE_OCR", "0").strip() == "1"


def parse_with_docling(path: str | Path, filename: str, *, ocr: bool | None = None) -> ParsedDoc:
    state = _DoclingState()
    try:
        result = _get_converter(_ocr_default(ocr)).convert(str(path), raises_on_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("docling failed on %s: %s", filename, exc)
        if Path(filename).suffix.lower() == ".pdf":
            return parse_pdf_text(path, filename)
        raise ParseError("That document could not be read.") from exc
    blocks = _docling_blocks(result.document, state)
    try:
        pages = len(result.document.pages)
    except Exception:  # noqa: BLE001
        pages = 0
    return ParsedDoc(title=state.best_title(filename), blocks=blocks, pages=pages)


def pdf_page_count(path: str | Path) -> int:
    from pypdf import PdfReader

    try:
        return len(PdfReader(str(path)).pages)
    except Exception as exc:  # noqa: BLE001
        raise ParseError("That PDF could not be read.") from exc


def _pdf_text_pages(path: str | Path, first: int, last: int) -> list[Block]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[Block] = []
    for number in range(first, min(last, len(reader.pages)) + 1):
        try:
            text = reader.pages[number - 1].extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        for para in _paragraphs(text):
            blocks.append(Block(kind="text", text=re.sub(r"[ \t]+", " ", para), page=number))
    return blocks


def iter_parts(path: str | Path, filename: str, *, batch_pages: int = 40, ocr: bool | None = None):
    """Yield (ParsedDoc part, pages done, total pages). A big PDF goes through Docling a
    batch of pages at a time, so a 500 MB book never sits in memory whole; headings and
    figure numbers carry over between batches."""
    ext = Path(filename).suffix.lower()
    if ext != ".pdf" or not docling_available():
        parsed = parse_file(path, filename, ocr=ocr)
        yield parsed, parsed.pages or 1, parsed.pages or 1
        return
    total = pdf_page_count(path)
    if total <= batch_pages:
        parsed = parse_with_docling(path, filename, ocr=ocr)
        yield parsed, total, total
        return
    state = _DoclingState()
    converter = _get_converter(_ocr_default(ocr))
    for first in range(1, total + 1, batch_pages):
        last = min(total, first + batch_pages - 1)
        try:
            result = converter.convert(str(path), raises_on_error=True, page_range=(first, last))
            blocks = _docling_blocks(result.document, state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("docling failed on pages %s-%s of %s: %s; text only", first, last, filename, exc)
            blocks = _pdf_text_pages(path, first, last)
        yield ParsedDoc(title=state.best_title(filename), blocks=blocks, pages=total), last, total


def webp_bytes(png: bytes, max_side: int = 1600) -> tuple[bytes, int, int]:
    """Store figures as WebP (much smaller than PNG). Falls back to PNG without Pillow."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return png, 0, 0
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.thumbnail((max_side, max_side))
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=82, method=4)
    return out.getvalue(), image.width, image.height
