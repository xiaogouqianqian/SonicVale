import html
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

import soundfile as sf

from app.core.config import getFfmpegPath


CSS_CONTENT = """body {
  font-family: serif;
  line-height: 1.7;
  margin: 5%%;
}

h1 {
  margin-bottom: 1.2em;
}

p {
  margin: 0 0 0.9em;
  text-indent: 2em;
}

.line {
  text-indent: 0;
}
"""


def _safe_text(value: str | None, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _safe_file_stem(prefix: str, index: int) -> str:
    return f"{prefix}_{index:03d}"


def _format_clock(seconds: float) -> str:
    total_millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_millis, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


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

    content = _safe_text(chapter_text)
    if not content:
        content = "本章暂无可导出的正文。"

    parts = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    if not parts:
        parts = [line.strip() for line in content.splitlines() if line.strip()]
    if not parts:
        parts = [content]

    blocks = []
    for index, part in enumerate(parts, start=1):
        anchor = f"{chapter_token}-para-{index:04d}"
        escaped_text = html.escape(part)
        blocks.append(f'<p><span id="{anchor}">{escaped_text}</span></p>')
    return "\n".join(blocks), []


def _build_chapter_xhtml(title: str, body_html: str) -> str:
    escaped_title = html.escape(title)
    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" lang=\"zh-CN\" xml:lang=\"zh-CN\">
  <head>
    <title>{escaped_title}</title>
    <link rel=\"stylesheet\" type=\"text/css\" href=\"../styles/book.css\" />
  </head>
  <body epub:type=\"bodymatter chapter\">
    <section>
      <h1>{escaped_title}</h1>
      {body_html}
    </section>
  </body>
</html>
"""


def _build_nav_xhtml(book_title: str, nav_items: list[dict]) -> str:
    nav_links = "\n".join(
        f'        <li><a href="{html.escape(item["href"])}">{html.escape(item["title"])}</a></li>'
        for item in nav_items
    )
    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\" xmlns:epub=\"http://www.idpf.org/2007/ops\" lang=\"zh-CN\" xml:lang=\"zh-CN\">
  <head>
    <title>{html.escape(book_title)}</title>
  </head>
  <body>
    <nav epub:type=\"toc\" id=\"toc\">
      <h1>{html.escape(book_title)}</h1>
      <ol>
{nav_links}
      </ol>
    </nav>
  </body>
</html>
"""


def _build_smil(chapter_name: str, chapter_token: str, anchors: list[str], segments: list[dict]) -> str:
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
    <head>
        <meta name=\"dc:title\" content=\"{html.escape(chapter_name)}\" />
    </head>
    <body>
        <seq id=\"{chapter_token}-seq\" epub:textref=\"../text/{chapter_token}.xhtml\">
{os.linesep.join(items)}
        </seq>
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


def _build_package_opf(
    title: str,
    creator: str,
    language: str,
    identifier: str,
    overlay_metadata: list[str],
    manifest_items: list[str],
    spine_items: list[str],
    total_duration: float,
) -> str:
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    duration_meta = ""
    if total_duration > 0:
        duration_meta = f'    <meta property="media:duration">{_format_clock(total_duration)}</meta>\n'

    creator_meta = f"    <dc:creator>{html.escape(creator)}</dc:creator>\n" if creator else ""
    overlays = "".join(f"    {item}\n" for item in overlay_metadata)
    manifest = "\n".join(f"    {item}" for item in manifest_items)
    spine = "\n".join(f"    {item}" for item in spine_items)

    return f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<package xmlns=\"http://www.idpf.org/2007/opf\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\" version=\"3.0\" unique-identifier=\"book-id\">
  <metadata>
    <dc:identifier id=\"book-id\">{html.escape(identifier)}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>{html.escape(language)}</dc:language>
{creator_meta}    <meta property=\"dcterms:modified\">{modified}</meta>
{overlays}{duration_meta}  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine>
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


def export_project_audiobook_epub(
    project,
    chapters,
    chapter_lines_map: dict[int, list],
    output_path: str,
    line_service,
    creator: str | None = None,
    language: str = "zh-CN",
    identifier: str | None = None,
):
    if not chapters:
        raise ValueError("没有可导出的章节")

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
            archive.writestr(
                "mimetype",
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr("META-INF/container.xml", _build_container_xml())
            archive.writestr("OEBPS/styles/book.css", CSS_CONTENT)

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
                    _export_chapter_audio(
                        line_service,
                        [item["audio_path"] for item in valid_segments],
                        output_mp3_path,
                    )
                    archive.write(output_mp3_path, f"OEBPS/audio/{chapter_token}.mp3")
                    archive.writestr(
                        f"OEBPS/{xhtml_href}",
                        _build_chapter_xhtml(chapter_title, body_html),
                    )
                    archive.writestr(
                        f"OEBPS/smil/{chapter_token}.smil",
                        _build_smil(
                            chapter_title,
                            chapter_token,
                            [item["anchor"] for item in valid_segments],
                            valid_segments,
                        ),
                    )
                    total_duration += chapter_duration
                else:
                    text_only_chapter_count += 1
                    manifest_items.append(
                        f'<item id="{xhtml_id}" href="{xhtml_href}" media-type="application/xhtml+xml" />'
                    )
                    archive.writestr(
                        f"OEBPS/{xhtml_href}",
                        _build_chapter_xhtml(chapter_title, body_html),
                    )

            if audio_chapter_count == 0:
                raise ValueError("没有任何可导出的章节音频，请先生成章节台词音频")

            archive.writestr("OEBPS/nav.xhtml", _build_nav_xhtml(book_title, nav_items))
            archive.writestr(
                "OEBPS/package.opf",
                _build_package_opf(
                    title=book_title,
                    creator=book_creator,
                    language=_safe_text(language, "zh-CN"),
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
    }