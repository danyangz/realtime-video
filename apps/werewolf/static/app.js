import { AudioEngine } from '/shared/audio.js';
import { Avatar, fakeLevels } from '/shared/avatar.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const ROLE_ZH = { werewolf: '狼人', villager: '平民', seer: '预言家', witch: '女巫', hunter: '猎人', idiot: '白痴' };
const CHOICE_LABEL = {
  wolf_kill: '狼人刀人', seer_check: '查验', witch_save: '解药', witch_poison: '毒药', run_sheriff: '竞选警长',
  sheriff_vote: '警长投票', speech_order: '发言顺序', exile_vote: '放逐投票', hunter_shoot: '猎人开枪', badge_transfer: '移交警徽',
};

const audio = new AudioEngine();
let ws = null;
let caps = { tts: true, ptt: true };
let state = null; // latest snapshot from the server
let request = null; // pending decision for me
let picked = null;
let speeches = new Map(); // speech id -> {el, text}
let speaking = null; // seat currently speaking
let activeAudio = -1;
let audioEnded = false;
let chosenRole = 'random';
let chosenCast = null;
let chosenLevel = 'standard';
let casts = [];
let gameOver = null;

// Avatars: one small animated face per seat, plus a big one on the stage for whoever is talking.
const ZERO = { rms: 0, bright: 0 };
let seatKey = '';
let seatEls = new Map(); // seat -> card element
let seatAvatars = new Map(); // seat -> Avatar
let speechSeat = new Map(); // speech id -> seat
let thinkingSeat = null; // AI whose speech has started but no words yet
let audioSeat = null; // seat (or 'judge') whose voice is playing
let stageAvatar = null;
let stageWho = null;
let stageLookKey = '';
let lastNarration = '';

// ------------------------------------------------------------------ socket

function wsUrl() {
  const u = new URL('ws', location.href);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.href;
}

function connect() {
  return new Promise((resolve, reject) => {
    const sock = new WebSocket(wsUrl());
    sock.binaryType = 'arraybuffer';
    sock.onopen = () => resolve(sock);
    sock.onerror = () => reject(new Error('无法连接服务器，确认 python -m server 正在运行'));
    sock.onmessage = onMessage;
    sock.onclose = () => {
      if (ws === sock) {
        ws = null;
        toast('与服务器的连接断开了，点“新的一局”重新开始');
      }
    };
  });
}

