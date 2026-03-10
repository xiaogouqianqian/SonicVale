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

## 8. 源 EPUB 项目模式

- 新增 `audio_epub` 项目模式：创建项目时可直接上传源 EPUB，后端会保存原始文件并保留原书结构映射信息。
- 项目与章节模型扩展了源书关联字段，用于记录原始 `manifest/item` 与章节资源的对应关系。
- `audio_epub` 模式下会锁定通用 `TXT/EPUB` 章节导入，避免破坏原书结构。
- 导出时不再重新拼整本普通 EPUB，而是基于源 EPUB 做增强处理并回写音频资源。

## 9. EPUB 有声书导出增强

- 项目详情页新增 EPUB 3 有声书导出入口，支持按勾选章节优先导出；未勾选时导出整个项目。
- 后端新增 `epub_exporter` 导出链路：复用现有音频生成结果，自动拼装章节音频、XHTML、SMIL、OPF 并打包输出。
- 对于无音频章节，会保留纯文本章节内容；如果整本书都没有可用音频，则直接阻止导出并提示错误。
- `audio_epub` 模式下走“源 EPUB 增强导出”链路：复制原书、注入章节级音频、更新 OPF 后重新打包。

---

## 开发环境

### 1. 环境要求

- Windows
- Python 3.12
- Node.js 20 LTS
- npm

### 2. 克隆后初始化

在仓库根目录创建项目专用虚拟环境：

```powershell
python -m venv .venv
```

安装后端依赖：

```powershell
cd SonicVale
..\.venv\Scripts\python.exe -m pip install --upgrade pip
..\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

安装前端依赖：

```powershell
cd ..\sonicvale-front
npm install
```

回到仓库根目录后，使用下面的脚本启动开发环境：

```powershell
cd ..
.\start-dev.bat
```

说明：

- `start-dev.bat` 已配置为强制使用仓库根目录下的 `.venv`，不会依赖系统全局 Python。
- 开发时产生的用户数据默认写入仓库根目录下的 `userdata`，该目录已被 Git 忽略，不会自动上传。

### 3. FFmpeg 要求

本项目依赖 FFmpeg 进行音频处理。克隆仓库后，需要满足以下两种方式之一：

1. 在系统中安装 FFmpeg，并确保终端执行 `ffmpeg -version` 能成功。
2. 将 `ffmpeg.exe` 放到 `SonicVale/app/core/ffmpeg/ffmpeg.exe`。

后端启动时会优先查找项目内的 FFmpeg，找不到时再回退到系统环境变量中的 `ffmpeg`。


## 📜 开源协议与二次开发约束

### 1. 上游仓库链接

- 音谷（SonicVale）仓库：`https://github.com/xcLee001/SonicVale`
- GPT-SoVITS-Inference 仓库：`https://github.com/X-T-E-R/GPT-SoVITS-Inference`

本仓库功能实现参考并集成以上上游项目能力，发布与分发时请一并保留来源说明。

### 2. 开源协议

- 本项目采用 [GNU Affero General Public License v3.0 (AGPL-3.0)](./LICENSE)。
- 分发、修改或以网络服务形式提供本项目时，应按 AGPL-3.0 要求公开对应源码并保留原始声明。
