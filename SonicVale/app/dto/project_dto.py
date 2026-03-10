from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class ProjectCreateDTO(BaseModel):
    name: str
    description: Optional[str] = None
    llm_provider_id: Optional[int] = None
    llm_model: Optional[str] = None
    tts_provider_id: Optional[int] = None
    prompt_id: Optional[int] = None
    # 精准填充
    is_precise_fill: Optional[int] = None
    # 项目路径
    project_root_path : Optional[str] = None
    project_mode: Optional[str] = "standard"

class ProjectResponseDTO(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    llm_provider_id: Optional[int] = None
    llm_model: Optional[str] = None
    tts_provider_id: Optional[int] = None
    prompt_id: Optional[int] = None
    # 精准填充
    is_precise_fill : Optional[int] = None
    # 项目路径
    project_root_path : Optional[str] = None
    project_mode: Optional[str] = "standard"
    source_epub_path: Optional[str] = None
    source_epub_name: Optional[str] = None
    source_epub_hash: Optional[str] = None
    source_epub_opf_path: Optional[str] = None
    source_epub_imported_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ProjectImportDTO(BaseModel):
    id : int
    content: str


class ProjectAudiobookExportDTO(BaseModel):
    export_path: str
    chapter_ids: Optional[list[int]] = None
    creator: Optional[str] = None
    language: Optional[str] = "zh-CN"
    identifier: Optional[str] = None
    export_mode: Optional[str] = "standard"
