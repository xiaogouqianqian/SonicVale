from fastapi import APIRouter, Depends, HTTPException
from typing import List
import json

from sqlalchemy.orm import Session

from app.core.response import Res
from app.db.database import get_db
from app.dto.tts_provider_dto import (
    TTSProviderCreateDTO,
    TTSProviderResponseDTO,
    GPTSoVITSPathDTO,
    GPTSoVITSImportModelDTO,
    GPTSoVITSModelDTO,
    GPTSoVITSSyncResultDTO,
)
from app.entity.tts_provider_entity import TTSProviderEntity
from app.models.po import VoicePO
from app.repositories.voice_repository import VoiceRepository
from app.services.tts_provider_service import TTSProviderService
from app.repositories.tts_provider_repository import TTSProviderRepository
from app.services.gptsovits_inference_service import GPTSoVITSInferenceService

# 初始化 router
router = APIRouter(prefix="/tts_providers", tags=["TTSProviders"])

# 依赖注入（实际TTS供应商可用 DI 容器）

def get_service(db: Session = Depends(get_db)) -> TTSProviderService:
    repository = TTSProviderRepository(db)  # ✅ 传入 db
    return TTSProviderService(repository)


def _safe_load_params(params: str | None) -> dict:
    if not params:
        return {}
    try:
        return json.loads(params)
    except Exception:
        return {}


def _safe_dump_params(params: dict) -> str:
    return json.dumps(params, ensure_ascii=False)


# 按id查找
@router.get("/{tts_provider_id}", response_model=Res[TTSProviderResponseDTO],
            summary="查询TTS供应商",
            description="根据TTS供应商ID查询TTS供应商信息")
def get_tts_provider(tts_provider_id: int, service: TTSProviderService = Depends(get_service)):
    entity = service.get_tts_provider(tts_provider_id)
    if entity:
        res = TTSProviderResponseDTO(**entity.__dict__)
        return Res(data=res, code=200, message="查询成功")
    else:
        return Res(data=None, code=404, message="TTS供应商不存在")

@router.get("/", response_model=Res[List[TTSProviderResponseDTO]],
            summary="查询所有TTS供应商",
            description="查询所有TTS供应商信息")
def get_all_tts_providers(service: TTSProviderService = Depends(get_service)):
    entities = service.get_all_tts_providers()
    dtos = [TTSProviderResponseDTO(**e.__dict__) for e in entities]
    return Res(data=dtos, code=200, message="查询成功")


# ------------------- 修改TTS供应商 -------------------
@router.put("/{tts_provider_id}", response_model=Res[TTSProviderCreateDTO],
            summary="修改TTS供应商",
            description="根据TTS供应商ID修改TTS供应商信息")
def update_tts_provider(tts_provider_id: int, dto: TTSProviderCreateDTO, service: TTSProviderService = Depends(get_service)):

    # 先根据id进行查找
    tts_provider = service.get_tts_provider(tts_provider_id)
    if not tts_provider:
        return Res(data=None, code=400, message="TTS供应商不存在")

    success = service.update_tts_provider(tts_provider_id,dto.dict(exclude_unset=True))
    if success:
        return Res(data=dto, code=200, message="更新成功")
    else:
        return Res(data=None, code=400, message="更新失败")



# 测试tts是否正常
@router.post("/test", response_model=Res)
def test_tts_provider(dto: TTSProviderCreateDTO, service: TTSProviderService = Depends(get_service)):
    """
    测试tts是否正常
    """
    entity  = TTSProviderEntity(**dto.dict())
    success = service.test_tts_provider(entity)
    if success:
        return Res(data=None, code=200, message="测试成功")
    else:
        return Res(data=None, code=400, message="测试失败")


@router.post("/{tts_provider_id}/gptsovits/validate_path", response_model=Res[bool])
def validate_gptsovits_path(
    tts_provider_id: int,
    dto: GPTSoVITSPathDTO,
    service: TTSProviderService = Depends(get_service),
):
    provider = service.get_tts_provider(tts_provider_id)
    if not provider:
        return Res(data=False, code=404, message="TTS供应商不存在")

    gpt_service = GPTSoVITSInferenceService()
    ok, msg = gpt_service.validate_project_path(dto.project_path)
    return Res(data=ok, code=200 if ok else 400, message=msg)


