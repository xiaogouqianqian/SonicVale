import html
import difflib
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import soundfile as sf
from bs4 import BeautifulSoup, NavigableString
from pypinyin import lazy_pinyin

from app.core.config import getFfmpegPath
from app.core.epub_parser import get_epub_opf_path
from app.core.project_assets import PROJECT_MODE_AUDIO_EPUB, normalize_project_mode
from app.core.text_correct_engine import TextCorrectorFinal


FLOWING_CSS_CONTENT = """body {
  font-family: serif;
  line-height: 1.7;
  margin: 5%%;
}

h1 {
  margin-bottom: 1.2em;
}

.chapter-audio {
    margin: 0 0 1.4em;
    text-indent: 0;
}

.chapter-audio audio {
    width: 100%%;
}

p {
  margin: 0 0 0.9em;
  text-indent: 2em;
}

.line {
  text-indent: 0;
}
"""

APPLE_PAGE_WIDTH = 1200
APPLE_PAGE_HEIGHT = 1600

APPLE_FIXED_LAYOUT_CSS = f"""html, body {{
  margin: 0;
  padding: 0;
  width: 100%%;
  height: 100%%;
}}

body {{
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: #efe5d4;
  color: #2a211b;
}}

.page {{
  box-sizing: border-box;
  position: relative;
  width: {APPLE_PAGE_WIDTH}px;
  height: {APPLE_PAGE_HEIGHT}px;
  padding: 118px 108px 126px;
  overflow: hidden;
  background:
    radial-gradient(circle at top right, rgba(255, 255, 255, 0.55), transparent 32%%),
    linear-gradient(180deg, #f7efdf 0%%, #efe3cf 100%%);
}}

.chapter-title {{
  margin: 0 0 52px;
  font-size: 52px;
  line-height: 1.2;
  letter-spacing: 1px;
}}

.line,
.paragraph {{
  margin: 0 0 24px;
  font-size: 37px;
  line-height: 1.62;
  word-break: break-word;
}}

.paragraph {{
  text-indent: 2em;
}}

.page-footer {{
  position: absolute;
  right: 96px;
  bottom: 64px;
  font-size: 24px;
  color: rgba(42, 33, 27, 0.56);
}}

.-epub-media-overlay-active {{
  color: #7a1f16;
  background: rgba(255, 214, 153, 0.55);
  border-radius: 10px;
  box-shadow: 0 0 0 4px rgba(255, 214, 153, 0.28);
}}
"""

SOURCE_EPUB_OVERLAY_STYLE = """.sonicvale-audio {
    margin: 0 0 1.2em;
    text-indent: 0;
}

.sonicvale-audio audio {
    width: 100%;
}

.-epub-media-overlay-active {
    color: #7a1f16;
    background: rgba(255, 214, 153, 0.55);
    border-radius: 0.18em;
    box-shadow: 0 0 0 0.16em rgba(255, 214, 153, 0.24);
}
"""


OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}
TEXT_CORRECTOR = TextCorrectorFinal()


