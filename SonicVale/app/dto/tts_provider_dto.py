from datetime import datetime

from pydantic import BaseModel
from typing import Optional


class TTSProviderCreateDTO(BaseModel):
    name: Optional[str] = None
    id: Optional[int] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    custom_params: Optional[str] = None
    status: Optional[int] = None



class TTSProviderResponseDTO(BaseModel):
    """业务实体：tts_provider"""
    name: str
    id: Optional[int] = None
    api_base_url : Optional[str] = None
    api_key: Optional[str] = None
    custom_params: Optional[str] = None
    status : Optional[int] = None
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class GPTSoVITSPathDTO(BaseModel):
    project_path: str


class GPTSoVITSImportModelDTO(BaseModel):
    project_path: str
    source_model_dir: str


class GPTSoVITSModelDTO(BaseModel):
    name: str
    model_dir: str
    infer_config_path: str


class GPTSoVITSSyncResultDTO(BaseModel):
    created: int
    skipped: int
    total: int