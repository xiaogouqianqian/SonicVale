import os
import posixpath
import zipfile
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from ebooklib import epub


CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}


def _safe_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _normalize_href(href: str | None) -> str:
    value = _safe_text(href)
    if not value:
        return ""
    return posixpath.normpath(value.split("#", 1)[0])


def get_epub_opf_path(file_path: str) -> str:
    with zipfile.ZipFile(file_path, "r") as archive:
        container_xml = archive.read("META-INF/container.xml")

    root = ET.fromstring(container_xml)
    rootfile = root.find("container:rootfiles/container:rootfile", CONTAINER_NS)
    if rootfile is None:
        raise ValueError("EPUB 缺少 container.xml rootfile 定义")

    full_path = rootfile.attrib.get("full-path", "").strip()
    if not full_path:
        raise ValueError("EPUB 缺少 OPF 路径信息")
    return full_path


def _extract_toc_items(toc_items):
    result = []
    for item in toc_items:
        if isinstance(item, (list, tuple)):
            if item and isinstance(item[0], epub.Section):
                result.append((item[0].title, item[0].href))
            elif item and isinstance(item[0], epub.Link):
                result.append((item[0].title, item[0].href))

            if len(item) > 1:
                result.extend(_extract_toc_items(item[1]))
        elif isinstance(item, epub.Link):
            result.append((item.title, item.href))
        elif isinstance(item, epub.Section):
            result.append((item.title, item.href))
    return result


def parse_epub_book(file_path: str) -> dict:
    """解析 epub 文件，返回书籍元信息与章节源文件映射。"""
    book = epub.read_epub(file_path)
    opf_path = get_epub_opf_path(file_path)

    toc_list = _extract_toc_items(book.toc) if book.toc else []
    toc_map = {
        _normalize_href(href): _safe_text(title)
        for title, href in toc_list
        if _normalize_href(href)
    }

    metadata_title = ""
    metadata_language = ""
    metadata_identifier = ""

    title_meta = book.get_metadata("DC", "title")
    if title_meta:
        metadata_title = _safe_text(title_meta[0][0])

    language_meta = book.get_metadata("DC", "language")
    if language_meta:
        metadata_language = _safe_text(language_meta[0][0], "zh-CN")

    identifier_meta = book.get_metadata("DC", "identifier")
    if identifier_meta:
        metadata_identifier = _safe_text(identifier_meta[0][0])

    chapters = []
    current = {
        "chapter_name": "前言/未命名",
        "content": "",
        "source_href": "",
        "source_item_id": "",
        "order_index": 1,
    }

    for spine_item in book.spine:
        idref = spine_item[0] if isinstance(spine_item, tuple) else spine_item
        if idref == "nav":
            continue

        item = book.get_item_with_id(idref)
        if not item or not isinstance(item, epub.EpubHtml):
            continue

        source_href = _normalize_href(getattr(item, "file_name", None))
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for script in soup(["script", "style"]):
            script.extract()

        content = soup.get_text(separator="\n", strip=True)
        if not _safe_text(content):
            continue

        toc_title = toc_map.get(source_href)
        if toc_title:
            if _safe_text(current.get("content")):
                current["order_index"] = len(chapters) + 1
                chapters.append(current)
            current = {
                "chapter_name": toc_title,
                "content": "",
                "source_href": source_href,
                "source_item_id": idref,
                "order_index": len(chapters) + 1,
            }
        elif not _safe_text(current.get("source_href")):
            heading = soup.find(["h1", "h2", "title"])
            fallback_title = _safe_text(heading.get_text(strip=True) if heading else None, "前言/未命名")
            current["chapter_name"] = fallback_title
            current["source_href"] = source_href
            current["source_item_id"] = idref

        current["content"] += content + "\n"

    if _safe_text(current.get("content")):
        current["order_index"] = len(chapters) + 1
        chapters.append(current)

    return {
        "metadata": {
            "title": metadata_title,
            "language": metadata_language or "zh-CN",
            "identifier": metadata_identifier,
            "opf_path": opf_path,
        },
        "chapters": chapters,
    }


def parse_epub(file_path: str) -> list[dict]:
    """解析 epub 文件, 返回章节列表

    每个章节是一个 dict, 包含章节名和文本内容
    """
    return parse_epub_book(file_path)["chapters"]