def _safe_text(value: str | None, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _normalize_match_text(text: str | None) -> str:
    return TEXT_CORRECTOR.clean_text(text or "")


def _to_pronunciation(text: str | None) -> str:
    normalized = _normalize_match_text(text)
    if not normalized:
        return ""
    return " ".join(lazy_pinyin(normalized, errors="ignore"))


def _sequence_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _best_sentence_alignment_score(target_text: str, candidate_text: str) -> float:
    normalized_target = _normalize_match_text(target_text)
    normalized_candidate = _normalize_match_text(candidate_text)
    if not normalized_target or not normalized_candidate:
        return 0.0

    if normalized_target in normalized_candidate:
        return 1.0

    target_pron = _to_pronunciation(normalized_target)
    candidate_pron = _to_pronunciation(normalized_candidate)
    best_score = max(
        _sequence_ratio(normalized_target, normalized_candidate),
        _sequence_ratio(target_pron, candidate_pron),
    )

    candidate_sentences = TEXT_CORRECTOR.split_sentences(candidate_text or "")
    for sentence in candidate_sentences:
        normalized_sentence = _normalize_match_text(sentence)
        if not normalized_sentence:
            continue
        if normalized_target == normalized_sentence or normalized_target in normalized_sentence:
            return 1.0

        sentence_pron = _to_pronunciation(normalized_sentence)
        best_score = max(
            best_score,
            _sequence_ratio(normalized_target, normalized_sentence),
            _sequence_ratio(target_pron, sentence_pron),
        )

    return best_score


def _safe_file_stem(prefix: str, index: int) -> str:
    return f"{prefix}_{index:03d}"


def _normalize_language(language: str | None, export_mode: str) -> str:
    value = _safe_text(language, "zh-CN")
    if export_mode == "apple_books_read_aloud":
        normalized = value.lower()
        if normalized in {"zh", "zh-cn", "zh-hans-cn", "zh-chs"}:
            return "zh-Hans"
        if normalized in {"zh-tw", "zh-hk", "zh-hant", "zh-hant-tw", "zh-cht"}:
            return "zh-Hant"
    return value


def _format_clock(seconds: float) -> str:
    total_millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_millis, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _split_text_parts(chapter_text: str | None) -> list[str]:
    content = _safe_text(chapter_text)
    if not content:
        return ["本章暂无可导出的正文。"]

    parts = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    if not parts:
        parts = [line.strip() for line in content.splitlines() if line.strip()]
    if not parts:
        parts = [content]
    return parts


def _build_text_blocks(lines, chapter_text: str | None, chapter_token: str):
    if lines:
        blocks = []
        entries = []
        for index, line in enumerate(lines, start=1):
            anchor = f"{chapter_token}-line-{index:04d}"
            raw_text = _safe_text(getattr(line, "text_content", None))
            escaped_text = html.escape(raw_text) if raw_text else "&#160;"
            blocks.append(f'<p class="line"><span id="{anchor}">{escaped_text}</span></p>')
            entries.append({
                "anchor": anchor,
                "line": line,
                "text": raw_text,
            })
        return "\n".join(blocks), entries

    blocks = []
    for index, part in enumerate(_split_text_parts(chapter_text), start=1):
        anchor = f"{chapter_token}-para-{index:04d}"
        escaped_text = html.escape(part)
        blocks.append(f'<p><span id="{anchor}">{escaped_text}</span></p>')
    return "\n".join(blocks), []


def _build_chapter_xhtml(title: str, body_html: str, language: str, audio_href: str | None = None) -> str:
        escaped_title = html.escape(title)
        escaped_language = html.escape(language)
        audio_block = ""
        if audio_href:
                escaped_audio_href = html.escape(audio_href)
                audio_block = f"""
            <div class=\"chapter-audio\">
                <audio controls=\"controls\" preload=\"none\">
                    <source src=\"{escaped_audio_href}\" type=\"audio/mpeg\" />
                </audio>
            </div>"""

        return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" lang=\"{escaped_language}\" xml:lang=\"{escaped_language}\">
    <head>
        <title>{escaped_title}</title>
        <link rel=\"stylesheet\" type=\"text/css\" href=\"../styles/book.css\" />
    </head>
    <body epub:type=\"bodymatter chapter\">
        <section>
            <h1>{escaped_title}</h1>
{audio_block}
            {body_html}
        </section>
    </body>
</html>
"""


def _build_inline_audio_block(audio_href: str, title: str | None = None) -> str:
    escaped_audio_href = html.escape(audio_href)
    return (
        f'\n<section class="sonicvale-audio" epub:type="bridgehead">'
        '<audio controls="controls" preload="none">'
        f'<source src="{escaped_audio_href}" type="audio/mpeg" />'
        '</audio>'
        '</section>\n'
    )


def _build_source_epub_smil(text_href: str, audio_href: str, segments: list[dict]) -> str:
    items = []
    for index, segment in enumerate(segments, start=1):
        items.append(
            f'    <par id="{segment["anchor"]}-par-{index:03d}">\n'
            f'      <text src="{html.escape(text_href)}#{html.escape(segment["anchor"])}" />\n'
            f'      <audio src="{html.escape(audio_href)}" clipBegin="{segment["clip_begin"]}" clipEnd="{segment["clip_end"]}" />\n'
            '    </par>'
        )

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<smil xmlns=\"http://www.w3.org/ns/SMIL\" xmlns:epub=\"http://www.idpf.org/2007/ops\" version=\"3.0\">
  <body>
    <seq id="source-overlay-seq" epub:textref="{html.escape(text_href)}">
{os.linesep.join(items)}
    </seq>
  </body>
</smil>
"""


def _iter_overlay_block_candidates(body_tag):
    allowed_names = {"p", "div", "li", "blockquote", "section", "article", "td", "dd"}
    for element in body_tag.find_all(allowed_names):
        css_classes = element.attrs.get("class", []) if hasattr(element, "attrs") else []
        if "sonicvale-audio" in css_classes:
            continue
        if any(
            "sonicvale-overlay-group" in (ancestor.attrs.get("class", []) if hasattr(ancestor, "attrs") else [])
            for ancestor in element.parents
        ):
            continue
        has_nested_block = any(
            child is not element and _safe_text(child.get_text("", strip=True))
            for child in element.find_all(allowed_names)
        )
        if has_nested_block:
            continue
        text = _safe_text(element.get_text("", strip=True))
        if text:
            yield element


def _blocks_are_consecutive(left_block, right_block) -> bool:
    sibling = left_block.next_sibling
    while sibling is not None:
        if isinstance(sibling, NavigableString) and not _safe_text(str(sibling)):
            sibling = sibling.next_sibling
            continue
        return sibling is right_block
    return False


def _iter_block_windows(blocks: list, start_index: int, end_index: int, max_window_size: int = 4):
    for index in range(start_index, end_index):
        yield index, [blocks[index]]
        parent = getattr(blocks[index], "parent", None)
        window = [blocks[index]]
        for next_index in range(index + 1, min(end_index, index + max_window_size)):
            candidate = blocks[next_index]
            if getattr(candidate, "parent", None) is not parent:
                break
            if not _blocks_are_consecutive(window[-1], candidate):
                break
            window = window + [candidate]
            yield index, window


def _anchor_block_window(soup: BeautifulSoup, blocks: list, preferred_anchor: str) -> tuple[str, int]:
    if len(blocks) == 1:
        existing_id = _safe_text(blocks[0].attrs.get("id"))
        anchor = existing_id or preferred_anchor
        blocks[0]["id"] = anchor
        return anchor, 1

    first_block = blocks[0]
    parent = first_block.parent
    wrapper = soup.new_tag("div")
    wrapper["id"] = preferred_anchor
    wrapper["class"] = ["sonicvale-overlay-group"]
    first_block.insert_before(wrapper)

    for block in blocks:
        wrapper.append(block.extract())

    return preferred_anchor, len(blocks)


def _attach_block_anchor(soup: BeautifulSoup, body_tag, target_text: str, preferred_anchor: str, start_index: int):
    normalized_target = _normalize_match_text(target_text)
    if not normalized_target:
        return False, preferred_anchor, start_index

    blocks = list(_iter_overlay_block_candidates(body_tag))
    best_index = None
    best_anchor = preferred_anchor
    best_score = 0.0
    search_end = min(len(blocks), max(0, start_index) + 24)
    best_window = None

    for index, window in _iter_block_windows(blocks, max(0, start_index), search_end):
        block_text = "".join(block.get_text("", strip=True) for block in window)
        score = _best_sentence_alignment_score(target_text, block_text)
        score += min(0.03, max(0, len(window) - 1) * 0.01)
        if score > best_score:
            best_index = index
            best_window = window
            if len(window) == 1:
                existing_id = _safe_text(window[0].attrs.get("id"))
                best_anchor = existing_id or preferred_anchor
            else:
                best_anchor = preferred_anchor
            best_score = score

            if score >= 0.995:
                break

    if best_index is None or best_score < 0.62 or not best_window:
        return False, preferred_anchor, max(0, start_index)

    best_anchor, window_size = _anchor_block_window(soup, best_window, best_anchor)
    return True, best_anchor, best_index + window_size


def _iter_overlay_text_nodes(body_tag):
    for node in body_tag.descendants:
        if not isinstance(node, NavigableString):
            continue
        if not _safe_text(str(node)):
            continue

        parent = getattr(node, "parent", None)
        if parent is None:
            continue
        if getattr(parent, "name", "") in {"script", "style", "audio"}:
            continue

        skip_node = False
        for ancestor in getattr(node, "parents", []):
            css_classes = ancestor.attrs.get("class", []) if hasattr(ancestor, "attrs") else []
            if "sonicvale-audio" in css_classes:
                skip_node = True
                break
        if skip_node:
            continue

        yield node


def _wrap_text_occurrence(soup: BeautifulSoup, body_tag, target_text: str, anchor: str, start_index: int):
    raw_target = _safe_text(target_text)
    if not raw_target:
        return False, start_index

    normalized_target = re.sub(r"\s+", "", raw_target)
    text_nodes = list(_iter_overlay_text_nodes(body_tag))
    for index in range(max(0, start_index), len(text_nodes)):
        node = text_nodes[index]
        raw_text = str(node)
        match_pos = raw_text.find(raw_target)
        matched_text = raw_target

        compact_text = re.sub(r"\s+", "", raw_text)
        if match_pos < 0 and compact_text == normalized_target:
            match_pos = 0
            matched_text = raw_text

        if match_pos < 0 and normalized_target and normalized_target in compact_text:
            match_pos = 0
            matched_text = raw_text

        if match_pos < 0:
            continue

        before_text = raw_text[:match_pos]
        after_text = raw_text[match_pos + len(matched_text):]

        fragments = []
        if before_text:
            fragments.append(NavigableString(before_text))

        span_tag = soup.new_tag("span")
        span_tag["id"] = anchor
        span_tag.string = matched_text
        fragments.append(span_tag)

        if after_text:
            fragments.append(NavigableString(after_text))

        for fragment in reversed(fragments):
            node.insert_after(fragment)
        node.extract()
        return True, index

    return False, max(0, start_index)


def _build_nav_xhtml(book_title: str, nav_items: list[dict], language: str, landmark_items: list[dict] | None = None) -> str:
    nav_links = "\n".join(
        f'        <li><a href="{html.escape(item["href"])}">{html.escape(item["title"])} </a></li>'
        for item in nav_items
    )
    landmarks_block = ""
    if landmark_items:
        landmark_links = "\n".join(
            f'        <li><a href="{html.escape(item["href"])}" epub:type="{html.escape(item["epub_type"])}">{html.escape(item["title"])} </a></li>'
            for item in landmark_items
        )
        landmarks_block = (
            "\n    <nav epub:type=\"landmarks\" id=\"landmarks\">\n"
            "      <h2>Landmarks</h2>\n"
            "      <ol>\n"
            f"{landmark_links}\n"
            "      </ol>\n"
            "    </nav>"
        )

    escaped_language = html.escape(language)
    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" lang=\"{escaped_language}\" xml:lang=\"{escaped_language}\">
  <head>
    <title>{html.escape(book_title)}</title>
  </head>
  <body>
    <nav epub:type=\"toc\" id=\"toc\">
      <h1>{html.escape(book_title)}</h1>
      <ol>
{nav_links}
      </ol>
    </nav>{landmarks_block}
  </body>
</html>
"""


def _build_smil(chapter_token: str, anchors: list[str], segments: list[dict]) -> str:
    items = []
    current_offset = 0.0
    for anchor, segment in zip(anchors, segments):
        duration = max(0.0, float(segment["duration"]))
        begin = _format_clock(current_offset)
        current_offset += duration
        end = _format_clock(current_offset)
        items.append(
            "      <par>\n"
            f"        <text src=\"../text/{chapter_token}.xhtml#{anchor}\" />\n"
            f"        <audio src=\"../audio/{chapter_token}.mp3\" clipBegin=\"{begin}\" clipEnd=\"{end}\" />\n"
            "      </par>"
        )

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<smil xmlns=\"http://www.w3.org/ns/SMIL\" xmlns:epub=\"http://www.idpf.org/2007/ops\" version=\"3.0\">
  <body>
    <seq id=\"{chapter_token}-seq\" epub:textref=\"../text/{chapter_token}.xhtml\">
{os.linesep.join(items)}
    </seq>
  </body>
</smil>
"""


def _estimate_page_entry_weight(text: str, kind: str) -> int:
    base = max(1, len(text))
    if kind == "paragraph":
        return base + 32
    return base + 18


def _paginate_entries(entries: list[dict], first_page_budget: int, page_budget: int, max_items: int) -> list[list[dict]]:
    pages: list[list[dict]] = []
    current: list[dict] = []
    current_budget = first_page_budget
    current_weight = 0

    for entry in entries:
        entry_weight = _estimate_page_entry_weight(entry["text"], entry["kind"])
        if current and (current_weight + entry_weight > current_budget or len(current) >= max_items):
            pages.append(current)
            current = []
            current_budget = page_budget
            current_weight = 0

        current.append(entry)
        current_weight += entry_weight

    if current or not pages:
        pages.append(current)

    return pages


def _build_fixed_layout_entries(lines, chapter_text: str | None, chapter_token: str) -> list[dict]:
    if lines:
        entries = []
        for index, line in enumerate(lines, start=1):
            anchor = f"{chapter_token}-line-{index:04d}"
            raw_text = _safe_text(getattr(line, "text_content", None), "……")
            entry = {
                "anchor": anchor,
                "text": raw_text,
                "kind": "line",
            }
            audio_path = getattr(line, "audio_path", None)
            if audio_path and os.path.exists(audio_path):
                entry["audio_path"] = audio_path
                entry["duration"] = float(sf.info(audio_path).duration)
            entries.append(entry)
        return entries

    entries = []
    for index, part in enumerate(_split_text_parts(chapter_text), start=1):
        entries.append({
            "anchor": f"{chapter_token}-para-{index:04d}",
            "text": part,
            "kind": "paragraph",
        })
    return entries


def _build_fixed_page_xhtml(
    title: str,
    entries: list[dict],
    page_number: int,
    total_pages: int,
    language: str,
    include_title: bool,
) -> str:
    content_blocks = []
    if include_title:
        content_blocks.append(f'<h1 class="chapter-title">{html.escape(title)}</h1>')
    for entry in entries:
        text = html.escape(entry["text"]) if entry["text"] else "&#160;"
        css_class = "line" if entry["kind"] == "line" else "paragraph"
        content_blocks.append(f'<p class="{css_class}"><span id="{entry["anchor"]}">{text}</span></p>')

    if not content_blocks:
        content_blocks.append('<p class="paragraph">本页暂无内容。</p>')

    escaped_language = html.escape(language)
    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" lang=\"{escaped_language}\" xml:lang=\"{escaped_language}\">
  <head>
    <title>{html.escape(title)}</title>
    <meta name=\"viewport\" content=\"width={APPLE_PAGE_WIDTH}, height={APPLE_PAGE_HEIGHT}\" />
    <link rel=\"stylesheet\" type=\"text/css\" href=\"../styles/book.css\" />
  </head>
  <body>
    <section class=\"page\" epub:type=\"bodymatter chapter\">
      {' '.join(content_blocks)}
      <div class=\"page-footer\">{page_number} / {total_pages}</div>
    </section>
  </body>
</html>
"""


def _build_fixed_layout_smil(page_token: str, xhtml_href: str, segments: list[dict]) -> str:
    items = []
    for index, segment in enumerate(segments, start=1):
        clip_end = _format_clock(max(0.0, float(segment["duration"])))
        items.append(
            f'    <par id="{page_token}-par-{index:03d}">\n'
            f'      <text src="../{xhtml_href}#{segment["anchor"]}" />\n'
            f'      <audio src="../audio/{segment["audio_file"]}" clipBegin="00:00:00.000" clipEnd="{clip_end}" />\n'
            '    </par>'
        )

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<smil xmlns=\"http://www.w3.org/ns/SMIL\" version=\"3.0\" profile=\"http://www.idpf.org/epub/30/profile/content/\">
  <body>
{os.linesep.join(items)}
  </body>
</smil>
"""


def _build_container_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">
  <rootfiles>
    <rootfile full-path=\"OEBPS/package.opf\" media-type=\"application/oebps-package+xml\" />
  </rootfiles>
</container>
"""


def _extract_epub_to_directory(source_epub_path: str, extract_dir: str):
    with zipfile.ZipFile(source_epub_path, "r") as archive:
        archive.extractall(extract_dir)


def _normalize_book_href(source_href: str) -> str:
    return posixpath.normpath(_safe_text(source_href)).lstrip("./")


def _get_opf_relative_dir(opf_rel_path: str) -> str:
    rel_dir = posixpath.dirname(_normalize_book_href(opf_rel_path))
    return "" if rel_dir == "." else rel_dir


def _resolve_source_manifest_href(source_href: str, opf_rel_path: str) -> str:
    normalized_source = _normalize_book_href(source_href)
    opf_rel_dir = _get_opf_relative_dir(opf_rel_path)
    if opf_rel_dir and normalized_source.startswith(f"{opf_rel_dir}/"):
        return posixpath.relpath(normalized_source, opf_rel_dir)
    return normalized_source


def _resolve_source_file_path(extract_dir: str, opf_rel_path: str, source_href: str) -> str:
    normalized_source = _normalize_book_href(source_href)
    opf_rel_dir = _get_opf_relative_dir(opf_rel_path)

    if opf_rel_dir and normalized_source.startswith(f"{opf_rel_dir}/"):
        return os.path.join(extract_dir, *normalized_source.split("/"))

    if opf_rel_dir:
        return os.path.join(extract_dir, *opf_rel_dir.split("/"), *normalized_source.split("/"))

    return os.path.join(extract_dir, *normalized_source.split("/"))


def _pack_epub_from_directory(extract_dir: str, output_path: str):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    mimetype_path = os.path.join(extract_dir, "mimetype")
    if not os.path.exists(mimetype_path):
        raise ValueError("源 EPUB 缺少 mimetype 文件")

    with zipfile.ZipFile(output_path, "w") as archive:
        archive.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        for root, _, files in os.walk(extract_dir):
            files.sort()
            for file_name in files:
                full_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(full_path, extract_dir).replace("\\", "/")
                if rel_path == "mimetype":
                    continue
                archive.write(full_path, rel_path, compress_type=zipfile.ZIP_DEFLATED)


def _inject_audio_into_xhtml(file_path: str, audio_href: str, chapter_title: str, overlay_lines: list[dict]):
    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()

    soup = BeautifulSoup(content, "html.parser")
    body_tag = soup.body
    if body_tag is None:
        raise ValueError(f"章节文件缺少 body 标签: {file_path}")

    head_tag = soup.head
    if head_tag is None:
        html_tag = soup.find("html") or soup
        head_tag = soup.new_tag("head")
        html_tag.insert(0, head_tag)

    overlay_style = head_tag.find("style", attrs={"id": "sonicvale-overlay-style"})
    if overlay_style is None:
        overlay_style = soup.new_tag("style")
        overlay_style["id"] = "sonicvale-overlay-style"
        overlay_style.string = SOURCE_EPUB_OVERLAY_STYLE
        head_tag.append(overlay_style)

    audio_section = body_tag.find("section", class_="sonicvale-audio")
    audio_fragment = BeautifulSoup(_build_inline_audio_block(audio_href, chapter_title), "html.parser")
    replacement_section = audio_fragment.find("section")
    if replacement_section is not None:
        if audio_section is None:
            body_tag.insert(0, replacement_section)
        else:
            audio_section.replace_with(replacement_section)

    matched_segments = []
    search_start = 0
    block_search_start = 0
    current_offset = 0.0
    for overlay_line in overlay_lines:
        duration = float(overlay_line["duration"])
        clip_begin = _format_clock(current_offset)
        current_offset += duration
        clip_end = _format_clock(current_offset)

        matched, search_start = _wrap_text_occurrence(
            soup=soup,
            body_tag=body_tag,
            target_text=overlay_line["text"],
            anchor=overlay_line["anchor"],
            start_index=search_start,
        )
        anchor_to_use = overlay_line["anchor"]
        if not matched:
            matched, anchor_to_use, block_search_start = _attach_block_anchor(
                soup=soup,
                body_tag=body_tag,
                target_text=overlay_line["text"],
                preferred_anchor=overlay_line["anchor"],
                start_index=block_search_start,
            )
        if matched:
            matched_segments.append({
                "anchor": anchor_to_use,
                "clip_begin": clip_begin,
                "clip_end": clip_end,
                "duration": duration,
            })

    xml_prefix = "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" if content.lstrip().startswith("<?xml") else ""
    doctype_match = re.search(r"<!DOCTYPE[^>]+>", content, flags=re.IGNORECASE)
    doctype_prefix = f"{doctype_match.group(0)}\n" if doctype_match else ""
    rendered = soup.decode(formatter=None)

    with open(file_path, "w", encoding="utf-8") as file:
        file.write(f"{xml_prefix}{doctype_prefix}{rendered}")

    return matched_segments


def _update_opf_for_audio(opf_path: str, chapter_audio_items: list[dict], total_duration: float, opf_rel_path: str):
    tree = ET.parse(opf_path)
    root = tree.getroot()
    manifest = root.find("opf:manifest", OPF_NS)
    metadata = root.find("opf:metadata", OPF_NS)
    if manifest is None:
        raise ValueError("OPF 缺少 manifest 节点")

    prefix_attr = root.attrib.get("prefix", "").strip()
    media_prefix = "media: http://www.idpf.org/epub/vocab/overlays/#"
    if media_prefix not in prefix_attr:
        root.set("prefix", f"{prefix_attr} {media_prefix}".strip())

    existing_ids = {item.attrib.get("id") for item in manifest.findall("opf:item", OPF_NS)}
    manifest_items = manifest.findall("opf:item", OPF_NS)
    for item in chapter_audio_items:
        if item["audio_id"] not in existing_ids:
            audio_item = ET.Element(f"{{{OPF_NS['opf']}}}item")
            audio_item.set("id", item["audio_id"])
            audio_item.set("href", item["audio_href"])
            audio_item.set("media-type", "audio/mpeg")
            manifest.append(audio_item)
            existing_ids.add(item["audio_id"])

        if item.get("smil_id") and item["smil_id"] not in existing_ids:
            smil_item = ET.Element(f"{{{OPF_NS['opf']}}}item")
            smil_item.set("id", item["smil_id"])
            smil_item.set("href", item["smil_href"])
            smil_item.set("media-type", "application/smil+xml")
            manifest.append(smil_item)
            existing_ids.add(item["smil_id"])

        xhtml_item = None
        source_item_id = _safe_text(item.get("source_item_id"))
        if source_item_id:
            for manifest_item in manifest_items:
                if _safe_text(manifest_item.attrib.get("id")) == source_item_id:
                    xhtml_item = manifest_item
                    break

        if xhtml_item is None:
            target_manifest_href = _resolve_source_manifest_href(item["source_href"], opf_rel_path)
            for manifest_item in manifest_items:
                manifest_href = posixpath.normpath((manifest_item.attrib.get("href") or "").split("#", 1)[0])
                if manifest_href == target_manifest_href:
                    xhtml_item = manifest_item
                    break

        if xhtml_item is not None and item.get("smil_id"):
            xhtml_item.set("media-overlay", item["smil_id"])

    if metadata is not None:
        modified_meta = None
        active_class_meta = None
        book_duration_meta = None
        for meta in metadata.findall("opf:meta", OPF_NS):
            if meta.attrib.get("property") == "dcterms:modified":
                modified_meta = meta
            elif meta.attrib.get("property") == "media:active-class":
                active_class_meta = meta
            elif meta.attrib.get("property") == "media:duration" and not meta.attrib.get("refines"):
                book_duration_meta = meta

        if modified_meta is None:
            modified_meta = ET.Element(f"{{{OPF_NS['opf']}}}meta")
            modified_meta.set("property", "dcterms:modified")
            metadata.append(modified_meta)

        if active_class_meta is None:
            active_class_meta = ET.Element(f"{{{OPF_NS['opf']}}}meta")
            active_class_meta.set("property", "media:active-class")
            metadata.append(active_class_meta)

        if book_duration_meta is None:
            book_duration_meta = ET.Element(f"{{{OPF_NS['opf']}}}meta")
            book_duration_meta.set("property", "media:duration")
            metadata.append(book_duration_meta)

        modified_meta.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        active_class_meta.text = "-epub-media-overlay-active"
        book_duration_meta.text = _format_clock(total_duration)

        existing_refines = {
            meta.attrib.get("refines")
            for meta in metadata.findall("opf:meta", OPF_NS)
            if meta.attrib.get("property") == "media:duration" and meta.attrib.get("refines")
        }
        for item in chapter_audio_items:
            if not item.get("smil_id"):
                continue
            refine_id = f'#{item["smil_id"]}'
            if refine_id in existing_refines:
                continue
            smil_duration_meta = ET.Element(f"{{{OPF_NS['opf']}}}meta")
            smil_duration_meta.set("property", "media:duration")
            smil_duration_meta.set("refines", refine_id)
            smil_duration_meta.text = _format_clock(item["duration"])
            metadata.append(smil_duration_meta)

    tree.write(opf_path, encoding="utf-8", xml_declaration=True)


def _export_source_epub_audiobook(
    project,
    chapters,
    chapter_lines_map: dict[int, list],
    output_path: str,
    line_service,
):
    source_epub_path = _safe_text(getattr(project, "source_epub_path", None))
    if not source_epub_path or not os.path.exists(source_epub_path):
        raise ValueError("当前项目缺少源 EPUB 文件，请重新创建或重新导入源文件")

    temp_dir = tempfile.mkdtemp(prefix="sonicvale_source_epub_")
    extract_dir = os.path.join(temp_dir, "book")
    audio_temp_dir = os.path.join(temp_dir, "audio")
    os.makedirs(extract_dir, exist_ok=True)
    os.makedirs(audio_temp_dir, exist_ok=True)

    total_duration = 0.0
    audio_chapter_count = 0
    text_only_chapter_count = 0
    skipped_audio_line_count = 0
    chapter_audio_items: list[dict] = []

    try:
        _extract_epub_to_directory(source_epub_path, extract_dir)
        opf_rel_path = _safe_text(getattr(project, "source_epub_opf_path", None)) or get_epub_opf_path(source_epub_path)
        opf_full_path = os.path.join(extract_dir, *opf_rel_path.split("/"))
        if not os.path.exists(opf_full_path):
            raise ValueError("源 EPUB 的 OPF 文件不存在")

        opf_dir = os.path.dirname(opf_full_path)
        audio_rel_dir = "sonicvale_audio"
        smil_rel_dir = "sonicvale_smil"
        audio_output_dir = os.path.join(opf_dir, audio_rel_dir)
        smil_output_dir = os.path.join(opf_dir, smil_rel_dir)
        os.makedirs(audio_output_dir, exist_ok=True)
        os.makedirs(smil_output_dir, exist_ok=True)

        chapter_lookup = {
            _safe_text(getattr(chapter, "source_href", None)): chapter
            for chapter in chapters
            if _safe_text(getattr(chapter, "source_href", None))
        }

        if not chapter_lookup:
            raise ValueError("当前 EPUB 项目缺少章节源文件映射，无法基于原书增强导出")

        for index, chapter in enumerate(chapters, start=1):
            source_href = _safe_text(getattr(chapter, "source_href", None))
            if not source_href:
                text_only_chapter_count += 1
                continue

            valid_audio_paths = []
            overlay_lines = []
            chapter_duration = 0.0
            chapter_lines = chapter_lines_map.get(chapter.id) or []
            sorted_lines = sorted(
                chapter_lines,
                key=lambda item: ((getattr(item, "line_order", None) or 0), (getattr(item, "id", 0) or 0)),
            )
            for line_index, line in enumerate(sorted_lines, start=1):
                audio_path = getattr(line, "audio_path", None)
                if audio_path and os.path.exists(audio_path):
                    valid_audio_paths.append(audio_path)
                    duration = float(sf.info(audio_path).duration)
                    chapter_duration += duration
                    overlay_lines.append({
                        "anchor": f"source-line-{index:03d}-{line_index:04d}",
                        "text": _safe_text(getattr(line, "text_content", None)),
                        "duration": duration,
                    })
                elif _safe_text(getattr(line, "text_content", None)):
                    skipped_audio_line_count += 1

            if not valid_audio_paths:
                text_only_chapter_count += 1
                continue

            chapter_token = _safe_file_stem("chapter_audio", index)
            temp_mp3_path = os.path.join(audio_temp_dir, f"{chapter_token}.mp3")
            final_audio_href = posixpath.join(audio_rel_dir, f"{chapter_token}.mp3")
            final_audio_path = os.path.join(audio_output_dir, f"{chapter_token}.mp3")
            final_smil_href = posixpath.join(smil_rel_dir, f"{chapter_token}.smil")
            final_smil_path = os.path.join(smil_output_dir, f"{chapter_token}.smil")
            chapter_title = _safe_text(getattr(chapter, "title", None), f"第{index}章")

            _export_chapter_audio(line_service, valid_audio_paths, temp_mp3_path)
            shutil.copyfile(temp_mp3_path, final_audio_path)
            total_duration += chapter_duration

            chapter_file_path = _resolve_source_file_path(extract_dir, opf_rel_path, source_href)
            if not os.path.exists(chapter_file_path):
                raise ValueError(f"源 EPUB 章节文件不存在: {source_href}")

            chapter_manifest_href = _resolve_source_manifest_href(source_href, opf_rel_path)
            audio_href_from_chapter = posixpath.relpath(final_audio_href, posixpath.dirname(chapter_manifest_href) or ".")
            matched_segments = _inject_audio_into_xhtml(
                chapter_file_path,
                audio_href_from_chapter,
                chapter_title,
                overlay_lines,
            )

            chapter_audio_items.append({
                "audio_id": f"{chapter_token}-audio",
                "audio_href": final_audio_href,
                "smil_id": f"{chapter_token}-smil" if matched_segments else None,
                "smil_href": final_smil_href if matched_segments else None,
                "source_href": source_href,
                "source_item_id": _safe_text(getattr(chapter, "source_item_id", None)),
                "duration": chapter_duration,
            })

            if matched_segments:
                text_href_from_smil = posixpath.relpath(chapter_manifest_href, posixpath.dirname(final_smil_href) or ".")
                audio_href_from_smil = posixpath.relpath(final_audio_href, posixpath.dirname(final_smil_href) or ".")
                with open(final_smil_path, "w", encoding="utf-8") as smil_file:
                    smil_file.write(_build_source_epub_smil(text_href_from_smil, audio_href_from_smil, matched_segments))
            else:
                skipped_audio_line_count += len([item for item in overlay_lines if item.get("text")])

            audio_chapter_count += 1

        if audio_chapter_count == 0:
            raise ValueError("没有任何可导出的章节音频，请先生成章节台词音频")

        _update_opf_for_audio(opf_full_path, chapter_audio_items, total_duration, opf_rel_path)
        _pack_epub_from_directory(extract_dir, output_path)
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "success": True,
        "output_path": output_path,
        "chapter_count": len(chapters),
        "audio_chapter_count": audio_chapter_count,
        "text_only_chapter_count": text_only_chapter_count,
        "skipped_audio_line_count": skipped_audio_line_count,
        "duration": _format_clock(total_duration),
        "export_mode": "source_epub_enhanced",
        "export_mode_label": "源 EPUB 增强有声书",
    }


def _build_package_opf(
    title: str,
    creator: str,
    language: str,
    identifier: str,
    overlay_metadata: list[str],
    manifest_items: list[str],
    spine_items: list[str],
    total_duration: float,
    extra_metadata: list[str] | None = None,
    package_prefix: str | None = None,
    spine_attributes: str = "",
) -> str:
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    duration_meta = ""
    if total_duration > 0:
        duration_meta = f'    <meta property="media:duration">{_format_clock(total_duration)}</meta>\n'

    creator_meta = f"    <dc:creator>{html.escape(creator)}</dc:creator>\n" if creator else ""
    overlays = "".join(f"    {item}\n" for item in overlay_metadata)
    extras = "".join(f"    {item}\n" for item in (extra_metadata or []))
    manifest = "\n".join(f"    {item}" for item in manifest_items)
    spine = "\n".join(f"    {item}" for item in spine_items)
    package_prefix_attr = f' prefix="{html.escape(package_prefix)}"' if package_prefix else ""
    spine_attr = f" {spine_attributes.strip()}" if spine_attributes.strip() else ""

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<package xmlns=\"http://www.idpf.org/2007/opf\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\" version=\"3.0\" unique-identifier=\"book-id\"{package_prefix_attr}>
  <metadata>
    <dc:identifier id=\"book-id\">{html.escape(identifier)}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>{html.escape(language)}</dc:language>
{creator_meta}    <meta property=\"dcterms:modified\">{modified}</meta>
{extras}{overlays}{duration_meta}  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine{spine_attr}>
{spine}
  </spine>
</package>
"""


def _export_chapter_audio(line_service, source_paths: list[str], output_mp3_path: str):
    ffmpeg_path = getFfmpegPath()
    temp_wav_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.wav")
    try:
        line_service.concat_wav_files(source_paths, temp_wav_path)
        cmd = [
            ffmpeg_path,
            "-y",
            "-i", temp_wav_path,
            "-vn",
            "-codec:a", "libmp3lame",
            "-q:a", "2",
            output_mp3_path,
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(cmd, check=True, creationflags=creationflags)
    finally:
        if os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)


def _convert_audio_to_m4a(source_path: str, output_path: str):
    ffmpeg_path = getFfmpegPath()
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", source_path,
        "-vn",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ac", "2",
        "-ar", "44100",
        output_path,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(cmd, check=True, creationflags=creationflags)


def _export_flowing_audiobook_epub(
    project,
    chapters,
    chapter_lines_map: dict[int, list],
    output_path: str,
    line_service,
    creator: str | None = None,
    language: str = "zh-CN",
    identifier: str | None = None,
):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="sonicvale_epub_")
    book_title = _safe_text(getattr(project, "name", None), "SonicVale Audiobook")
    book_creator = _safe_text(creator)
    book_identifier = _safe_text(identifier, f"urn:uuid:{uuid.uuid4()}")

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        '<item id="book-css" href="styles/book.css" media-type="text/css" />',
    ]
    overlay_metadata = []
    spine_items = []
    nav_items = []
    total_duration = 0.0
    audio_chapter_count = 0
    text_only_chapter_count = 0
    skipped_audio_line_count = 0

    try:
        with zipfile.ZipFile(output_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", _build_container_xml())
            archive.writestr("OEBPS/styles/book.css", FLOWING_CSS_CONTENT)

            for index, chapter in enumerate(chapters, start=1):
                chapter_token = _safe_file_stem("chapter", index)
                xhtml_id = f"{chapter_token}-xhtml"
                xhtml_href = f"text/{chapter_token}.xhtml"
                chapter_title = _safe_text(getattr(chapter, "title", None), f"第{index}章")
                lines = chapter_lines_map.get(chapter.id) or []
                body_html, text_entries = _build_text_blocks(lines, getattr(chapter, "text_content", None), chapter_token)
                nav_items.append({"href": xhtml_href, "title": chapter_title})
                spine_items.append(f'<itemref idref="{xhtml_id}" />')

                valid_segments = []
                for entry in text_entries:
                    line = entry["line"]
                    audio_path = getattr(line, "audio_path", None)
                    if audio_path and os.path.exists(audio_path):
                        duration = float(sf.info(audio_path).duration)
                        valid_segments.append({
                            "anchor": entry["anchor"],
                            "audio_path": audio_path,
                            "duration": duration,
                        })
                    elif entry["text"]:
                        skipped_audio_line_count += 1

                if valid_segments:
                    audio_chapter_count += 1
                    smil_id = f"{chapter_token}-smil"
                    audio_id = f"{chapter_token}-audio"
                    chapter_duration = sum(item["duration"] for item in valid_segments)
                    chapter_audio_href = f"../audio/{chapter_token}.mp3"
                    manifest_items.append(
                        f'<item id="{xhtml_id}" href="{xhtml_href}" media-type="application/xhtml+xml" media-overlay="{smil_id}" />'
                    )
                    manifest_items.append(
                        f'<item id="{smil_id}" href="smil/{chapter_token}.smil" media-type="application/smil+xml" />'
                    )
                    manifest_items.append(
                        f'<item id="{audio_id}" href="audio/{chapter_token}.mp3" media-type="audio/mpeg" />'
                    )
                    overlay_metadata.append(
                        f'<meta property="media:duration" refines="#{smil_id}">{_format_clock(chapter_duration)}</meta>'
                    )

                    output_mp3_path = os.path.join(temp_dir, f"{chapter_token}.mp3")
                    _export_chapter_audio(line_service, [item["audio_path"] for item in valid_segments], output_mp3_path)
                    archive.write(output_mp3_path, f"OEBPS/audio/{chapter_token}.mp3")
                    archive.writestr(
                        f"OEBPS/{xhtml_href}",
                        _build_chapter_xhtml(chapter_title, body_html, language, audio_href=chapter_audio_href),
                    )
                    archive.writestr(
                        f"OEBPS/smil/{chapter_token}.smil",
                        _build_smil(chapter_token, [item["anchor"] for item in valid_segments], valid_segments),
                    )
                    total_duration += chapter_duration
                else:
                    text_only_chapter_count += 1
                    manifest_items.append(
                        f'<item id="{xhtml_id}" href="{xhtml_href}" media-type="application/xhtml+xml" />'
                    )
                    archive.writestr(f"OEBPS/{xhtml_href}", _build_chapter_xhtml(chapter_title, body_html, language))

            if audio_chapter_count == 0:
                raise ValueError("没有任何可导出的章节音频，请先生成章节台词音频")

            archive.writestr("OEBPS/nav.xhtml", _build_nav_xhtml(book_title, nav_items, language))
            archive.writestr(
                "OEBPS/package.opf",
                _build_package_opf(
                    title=book_title,
                    creator=book_creator,
                    language=language,
                    identifier=book_identifier,
                    overlay_metadata=overlay_metadata,
                    manifest_items=manifest_items,
                    spine_items=spine_items,
                    total_duration=total_duration,
                ),
            )
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "success": True,
        "output_path": output_path,
        "chapter_count": len(chapters),
        "audio_chapter_count": audio_chapter_count,
        "text_only_chapter_count": text_only_chapter_count,
        "skipped_audio_line_count": skipped_audio_line_count,
        "duration": _format_clock(total_duration),
        "export_mode": "standard",
        "export_mode_label": "标准 EPUB 3 有声书",
    }