async function ensureSocket() {
  if (!ws || ws.readyState > 1) ws = await connect();
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

function onMessage(ev) {
  if (typeof ev.data !== 'string') {
    const id = new DataView(ev.data).getUint32(0, true);
    if (id === activeAudio) audio.play(ev.data.slice(4));
    return;
  }
  const m = JSON.parse(ev.data);
  switch (m.type) {
    case 'hello':
      caps = m;
      if (!m.tts) {
        $('ttsCheck').checked = false;
        $('ttsCheck').disabled = true;
        $('startHint').textContent = '没有配置 TOGETHER_API_KEY，只能文字模式。';
      }
      if (m.fake_ai) $('startHint').textContent = '当前是脚本 AI（WEREWOLF_FAKE_AI=1），不会调用模型。';
      renderLevels(m.levels || []);
      break;
    case 'fatal':
      toast(m.message, 12000);
      break;
    case 'state':
      state = m;
      renderTop();
      renderSeats();
      break;
    case 'log':
      addLog(m);
      break;
    case 'speech_start':
      speechStart(m);
      break;
    case 'speech_delta':
      speechDelta(m);
      break;
    case 'speech_end':
      speechEnd(m);
      break;
    case 'audio_start':
      activeAudio = m.id;
      audioEnded = false;
      audio.startTurn(m.sample_rate);
      audioSeat = speechSeat.has(m.id) ? speechSeat.get(m.id) : 'judge';
      if (audioSeat === 'judge') setCaption(lastNarration);
      updateAvatars();
      renderAction();
      break;
    case 'tts_end':
      if (m.id === activeAudio) {
        audioEnded = true;
        audio.endTurn();
        maybePlaybackDone();
      }
      break;
    case 'request':
      request = m;
      picked = null;
      renderAction();
      renderSeats();
      break;
    case 'request_done':
      if (request && request.id === m.id) {
        request = null;
        picked = null;
        renderAction();
        renderSeats();
      }
      break;
    case 'ptt_text':
      pttResult(m);
      break;
    case 'game_over':
      gameOver = m;
      showEnd(m);
      break;
    case 'error':
      toast(m.message);
      break;
  }
}

// ------------------------------------------------------------------ audio

audio.onPlayerEmpty = (empty) => {
  if (empty) maybePlaybackDone();
};

function maybePlaybackDone() {
  if (activeAudio > 0 && audioEnded && audio.playerEmpty) finishAudio();
}

function finishAudio() {
  send({ type: 'playback_done', id: activeAudio });
  activeAudio = -1;
  audioEnded = false;
  audioSeat = null;
  updateAvatars();
  renderAction();
}

function skipAudio() {
  if (activeAudio > 0) {
    audio.flush();
    finishAudio();
  }
}

// ------------------------------------------------------------------ rendering

function player(seat) {
  return state && state.players.find((p) => p.seat === seat);
}

function renderTop() {
  const phaseName = { setup: '开局', night: '夜晚', day: '白天', end: '游戏结束' }[state.phase] || '';
  $('phase').textContent = state.day ? `第 ${state.day} ${state.phase === 'night' ? '夜' : '天'} · ${phaseName}` : phaseName;
  document.body.dataset.phase = state.phase;
  const me = state.me;
  if (state.spectate) {
    $('me').hidden = false;
    $('me').className = 'me';
    $('me').innerHTML = '观战中 · <b>上帝视角</b>';
  } else if (me) {
    $('me').hidden = false;
    $('me').className = 'me' + (me.role === 'werewolf' ? ' wolf' : '');
    let extra = '';
    if (me.potions) extra = ` · 解药${me.potions.antidote ? '✓' : '✗'} 毒药${me.potions.poison ? '✓' : '✗'}`;
    $('me').innerHTML = `我是 ${me.seat}号 · <b>${esc(me.role_zh)}</b>${me.alive ? '' : ' · 已出局'}${extra}`;
  }
  $('ttsBtn').setAttribute('aria-pressed', String(!!state.tts));
}

function tagsHtml(p) {
  const tags = [];
  if (p.is_human) tags.push('<span class="tag you">你</span>');
  if (p.sheriff) tags.push('<span class="tag sheriff">警长</span>');
  if (p.role) tags.push(`<span class="tag role ${p.role === 'werewolf' ? 'werewolf' : 'good'}">${ROLE_ZH[p.role]}</span>`);
  if (p.mark) tags.push(`<span class="tag ${p.mark === '查杀' ? 'mark-kill' : 'mark-gold'}">${p.mark}</span>`);
  if (!p.can_vote && p.alive) tags.push('<span class="tag">不能投票</span>');
  return tags.join('');
}

function buildSeats(key) {
  seatKey = key;
  seatAvatars.forEach((a) => a.destroy());
  seatAvatars = new Map();
  seatEls = new Map();
  for (const [boxId, seats] of [['seatsL', [1, 2, 3, 4, 5, 6]], ['seatsR', [7, 8, 9, 10, 11, 12]]]) {
    const box = $(boxId);
    box.innerHTML = '';
    for (const s of seats) {
      const p = player(s);
      if (!p) continue;
      const el = document.createElement('div');
      el.className = 'seat';
      el.dataset.seat = s;
      el.innerHTML = `<span class="num">${s}</span><div class="avatar" style="--c:${p.color}"></div>
        <div class="info"><div class="name">${esc(p.name)}</div><div class="tags"></div></div>`;
      el.addEventListener('click', () => pickSeat(s));
      box.appendChild(el);
      seatEls.set(s, el);
      seatAvatars.set(s, new Avatar(el.querySelector('.avatar'), { look: p.look, mini: true, getLevels: () => levelsFor(s) }));
    }
  }
}

function renderSeats() {
  if (!state) return;
  const key = state.players.map((p) => `${p.name}/${p.color}`).join('|');
  if (key !== seatKey) buildSeats(key);
  const selectable = request && request.mode === 'choose' && request.seats
    ? new Set(request.options.map((o) => o.key)) : new Set();
  for (const p of state.players) {
    const el = seatEls.get(p.seat);
    if (!el) continue;
    el.classList.toggle('dead', !p.alive);
    el.classList.toggle('me', p.is_human);
    el.classList.toggle('speaking', isTalking(p.seat) || thinkingSeat === p.seat);
    el.classList.toggle('selectable', selectable.has(p.seat));
    el.classList.toggle('selected', picked === p.seat && selectable.has(p.seat));
    const tags = tagsHtml(p);
    const box = el.querySelector('.tags');
    if (box.innerHTML !== tags) box.innerHTML = tags;
  }
  updateAvatars();
}

// ------------------------------------------------------------------ avatars & stage

function isTalking(who) {
  if (audioSeat === who) return true;
  const p = typeof who === 'number' ? player(who) : null;
  if (p && p.is_human) return false; // you type; your face doesn't fake-talk
  return speaking === who && thinkingSeat !== who;
}

// Lip sync: the real voice when it's playing; a fake rhythm while text streams with voice off.
function levelsFor(who) {
  if (who != null && audioSeat === who) return audio.outputLevels();
  if (who != null && speaking === who && audioSeat == null && !(state && state.tts)) return fakeLevels();
  return ZERO;
}

function avatarState(seat) {
  const p = player(seat);
  if (p && !p.alive && !isTalking(seat)) return 'dead';
  if (thinkingSeat === seat) return 'thinking';
  if (isTalking(seat)) return 'speaking';
  return 'idle';
}

function updateAvatars() {
  for (const [seat, a] of seatAvatars) a.setState(avatarState(seat));
  updateStage();
}

function setCaption(text) {
  $('stageCaption').textContent = text ? (text.length > 70 ? '…' + text.slice(-70) : text) : '';
}

function stageShow(who, look, lookKey, name) {
  if (!look) return;
  if (!stageAvatar) stageAvatar = new Avatar($('stageAvatar'), { look, getLevels: () => levelsFor(stageWho) });
  else if (lookKey !== stageLookKey) stageAvatar.setLook(look);
  if (lookKey !== stageLookKey) setCaption('');
  stageWho = who;
  stageLookKey = lookKey;
  $('stageName').textContent = name;
}

function updateStage() {
  if (!state || !state.judge) return;
  const who = audioSeat ?? speaking ?? thinkingSeat ?? stageWho ?? 'judge';
  if (who === 'judge') {
    stageShow('judge', state.judge.look, `judge:${state.cast}`, `${state.judge.name} · 法官`);
    stageAvatar.setState(audioSeat === 'judge' ? 'speaking' : 'idle');
    return;
  }
  const p = player(who);
  if (!p) return;
  stageShow(who, p.look, `seat:${who}:${p.name}`, `${who}号 ${p.name}${p.alive ? '' : ' · 已出局'}`);
  stageAvatar.setState(avatarState(who) === 'dead' ? 'idle' : avatarState(who));
}

// A still face for chat bubbles (no animation loop).
function stillFace(container, look) {
  new Avatar(container, { look, mini: true }).destroy();
}

function nearBottom() {
  const log = $('log');
  return log.scrollHeight - log.scrollTop - log.clientHeight < 80;
}

function append(el) {
  const stick = nearBottom();
  $('log').appendChild(el);
  if (stick) $('log').scrollTop = $('log').scrollHeight;
}

function addLog(m) {
  const el = document.createElement('div');
  if (m.kind === 'phase') {
    el.className = 'divider';
    el.textContent = `${m.data.phase === 'night' ? '🌙' : '☀️'} ${m.text}`;
  } else if (m.kind === 'vote') {
    el.className = 'sys vote';
    el.textContent = '🗳 ' + m.text;
  } else {
    if (m.kind === 'narration' && !m.private) lastNarration = m.text;
    el.className = 'sys' + (m.private ? ' private' : '') + (m.kind === 'end' ? ' end' : '');
    el.innerHTML = (m.private ? `<b class="aud">${esc(m.audience || '仅你可见')}</b> · ` : '') + esc(m.text);
  }
  append(el);
  if (!request) renderAction();
}

function speechStart(m) {
  const p = player(m.seat);
  const el = document.createElement('div');
  el.className = 'msg live' + (p && p.is_human ? ' human' : '') + (m.kind === 'wolf_chat' ? ' wolfchat' : '');
  const name = p ? p.name : `${m.seat}号`;
  el.innerHTML = `<div class="avatar" style="--c:${p ? p.color : '#888'}"></div>
    <div class="body"><div class="who"><b>${m.seat}号 ${esc(name)}</b><span class="kind">${esc(m.label)}</span></div>
    <div class="bubble"></div></div>`;
  if (p) stillFace(el.querySelector('.avatar'), p.look);
  speeches.set(m.id, { el, text: '', seat: m.seat });
  speechSeat.set(m.id, m.seat);
  speaking = m.seat;
  thinkingSeat = p && !p.is_human ? m.seat : null;
  setCaption('');
  append(el);
  renderSeats();
  if (!request) renderAction();
}

function speechDelta(m) {
  const s = speeches.get(m.id);
  if (!s) return;
  const stick = nearBottom();
  s.text += m.text;
  s.el.querySelector('.bubble').textContent = s.text;
  if (stick) $('log').scrollTop = $('log').scrollHeight;
  if (thinkingSeat === s.seat) {
    thinkingSeat = null;
    renderSeats();
  }
  if (stageWho === s.seat) setCaption(s.text);
}

function speechEnd(m) {
  const s = speeches.get(m.id);
  if (s) {
    s.el.classList.remove('live');
    s.el.querySelector('.bubble').textContent = m.text;
    if (stageWho === s.seat) setCaption(m.text);
  }
  speaking = null;
  thinkingSeat = null;
  renderSeats();
  if (!request) renderAction();
}

function waitingText() {
  if (gameOver) return '游戏结束。';
  if (!state) return '点“开始游戏”。';
  if (speaking) {
    const p = player(speaking);
    return `${speaking}号 ${p ? esc(p.name) : ''} 正在发言`;
  }
  if (state.phase === 'night') return '夜晚，大家正在行动<span class="dots"></span>';
  return '等待其他玩家<span class="dots"></span>';
}

function renderAction() {
  const box = $('action');
  if (!request) {
    const skip = activeAudio > 0 ? '<button class="btn ghost" id="skipBtn" type="button">跳过语音 ⏭</button>' : '';
    box.innerHTML = `<div class="status"><span>${waitingText()}</span><span class="spacer" style="flex:1"></span>${skip}</div>`;
    if (skip) $('skipBtn').onclick = skipAudio;
    return;
  }
  if (request.mode === 'speak') {
    box.innerHTML = `
      <div class="prompt">轮到你 <b>${esc(request.label)}</b>：${esc(request.prompt)}</div>
      <textarea id="speakText" placeholder="输入你的发言…（⌘/Ctrl + Enter 发送）" maxlength="500"></textarea>
      <div class="row">
        ${caps.ptt ? '<button class="btn ptt" id="pttBtn" type="button">🎤 按住说话</button>' : ''}
        <button class="btn ghost" id="autoBtn" type="button">让 AI 替我说</button>
        <span style="flex:1"></span>
        <button class="btn primary" id="sendBtn" type="button">发送</button>
      </div>`;
    const ta = $('speakText');
    ta.focus();
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitSpeech();
    });
    $('sendBtn').onclick = submitSpeech;
    $('autoBtn').onclick = () => respond({ auto: true });
    if (caps.ptt) bindPtt($('pttBtn'));
    return;
  }
  // choose
  const label = CHOICE_LABEL[request.kind] || '选择';
  const opts = request.seats ? '' : request.options
    .map((o) => `<button class="btn option${picked === o.key ? ' on' : ''}" data-key="${o.key}" type="button">${esc(o.label)}</button>`).join('');
  const pickedLabel = picked ? (request.options.find((o) => o.key === picked) || {}).label : null;
  box.innerHTML = `
    <div class="prompt"><b>${label}</b> · ${esc(request.prompt)}</div>
    ${request.seats ? `<div class="picked">点击两侧的玩家头像选择 · 已选：<b>${pickedLabel ? esc(pickedLabel) : '无'}</b></div>` : `<div class="row">${opts}</div>`}
    <div class="row" style="margin-top:10px">
      ${request.allow_skip ? `<button class="btn" id="skipChoice" type="button">${esc(request.skip_label)}</button>` : ''}
      <button class="btn ghost" id="autoBtn" type="button">让 AI 替我选</button>
      <span style="flex:1"></span>
      <button class="btn primary" id="okBtn" type="button" ${picked ? '' : 'disabled'}>确认</button>
    </div>`;
  box.querySelectorAll('.option').forEach((b) => (b.onclick = () => { picked = Number(b.dataset.key); renderAction(); }));
  if (request.allow_skip) $('skipChoice').onclick = () => respond({ value: 0 });
  $('autoBtn').onclick = () => respond({ auto: true });
  $('okBtn').onclick = () => picked && respond({ value: picked });
}