@router.post("/{tts_provider_id}/gptsovits/scan_models", response_model=Res[List[GPTSoVITSModelDTO]])
def scan_gptsovits_models(
    tts_provider_id: int,
    dto: GPTSoVITSPathDTO,
    service: TTSProviderService = Depends(get_service),
):
    provider = service.get_tts_provider(tts_provider_id)
    if not provider:
        return Res(data=[], code=404, message="TTS供应商不存在")

    gpt_service = GPTSoVITSInferenceService()
    ok, msg = gpt_service.validate_project_path(dto.project_path)
    if not ok:
        return Res(data=[], code=400, message=msg)

    models = gpt_service.scan_models(dto.project_path)
    data = [GPTSoVITSModelDTO(**m.__dict__) for m in models]
    return Res(data=data, code=200, message=f"扫描完成，共 {len(data)} 个模型")


@router.post("/{tts_provider_id}/gptsovits/import_model", response_model=Res[str])
def import_gptsovits_model(
    tts_provider_id: int,
    dto: GPTSoVITSImportModelDTO,
    service: TTSProviderService = Depends(get_service),
):
    provider = service.get_tts_provider(tts_provider_id)
    if not provider:
        return Res(data=None, code=404, message="TTS供应商不存在")

    gpt_service = GPTSoVITSInferenceService()
    ok, msg = gpt_service.validate_project_path(dto.project_path)
    if not ok:
        return Res(data=None, code=400, message=msg)

    try:
        target_dir = gpt_service.import_model(dto.project_path, dto.source_model_dir)
        return Res(data=target_dir, code=200, message="模型导入成功")
    except ValueError as e:
        return Res(data=None, code=400, message=str(e))
    except Exception as e:
        return Res(data=None, code=500, message=f"模型导入失败: {e}")


@router.post("/{tts_provider_id}/gptsovits/sync_models", response_model=Res[GPTSoVITSSyncResultDTO])
def sync_gptsovits_models(
    tts_provider_id: int,
    dto: GPTSoVITSPathDTO,
    db: Session = Depends(get_db),
    service: TTSProviderService = Depends(get_service),
):
    provider = service.get_tts_provider(tts_provider_id)
    if not provider:
        return Res(data=None, code=404, message="TTS供应商不存在")

    gpt_service = GPTSoVITSInferenceService()
    ok, msg = gpt_service.validate_project_path(dto.project_path)
    if not ok:
        return Res(data=None, code=400, message=msg)

    models = gpt_service.scan_models(dto.project_path)
    repo = VoiceRepository(db)

    created = 0
    skipped = 0
    for model in models:
        exists = repo.get_by_name(model.name, tts_provider_id)
        if exists:
            skipped += 1
            continue
        repo.create(
            VoicePO(
                name=model.name,
                tts_provider_id=tts_provider_id,
                reference_path="",
                description=None,
                is_multi_emotion=0,
            )
        )
        created += 1

    params = _safe_load_params(provider.custom_params)
    params["project_path"] = dto.project_path
    service.update_tts_provider(tts_provider_id, {"name": provider.name, "custom_params": _safe_dump_params(params)})

    result = GPTSoVITSSyncResultDTO(created=created, skipped=skipped, total=len(models))
    return Res(data=result, code=200, message="同步完成")



# ------------------- 删除TTS供应商 -------------------
# @router.delete("/{tts_provider_id}", response_model=Res,
#                summary="删除TTS供应商",
#                description="根据TTS供应商ID删除TTS供应商,并且级联删除TTS供应商下所有章节以及内容")
# def delete_tts_provider(tts_provider_id: int, service: TTSProviderService = Depends(get_service)):
#     success = service.delete_tts_provider(tts_provider_id)
#     # todo 级联删除TTS供应商所有相关内容，比如TTS供应商下所有章节以及内容
#     if success:
#         return Res(data=None, code=200, message="删除成功")
#     else:
#         return Res(data=None, code=400, message="删除失败或TTS供应商不存在")