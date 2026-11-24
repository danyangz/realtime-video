// "小星": a hand-drawn SVG cartoon whose mouth follows the TTS audio.
// States: idle | listening | thinking | speaking

const C = {
  skin: '#FFE1CC',
  skinShade: '#F2C2A5',
  hair: '#3A2E4D',
  hairLight: '#54456B',
  sweater: '#5B8DEF',
  sweaterDark: '#4A76D1',
  collar: '#F4F6FF',
  eye: '#2A2138',
  cheek: '#FF8FA3',
  mouth: '#6B2234',
  tongue: '#FF7A8C',
  star: '#FFC94A',
};

const SVG = `
<svg viewBox="0 0 400 420" preserveAspectRatio="xMidYMax meet" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="卡通形象小星">
  <defs>
    <clipPath id="mouthClip"><path id="mouthClipPath" d=""/></clipPath>
  </defs>

  <g id="body">
    <rect x="178" y="270" width="44" height="70" rx="16" fill="${C.skinShade}"/>
    <path d="M 62 420 C 70 352, 128 318, 200 318 C 272 318, 330 352, 338 420 Z" fill="${C.sweater}"/>
    <path d="M 120 420 C 124 380, 136 356, 150 344" stroke="${C.sweaterDark}" stroke-width="5" fill="none" stroke-linecap="round"/>
    <path d="M 280 420 C 276 380, 264 356, 250 344" stroke="${C.sweaterDark}" stroke-width="5" fill="none" stroke-linecap="round"/>
    <path d="M 160 324 Q 200 366 240 324 Q 200 344 160 324 Z" fill="${C.collar}"/>
  </g>

  <g id="head">
    <ellipse cx="200" cy="178" rx="124" ry="118" fill="${C.hair}"/>
    <path d="M 82 200 C 78 250, 92 282, 112 296 L 120 240 Z" fill="${C.hair}"/>
    <path d="M 318 200 C 322 250, 308 282, 288 296 L 280 240 Z" fill="${C.hair}"/>
    <ellipse cx="200" cy="204" rx="104" ry="98" fill="${C.skin}"/>

    <path d="M 94 196 C 88 118, 140 76, 200 76 C 260 76, 312 118, 306 196
             C 296 170, 280 148, 258 138 C 250 156, 230 164, 210 160
             C 196 154, 186 142, 180 130 C 168 152, 136 168, 104 176 C 100 182, 96 188, 94 196 Z"
          fill="${C.hair}"/>
    <path d="M 150 104 C 170 92, 196 88, 214 92" stroke="${C.hairLight}" stroke-width="6" fill="none" stroke-linecap="round" opacity="0.7"/>
    <path d="M 204 80 C 196 52, 220 40, 230 56" stroke="${C.hair}" stroke-width="7" fill="none" stroke-linecap="round"/>
    <g transform="translate(274 118) rotate(14)">
      <path d="M 0 -15 L 4.4 -5 L 15 -4.6 L 6.8 2.6 L 9.3 13 L 0 7.4 L -9.3 13 L -6.8 2.6 L -15 -4.6 L -4.4 -5 Z"
            fill="${C.star}" stroke="#E8A92A" stroke-width="1.5" stroke-linejoin="round"/>
    </g>

    <g id="browL"><path d="M 136 156 Q 154 146 172 153" stroke="${C.hair}" stroke-width="6.5" fill="none" stroke-linecap="round"/></g>
    <g id="browR"><path d="M 228 153 Q 246 146 264 156" stroke="${C.hair}" stroke-width="6.5" fill="none" stroke-linecap="round"/></g>

    <g id="eyeL">
      <ellipse cx="154" cy="196" rx="14.5" ry="18.5" fill="${C.eye}"/>
      <circle cx="149" cy="189" r="5.4" fill="#fff"/>
      <circle cx="159.5" cy="202" r="2.4" fill="#fff"/>
    </g>
    <g id="eyeR">
      <ellipse cx="246" cy="196" rx="14.5" ry="18.5" fill="${C.eye}"/>
      <circle cx="241" cy="189" r="5.4" fill="#fff"/>
      <circle cx="251.5" cy="202" r="2.4" fill="#fff"/>
    </g>

    <ellipse cx="124" cy="234" rx="19" ry="10.5" fill="${C.cheek}" opacity="0.42"/>
    <ellipse cx="276" cy="234" rx="19" ry="10.5" fill="${C.cheek}" opacity="0.42"/>
    <path d="M 195 222 Q 200 227 205 222" stroke="${C.skinShade}" stroke-width="3.5" fill="none" stroke-linecap="round"/>

    <g id="mouthGroup">
      <path id="smile" d="M 182 250 Q 200 264 218 250" stroke="${C.mouth}" stroke-width="4.5" fill="none" stroke-linecap="round"/>
      <path id="mouth" d="" fill="${C.mouth}"/>
      <ellipse id="tongue" cx="200" cy="260" rx="10" ry="6" fill="${C.tongue}" clip-path="url(#mouthClip)"/>
    </g>
  </g>

  <g id="thinking" opacity="0">
    <circle cx="322" cy="96" r="6" fill="#fff" opacity="0.85"/>
    <circle cx="342" cy="72" r="8.5" fill="#fff" opacity="0.85"/>
    <circle cx="366" cy="44" r="11" fill="#fff" opacity="0.85"/>
  </g>
</svg>`;

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
// Frame-rate independent exponential smoothing.
const approach = (cur, target, rate, dt) => cur + (target - cur) * (1 - Math.exp(-rate * dt));

