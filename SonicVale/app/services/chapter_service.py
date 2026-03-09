import json
import os
import re
import shutil
import threading
from collections import defaultdict
from typing import List

from sqlalchemy import Sequence

from app.core.config import getConfigPath
from app.core.text_correct_engine import TextCorrectorFinal
from app.core.tts_engine import TTSEngine
from app.db.database import SessionLocal
from app.dto.line_dto import LineInitDTO
from app.entity.chapter_entity import ChapterEntity
from app.entity.line_entity import LineEntity
from app.models.po import ChapterPO, RolePO, LinePO

from app.repositories.chapter_repository import ChapterRepository
from app.repositories.line_repository import LineRepository

from app.core.prompts import get_context2lines_prompt, get_add_smart_role_and_voice
from app.repositories.llm_provider_repository import LLMProviderRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.role_repository import RoleRepository
from app.core.llm_engine import LLMEngine
from app.repositories.voice_repository import VoiceRepository


class ChapterService:

    def __init__(self, repository: ChapterRepository):
        """注入 repository"""
        self.repository = repository

    def create_chapter(self,  entity: ChapterEntity, after_chapter_id: int = None):
        """创建新章节
        - 检查同名章节是否存在
        - 如果存在，抛出异常或返回错误
        - 如果指定了 after_chapter_id，在该章节之下插入
        - 调用 repository.create 插入数据库
        """

        chapter = self.repository.get_by_name(entity.title, entity.project_id)
        if chapter:
            print("同名章节已存在")
            return None
        
        # 获取所有章节（已按 order_index 排序）
        all_chapters = list(self.repository.get_all(entity.project_id))
        
        # 默认新章节的 order_index 在末尾
        new_order_index = len(all_chapters) + 1
        
        # 如果指定了 after_chapter_id，重新计算插入位置
        if after_chapter_id:
            # 找到指定章节的位置并计算新的 order_index
            for i, ch in enumerate(all_chapters):
                if ch.id == after_chapter_id:
                    new_order_index = i + 2  # 插入在该位置之后
                    # 将原来在 i+1 位置及之后的所有章节 order_index 递增
                    for j in range(i + 1, len(all_chapters)):
                        old_index = all_chapters[j].order_index or (j + 1)
                        self.repository.update(all_chapters[j].id, {'order_index': old_index + 1})
                    break
        
        # 设置新章节的 order_index
        entity.order_index = new_order_index
        
        # 创建新章节
        po = ChapterPO(**entity.__dict__)
        res = self.repository.create(po)

        # res(po) --> entity
        data = {k: v for k, v in res.__dict__.items() if not k.startswith("_")}
        entity = ChapterEntity(**data)

        return entity


    def get_chapter(self, chapter_id: int) -> ChapterEntity | None:
        """根据 ID 查询章节"""
        po = self.repository.get_by_id(chapter_id)
        if not po:
            return None
        data = {k: v for k, v in po.__dict__.items() if not k.startswith("_")}
        res = ChapterEntity(**data)
        return res

    def get_all_chapters(self,project_id: int) -> Sequence[ChapterEntity]:
        """获取所有章节列表"""
        pos = self.repository.get_all(project_id)
        # pos -> entities

        entities = [
            ChapterEntity(**{k: v for k, v in po.__dict__.items() if not k.startswith("_")})
            for po in pos
        ]
        return entities

    def update_chapter(self, chapter_id: int, data:dict) -> bool:
        """更新章节
        - 可以只更新部分字段
        - 检查同名冲突
        - 检查project_id不能改变
        """
        title = data["title"]
        project_id = data["project_id"]
        if self.repository.get_by_name(title, project_id) and self.repository.get_by_name(title,project_id).id != chapter_id:
            return False
        po = self.repository.get_by_id(chapter_id)
        # 防止改变project_id
        if po.project_id != project_id:
            return False
        self.repository.update(chapter_id, data)
        return True

    def delete_chapter(self, chapter_id: int) -> bool:
        """删除章节
        """
        db = SessionLocal()
        try :
            chapter = self.repository.get_by_id(chapter_id)

        #     移除资源内容
            # 删除该路径所有内容
            project_repository = ProjectRepository(db)
            project = project_repository.get_by_id(chapter.project_id)
            chapter_path = os.path.join(project.project_root_path, str(chapter.project_id), str(chapter_id))
            if os.path.exists(chapter_path):
                shutil.rmtree(chapter_path)  # 删除整个文件夹及其所有内容
                print(f"已删除目录及内容: {chapter_path}")
            else:
                print(f"目录不存在: {chapter_path}")
            #     先删除资源，再删除记录
            res = self.repository.delete(chapter_id)
            # 删除章节下所有台词
            line_repository = LineRepository(db)
            line_res = line_repository.delete_all_by_chapter_id(chapter_id)
        finally:
            db.close()
        return res

    # 先获取章节内容
    def split_text(self, chapter_id: int, max_length: int = 1500) -> List[str]:
        """
        将文本按标点/换行断句，并按最大长度分组，确保每段以标点结束。
        支持中英文标点和换行符。
        """
        content = self.get_chapter(chapter_id).text_content
        # 去掉空行
        content = "\n".join([line for line in content.split("\n") if line.strip()])

        # 如果最后没有句号/问号/感叹号/点号，自动补一个句号
        if not re.search(r'[。！？.!?]$', content):
            content += "。"

        # 使用正则分割，支持中英文标点 + 逗号 + 换行
        # [] 里列出所有可能的结束符号
        sentences = re.findall(r'[^。！？.!?,，\n]*[。！？.!?,，\n]', content, re.MULTILINE | re.DOTALL)

        chunks = []
        buffer = ""

        for sentence in sentences:
            if len(buffer) + len(sentence) <= max_length:
                buffer += sentence
            else:
                if buffer:
                    chunks.append(buffer.strip())
                buffer = sentence

        if buffer:
            chunks.append(buffer.strip())

        return chunks

    # 然后进行划分

    # 然后循环解析，并保存
    def fill_prompt(self,template: str, characters: list[str], emotions: list[str], strengths: list[str],
                    novel_content: str) -> str:
        result = template
        result = result.replace("{possible_characters}", ", ".join(characters))
        result = result.replace("{possible_emotions}", ", ".join(emotions))
        result = result.replace("{possible_strengths}", ", ".join(strengths))
        result = result.replace("{novel_content}", novel_content)
        return result

    def _is_plain_narrator_role(self, role_name: str) -> bool:
        role = (role_name or "").strip()
        return role in {"旁白", "旁白:", "旁白："}

    def _is_perspective_narrator_role(self, role_name: str) -> bool:
        role = (role_name or "").strip()
        return role.startswith("旁白-") and role.endswith("视角")

    def _canonicalize_narrator_role(self, role_name: str) -> str:
        """将旁白角色名归一化为 `旁白-<角色名>视角`，纯旁白返回 `旁白`。"""
        raw_role = (role_name or "").strip()
        if not raw_role:
            return raw_role

        if self._is_plain_narrator_role(raw_role):
            return "旁白"

        compact = re.sub(r"\s+", "", raw_role)
        if "旁白" not in compact:
            return raw_role

        if "视角" not in compact:
            if compact in {"旁白", "旁白:", "旁白："}:
                return "旁白"
            return raw_role

        perspective = None

        # 形式1：旁白-林凡视角 / 旁白：林凡视角 / 旁白林凡视角
        if compact.startswith("旁白"):
            m = re.match(r"^旁白[-:：_]*([^:：_\-]+?)视角$", compact)
            if m:
                perspective = m.group(1)

        # 形式2：林凡视角旁白 / 林凡视角-旁白
        if perspective is None and compact.endswith("旁白"):
            m = re.match(r"^([^:：_\-]+?)视角[-:：_]*旁白$", compact)
            if m:
                perspective = m.group(1)

        # 兜底：只要含有“视角”和“旁白”，尽量提取“视角”前文本。
        if perspective is None:
            m = re.search(r"([^:：_\-]+?)视角", compact)
            if m:
                perspective = m.group(1)

        if perspective:
            if perspective in {"未知", "不明", "无"}:
                perspective = "未知"
            return f"旁白-{perspective}视角"

        return "旁白"

    def _infer_narrator_perspective(self, text_content: str, candidate_roles: List[str],
                                    prev_role: str = None, next_role: str = None) -> str:
        text = text_content or ""

        # 只在真实角色里做匹配，不把旁白类角色作为候选视角。
        filtered_roles = [
            (role or "").strip()
            for role in (candidate_roles or [])
            if role and not str(role).strip().startswith("旁白")
        ]

        # 优先使用文本中显式出现次数最多的角色名作为视角。
        if text and filtered_roles:
            unique_roles = sorted(set(filtered_roles), key=len, reverse=True)
            best_role = None
            best_count = 0
            for role in unique_roles:
                count = text.count(role)
                if count > best_count:
                    best_role = role
                    best_count = count
            if best_role and best_count > 0:
                return best_role

        # 次优先用邻近台词角色兜底，保证同一语境中的视角一致性。
        for nearby in [prev_role, next_role]:
            nearby_role = (nearby or "").strip()
            if nearby_role and not self._is_plain_narrator_role(nearby_role) and not self._is_perspective_narrator_role(
                    nearby_role):
                return nearby_role

        return "未知"

    def _normalize_narrator_roles(self, parsed_data: list, candidate_roles: List[str]) -> list:
        if not isinstance(parsed_data, list):
            return parsed_data

        normalized = []
        for idx, item in enumerate(parsed_data):
            if not isinstance(item, dict):
                normalized.append(item)
                continue

            role_name = (item.get("role_name") or "").strip()
            canonical_role = self._canonicalize_narrator_role(role_name)

            # 非旁白类角色不处理。
            if "旁白" not in canonical_role:
                normalized.append(item)
                continue

            # 已是可用视角旁白，直接归一化写回。
            if self._is_perspective_narrator_role(canonical_role):
                new_item = item.copy()
                new_item["role_name"] = canonical_role
                normalized.append(new_item)
                continue

            prev_role = None
            for j in range(idx - 1, -1, -1):
                prev_item = parsed_data[j]
                if isinstance(prev_item, dict):
                    prev_role = self._canonicalize_narrator_role((prev_item.get("role_name") or "").strip())
                    if prev_role:
                        break

            next_role = None
            for j in range(idx + 1, len(parsed_data)):
                next_item = parsed_data[j]
                if isinstance(next_item, dict):
                    next_role = self._canonicalize_narrator_role((next_item.get("role_name") or "").strip())
                    if next_role:
                        break

            perspective = self._infer_narrator_perspective(
                item.get("text_content", ""),
                candidate_roles or [],
                prev_role,
                next_role,
            )
            new_item = item.copy()
            new_item["role_name"] = f"旁白-{perspective}视角"
            normalized.append(new_item)

        return normalized

    def _split_sentences_for_postprocess(self, text: str) -> List[str]:
        """按句号/问号/感叹号/分号/换行切分为句，保留完整句。"""
        if not text:
            return []

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        sentences = []
        buffer = ""

        for ch in normalized:
            if ch == "\n":
                if buffer.strip():
                    sentences.append(buffer.strip())
                buffer = ""
                continue

            buffer += ch
            if ch in "。！？!?；;":
                if buffer.strip():
                    sentences.append(buffer.strip())
                buffer = ""

        if buffer.strip():
            sentences.append(buffer.strip())

        return sentences

    def _split_text_by_sentence_target(self, text: str, target_len: int = 50) -> List[str]:
        """尽量控制在 target_len 左右，优先保证完整句；单句过长则保留原句。"""
        text = (text or "").strip()
        if not text:
            return []

        sentences = self._split_sentences_for_postprocess(text)
        if not sentences:
            return [text]

        segments = []
        current = ""

        for sentence in sentences:
            if not current:
                current = sentence
                continue

            if len(current) + len(sentence) <= target_len:
                current += sentence
            else:
                segments.append(current.strip())
                current = sentence

        if current.strip():
            segments.append(current.strip())

        return segments

    def _split_parsed_lines_by_sentence_target(self, parsed_data: list, target_len: int = 50) -> list:
        """后处理切分：同角色也切分，但不在句中硬切。"""
        if not isinstance(parsed_data, list):
            return parsed_data

        result = []
        for item in parsed_data:
            if not isinstance(item, dict):
                result.append(item)
                continue

            text_content = item.get("text_content", "")
            chunks = self._split_text_by_sentence_target(text_content, target_len)
            if len(chunks) <= 1:
                result.append(item)
                continue

            for chunk in chunks:
                new_item = item.copy()
                new_item["text_content"] = chunk
                result.append(new_item)

        return result

    def para_content(self, prompt:str,chapter_id: int,content: str = None,role_names: List[str] = None,emotion_names: List[str] = None,strength_names: List[str] = None,is_precise_fill: int = 0):
        db = SessionLocal()
        try :
    #         获取content
            chapter = self.repository.get_by_id(chapter_id)
            # content = chapter.text_content
    #          获取角色列表
    #         role_repository = RoleRepository(db)
    #         roles = role_repository.get_all(chapter.project_id)
    #         role_names = [role.name for role in roles]
    #         组装prompt
    #         prompt = get_context2lines_prompt(role_names, content,emotion_names,strength_names)
            prompt = self.fill_prompt(prompt, role_names, emotion_names, strength_names, content)

        #   获取llm_provider

            project_repository = ProjectRepository(db)
            project = project_repository.get_by_id(chapter.project_id)
            llm_provider_id = project.llm_provider_id
            #
            llm_provider_repository = LLMProviderRepository(db)
            llm_provider = llm_provider_repository.get_by_id(llm_provider_id)
            llm = LLMEngine(llm_provider.api_key, llm_provider.api_base_url, project.llm_model, llm_provider.custom_params)
            try:
                llm.generate_text_test("请输出一份用户信息，严格使用 JSON 格式，不要包含任何额外文字。字段包括：name, age, city")
                print("LLM可用")
            except Exception as e:
                print("LLM不可用")
                return {
                    "success": False,
                    "message": f"LLM 不可用: {str(e)}"
                }
            print("开始内容解析")
            try:
                result = llm.generate_text(prompt)
                # 解析json，并且构造为List[LineInitDTO]
                # 解析 JSON 字符串为 Python 对象
                parsed_data = llm.save_load_json(result)
                if not parsed_data:
                    return {
                        "success": False,
                        "message": "JSON 解析失败或返回空对象",
                    }
                # 这里进行自动填充

                if is_precise_fill == 1:
                    print("开始自动填充")
                    corrector = TextCorrectorFinal()
                    parsed_data = corrector.correct_ai_text(content, parsed_data)

                # 严格后处理：将 LLM 误输出的“旁白”统一改写为带视角的旁白角色。
                parsed_data = self._normalize_narrator_roles(parsed_data, role_names)

                # 严格后处理：按完整句切分长台词，目标长度约 50 字。
                parsed_data = self._split_parsed_lines_by_sentence_target(parsed_data, target_len=50)

                # parsed_data = json.loads(result)
                # 构造 List[LineInitDTO]
                line_dtos: List[LineInitDTO] = [LineInitDTO(**item) for item in parsed_data]
                return {
                    "success": True,
                    "data": line_dtos
                }

            except Exception as e:
                print("调用 LLM 出错：", e)
                return {
                    "success": False,
                    "message": f"调用 LLM 出错: {str(e)}"
                }
        finally:
            db.close()


    # 导出指令
    # def get_prompt_content(self,project_id, chapter_id,prompt):
    #     db = SessionLocal()
    #     try:
    #         #         获取content
    #         chapter = self.repository.get_by_id(chapter_id)
    #         content = chapter.text_content
    #         #          获取角色列表
    #         role_repository = RoleRepository(db)
    #         roles = role_repository.get_all(chapter.project_id)
    #         role_names = [role.name for role in roles]
    #         #         组装prompt
    #         # 获取project
    #
    #         prompt = self.fill_prompt(prompt, role_names, emotion_names, strength_names, content)
    #         prompt = get_context2lines_prompt(role_names, content)
    #         return  prompt
    #     finally:
    #         db.close()
    def add_smart_role_and_voice(self,project,content, role_names, voice_names):
        # 智能匹配提示词，要写死吗？
        db = SessionLocal()
        try:
            llm_provider_id = project.llm_provider_id
            llm_provider_repository = LLMProviderRepository(db)
            llm_provider = llm_provider_repository.get_by_id(llm_provider_id)
            llm = LLMEngine(llm_provider.api_key, llm_provider.api_base_url, project.llm_model, llm_provider.custom_params)
            prompt = get_add_smart_role_and_voice(content,role_names, voice_names)
            result = llm.generate_smart_text(prompt)
            parse_data = llm.save_load_json(result)
            # 获取项目所有音色
            voice_repository = VoiceRepository(db)
            voices = voice_repository.get_all(project.tts_provider_id)
            # map name- id
            voice_id_map = {voice.name: voice.id for voice in voices}


            # 对角色进行update
            role_repository = RoleRepository(db)
            res = []
            for item in parse_data:
                role = role_repository.get_by_name( item["role_name"],project.id)
                if role:
                    if item["voice_name"]:
                        print("更新角色音色：", item["role_name"], item["voice_name"])
                        role_repository.update(role.id, {"default_voice_id": voice_id_map.get(item["voice_name"])})
                        res.append({"role_name": item["role_name"], "voice_name": item["voice_name"]})

            return True,res
        except Exception as e:
            print("LLM智能匹配出错：", e)
            return False, []
        finally:
            db.close()





