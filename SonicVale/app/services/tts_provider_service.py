import requests
import json
from sqlalchemy import Sequence

from app.entity.tts_provider_entity import TTSProviderEntity
from app.models.po import TTSProviderPO
from app.repositories.tts_provider_repository import TTSProviderRepository


class TTSProviderService:

    def __init__(self, repository: TTSProviderRepository):
        """注入 repository"""
        self.repository = repository

    def get_all_tts_providers(self) -> list[TTSProviderEntity]:
        """查询所有tts供应商"""
        pos = self.repository.get_all()
        res = [TTSProviderEntity(**{k: v for k, v in po.__dict__.items() if not k.startswith("_")}) for po in pos]
        return res

    def get_tts_provider(self, tts_provider_id: int) -> TTSProviderEntity | None:
        """根据 ID 查询tts供应商"""
        po = self.repository.get_by_id(tts_provider_id)
        if not po:
            return None
        data = {k: v for k, v in po.__dict__.items() if not k.startswith("_")}
        res = TTSProviderEntity(**data)
        return res


    def update_tts_provider(self, tts_provider_id: int, data:dict) -> bool:
        """更新tts供应商
        - 可以只更新部分字段
        - 检查同名冲突
        - 检查project_id不能改变
        """
        name = data.get("name")
        if not name:
            return False
        if self.repository.get_by_name(name) and self.repository.get_by_name(name).id != tts_provider_id:
            return False
        self.repository.update(tts_provider_id, data)
        return True

    def delete_tts_provider(self, tts_provider_id: int) -> bool:
        """删除tts供应商
        """
        res = self.repository.delete(tts_provider_id)
        return res

    def create_default_tts_provider(self):
        """创建默认的tts供应商"""
        if self.repository.get_by_name("index_tts") :
            pass
        elif not self.repository.get_by_id(1):
            po = TTSProviderPO(name="index_tts", id=1, status=1, api_base_url="", api_key="", custom_params=None)
            self.repository.create(po)

        if not self.repository.get_by_name("gptsovits_inference") and not self.repository.get_by_id(2):
            default_params = json.dumps(
                {
                    "engine_type": "gptsovits_inference",
                    "project_path": "",
                    "text_language": "中文",
                    "default_format": "wav",
                    "default_speed": 1.0,
                },
                ensure_ascii=False,
            )
            po = TTSProviderPO(
                name="gptsovits_inference",
                id=2,
                status=1,
                api_base_url="http://127.0.0.1:5000",
                api_key="",
                custom_params=default_params,
            )
            self.repository.create(po)

    def test_tts_provider(self, entity: TTSProviderEntity):
        # 拿到url
        api_base_url = entity.api_base_url
        if not api_base_url:
            return False
        # ping api
        # 调用
        try:
            is_gpt_sovits = (entity.name or "").lower() == "gptsovits_inference"
            test_url = f"{api_base_url.rstrip('/')}/character_list" if is_gpt_sovits else api_base_url
            resp = requests.get(test_url, timeout=5)

            # 如果返回 200-399 都认为是通的（有些服务会 302 重定向）
            if 200 <= resp.status_code < 400:
                try:
                    data = resp.json()
                    if is_gpt_sovits:
                        return isinstance(data, dict)
                    if "endpoints" in data:
                        return True
                    else:
                        print("TTS provider test failed: 'endpoints' missing in response")
                        return False
                except ValueError:
                    print("TTS provider test failed: response is not valid JSON")
                    return False
            else:
                print(f"TTS provider test failed: status {resp.status_code}")
                return False

        except Exception as e:
            # 这里可以打印日志，方便排查
            print(f"TTS provider test failed: {e}")
            return False



