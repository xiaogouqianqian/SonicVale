<div align="center">

# 音谷 - 修改说明（Fork 版本）

</div>

<p align="center">
<img src="https://img.shields.io/badge/license-AGPLv3-blue?logo=gnu" />
<img src="https://img.shields.io/badge/release-v1.1.3-brightgreen?logo=semver" />
<img src="https://img.shields.io/badge/type-change--summary-orange" />
</p>

> 本软件基于开源项目《音谷》二次开发

---

## 📌 相比原仓库的改动汇总

以下为当前仓库相对原仓库的主要改动（按模块归类）：

## 1. 章节与文本导入

- 新增 EPUB 导入能力：支持上传 `.epub` 文件并自动解析为章节（后端新增 `epub_parser`，前端导入入口升级为 `TXT/EPUB`）。
- 章节创建支持“插入到指定章节后”：新增 `after_chapter_id`，可在当前章节后插入新章节并自动维护顺序。
- 章节查询排序增强：按 `order_index` 与 `id` 稳定排序返回。

## 2. LLM 拆分与旁白规则优化

- 提示词规则升级：强调“完整句优先切分”，降低生硬截断。
- 旁白命名规则升级：由单一 `旁白` 改为 `旁白-<角色名>视角`，无法判断时使用 `旁白-未知视角`。
- 新增后处理逻辑：

1. 自动归一化旁白角色名。
2. 结合上下文推断旁白视角。
3. 按句子边界切分长台词（目标约 50 字）。

- 文本纠错兜底同步调整：遗漏句补回时默认角色为 `旁白-未知视角`。

## 3. 台词批次（Batch）能力

- 新增 `batch_tag` 字段（DTO / Entity / ORM / 数据库迁移）。
- 台词生成策略由“覆盖旧结果”调整为“追加新批次”。
- 新增批次能力：

1. 查询章节批次列表。
2. 按批次筛选台词。
3. 按批次删除台词。

- 前端支持批次筛选、批次删除，并在新增/插入台词时继承批次归属。

## 4. 批量操作增强

- 新增“批量生成台词”接口：支持按章节列表顺序调用 LLM。
- 前端章节树支持勾选，新增“批量删除章节”“批量生成台词”。

## 5. TTS 与 GPT-SoVITS Inference 集成

- 新增 `gptsovits_inference` 引擎接入：

1. 增加推理引擎类与构建工厂。
2. 支持按角色名与情绪进行语音合成。
3. 运行时传入情绪名（不再固定为 `None`）。

- `tts_provider` 新增 `custom_params` 配置字段（含迁移与默认值初始化）。
- 默认 TTS 提供方扩展：新增 `gptsovits_inference` 默认配置。

## 6. GPT-SoVITS 模型管理

- 新增后端服务：路径校验、模型扫描、模型导入、模型同步到音色库。
- 新增 API：

1. 校验 GPT-SoVITS 项目路径。
2. 扫描模型。
3. 导入模型目录。
4. 同步模型到 `voice`。

- 前端配置中心新增 GPT-SoVITS 可视化管理入口。
- 前端音色管理页对 GPT-SoVITS 场景做功能分流（新增/刷新模型，禁用不适用的本地参考音频导入导出能力）。

## 7. 依赖与工程化

- 后端新增 EPUB 解析依赖：`ebooklib`、`beautifulsoup4`。
- 前端版本号更新至 `1.1.3`，锁文件同步调整（移除 `jszip` 相关链路）。
- 新增 `start-dev.bat`，用于一键启动后端与前端开发环境。

---

## 📜 开源协议与二次开发约束

### 1. 上游仓库链接

- 音谷（SonicVale）仓库：`https://github.com/xcLee001/SonicVale`
- GPT-SoVITS-Inference 仓库：`https://github.com/X-T-E-R/GPT-SoVITS-Inference`

本仓库功能实现参考并集成以上上游项目能力，发布与分发时请一并保留来源说明。

### 2. 开源协议

- 本项目采用 [GNU Affero General Public License v3.0 (AGPL-3.0)](./LICENSE)。
- 分发、修改或以网络服务形式提供本项目时，应按 AGPL-3.0 要求公开对应源码并保留原始声明。