function pickSeat(seat) {
  if (!request || request.mode !== 'choose' || !request.seats) return;
  if (!request.options.some((o) => o.key === seat)) return;
  picked = seat;
  renderSeats();
  renderAction();
}

function respond(extra) {
  if (!request) return;
  send({ type: 'response', id: request.id, ...extra });
  request = null;
  picked = null;
  renderAction();
  renderSeats();
}

function submitSpeech() {
  const text = $('speakText').value.trim();
  if (!text) {
    toast('先说点什么，或者点“让 AI 替我说”');
    return;
  }
  respond({ value: text });
}

// ------------------------------------------------------------------ push to talk

let recording = false;

function bindPtt(btn) {
  const start = async (e) => {
    e.preventDefault();
    if (recording) return;
    recording = true;
    btn.classList.add('rec');
    btn.textContent = '🎙 松开结束';
    try {
      await audio.init();
      send({ type: 'ptt_start' });
      await audio.startMic((buf) => {
        if (recording && ws && ws.readyState === 1) ws.send(buf);
      });
    } catch (err) {
      recording = false;
      btn.classList.remove('rec');
      btn.textContent = '🎤 按住说话';
      toast(err && err.name === 'NotAllowedError' ? '需要麦克风权限才能按住说话' : String(err.message || err));
    }
  };
  const stop = () => {
    if (!recording) return;
    recording = false;
    audio.stopMic();
    send({ type: 'ptt_end' });
    btn.classList.remove('rec');
    btn.textContent = '识别中…';
    btn.disabled = true;
  };
  btn.addEventListener('pointerdown', start);
  btn.addEventListener('pointerup', stop);
  btn.addEventListener('pointerleave', stop);
  btn.addEventListener('pointercancel', stop);
}

