import os
import os
from ebooklib import epub
from bs4 import BeautifulSoup


def parse_epub(file_path: str) -> list[dict]:
    """解析 epub 文件, 返回章节列表

    每个章节是一个 dict, 包含章节名和文本内容
    """
    book = epub.read_epub(file_path)
    chapters = []

    # flatten toc into (title, href) pairs
    def extract_toc(toc_items):
        result = []
        for item in toc_items:
            if isinstance(item, (list, tuple)):
                if isinstance(item[0], epub.Section):
                    result.append((item[0].title, item[0].href))
                elif isinstance(item[0], epub.Link):
                    result.append((item[0].title, item[0].href))
                result.extend(extract_toc(item[1]))
            elif isinstance(item, epub.Link):
                result.append((item.title, item.href))
            elif isinstance(item, epub.Section):
                result.append((item.title, item.href))
        return result

    toc_list = extract_toc(book.toc) if book.toc else []
    toc_map = {href.split('#')[0]: title for title, href in toc_list if href}

    current = {"chapter_name": "前言/未命名", "content": ""}
    for spine_item in book.spine:
        idref = spine_item[0] if isinstance(spine_item, tuple) else spine_item
        if idref == 'nav':
            continue
        item = book.get_item_with_id(idref)
        if not item or not isinstance(item, epub.EpubHtml):
            continue
        fname = item.file_name or ''
        if fname in toc_map:
            if current["content"].strip():
                chapters.append(current)
            current = {"chapter_name": toc_map[fname], "content": ""}
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        for script in soup(["script", "style"]):
            script.extract()
        text = soup.get_text(separator='\n', strip=True)
        if text:
            current["content"] += text + "\n"
    if current["content"].strip():
        chapters.append(current)
    return chapters