export class Avatar {
  constructor(container, { getLevels, getMicLevel }) {
    container.innerHTML = SVG;
    const $ = (id) => container.querySelector('#' + id);
    this.el = {
      head: $('head'), body: $('body'), eyeL: $('eyeL'), eyeR: $('eyeR'), browL: $('browL'), browR: $('browR'),
      mouth: $('mouth'), smile: $('smile'), tongue: $('tongue'), clip: $('mouthClipPath'), thinking: $('thinking'),
    };
    this.getLevels = getLevels;
    this.getMicLevel = getMicLevel;
    this.state = 'idle';
    this.s = {
      open: 0, bright: 0.4, blinkStart: -1, nextBlink: 1.5, gx: 0, gy: 0, tgx: 0, tgy: 0, nextSaccade: 1,
      browL: 0, browR: 0, tilt: 0, dy: 0, micActive: 0, think: 0,
    };
    this.t = 0;
    this.last = performance.now();
    requestAnimationFrame((now) => this.frame(now));
  }

  setState(state) {
    this.state = state;
  }

  frame(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.t += dt;
    const t = this.t;
    const s = this.s;
    const st = this.state;

    // --- lip sync -------------------------------------------------------
    const lv = st === 'speaking' ? this.getLevels() : { rms: 0, bright: 0 };
    const openTarget = Math.pow(clamp((lv.rms - 0.005) / 0.08, 0, 1), 0.7);
    s.open = approach(s.open, openTarget, openTarget > s.open ? 38 : 16, dt);
    s.bright = approach(s.bright, clamp((lv.bright - 0.1) / 0.4, 0, 1), 10, dt);

    const mic = this.getMicLevel();
    s.micActive = approach(s.micActive, mic > 0.02 ? 1 : 0, mic > 0.02 ? 8 : 2, dt);

    // --- blinking -------------------------------------------------------
    if (t >= s.nextBlink) {
      s.blinkStart = t;
      s.nextBlink = t + (Math.random() < 0.15 ? 0.32 : 2.4 + Math.random() * 3.6);
    }
    const bt = t - s.blinkStart;
    const blink = bt >= 0 && bt < 0.16 ? (bt < 0.07 ? bt / 0.07 : 1 - (bt - 0.07) / 0.09) : 0;

    // --- gaze -----------------------------------------------------------
    if (st === 'thinking') {
      s.tgx = -7;
      s.tgy = -8;
    } else if (t >= s.nextSaccade) {
      const r = st === 'speaking' ? 2 : 3.5;
      s.tgx = (Math.random() * 2 - 1) * r;
      s.tgy = (Math.random() * 2 - 1) * r * 0.6;
      s.nextSaccade = t + 0.8 + Math.random() * 2.2;
    }
    s.gx = approach(s.gx, s.tgx, 14, dt);
    s.gy = approach(s.gy, s.tgy, 14, dt);

    // --- head & brows ---------------------------------------------------
    s.think = approach(s.think, st === 'thinking' ? 1 : 0, 6, dt);
    const listeningNod = st !== 'speaking' ? s.micActive * Math.sin(t * 3.2) * 1.6 : 0;
    const tiltTarget = 5 * s.think + Math.sin(t * 0.7) * 1.4 + (st === 'speaking' ? Math.sin(t * 2.1) * s.open * 3 : 0);
    s.tilt = approach(s.tilt, tiltTarget, 6, dt);
    s.dy = approach(s.dy, -s.open * 3.5 + listeningNod, 10, dt);

    const browTargetL = -s.open * 5 - 7 * s.think - 3 * s.micActive * (st === 'speaking' ? 0 : 1);
    const browTargetR = -s.open * 5 + 1.5 * s.think - 3 * s.micActive * (st === 'speaking' ? 0 : 1);
    s.browL = approach(s.browL, browTargetL, 12, dt);
    s.browR = approach(s.browR, browTargetR, 12, dt);

    // --- apply ----------------------------------------------------------
    const e = this.el;
    e.head.setAttribute('transform', `translate(0 ${s.dy.toFixed(2)}) rotate(${s.tilt.toFixed(2)} 200 300)`);
    const breathe = 1 + Math.sin(t * 1.6) * 0.008;
    e.body.setAttribute('transform', `translate(200 420) scale(1 ${breathe.toFixed(4)}) translate(-200 -420)`);

    const eyeScale = Math.max(0.08, 1 - blink);
    for (const [eye, cx] of [[e.eyeL, 154], [e.eyeR, 246]]) {
      eye.setAttribute(
        'transform',
        `translate(${(cx + s.gx).toFixed(2)} ${(196 + s.gy).toFixed(2)}) scale(1 ${eyeScale.toFixed(3)}) translate(${-cx} -196)`,
      );
    }
    e.browL.setAttribute('transform', `translate(0 ${s.browL.toFixed(2)}) rotate(${(-4 * s.think).toFixed(2)} 154 152)`);
    e.browR.setAttribute('transform', `translate(0 ${s.browR.toFixed(2)})`);
    e.thinking.setAttribute('opacity', (s.think * (0.6 + 0.4 * Math.sin(t * 4))).toFixed(3));

    this.drawMouth(s.open, s.bright);
    requestAnimationFrame((n) => this.frame(n));
  }

