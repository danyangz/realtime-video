# 本地 AI 应用

一个服务里跑多个 AI 应用，共用同一套语音识别、TTS 和 LLM 模块。全部使用开源软件和开源权重模型：语音识别在本机跑，LLM 和 TTS 通过 Together（以及可选的 OpenRouter）调用开源权重模型。

| 应用 | 地址 | 说明 |
|---|---|---|
| 🐺 狼人杀 | `/werewolf/` | 和 11 个 AI 玩 12 人标准局，AI 发言用不同的声音念出来 |
| 🌟 和小星视频聊天 | `/video-chat/` | 和卡通形象实时语音聊天，可以随时打断她 |

## 快速开始

需要 Python 3.10+ 和 Chrome。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # 然后在 .env 里填 TOGETHER_API_KEY
.venv/bin/python -m server    # 打开 http://localhost:8000 ，选一个应用
```

在 Apple 芯片的 Mac 上，第一次启动会下载本地语音识别模型（约 3.5GB，存在 `~/.cache/huggingface`），之后启动只需一两秒。

## 🐺 狼人杀

**规则**：12 人标准局"预女猎白"，即 4 狼人、4 平民，加预言家、女巫、猎人、白痴；屠边胜利。

- 第一天竞选警长，警长投票算 1.5 票，决定发言顺序并最后发言，出局时移交或撕掉警徽。
- 首夜出局和被放逐的玩家有遗言。
- 平票时进入 PK，再平就无人出局。
- 女巫不能自救，同一晚不能两瓶药都用。
- 猎人被毒死时不能开枪。
- 白痴被放逐时翻牌免死，但失去投票权。

**怎么玩**：开局可以选身份，也可以随机，或者只观战（上帝视角）。

- **发言**：轮到你时直接打字，或者按住"按住说话"用本地 Qwen3-ASR 识别，识别结果会先放进输入框，你确认后再发送。
- **投票和夜晚技能**：点两侧的玩家头像。
- **托管**：随时可以点"让 AI 替我说/选"，这一次由 AI 代替你。
- **语音**：AI 发言时可以点"跳过语音"，右上角可以关掉语音。
- **复盘**：游戏结束后会公布所有身份，还能查看每个 AI 每一步的"内心戏"，也就是它们私下的推理。

**AI 玩家**：每个 AI 只能看到自己该看到的信息：村民看不到狼人夜聊，预言家的查验结果只有自己知道。

AI 的提示词里有两样东西帮它推理：

- **老玩家的推理常识**：比如"没人对跳的预言家通常是真的""被查杀又没出局的人就是毒药目标"。
- **自动整理的局势表**：谁跳了什么身份、报了什么验人结果，以及每天的出局和投票记录。

开局时可以选 **AI 水平**（都是开源权重模型）：

| 水平 | 模型 | 测评正确率 | 每步决策 | 价格（每百万 token，输入/输出） |
|---|---|---|---|---|
| 标准（默认） | DeepSeek-V4.1-Flash | 约 90% 以上 | 约 0.6 秒 | $0.30 / $1.20 |
| 高手 | Kimi-K3 | 100% | 约 3.6 秒 | $3 / $15 |

**测评**：[scripts/werewolf_eval.py](scripts/werewolf_eval.py) 里有 9 个有标准答案的局面，比如预言家该投自己查杀的人、猎人该带走被查杀的人、女巫该毒被查杀的人。

- 改提示词之前，DeepSeek 只有 74%，会出现"猎人开枪打预言家"这种错。
- 推理模式没有用：这些模型一开推理就要想十几秒到一分钟，一局上百次调用撑不住。
- 改提示词、换模型之前，先跑一遍测评：

```bash
.venv/bin/python scripts/werewolf_eval.py --models together:deepseek-ai/DeepSeek-V4.1-Flash together:moonshotai/Kimi-K3
```

**角色组**：开局时可以选。每个角色的长相、声音和说话风格三者一致。

- **西游记**（默认）：孙悟空、猪八戒、唐僧、沙僧、嫦娥、白骨精、牛魔王、铁扇公主、红孩儿、哪吒、二郎神、女儿国国王，法官是土地公。
  - 例子：孙悟空是猴脸、戴金箍，声音清亮急躁，自称"俺老孙"。
  - 角色设定只影响口吻，狼人杀身份仍以发牌为准。
  - 这些角色出自公有领域的古典小说。形象是原创绘制，声音不模仿任何演员。
- **都市**：12 个现代人设，法官是西装主持人。

**会动的头像**：

- **舞台**：中间显示当前发言人（或法官）的大头像，嘴跟着语音的音量和音色动，下面有字幕。
- **座位**：小头像会眨眼。AI 正在想发言时头上会冒思考泡泡，出局的人闭眼变灰。
- **关掉语音时**：按文字流出的节奏模拟口型。
- 所有角色一览：`/werewolf/gallery.html`

**声音从哪来**：都是 Together 托管的开源权重 TTS 模型里的预设音色，没有做声音克隆。

| 模型 | 音色 | 说明 |
|---|---|---|
| Qwen3-TTS 1.7B | 5 个中文原生音色 | 可以用文字描述语气，比如孙悟空是"嗓音清亮，语速快，机灵顽皮，神气十足" |
| Kokoro-82M | 8 个中文音色 | 不能描述语气 |

13 个音色正好分给 12 个 AI 和法官，一人一个。

**新增角色组**：在 [apps/werewolf/personas.py](apps/werewolf/personas.py) 里加一个 `Cast`。每个角色需要：

- 名字
- 性格和口头禅（写进 AI 的提示词）
- 音色和语气描述
- 外观参数

外观参数可以组合这些选项（完整列表见 `core/static/avatar.js`）：

- 脸型：人 / 猴 / 猪
- 发型和发色
- 眉形、肤色、服装
- 配饰：金箍、僧帽、牛角、第三只眼、胡子、眼镜、芭蕉扇等

**在终端里看 AI 对战**（不开浏览器；可以给座位分配不同模型，比较谁更会玩）：

```bash
.venv/bin/python scripts/werewolf_sim.py
.venv/bin/python scripts/werewolf_sim.py --models together:deepseek-ai/DeepSeek-V4.1-Flash together:Qwen/Qwen3.5-9B
.venv/bin/python scripts/werewolf_sim.py --fake      # 脚本 AI，不调用模型
```

完整记录（包含每个 AI 的思考）保存在 `out/werewolf/`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WEREWOLF_MODELS` | `together:deepseek-ai/DeepSeek-V4.1-Flash` | "标准"水平用的模型，逗号分隔多个时轮流分给各座位。只填开源权重模型 |
| `WEREWOLF_SPEECH_CHARS` | `150` | 每段发言的字数上限 |
| `WEREWOLF_TEMPERATURE` | `0.8` | 发言的随机程度 |
| `WEREWOLF_FAKE_AI` | `false` | 用脚本 AI 代替模型（调界面用，不花钱） |

