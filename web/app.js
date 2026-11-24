import { AudioEngine } from './audio.js';
import { Avatar } from './avatar.js';

const $ = (id) => document.getElementById(id);

const audio = new AudioEngine();
let muted = false;
// Preview the avatar without a call: ?demo=speaking | thinking | listening
const demo = new URLSearchParams(location.search).get('demo');
const fakeLevels = () => {
  const t = performance.now() / 1000;
  const syllable = Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3 + 1));
  return { rms: 0.11 * syllable, bright: 0.3 + 0.3 * Math.sin(t * 3.1) };
};
const avatar = new Avatar($('avatar'), {
  getLevels: () => (demo === 'speaking' ? fakeLevels() : audio.outputLevels()),
  getMicLevel: () => (muted ? 0 : audio.micLevel()),
});
if (demo) avatar.setState(demo);

let ws = null;
let inCall = false;
let activeTurn = -1; // turn whose audio is currently allowed to play
let ttsEnded = false;
let phase = 'listening';
let bot = { turn: -1, el: null, text: '' };
let callStart = 0;
let timerId = null;
let capTimer = null;

const STATUS = {
  idle: '未通话',
  connecting: '连接中…',
  listening: '在听',
  thinking: '思考中…',
  speaking: '说话中',
};

function setStatus(key) {
  $('status').textContent = STATUS[key] || key;
  $('dot').dataset.state = key;
  $('stage').dataset.state = key;
}

function toast(msg, ms = 5000) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), ms);
}

// ------------------------------------------------------------- socket

function connect() {
  return new Promise((resolve, reject) => {
    const sock = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    sock.binaryType = 'arraybuffer';
    sock.onopen = () => resolve(sock);
    sock.onerror = () => reject(new Error('无法连接服务器，确认 python -m server 正在运行'));
    sock.onmessage = onMessage;
    sock.onclose = () => {
      if (ws !== sock) return;
      ws = null;
      if (inCall) {
        toast('与服务器的连接断开了');
        endCall(false);
      }
    };
  });
}

async function ensureSocket() {
  if (!ws || ws.readyState > 1) ws = await connect();
  return ws;
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

function onMessage(ev) {
  if (typeof ev.data !== 'string') {
    // [uint32 turn id][PCM16 mono]
    const turn = new DataView(ev.data).getUint32(0, true);
    if (turn === activeTurn) audio.play(ev.data.slice(4));
    return;
  }
  const m = JSON.parse(ev.data);
  switch (m.type) {
    case 'hello':
      $('models').textContent = `LLM：${m.llm}\nTTS：${m.tts}`;
      $('models').style.whiteSpace = 'pre-line';
      break;
    case 'fatal':
      toast(m.message, 15000);
      endCall(false);
      break;
    case 'stt_ready':
      if (inCall) setStatus('listening');
      break;
    case 'state':
      phase = m.value;
      avatar.setState(phase === 'listening' ? (inCall ? 'listening' : 'idle') : phase);
      if (inCall || phase !== 'listening') setStatus(phase);
      else setStatus('idle');
      break;
    case 'user_partial':
      showCaption('user', m.text, true);
      break;
    case 'user_final':
      addMessage('user', m.text, m.typed);
      showCaption('user', m.text, false);
      break;
    case 'assistant_delta':
      appendBot(m.turn, m.text);
      break;
    case 'assistant_done':
      if (bot.turn === m.turn && bot.el) bot.el.textContent = m.text;
      break;
    case 'audio_start':
      activeTurn = m.turn;
      ttsEnded = false;
      audio.startTurn(m.sample_rate);
      break;
    case 'tts_end':
      if (m.turn === activeTurn) {
        ttsEnded = true;
        audio.endTurn();
        maybePlaybackDone();
      }
      break;
    case 'interrupt':
      audio.flush();
      if (bot.turn === m.turn && bot.el && phase === 'speaking') bot.el.classList.add('cut');
      activeTurn = -1;
      ttsEnded = false;
      break;
    case 'metrics':
      showMetrics(m);
      break;
    case 'error':
      toast(m.message);
      break;
  }
}

audio.onPlayerEmpty = (empty) => {
  if (empty) maybePlaybackDone();
};

function maybePlaybackDone() {
  if (ttsEnded && activeTurn > 0 && audio.playerEmpty) {
    send({ type: 'playback_done', turn: activeTurn });
    ttsEnded = false;
  }
}

// ------------------------------------------------------------- call control

async function startCall() {
  const btn = $('callBtn');
  btn.disabled = true;
  setStatus('connecting');
  try {
    await audio.init();
    await ensureSocket();
    await audio.startMic((buf) => {
      if (ws && ws.readyState === 1) ws.send(buf);
    });
    send({ type: 'start' });
    inCall = true;
    muted = false;
    audio.setMicMuted(false);
    btn.classList.add('hang');
    $('callLabel').textContent = '挂断';
    $('micBtn').disabled = false;
    $('micBtn').setAttribute('aria-pressed', 'false');
    avatar.setState('listening');
    callStart = Date.now();
    $('timer').hidden = false;
    timerId = setInterval(updateTimer, 1000);
    updateTimer();
  } catch (e) {
    audio.stopMic();
    setStatus('idle');
    const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
    toast(denied ? '需要麦克风权限才能语音聊天（地址栏左侧可以重新允许）' : e.message || String(e));
  } finally {
    btn.disabled = false;
  }
}

function endCall(notify = true) {
  if (notify) send({ type: 'stop' });
  audio.stopMic();
  audio.flush();
  activeTurn = -1;
  ttsEnded = false;
  inCall = false;
  $('callBtn').classList.remove('hang');
  $('callLabel').textContent = '开始通话';
  $('micBtn').disabled = true;
  clearInterval(timerId);
  $('timer').hidden = true;
  avatar.setState('idle');
  setStatus('idle');
}

function updateTimer() {
  const s = Math.floor((Date.now() - callStart) / 1000);
  $('timer').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

function interrupt() {
  if (phase === 'speaking' || phase === 'thinking') send({ type: 'interrupt' });
}

$('callBtn').addEventListener('click', () => (inCall ? endCall() : startCall()));

$('micBtn').addEventListener('click', () => {
  muted = !muted;
  audio.setMicMuted(muted);
  $('micBtn').setAttribute('aria-pressed', String(muted));
  $('micBtn').querySelector('span').textContent = muted ? '取消静音' : '静音';
});

let camStream = null;
$('camBtn').addEventListener('click', async () => {
  if (camStream) {
    camStream.getTracks().forEach((t) => t.stop());
    camStream = null;
    $('pip').hidden = true;
    $('camBtn').setAttribute('aria-pressed', 'false');
    return;
  }
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
    $('selfview').srcObject = camStream;
    $('pip').hidden = false;
    $('camBtn').setAttribute('aria-pressed', 'true');
  } catch (e) {
    toast('打不开摄像头：' + (e.message || e.name));
  }
});

$('avatar').addEventListener('click', interrupt);
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && document.activeElement !== $('textInput')) {
    e.preventDefault();
    interrupt();
  }
});

