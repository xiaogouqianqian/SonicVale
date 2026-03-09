import os
import shutil
import tempfile

from fastapi import APIRouter, Depends, HTTPException
from typing import List

from sqlalchemy.orm import Session

from app.core.config import getConfigPath
from app.core.epub_exporter import export_project_audiobook_epub
from app.core.response import Res
from app.db.database import get_db
from app.dto.project_dto import ProjectCreateDTO, ProjectResponseDTO, ProjectImportDTO, ProjectAudiobookExportDTO
from app.entity.chapter_entity import ChapterEntity
from app.entity.project_entity import ProjectEntity
from app.models.po import ChapterPO
from app.repositories.chapter_repository import ChapterRepository
from app.repositories.line_repository import LineRepository
from app.repositories.llm_provider_repository import LLMProviderRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.tts_provider_repository import TTSProviderRepository
from app.services.chapter_service import ChapterService
from app.services.line_service import LineService
from app.services.project_service import ProjectService
from app.repositories.project_repository import ProjectRepository
from app.services.role_service import RoleService

# 初始化 router
router = APIRouter(prefix="/projects", tags=["Projects"])

# 依赖注入（实际项目可用 DI 容器）

def get_service(db: Session = Depends(get_db)) -> ProjectService:
    repository = ProjectRepository(db)  # ✅ 传入 db
    return ProjectService(repository)

def get_chapter_service(db: Session = Depends(get_db)) -> ChapterService:
    repository = ChapterRepository(db)  # ✅ 传入 db
    return ChapterService(repository)

def get_role_service(db: Session = Depends(get_db)) -> RoleService:
    repository = RoleRepository(db)  # ✅ 传入 db
    return RoleService(repository)


def get_line_service(db: Session = Depends(get_db)) -> LineService:
    repository = LineRepository(db)
    role_repository = RoleRepository(db)
    tts_repository = TTSProviderRepository(db)
    return LineService(repository, role_repository, tts_repository)


@router.post("/", response_model=Res[ProjectResponseDTO],
             summary="创建项目",
             description="根据项目信息创建项目，项目名称不可重复")
def create_project(dto: ProjectCreateDTO, service: ProjectService = Depends(get_service)):
    """
    创建项目
    - dto: 前端 POST JSON 传入参数
    - service: Service 层注入
    """
    try:
        # DTO → Entity
        entity = ProjectEntity(**dto.__dict__)

        # 调用 Service 创建项目（返回 True/False）
        entityRes,message = service.create_project(entity)

        # 返回统一 Response
        if entityRes is not None:
            # 创建成功，可以返回 DTO 或者部分字段
            res = ProjectResponseDTO(**entityRes.__dict__)
            return Res(data=res, code=200, message="创建成功")
        else:
            return Res(data=None, code=400, message=message)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# 按id查找
@router.get("/{project_id}", response_model=Res[ProjectResponseDTO],
            summary="查询项目",
            description="根据项目ID查询项目信息")
def get_project(project_id: int, service: ProjectService = Depends(get_service)):
    entity = service.get_project(project_id)
    if entity:
        res = ProjectResponseDTO(**entity.__dict__)
        return Res(data=res, code=200, message="查询成功")
    else:
        return Res(data=None, code=404, message="项目不存在")

@router.get("/", response_model=Res[List[ProjectResponseDTO]],
            summary="查询所有项目",
            description="查询所有项目信息")
def get_all_projects(service: ProjectService = Depends(get_service)):
    entities = service.get_all_projects()
    dtos = [ProjectResponseDTO(**e.__dict__) for e in entities]
    return Res(data=dtos, code=200, message="查询成功")


# ------------------- 修改项目 -------------------
@router.put("/{project_id}", response_model=Res[ProjectCreateDTO],
            summary="修改项目",
            description="根据项目ID修改项目信息")
def update_project(project_id: int, dto: ProjectCreateDTO, service: ProjectService = Depends(get_service)):

    # 先根据id进行查找
    project = service.get_project(project_id)
    if not project:
        return Res(data=None, code=400, message="项目不存在")

    success = service.update_project(project_id,dto.dict())
    if success:
        return Res(data=dto, code=200, message="更新成功")
    else:
        return Res(data=None, code=400, message="更新失败")


# ------------------- 删除项目 -------------------
@router.delete("/{project_id}", response_model=Res,
               summary="删除项目",
               description="根据项目ID删除项目,并且级联删除项目下所有章节以及内容")