def _export_apple_read_aloud_epub(
    project,
    chapters,
    chapter_lines_map: dict[int, list],
    output_path: str,
    creator: str | None = None,
    language: str = "zh-Hans",
    identifier: str | None = None,
):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="sonicvale_apple_epub_")
    book_title = _safe_text(getattr(project, "name", None), "SonicVale Read Aloud")
    book_creator = _safe_text(creator)
    book_identifier = _safe_text(identifier, f"urn:uuid:{uuid.uuid4()}")

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        '<item id="book-css" href="styles/book.css" media-type="text/css" />',
    ]
    overlay_metadata = []
    spine_items = []
    nav_items = []
    landmark_items = []
    total_duration = 0.0
    audio_chapter_count = 0
    text_only_chapter_count = 0
    skipped_audio_line_count = 0
    page_count = 0

    try:
        with zipfile.ZipFile(output_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", _build_container_xml())
            archive.writestr("OEBPS/styles/book.css", APPLE_FIXED_LAYOUT_CSS)

            chapter_page_sets = []
            total_pages = 0
            for chapter_index, chapter in enumerate(chapters, start=1):
                chapter_token = _safe_file_stem("chapter", chapter_index)
                chapter_title = _safe_text(getattr(chapter, "title", None), f"第{chapter_index}章")
                entries = _build_fixed_layout_entries(
                    chapter_lines_map.get(chapter.id) or [],
                    getattr(chapter, "text_content", None),
                    chapter_token,
                )
                page_groups = _paginate_entries(entries, first_page_budget=380, page_budget=470, max_items=12)
                chapter_page_sets.append((chapter_token, chapter_title, page_groups))
                total_pages += len(page_groups)

            if total_pages == 0:
                raise ValueError("没有可导出的章节内容")

            current_page_number = 0
            for chapter_token, chapter_title, page_groups in chapter_page_sets:
                chapter_has_audio = False

                for page_index, page_entries in enumerate(page_groups, start=1):
                    current_page_number += 1
                    page_count += 1
                    page_token = f"{chapter_token}_page_{page_index:03d}"
                    xhtml_id = f"{page_token}-xhtml"
                    xhtml_href = f"text/{page_token}.xhtml"

                    if page_index == 1:
                        nav_items.append({"href": xhtml_href, "title": chapter_title})
                        if not landmark_items:
                            landmark_items.append({
                                "href": xhtml_href,
                                "title": "开始阅读",
                                "epub_type": "bodymatter",
                            })

                    page_audio_segments = []
                    for audio_index, entry in enumerate(page_entries, start=1):
                        audio_path = entry.get("audio_path")
                        if audio_path and os.path.exists(audio_path):
                            audio_file = f"{page_token}_audio_{audio_index:03d}.m4a"
                            audio_output_path = os.path.join(temp_dir, audio_file)
                            _convert_audio_to_m4a(audio_path, audio_output_path)
                            archive.write(audio_output_path, f"OEBPS/audio/{audio_file}")
                            manifest_items.append(
                                f'<item id="{page_token}-audio-{audio_index:03d}" href="audio/{audio_file}" media-type="audio/m4a" />'
                            )
                            page_audio_segments.append({
                                "anchor": entry["anchor"],
                                "audio_file": audio_file,
                                "duration": float(entry["duration"]),
                            })
                        elif entry.get("text"):
                            skipped_audio_line_count += 1

                    archive.writestr(
                        f"OEBPS/{xhtml_href}",
                        _build_fixed_page_xhtml(
                            title=chapter_title,
                            entries=page_entries,
                            page_number=current_page_number,
                            total_pages=total_pages,
                            language=language,
                            include_title=page_index == 1,
                        ),
                    )

                    if page_audio_segments:
                        chapter_has_audio = True
                        smil_id = f"{page_token}-smil"
                        page_duration = sum(item["duration"] for item in page_audio_segments)
                        manifest_items.append(
                            f'<item id="{xhtml_id}" href="{xhtml_href}" media-type="application/xhtml+xml" media-overlay="{smil_id}" />'
                        )
                        manifest_items.append(
                            f'<item id="{smil_id}" href="smil/{page_token}.smil" media-type="application/smil+xml" />'
                        )
                        overlay_metadata.append(
                            f'<meta property="media:duration" refines="#{smil_id}">{_format_clock(page_duration)}</meta>'
                        )
                        archive.writestr(
                            f"OEBPS/smil/{page_token}.smil",
                            _build_fixed_layout_smil(page_token, xhtml_href, page_audio_segments),
                        )
                        total_duration += page_duration
                    else:
                        manifest_items.append(
                            f'<item id="{xhtml_id}" href="{xhtml_href}" media-type="application/xhtml+xml" />'
                        )

                    spine_items.append(f'<itemref idref="{xhtml_id}" />')

                if chapter_has_audio:
                    audio_chapter_count += 1
                else:
                    text_only_chapter_count += 1

            if audio_chapter_count == 0:
                raise ValueError("没有任何可导出的章节音频，请先生成章节台词音频")

            archive.writestr(
                "OEBPS/nav.xhtml",
                _build_nav_xhtml(book_title, nav_items, language, landmark_items=landmark_items),
            )
            archive.writestr(
                "OEBPS/package.opf",
                _build_package_opf(
                    title=book_title,
                    creator=book_creator,
                    language=language,
                    identifier=book_identifier,
                    overlay_metadata=overlay_metadata,
                    manifest_items=manifest_items,
                    spine_items=spine_items,
                    total_duration=total_duration,
                    extra_metadata=[
                        '<meta property="rendition:layout">pre-paginated</meta>',
                        '<meta property="rendition:spread">none</meta>',
                        '<meta property="media:active-class">-epub-media-overlay-active</meta>',
                    ],
                    package_prefix='rendition: http://www.idpf.org/vocab/rendition/#',
                ),
            )
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "success": True,
        "output_path": output_path,
        "chapter_count": len(chapters),
        "audio_chapter_count": audio_chapter_count,
        "text_only_chapter_count": text_only_chapter_count,
        "skipped_audio_line_count": skipped_audio_line_count,
        "duration": _format_clock(total_duration),
        "page_count": page_count,
        "export_mode": "apple_books_read_aloud",
        "export_mode_label": "Apple 图书固定版式朗读 EPUB",
    }