$('resetBtn').addEventListener('click', () => {
  send({ type: 'reset' });
  audio.flush();
  $('log').innerHTML = '';
  bot = { turn: -1, el: null, text: '' };
  $('capUser').textContent = '';
  $('capBot').textContent = '';
});

$('textForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('textInput');
  const text = input.value.trim();
  if (!text) return;
  try {
    await audio.init();
    await ensureSocket();
  } catch (err) {
    toast(err.message || String(err));
    return;
  }
  send({ type: 'text', text });
  input.value = '';
});

// ------------------------------------------------------------- transcript UI

function addMessage(role, text, typed = false) {
  const log = $('log');
  const empty = log.querySelector('.empty');
  if (empty) empty.remove();
  const el = document.createElement('div');
  el.className = `msg ${role}${typed ? ' typed' : ''}`;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function appendBot(turn, delta) {
  if (bot.turn !== turn || !bot.el) bot = { turn, el: addMessage('bot', ''), text: '' };
  bot.text += delta;
  bot.el.textContent = bot.text;
  $('log').scrollTop = $('log').scrollHeight;
  showCaption('bot', bot.text, false);
}

function showCaption(who, text, partial) {
  const el = who === 'user' ? $('capUser') : $('capBot');
  // Keep captions short: show the tail of long replies.
  el.textContent = text.length > 80 ? '…' + text.slice(-80) : text;
  el.classList.toggle('partial', !!partial);
  if (who === 'user') $('capBot').textContent = partial ? $('capBot').textContent : '';
  clearTimeout(capTimer);
  capTimer = setTimeout(() => {
    if (phase === 'listening') {
      $('capUser').textContent = '';
      $('capBot').textContent = '';
    }
  }, 8000);
}

function showMetrics(m) {
  const parts = [];
  if (m.total_ms != null) parts.push(`回复延迟 <b>${m.total_ms}</b> ms`);
  const detail = [];
  if (m.llm_first_token_ms != null) detail.push(`LLM 首字 ${m.llm_first_token_ms} ms`);
  if (m.tts_first_audio_ms != null) detail.push(`TTS 首包 ${m.tts_first_audio_ms} ms`);
  $('metrics').innerHTML = parts.join('') + (detail.length ? `（${detail.join(' · ')}）` : '') +
    '<br>不含你停顿后等待判定说完的约 0.6 秒';
}
