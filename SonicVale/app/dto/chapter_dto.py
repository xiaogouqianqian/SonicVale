from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class ChapterCreateDTO(BaseModel):
    title: str
    project_id: int
    order_index: Optional[int] = None
    id: Optional[int] = None
    text_content : Optional[str] = None
    after_chapter_id: Optional[int] = None

class ChapterResponseDTO(BaseModel):
    title: str
    project_id: int
    order_index: Optional[int] = None
    id: Optional[int] = None
    text_content: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