function pttResult(m) {
  const btn = $('pttBtn');
  if (btn) {
    btn.disabled = false;
    btn.textContent = '🎤 按住说话';
  }
  if (m.error) toast('没听清：' + m.error);
  const ta = $('speakText');
  if (ta && m.text) {
    ta.value = (ta.value ? ta.value + ' ' : '') + m.text;
    ta.focus();
  }
}

// ------------------------------------------------------------------ start / end

$('roleChoice').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  chosenRole = b.dataset.role;
  $('roleChoice').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
});

async function startGame() {
  try {
    await audio.init();
    await ensureSocket();
  } catch (e) {
    toast(e.message || String(e));
    return;
  }
  $('log').innerHTML = '';
  speeches = new Map();
  speechSeat = new Map();
  speaking = null;
  thinkingSeat = null;
  audioSeat = null;
  stageWho = null;
  request = null;
  gameOver = null;
  audio.flush();
  activeAudio = -1;
  setCaption('');
  send({
    type: 'start', role: chosenRole, cast: chosenCast, level: chosenLevel, name: $('nameInput').value.trim(),
    tts: $('ttsCheck').checked,
  });
  $('startModal').hidden = true;
  $('endModal').hidden = true;
  renderAction();
}

$('startBtn').onclick = startGame;
$('newBtn').onclick = () => { $('endModal').hidden = true; $('startModal').hidden = false; };
$('againBtn').onclick = () => { $('endModal').hidden = true; $('startModal').hidden = false; };
$('closeEnd').onclick = () => { $('endModal').hidden = true; };
$('ttsBtn').onclick = () => {
  const on = $('ttsBtn').getAttribute('aria-pressed') !== 'true';
  $('ttsBtn').setAttribute('aria-pressed', String(on));
  if (!on) skipAudio();
  send({ type: 'tts', on });
};