## 🌟 和小星视频聊天

你说话，小星听、想、回答，口型跟着声音动；她说话时你一开口她就停下。**建议戴耳机**，不然她可能听到自己的声音。

- **语音识别（本地）**：Silero VAD 判断你说完了没（停顿 0.6 秒算说完）。说话时 Qwen3-ASR-0.6B 每 0.7 秒出实时字幕；停顿 0.25 秒时 Qwen3-ASR-1.7B 就开始识别整句，等确认说完时结果通常已经出来了。在 M4 上，说完后 0.5 到 0.8 秒拿到文字（其中 0.6 秒是等待）。
- **LLM 和 TTS**：流式生成，第一句在逗号处就送去合成，边生成边说。
- **形象**：纯 SVG 手绘，按播放音频的音量和音色驱动嘴型。预览：`/video-chat/?demo=speaking`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SYSTEM_PROMPT` | 见 `apps/video_chat/config.py` | 小星的人设 |
| `BARGE_IN` | `true` | 是否允许开口打断 |
| `MAX_HISTORY_MESSAGES` | `24` | 带给 LLM 的历史条数 |

## 共享配置（.env）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TOGETHER_API_KEY` | （必填） | TTS 和默认的 LLM |
| `OPENROUTER_API_KEY` | | LLM 走 OpenRouter 时需要 |
| `LLM_PROVIDER` / `LLM_MODEL` | `together` / `Qwen/Qwen3.5-9B` | 视频聊天用的 LLM |
| `STT_ENGINE` | Apple 芯片：`local`，其他：`together` | 本地 Qwen3-ASR，或 Together Whisper |
| `ASR_MODEL` / `ASR_PARTIAL_MODEL` | Qwen3-ASR 1.7B / 0.6B（8-bit） | 最终识别 / 实时字幕 |
| `ASR_HOTWORDS` | | 额外的热词，加在各应用自己的热词后面 |
| `TTS_VOICE` | `vivian` | 默认音色 |
| `VAD_MIN_SILENCE_MS` | `600` | 停顿多久算说完 |
| `DEBUG_AUDIO` | `false` | 把识别模型收到的音频和结果存到 `out/debug/`，排查识别问题 |

