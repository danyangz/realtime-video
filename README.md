# realtime-video：和卡通形象实时视频聊天

浏览器里打开就能和卡通形象"小星"用中文语音聊天：你说话，她听、想、回答，口型跟着声音动。你可以随时开口打断她。

全部使用开源软件和开源权重模型。云端推理通过 Together（和可选的 OpenRouter）调用这些开源权重模型。

## 架构

```
浏览器                                   本地 Python 服务                     云端（开源权重模型）
┌──────────────────────┐   WebSocket    ┌────────────────────┐
│ 麦克风 → 16kHz PCM   │ ─────────────▶ │                    │ ──▶ Together Whisper large-v3（流式识别 + VAD）
│                      │                │  对话调度          │ ──▶ LLM：Qwen3.5-9B（Together）
│ 播放 ← PCM + 轮次号  │ ◀───────────── │  轮流说话 / 打断   │      或 Qwen3.6-35B-A3B（OpenRouter）
│ 卡通形象（SVG，口型  │   JSON 事件    │  分句 → TTS        │ ──▶ Together Qwen3-TTS（流式合成）
│ 跟随播放音量和音色） │                └────────────────────┘
└──────────────────────┘
```

- **语音识别**：Together 实时 Whisper，服务端 VAD 判断你说完了没（停顿 0.6 秒算说完）。
- **LLM**：流式输出，默认关闭思考模式（开着会多等几秒）。
- **TTS**：LLM 每生成一句（第一句在逗号处就切）就送去合成，边生成边说。
- **形象**：纯 SVG 手绘，根据播放音频的音量和高低频比例驱动嘴型，加上眨眼、视线、点头、思考气泡。
- **打断**：小星说话时你一开口，她就停下；你没说完就停顿时，系统会等你说完再一起回答。

## 快速开始

需要 Python 3.10+ 和 Chrome（Safari 也能用，Chrome 的回声消除更好）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # 然后在 .env 里填 TOGETHER_API_KEY
.venv/bin/python -m server    # 打开 http://localhost:8000
```

点"开始通话"，允许麦克风权限，直接说话即可。**建议戴耳机**，不然小星可能听到自己的声音。代码里有回声过滤，但戴耳机最稳。

没有麦克风时也可以在右侧打字聊天。想单独预览形象，可以打开 `http://localhost:8000/?demo=speaking`（或 `thinking`、`listening`）。

## 自检和选模型

```bash
.venv/bin/python scripts/smoke_test.py          # TTS→识别往返测试 + 对比几个 LLM 的首字延迟
.venv/bin/python scripts/smoke_test.py --e2e    # 再对正在运行的服务做一次完整对话（先启动 server）
.venv/bin/python scripts/smoke_test.py --models together:Qwen/Qwen3.5-9B openrouter:qwen/qwen3.6-35b-a3b
```

生成的音频在 `out/` 目录。选首字最快、回答质量满意的模型，写进 `.env` 的 `LLM_PROVIDER` / `LLM_MODEL`。

## 配置（.env）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TOGETHER_API_KEY` | （必填） | 语音识别和 TTS |
| `OPENROUTER_API_KEY` | | 只有 LLM 走 OpenRouter 时需要 |
| `LLM_PROVIDER` | `together` | `together` 或 `openrouter` |
| `LLM_MODEL` | Together：`Qwen/Qwen3.5-9B`；OpenRouter：`qwen/qwen3.6-35b-a3b` | 只填开源权重模型 |
| `LLM_DISABLE_THINKING` | `true` | 关闭混合模型的思考模式 |
| `SYSTEM_PROMPT` | 见 `server/config.py` | 小星的人设 |
| `TTS_VOICE` | `vivian` | 中文：`vivian`、`serena`（女），`uncle_fu`（男），`dylan`（北京话），`eric`（四川话） |
| `TTS_INSTRUCTIONS` | 亲切轻松的语气 | Qwen3-TTS 的说话风格指令 |
| `VAD_MIN_SILENCE_MS` | `600` | 停顿多久算说完。经常被抢话就调大，嫌慢就调小 |
| `BARGE_IN` | `true` | 是否允许开口打断 |
| `STT_PROMPT` | 简体中文提示 | 引导 Whisper 输出简体 |

**选模型时注意**：Qwen 名字里带 Plus、Max、Flash 的是闭源 API 模型，不要用。OpenRouter 上不要用 `openrouter/auto`，它可能路由到闭源模型。

## 延迟

界面右下角会显示每轮的"回复延迟"，即从拿到你的完整一句话，到小星发出第一个音。它由 LLM 首字时间和 TTS 首包时间组成。你实际感受到的延迟还要加上停顿判定的 0.6 秒，以及识别结果返回的时间。

## 文件

```
server/
  app.py       FastAPI：静态页面 + /ws
  session.py   对话调度：轮流说话、打断、历史记录
  stt.py       Together 实时 Whisper 客户端
  tts.py       Together TTS WebSocket 客户端（每轮一个连接，预热一个备用）
  llm.py       OpenAI 兼容流式接口（Together / OpenRouter）
  text.py      分句、清洗、繁转简、Whisper 幻觉过滤、回声判断
  config.py    配置
web/
  app.js       界面与协议
  audio.js     麦克风采集与播放
  avatar.js    卡通形象与口型
  worklets/    AudioWorklet：16kHz 降采样、播放重采样
scripts/smoke_test.py   联网自检
tests/                  单元测试（python -m pytest）
```

## 开源组件

| 组件 | 许可证 |
|---|---|
| Whisper large-v3（识别） | MIT |
| Qwen3-TTS 12Hz 1.7B（合成） | Apache 2.0 |
| Qwen3.5-9B / Qwen3.6-35B-A3B（LLM） | Apache 2.0 |
| FastAPI、Uvicorn、websockets、httpx、python-dotenv、zhconv | MIT / BSD |
| 卡通形象、前端代码 | 本项目原创 |

## 下一步

- 换成照片级真人形象：MuseTalk 1.5 等开源口型模型。先在 M4 上实测帧率，不够再放到云 GPU。
- 让小星"看见"你：把摄像头画面定时发给开源视觉语言模型（例如 Qwen3-VL）。
- 端到端语音模型（如 Qwen3-Omni）替代"识别 → LLM → TTS"三段，进一步降低延迟。