  drawMouth(open, bright) {
    const e = this.el;
    if (open < 0.06) {
      e.smile.setAttribute('opacity', '1');
      e.mouth.setAttribute('d', '');
      e.clip.setAttribute('d', '');
      e.tongue.setAttribute('opacity', '0');
      return;
    }
    e.smile.setAttribute('opacity', '0');
    const cx = 200, cy = 250;
    // Brighter sounds (i/e) -> wider mouth; darker sounds (o/u) -> rounder.
    const w = 26 + 20 * bright + 6 * open;
    const h = 4 + 30 * open * (1.05 - 0.35 * bright);
    const l = cx - w / 2, r = cx + w / 2;
    const d =
      `M ${l.toFixed(1)} ${cy} ` +
      `Q ${cx} ${(cy - h * 0.32).toFixed(1)} ${r.toFixed(1)} ${cy} ` +
      `Q ${cx} ${(cy + h * 1.3).toFixed(1)} ${l.toFixed(1)} ${cy} Z`;
    e.mouth.setAttribute('d', d);
    e.clip.setAttribute('d', d);
    e.tongue.setAttribute('opacity', '1');
    e.tongue.setAttribute('cx', cx);
    e.tongue.setAttribute('cy', (cy + h * 0.78).toFixed(1));
    e.tongue.setAttribute('rx', (w * 0.3).toFixed(1));
    e.tongue.setAttribute('ry', (h * 0.32).toFixed(1));
  }
}