function showEnd(m) {
  const mine = state && state.me;
  const won = mine ? ((m.winner === 'wolf') === (mine.role === 'werewolf')) : null;
  $('endTitle').textContent = `${m.winner === 'good' ? '好人阵营' : '狼人阵营'}获胜` +
    (won === null ? '' : won ? ' · 你赢了 🎉' : ' · 你输了');
  $('reveal').innerHTML = m.players.map((p) => `
    <div class="r ${p.role}"><b>${p.seat}号 ${esc(p.name)}</b>${esc(p.role_zh)}${p.alive ? '' : ' · 出局'}
    <br><small>${esc(p.model)}</small></div>`).join('');
  const seats = [...new Set(m.thoughts.map((t) => t.seat))].sort((a, b) => a - b);
  const tabs = $('thoughtTabs');
  tabs.innerHTML = seats.map((s) => {
    const p = m.players.find((x) => x.seat === s);
    return `<button type="button" data-seat="${s}">${s}号 ${esc(p.name)}（${esc(p.role_zh)}）</button>`;
  }).join('');
  const show = (seat) => {
    tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('on', Number(b.dataset.seat) === seat));
    $('thoughts').innerHTML = m.thoughts.filter((t) => t.seat === seat).map((t) => `
      <div class="thought"><div class="when">第 ${t.day} ${t.phase === 'night' ? '夜' : '天'} · ${esc(CHOICE_LABEL[t.kind] || t.label || t.kind)}</div>
      ${t.thought ? `<div class="t">💭 ${esc(t.thought)}</div>` : ''}<div class="o">→ ${esc(t.output)}</div></div>`).join('')
      || '<div class="thought">没有记录。</div>';
  };
  tabs.onclick = (e) => { const b = e.target.closest('button'); if (b) show(Number(b.dataset.seat)); };
  if (seats.length) show(seats[0]);
  $('endModal').hidden = false;
}

