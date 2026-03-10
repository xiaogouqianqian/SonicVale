
from dataclasses import dataclass
from datetime import datetime
from typing import Optional



@dataclass
class ChapterEntity:
    """业务实体：章节"""
    title: str
    project_id: int
    order_index: Optional[int] = None
    id: Optional[int] = None
    text_content : Optional[str] = None
    source_href: Optional[str] = None
    source_item_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