def delete_project(project_id: int, service: ProjectService = Depends(get_service), chapter_service: ChapterService = Depends(get_chapter_service),role_service: RoleService = Depends(get_role_service)):

    # 级联删除项目所有相关内容，比如项目下所有章节以及内容
    entities = chapter_service.get_all_chapters(project_id)
    for entity in entities:
        chapter_service.delete_chapter(entity.id)
    #     删除project目录
    project = service.get_project(project_id)

    project_path = os.path.join(project.project_root_path, str(project_id))
    if os.path.exists(project_path):
        shutil.rmtree(project_path)  # 删除整个文件夹及其所有内容
        print(f"已删除目录及内容: {project_path}")
    else:
        print(f"目录不存在: {project_path}")

    # 还要删除角色库中projet下的所有角色
    roles = role_service.get_all_roles(project_id)
    for role in roles:
        role_service.delete_role(role.id)
    success = service.delete_project(project_id)
    if success:
        return Res(data=None, code=200, message="删除成功")
    else:
        return Res(data=None, code=400, message="删除失败或项目不存在")

# 直接导入整本小说内容，然后解析，创建章节
@router.post("/{project_id}/import")
def import_project(project_id: int, dto: ProjectImportDTO,service: ProjectService = Depends(get_service),
                   chapter_service: ChapterService = Depends(get_chapter_service)):

    content = dto.content
    # 删除该项目下的所有章节
    # chapters = chapter_service.get_all_chapters(project_id)
    # for chapter in chapters:
    #     chapter_service.delete_chapter(chapter.id)
    # 解析content
    chapter_contents = service.parse_content(content)
    if len(chapter_contents) == 0:
        return Res(code=400, message="导入失败")

    # 批量创建章节
    for chapter_content in chapter_contents:
        name = chapter_content["chapter_name"]
        content = chapter_content["content"]
        print("批量创建章节", name)
        chapter_service.create_chapter(ChapterEntity(project_id=project_id, title=name, text_content=content))
    return Res(code=200, message="导入成功")


# 新增：EPUB 文件导入
from fastapi import File, UploadFile

@router.post("/{project_id}/import-epub")
async def import_epub(project_id: int,
                      file: UploadFile = File(...),
                      service: ProjectService = Depends(get_service),
                      chapter_service: ChapterService = Depends(get_chapter_service)):
    """上传 epub 文件，自动解析为章节并创建"""
    project = service.get_project(project_id)
    if project is None:
        return Res(code=400, message="项目不存在")

    # 将上传文件保存到临时位置
    suffix = os.path.splitext(file.filename)[1]
    if suffix.lower() != '.epub':
        return Res(code=400, message="仅支持 EPUB 格式文件")

    tmp_path = os.path.join(tempfile.gettempdir(), file.filename)
    with open(tmp_path, 'wb') as f:
        f.write(await file.read())

    # 解析 epub
    try:
        from app.core.epub_parser import parse_epub
        chapter_list = parse_epub(tmp_path)
    except Exception as e:
        return Res(code=500, message=f"EPUB 解析失败: {e}")
    finally:
        # 清理临时文件
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    if not chapter_list:
        return Res(code=400, message="未能解析出章节内容")

    for ch in chapter_list:
        chapter_service.create_chapter(ChapterEntity(project_id=project_id, title=ch.get("chapter_name"), text_content=ch.get("content")))

    return Res(code=200, message="EPUB 导入成功", data=chapter_list)


@router.post("/{project_id}/export-epub-audiobook")
def export_epub_audiobook(
    project_id: int,
    dto: ProjectAudiobookExportDTO,
    service: ProjectService = Depends(get_service),
    chapter_service: ChapterService = Depends(get_chapter_service),
    line_service: LineService = Depends(get_line_service),
):
    project = service.get_project(project_id)
    if project is None:
        return Res(code=400, message="项目不存在")

    chapters = list(chapter_service.get_all_chapters(project_id) or [])
    if dto.chapter_ids:
        selected_ids = set(dto.chapter_ids)
        chapters = [chapter for chapter in chapters if chapter.id in selected_ids]

    if not chapters:
        return Res(code=400, message="没有可导出的章节")

    chapter_lines_map = {
        chapter.id: list(line_service.get_all_lines(chapter.id) or [])
        for chapter in chapters
    }

    try:
        result = export_project_audiobook_epub(
            project=project,
            chapters=chapters,
            chapter_lines_map=chapter_lines_map,
            output_path=dto.export_path,
            line_service=line_service,
            creator=dto.creator,
            language=dto.language or "zh-CN",
            identifier=dto.identifier,
            export_mode=dto.export_mode or "standard",
        )
        return Res(code=200, message=f"{result.get('export_mode_label') or 'EPUB 3 有声书'}导出成功", data=result)
    except ValueError as exc:
        return Res(code=400, message=str(exc), data=None)
    except Exception as exc:
        return Res(code=500, message=f"EPUB 3 有声书导出失败: {exc}", data=None)