def export_project_audiobook_epub(
    project,
    chapters,
    chapter_lines_map: dict[int, list],
    output_path: str,
    line_service,
    creator: str | None = None,
    language: str = "zh-CN",
    identifier: str | None = None,
    export_mode: str = "standard",
):
    if not chapters:
        raise ValueError("没有可导出的章节")

    normalized_mode = _safe_text(export_mode, "standard")
    normalized_language = _normalize_language(language, normalized_mode)
    project_mode = normalize_project_mode(getattr(project, "project_mode", None))

    if project_mode == PROJECT_MODE_AUDIO_EPUB:
        if normalized_mode == "apple_books_read_aloud":
            raise ValueError("源 EPUB 模式暂不支持 Apple 图书固定版式朗读导出")
        return _export_source_epub_audiobook(
            project=project,
            chapters=chapters,
            chapter_lines_map=chapter_lines_map,
            output_path=output_path,
            line_service=line_service,
        )

    if normalized_mode == "apple_books_read_aloud":
        return _export_apple_read_aloud_epub(
            project=project,
            chapters=chapters,
            chapter_lines_map=chapter_lines_map,
            output_path=output_path,
            creator=creator,
            language=normalized_language,
            identifier=identifier,
        )

    return _export_flowing_audiobook_epub(
        project=project,
        chapters=chapters,
        chapter_lines_map=chapter_lines_map,
        output_path=output_path,
        line_service=line_service,
        creator=creator,
        language=normalized_language,
        identifier=identifier,
    )