**选模型时注意**：Qwen 名字带 Plus、Max、Flash 的是闭源 API 模型，不要用；OpenRouter 上不要用 `openrouter/auto`。

## 仓库结构

```
core/                 所有应用共用
  config.py           共享配置 + env 读取工具
  services.py         服务启动时创建一次的共享资源：LLM 客户端、本地识别模型
  llm.py              流式 LLM（Together / OpenRouter），每次调用可换模型
  tts.py              Together TTS（每句话可换声音）
  local_stt.py        本地识别：Silero VAD + Qwen3-ASR（MLX）
  stt.py              Together Whisper 流式识别
  text.py             分句、清洗、繁转简等
  app.py              AppSpec：一个应用要提供什么
  static/             共享前端：audio.js（麦克风/播放）、avatar.js（按外观参数生成会动的卡通头像）、worklets/
apps/
  __init__.py         应用注册表
  werewolf/           狼人杀：engine.py（规则，与 LLM/界面无关）、agent.py（AI 玩家）、
                      prompts.py、personas.py（角色组：名字/性格/声音/外观）、session.py（浏览器协议）、static/
  video_chat/         视频聊天：session.py、routes.py、static/
server/               统一入口：挂载所有应用 + 首页
scripts/              自检、本地识别测试、狼人杀终端对战和决策测评
tests/                python -m pytest
```

### 新增一个应用

1. 新建 `apps/<名字>/`，在 `__init__.py` 里定义 `APP = AppSpec(slug=..., title=..., router=..., static_dir=...)`。
2. `router` 是 FastAPI 的 `APIRouter`，比如提供一个 `/ws`。共享资源从 `ws.app.state.services` 取：`services.llm()`、`services.new_tts()`、`services.new_stt(...)`、`services.transcribe_clip(...)`。
3. 前端放在 `static/`，可以直接 `import { AudioEngine } from '/shared/audio.js'` 和 `import { Avatar } from '/shared/avatar.js'`。WebSocket 地址用相对路径 `ws`。
4. 在 `apps/__init__.py` 的 `APPS` 里加上它。它会出现在首页，地址是 `/<slug>/`。

## 自检

```bash
.venv/bin/python -m pytest                          # 单元测试，不需要 key
.venv/bin/python scripts/smoke_test.py              # TTS、识别、LLM 联网自检 + LLM 延迟对比
.venv/bin/python scripts/test_local_asr.py 录音.wav  # 本地识别测试，不需要 key
```

## 开源组件

| 组件 | 许可证 |
|---|---|
| DeepSeek-V4.1-Flash（狼人杀 AI，标准） | MIT |
| Kimi-K3（狼人杀 AI，高手） | 开源权重，自定义许可证 |
| Qwen3.5-9B / Qwen3.6-35B-A3B（视频聊天 LLM） | Apache 2.0 |
| Qwen3-ASR 1.7B / 0.6B（识别） | Apache 2.0 |
| Silero VAD v6.3 | MIT |
| Qwen3-TTS 12Hz 1.7B、Kokoro-82M（合成） | Apache 2.0 |
| Whisper large-v3（备选识别） | MIT |
| mlx-qwen3-asr、MLX、ONNX Runtime | Apache 2.0 / MIT / MIT |
| FastAPI、Uvicorn、websockets、httpx、python-dotenv、zhconv | MIT / BSD |
| 卡通形象、前端、游戏代码 | 本项目原创 |
