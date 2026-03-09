from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.po import ChapterPO


class ChapterRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, chapter_id: int) -> Optional[ChapterPO]:
        """根据 ID 查询项目"""
        return self.db.get(ChapterPO, chapter_id)

    def get_all(self, project_id: int) -> Sequence[ChapterPO]:
        """获取指定项目下的所有章节，按 order_index 排序"""
        stmt = select(ChapterPO).where(ChapterPO.project_id == project_id).order_by(ChapterPO.order_index.asc(), ChapterPO.id.asc())
        return self.db.execute(stmt).scalars().all()

    def create(self, chapter_data: ChapterPO) -> ChapterPO:
        """新建项目"""
        self.db.add(chapter_data)
        self.db.commit()
        self.db.refresh(chapter_data)
        return chapter_data

    def update(self, chapter_id: int, chapter_data: dict) -> Optional[ChapterPO]:
        """更新项目"""
        chapter = self.get_by_id(chapter_id)
        if not chapter:
            return None
        for key, value in chapter_data.items():
            if value is not None:  # 只更新不为空的字段
                setattr(chapter, key, value)

        self.db.commit()
        self.db.refresh(chapter)
        return chapter

    def delete(self, chapter_id: int) -> bool:
        """删除章节"""
        project = self.get_by_id(chapter_id)
        if not project:
            return False
        self.db.delete(project)
        self.db.commit()
        return True
    # def delete_all_by_project_id(self, project_id: int) -> bool:
    #     """删除指定项目下的所有章节"""
    #     pos = self.get_all(project_id)
    #     for po in pos:
    #         self.db.delete(po)
    #     self.db.commit()
    #     return True

    def get_by_name(self, name: str, project_id: int) -> Optional[ChapterPO]:
        """根据项目ID和章节名称查找章节"""
        stmt = (
            select(ChapterPO)
            .where(ChapterPO.title == name)
            .where(ChapterPO.project_id == project_id)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def search(self, keyword: str) -> Sequence[ChapterPO]:
        """模糊搜索"""
        stmt = select(ChapterPO).where(ChapterPO.title.ilike(f"%{keyword}%"))
        return self.db.execute(stmt).scalars().all()

    def shift_order_indices(self, project_id: int, from_index: int, shift: int) -> None:
        """将指定项目中 order_index >= from_index 的章节向后移动 shift 位"""
        stmt = (
            update(ChapterPO)
            .where(ChapterPO.project_id == project_id)
            .where(ChapterPO.order_index >= from_index)
            .values(order_index=ChapterPO.order_index + shift)
        )
        self.db.execute(stmt)
        self.db.commit()