function toast(msg, ms = 5000) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), ms);
}

// ------------------------------------------------------------------ AI level picker

function renderLevels(levels) {
  const box = $('levelChoice');
  box.innerHTML = levels.map((l) => `<button type="button" data-level="${l.key}">${esc(l.title)}</button>`).join('');
  const pick = (key) => {
    chosenLevel = key;
    box.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.level === key));
    const l = levels.find((x) => x.key === key);
    $('levelDesc').textContent = l ? l.desc : '';
  };
  box.onclick = (e) => { const b = e.target.closest('button'); if (b) pick(b.dataset.level); };
  if (levels.length) pick(levels.some((l) => l.key === chosenLevel) ? chosenLevel : levels[0].key);
}

// ------------------------------------------------------------------ cast picker

function showCast(key) {
  chosenCast = key;
  const c = casts.find((x) => x.key === key);
  $('castChoice').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.cast === key));
  const box = $('castPreview');
  box.innerHTML = '';
  for (const p of c.personas) {
    const f = document.createElement('div');
    f.className = 'face';
    f.style.setProperty('--c', p.color);
    f.title = `${p.name}：${p.style}`;
    box.appendChild(f);
    stillFace(f, p.look);
  }
  if (!state) stageShow('judge', c.judge.look, `judge:${c.key}`, `${c.judge.name} · 法官`);
}

fetch('casts').then((r) => r.json()).then((list) => {
  casts = list;
  $('castChoice').innerHTML = list.map((c) => `<button type="button" data-cast="${c.key}">${esc(c.title)}</button>`).join('');
  $('castChoice').onclick = (e) => { const b = e.target.closest('button'); if (b) showCast(b.dataset.cast); };
  showCast(list[0].key);
});

// Connect early so the start screen knows what's available (voice, push-to-talk).
ensureSocket().catch((e) => toast(e.message));
renderAction();

// For automated tests: ?autostart=<role|spectate> starts a game right away.
const auto = new URLSearchParams(location.search).get('autostart');
if (auto !== null) {
  chosenRole = auto || 'spectate';
  setTimeout(startGame, 300);
}
