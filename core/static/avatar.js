// Animated cartoon heads, drawn as SVG from a "look" description, with audio-driven lip sync.
// Shared by every app. States: idle | listening | thinking | speaking | dead
//
//   const a = new Avatar(container, { look, getLevels, getMicLevel, mini })
//   a.setState('speaking'); a.setLook(otherLook)
//
// A look is a plain object (see DEFAULT_LOOK); unknown keys are ignored.

export const DEFAULT_LOOK = {
  face: 'human', // human | monkey | pig
  skin: '#FFE1CC',
  skinShade: '#F2C2A5',
  fur: '#B0743A', // monkey head fur
  hair: '#3A2E4D',
  hairStyle: 'bob', // bob | short | spiky | long | bun | curly | buns2 | tufts | bald
  ahoge: false,
  brows: 'soft', // soft | thick | angry | white
  eye: '#2A2138',
  lips: '#6B2234',
  cheeks: '#FF8FA3',
  outfit: 'sweater', // sweater | shirt | tie | hoodie | robe | kasaya | armor
  shirt: '#5B8DEF',
  shirtShade: '#4A76D1',
  trim: '#F4F6FF',
  accent: '#FFC94A', // bows, bands, ornaments
  extras: [], // see EXTRAS_* below
  sclera: false, // white of the eye (helps on dark skin)
  mouthY: 250,
};

// The video chat's 小星.
export const XIAOXING = { ...DEFAULT_LOOK, ahoge: true, extras: ['star'] };

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const approach = (cur, target, rate, dt) => cur + (target - cur) * (1 - Math.exp(-rate * dt));
let uid = 0;

// ------------------------------------------------------------------ drawing

function backHair(L) {
  const h = L.hair;
  switch (L.hairStyle) {
    case 'bob':
      return `<ellipse cx="200" cy="178" rx="124" ry="118" fill="${h}"/>
        <path d="M 82 200 C 78 250, 92 282, 112 296 L 120 240 Z" fill="${h}"/>
        <path d="M 318 200 C 322 250, 308 282, 288 296 L 280 240 Z" fill="${h}"/>`;
    case 'long':
      return `<path d="M 80 190 C 70 92 140 52 200 52 C 260 52 330 92 320 190 L 334 336 C 304 356 262 350 252 322
        L 148 322 C 138 350 96 356 66 336 Z" fill="${h}"/>`;
    case 'bun':
      return `<circle cx="200" cy="66" r="40" fill="${h}"/><ellipse cx="200" cy="172" rx="118" ry="108" fill="${h}"/>`;
    case 'curly':
      return [[120, 150, 42], [150, 108, 46], [200, 92, 50], [250, 108, 46], [280, 150, 42], [104, 196, 30], [296, 196, 30]]
        .map(([x, y, r]) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${h}"/>`).join('');
    case 'buns2':
      return `<circle cx="134" cy="88" r="32" fill="${h}"/><circle cx="266" cy="88" r="32" fill="${h}"/>
        <ellipse cx="200" cy="162" rx="112" ry="92" fill="${h}"/>`;
    case 'short':
    case 'spiky':
      return `<ellipse cx="200" cy="162" rx="111" ry="90" fill="${h}"/>`;
    default:
      return '';
  }
}

function frontHair(L) {
  const h = L.hair;
  switch (L.hairStyle) {
    case 'bob':
      return `<path d="M 94 196 C 88 118, 140 76, 200 76 C 260 76, 312 118, 306 196
        C 296 170, 280 148, 258 138 C 250 156, 230 164, 210 160 C 196 154, 186 142, 180 130
        C 168 152, 136 168, 104 176 C 100 182, 96 188, 94 196 Z" fill="${h}"/>`;
    case 'long':
      return `<path d="M 96 198 C 90 116 140 74 200 74 C 262 74 312 116 304 198 C 292 150 262 114 214 108
        C 178 110 138 140 108 188 Z" fill="${h}"/>`;
    case 'bun':
      return `<path d="M 96 190 C 92 112 142 78 200 78 C 258 78 308 112 304 190 C 290 150 256 126 210 124
        C 170 124 130 146 96 190 Z" fill="${h}"/><rect x="176" y="96" width="48" height="10" rx="5" fill="${L.accent}"/>`;
    case 'short':
    case 'buns2':
      return `<path d="M 100 170 C 104 106 150 80 200 80 C 252 80 298 106 300 170 C 286 142 262 128 234 126
        C 214 136 186 136 166 126 C 138 128 114 142 100 170 Z" fill="${h}"/>`;
    case 'spiky':
      return `<path d="M 96 172 L 104 120 L 124 132 L 132 90 L 156 112 L 170 70 L 192 104 L 206 62 L 222 102 L 244 70
        L 254 110 L 278 88 L 280 130 L 300 122 L 304 172 C 286 146 262 132 234 130 C 214 140 186 140 166 130
        C 138 132 114 146 96 172 Z" fill="${h}"/>`;
    case 'curly':
      return [[132, 140, 28], [166, 122, 28], [200, 116, 30], [234, 122, 28], [268, 140, 28]]
        .map(([x, y, r]) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${h}"/>`).join('');
    case 'tufts':
      return `<path d="M 186 112 C 180 84 200 66 214 74 C 204 84 214 100 210 112 Z" fill="${h}"/>
        <path d="M 120 150 C 100 130 106 108 122 108 C 120 124 134 132 136 142 Z" fill="${h}"/>
        <path d="M 280 150 C 300 130 294 108 278 108 C 280 124 266 132 264 142 Z" fill="${h}"/>`;
    default:
      return '';
  }
}

const hairCoversEars = (L) => ['bob', 'long', 'bun', 'curly'].includes(L.hairStyle);

function ears(L) {
  if (L.face === 'monkey') {
    return `<circle cx="84" cy="206" r="30" fill="${L.fur}"/><circle cx="86" cy="206" r="17" fill="${L.skin}"/>
      <circle cx="316" cy="206" r="30" fill="${L.fur}"/><circle cx="314" cy="206" r="17" fill="${L.skin}"/>`;
  }
  if (L.face === 'pig' || hairCoversEars(L)) return '';
  return `<ellipse cx="97" cy="214" rx="15" ry="20" fill="${L.skin}"/><ellipse cx="303" cy="214" rx="15" ry="20" fill="${L.skin}"/>
    <path d="M 98 206 Q 90 214 98 224" stroke="${L.skinShade}" stroke-width="4" fill="none" stroke-linecap="round"/>
    <path d="M 302 206 Q 310 214 302 224" stroke="${L.skinShade}" stroke-width="4" fill="none" stroke-linecap="round"/>`;
}

function face(L) {
  if (L.face === 'monkey') {
    return `<ellipse cx="200" cy="200" rx="116" ry="110" fill="${L.fur}"/>
      <path d="M 186 92 L 194 70 L 202 90 L 212 68 L 216 94 Z" fill="${L.fur}"/>
      <circle cx="160" cy="192" r="48" fill="${L.skin}"/><circle cx="240" cy="192" r="48" fill="${L.skin}"/>
      <ellipse cx="200" cy="252" rx="72" ry="54" fill="${L.skin}"/>`;
  }
  return `<ellipse cx="200" cy="204" rx="104" ry="98" fill="${L.skin}"/>`;
}

function brows(L) {
  const c = L.brows === 'white' ? '#F4F1EA' : L.face === 'monkey' ? '#6E431F' : L.hair;
  const w = L.brows === 'thick' || L.brows === 'white' ? 10 : 6.5;
  const [l, r] = L.brows === 'angry'
    ? ['M 134 148 Q 154 148 174 158', 'M 226 158 Q 246 148 266 148']
    : ['M 136 156 Q 154 146 172 153', 'M 228 153 Q 246 146 264 156'];
  return `<g class="browL"><path d="${l}" stroke="${c}" stroke-width="${w}" fill="none" stroke-linecap="round"/></g>
    <g class="browR"><path d="${r}" stroke="${c}" stroke-width="${w}" fill="none" stroke-linecap="round"/></g>`;
}

function eyes(L) {
  const one = (cx, cls) => `<g class="${cls}">${L.sclera ? `<ellipse cx="${cx}" cy="196" rx="18" ry="21" fill="#FBF7F0"/>` : ''}
      <ellipse cx="${cx}" cy="196" rx="${L.sclera ? 12 : 14.5}" ry="${L.sclera ? 16 : 18.5}" fill="${L.eye}"/>
      <circle cx="${cx - 5}" cy="189" r="5.4" fill="#fff"/>
      <circle cx="${cx + 5.5}" cy="202" r="2.4" fill="#fff"/></g>`;
  return one(154, 'eyeL') + one(246, 'eyeR');
}

function nose(L) {
  if (L.face === 'pig') {
    return `<ellipse cx="200" cy="236" rx="31" ry="22" fill="#F39AAD" stroke="#D97B8E" stroke-width="3"/>
      <ellipse cx="189" cy="236" rx="5" ry="7" fill="#B95A6E"/><ellipse cx="211" cy="236" rx="5" ry="7" fill="#B95A6E"/>`;
  }
  if (L.face === 'monkey') {
    return `<ellipse cx="192" cy="232" rx="3.5" ry="4.5" fill="#7A4A2A"/><ellipse cx="208" cy="232" rx="3.5" ry="4.5" fill="#7A4A2A"/>`;
  }
  return `<path d="M 195 222 Q 200 227 205 222" stroke="${L.skinShade}" stroke-width="3.5" fill="none" stroke-linecap="round"/>`;
}

function cheeks(L) {
  if (!L.cheeks) return '';
  const y = L.face === 'pig' ? 248 : 234;
  return `<ellipse cx="124" cy="${y}" rx="19" ry="10.5" fill="${L.cheeks}" opacity="0.42"/>
    <ellipse cx="276" cy="${y}" rx="19" ry="10.5" fill="${L.cheeks}" opacity="0.42"/>`;
}

// Extras drawn behind the head (before back hair) / in front of the face / on the body.
const EXTRAS_BACK = {
  bull_horns: () => `<path d="M 118 136 C 80 118 58 78 66 34 C 86 78 112 98 146 108 Z" fill="#EFE3CC" stroke="#BCA88A" stroke-width="3"/>
    <path d="M 282 136 C 320 118 342 78 334 34 C 314 78 288 98 254 108 Z" fill="#EFE3CC" stroke="#BCA88A" stroke-width="3"/>`,
  pig_ears: () => '',
};

const EXTRAS_FRONT = {
  star: () => `<g transform="translate(274 118) rotate(14)"><path d="M 0 -15 L 4.4 -5 L 15 -4.6 L 6.8 2.6 L 9.3 13 L 0 7.4 L -9.3 13 L -6.8 2.6 L -15 -4.6 L -4.4 -5 Z" fill="#FFC94A" stroke="#E8A92A" stroke-width="1.5" stroke-linejoin="round"/></g>`,
  glasses: () => `<g fill="rgba(255,255,255,0.12)" stroke="#2A2138" stroke-width="5"><circle cx="154" cy="196" r="26"/><circle cx="246" cy="196" r="26"/></g>
    <path d="M 180 192 Q 200 184 220 192" stroke="#2A2138" stroke-width="5" fill="none"/>`,
  stubble: () => `<path d="M 132 258 Q 200 330 268 258 Q 250 300 200 304 Q 150 300 132 258 Z" fill="#000" opacity="0.13"/>`,
  mustache: (L) => `<path d="M 166 240 Q 184 228 200 237 Q 216 228 234 240 Q 216 248 200 243 Q 184 248 166 240 Z" fill="${L.brows === 'white' ? '#F4F1EA' : L.hair}"/>`,
  beard_long: () => `<path d="M 130 252 C 140 320 176 372 200 380 C 224 372 260 320 270 252 C 252 282 228 290 200 290 C 172 290 148 282 130 252 Z" fill="#F4F1EA" stroke="#DDD6C8" stroke-width="2"/>`,
  beard_red: () => `<path d="M 118 244 C 128 300 168 336 200 340 C 232 336 272 300 282 244 C 262 280 232 292 200 292 C 168 292 138 280 118 244 Z" fill="#9C3B22"/>`,
  freckles: () => [[116, 226], [128, 234], [140, 226], [260, 226], [272, 234], [284, 226]]
    .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.6" fill="#B07050" opacity="0.6"/>`).join(''),
  bow: (L) => `<g transform="translate(270 120) rotate(12)"><path d="M 0 0 L -22 -14 L -22 14 Z M 0 0 L 22 -14 L 22 14 Z" fill="${L.accent}"/><circle r="6" fill="${L.accent}" stroke="#0002" stroke-width="2"/></g>`,
  headband: (L) => `<path d="M 100 160 C 128 104 272 104 300 160" stroke="${L.accent}" stroke-width="12" fill="none" stroke-linecap="round"/>`,
  gold_band: () => `<path d="M 92 152 C 124 118 276 118 308 152" stroke="#E8B23A" stroke-width="13" fill="none" stroke-linecap="round"/>
    <path d="M 92 152 C 124 118 276 118 308 152" stroke="#FFE08A" stroke-width="3" fill="none" stroke-linecap="round"/>
    <circle cx="94" cy="158" r="9" fill="#E8B23A"/><circle cx="306" cy="158" r="9" fill="#E8B23A"/>`,
  monk_cap: () => {
    const petal = (x) => `<path d="M ${x - 22} 128 C ${x - 22} 92 ${x - 8} 78 ${x} 70 C ${x + 8} 78 ${x + 22} 92 ${x + 22} 128 Z" fill="#E8B23A" stroke="#B8862A" stroke-width="3"/>`;
    return [118, 159, 200, 241, 282].map(petal).join('') +
      `<path d="M 98 140 C 130 116 270 116 302 140 L 302 128 C 270 104 130 104 98 128 Z" fill="#C0392B"/>` +
      [118, 159, 200, 241, 282].map((x) => `<circle cx="${x}" cy="104" r="6" fill="#C0392B"/>`).join('');
  },
  black_cap: () => `<path d="M 102 154 C 106 94 150 66 200 66 C 250 66 294 94 298 154 C 262 138 138 138 102 154 Z" fill="#2B2B35"/>
    <path d="M 102 154 C 138 138 262 138 298 154" stroke="#44444f" stroke-width="5" fill="none"/>`,
  pig_ears: (L) => `<path d="M 118 140 C 84 112 60 84 66 60 C 96 70 128 92 150 118 Z" fill="${L.skin}" stroke="${L.skinShade}" stroke-width="3"/>
    <path d="M 282 140 C 316 112 340 84 334 60 C 304 70 272 92 250 118 Z" fill="${L.skin}" stroke="${L.skinShade}" stroke-width="3"/>`,
  nose_ring: () => `<ellipse cx="200" cy="226" rx="20" ry="11" fill="#0002"/><circle cx="200" cy="244" r="11" stroke="#E8B23A" stroke-width="5" fill="none"/>`,
  third_eye: () => `<path d="M 200 128 C 210 140 210 156 200 168 C 190 156 190 140 200 128 Z" fill="#fff" stroke="#C0392B" stroke-width="3"/>
    <ellipse cx="200" cy="148" rx="4" ry="8" fill="#2A2138"/>`,
  tall_crown: () => `<path d="M 140 108 L 150 44 L 176 86 L 200 30 L 224 86 L 250 44 L 260 108 Z" fill="#D9DEE8" stroke="#8C98AE" stroke-width="3"/>
    <circle cx="200" cy="80" r="8" fill="#C0392B"/>`,
  crown: () => `<path d="M 146 106 L 156 70 L 178 92 L 200 60 L 222 92 L 244 70 L 254 106 Z" fill="#F2C14E" stroke="#C99A2E" stroke-width="3"/>
    <circle cx="200" cy="86" r="6" fill="#D6336C"/><circle cx="168" cy="96" r="4" fill="#3BA3D0"/><circle cx="232" cy="96" r="4" fill="#3BA3D0"/>`,
  hairpins: () => `<path d="M 236 70 L 276 40" stroke="#E8B23A" stroke-width="5" stroke-linecap="round"/><circle cx="278" cy="38" r="7" fill="#D6336C"/>
    <path d="M 164 70 L 124 44" stroke="#E8B23A" stroke-width="5" stroke-linecap="round"/><circle cx="122" cy="42" r="6" fill="#E8B23A"/>`,
  bone_pin: () => `<path d="M 232 82 L 286 52" stroke="#F4F1EA" stroke-width="7" stroke-linecap="round"/>
    <circle cx="288" cy="48" r="7" fill="#F4F1EA"/><circle cx="294" cy="56" r="6" fill="#F4F1EA"/>`,
  rabbit_pin: () => `<g transform="translate(276 112)"><ellipse cx="-7" cy="-20" rx="5" ry="14" fill="#fff"/><ellipse cx="7" cy="-20" rx="5" ry="14" fill="#fff"/>
    <circle r="13" fill="#fff"/><circle cx="-4" cy="-2" r="2" fill="#D6336C"/><circle cx="4" cy="-2" r="2" fill="#D6336C"/></g>`,
  flame: () => `<path d="M 200 104 C 186 120 188 136 200 142 C 212 136 214 120 200 104 Z" fill="#FF6B2C"/>
    <path d="M 200 118 C 194 126 195 134 200 137 C 205 134 206 126 200 118 Z" fill="#FFD23F"/>`,
  earrings: () => `<circle cx="98" cy="246" r="6" fill="#E8B23A"/><circle cx="302" cy="246" r="6" fill="#E8B23A"/>`,
  eyeshadow: () => `<path d="M 134 176 Q 154 164 174 176" stroke="#7B3F8C" stroke-width="5" fill="none" opacity="0.7" stroke-linecap="round"/>
    <path d="M 226 176 Q 246 164 266 176" stroke="#7B3F8C" stroke-width="5" fill="none" opacity="0.7" stroke-linecap="round"/>`,
  straw_hat: () => `<path d="M 60 150 L 200 64 L 340 150 Q 200 172 60 150 Z" fill="#D8B86A" stroke="#B0904A" stroke-width="3"/>
    <path d="M 140 112 L 200 76 L 260 112" stroke="#B0904A" stroke-width="2" fill="none"/>`,
  earth_hat: () => `<path d="M 108 150 C 110 92 152 64 200 64 C 248 64 290 92 292 150 C 260 136 140 136 108 150 Z" fill="#8A5A2B"/>
    <path d="M 100 154 C 140 136 260 136 300 154" stroke="#6E4520" stroke-width="10" fill="none" stroke-linecap="round"/>`,
};

const EXTRAS_BODY = {
  beads: () => {
    let s = '';
    for (let i = 0; i <= 10; i++) {
      const a = Math.PI * (0.15 + 0.7 * (i / 10));
      s += `<circle cx="${(200 - Math.cos(a) * 78).toFixed(1)}" cy="${(318 + Math.sin(a) * 52).toFixed(1)}" r="9" fill="#5B3A29" stroke="#3E2619" stroke-width="2"/>`;
    }
    return s;
  },
  ring: () => `<ellipse cx="200" cy="322" rx="46" ry="12" fill="none" stroke="#E8B23A" stroke-width="9"/>`,
  fan: () => `<g transform="translate(330 360) rotate(-24)"><path d="M 0 0 C -46 -30 -50 -96 0 -118 C 50 -96 46 -30 0 0 Z" fill="#5DA35C" stroke="#3E7D3E" stroke-width="3"/>
    <path d="M 0 0 L 0 -112" stroke="#3E7D3E" stroke-width="3"/><path d="M 0 0 L 0 40" stroke="#8A5A2B" stroke-width="7" stroke-linecap="round"/></g>`,
};
const EXTRAS_BEHIND_BODY = {
  sash: () => `<path d="M 40 300 C 90 260 120 360 200 330 C 280 300 310 380 370 320 L 376 344 C 316 404 280 330 200 356 C 120 384 90 290 46 326 Z" fill="#E03E52" opacity="0.9"/>`,
};

function body(L) {
  const neck = `<rect x="178" y="270" width="44" height="70" rx="16" fill="${L.skinShade}"/>`;
  const torso = `<path d="M 62 420 C 70 352, 128 318, 200 318 C 272 318, 330 352, 338 420 Z" fill="${L.shirt}"/>`;
  const folds = `<path d="M 120 420 C 124 380, 136 356, 150 344" stroke="${L.shirtShade}" stroke-width="5" fill="none" stroke-linecap="round"/>
    <path d="M 280 420 C 276 380, 264 356, 250 344" stroke="${L.shirtShade}" stroke-width="5" fill="none" stroke-linecap="round"/>`;
  let detail;
  switch (L.outfit) {
    case 'shirt':
      detail = `<path d="M 166 322 L 200 362 L 234 322 L 222 318 L 200 342 L 178 318 Z" fill="${L.trim}"/>`;
      break;
    case 'tie':
      detail = `<path d="M 166 322 L 200 362 L 234 322 L 222 318 L 200 342 L 178 318 Z" fill="${L.trim}"/>
        <path d="M 192 344 L 208 344 L 214 404 L 200 418 L 186 404 Z" fill="${L.accent}"/>`;
      break;
    case 'hoodie':
      detail = `<path d="M 128 334 C 150 300 250 300 272 334 C 250 320 150 320 128 334 Z" fill="${L.shirtShade}"/>
        <path d="M 186 336 L 182 388 M 214 336 L 218 388" stroke="${L.trim}" stroke-width="4" stroke-linecap="round"/>`;
      break;
    case 'robe':
      detail = `<path d="M 160 324 L 236 420 L 256 420 L 186 320 Z" fill="${L.trim}"/>
        <path d="M 240 324 L 196 380" stroke="${L.trim}" stroke-width="10" stroke-linecap="round"/>`;
      break;
    case 'kasaya':
      detail = `<path d="M 160 324 L 236 420 L 256 420 L 186 320 Z" fill="${L.trim}"/>
        <path d="M 118 352 L 318 420 L 300 420 L 104 366 Z" fill="${L.accent}"/>`;
      break;
    case 'armor':
      detail = `<ellipse cx="108" cy="364" rx="44" ry="26" fill="${L.accent}" stroke="#0003" stroke-width="3"/>
        <ellipse cx="292" cy="364" rx="44" ry="26" fill="${L.accent}" stroke="#0003" stroke-width="3"/>
        <path d="M 160 340 Q 200 360 240 340 M 156 370 Q 200 392 244 370" stroke="${L.accent}" stroke-width="6" fill="none"/>`;
      break;
    default: // sweater
      detail = `<path d="M 160 324 Q 200 366 240 324 Q 200 344 160 324 Z" fill="${L.trim}"/>`;
  }
  const ex = (table) => (L.extras || []).map((k) => (table[k] ? table[k](L) : '')).join('');
  return `${ex(EXTRAS_BEHIND_BODY)}${neck}${torso}${folds}${detail}${ex(EXTRAS_BODY)}`;
}

function svgFor(L, id, mini) {
  const ex = (table) => (L.extras || []).map((k) => (table[k] ? table[k](L) : '')).join('');
  const my = L.mouthY;
  const beard = (L.extras || []).filter((k) => k.startsWith('beard') || k === 'stubble').map((k) => EXTRAS_FRONT[k](L)).join('');
  const front = (L.extras || []).filter((k) => !(k.startsWith('beard') || k === 'stubble' || k === 'mustache'))
    .map((k) => (EXTRAS_FRONT[k] ? EXTRAS_FRONT[k](L) : '')).join('');
  const mustache = (L.extras || []).includes('mustache') ? EXTRAS_FRONT.mustache(L) : '';
  const viewBox = mini ? '66 40 268 268' : '0 0 400 420';
  return `<svg viewBox="${viewBox}" preserveAspectRatio="xMidYMax meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs><clipPath id="mc${id}"><path class="clip" d=""/></clipPath></defs>
  <g class="body">${body(L)}</g>
  <g class="head">
    ${ex(EXTRAS_BACK)}${backHair(L)}${ears(L)}${face(L)}${frontHair(L)}
    ${L.ahoge ? `<path d="M 204 80 C 196 52 220 40 230 56" stroke="${L.hair}" stroke-width="7" fill="none" stroke-linecap="round"/>` : ''}
    ${['bob', 'long', 'bun'].includes(L.hairStyle) ? `<path d="M 150 104 C 170 92 196 88 214 92" stroke="#fff" stroke-width="6" fill="none" stroke-linecap="round" opacity="0.18"/>` : ''}
    ${brows(L)}${eyes(L)}${cheeks(L)}${nose(L)}${beard}
    <g class="mouthGroup">
      <path class="smile" d="M ${200 - 18} ${my} Q 200 ${my + 14} ${200 + 18} ${my}" stroke="${L.lips}" stroke-width="4.5" fill="none" stroke-linecap="round"/>
      <path class="mouth" d="" fill="${L.lips}"/>
      <ellipse class="tongue" cx="200" cy="${my + 10}" rx="10" ry="6" fill="#FF7A8C" clip-path="url(#mc${id})"/>
    </g>
    ${mustache}${front}
  </g>
  <g class="thinking" opacity="0">
    <circle cx="322" cy="96" r="6" fill="#fff" opacity="0.85"/>
    <circle cx="342" cy="72" r="8.5" fill="#fff" opacity="0.85"/>
    <circle cx="366" cy="44" r="11" fill="#fff" opacity="0.85"/>
  </g>
</svg>`;
}

// ------------------------------------------------------------------ animation

const live = new Set();
let ticking = false;
function tick(now) {
  for (const a of live) a.frame(now);
  if (live.size) requestAnimationFrame(tick);
  else ticking = false;
}

export class Avatar {
  constructor(container, { look = XIAOXING, getLevels = null, getMicLevel = null, mini = false } = {}) {
    this.container = container;
    this.getLevels = getLevels || (() => ({ rms: 0, bright: 0 }));
    this.getMicLevel = getMicLevel || (() => 0);
    this.mini = mini;
    this.id = ++uid;
    this.state = 'idle';
    this.s = {
      open: 0, bright: 0.4, blinkStart: -1, nextBlink: 1 + Math.random() * 3, gx: 0, gy: 0, tgx: 0, tgy: 0,
      nextSaccade: Math.random() * 2, browL: 0, browR: 0, tilt: 0, dy: 0, micActive: 0, think: 0, dead: 0,
    };
    this.t = Math.random() * 10; // desynchronise idle motion between avatars
    this.last = performance.now();
    this.setLook(look);
    live.add(this);
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(tick);
    }
  }

  setLook(look) {
    this.look = { ...DEFAULT_LOOK, ...(look || {}) };
    this.container.innerHTML = svgFor(this.look, this.id, this.mini);
    const q = (c) => this.container.querySelector('.' + c);
    this.el = {
      head: q('head'), body: q('body'), eyeL: q('eyeL'), eyeR: q('eyeR'), browL: q('browL'), browR: q('browR'),
      mouth: q('mouth'), smile: q('smile'), tongue: q('tongue'), clip: q('clip'), thinking: q('thinking'),
    };
  }

  setState(state) {
    this.state = state;
  }

  destroy() {
    live.delete(this);
  }

  frame(now) {
    if (!this.container.isConnected) {
      this.destroy();
      return;
    }
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.t += dt;
    const t = this.t;
    const s = this.s;
    const st = this.state;
    s.dead = approach(s.dead, st === 'dead' ? 1 : 0, 4, dt);

    // --- lip sync -------------------------------------------------------
    const lv = st === 'speaking' ? this.getLevels() : { rms: 0, bright: 0 };
    const openTarget = Math.pow(clamp((lv.rms - 0.005) / 0.08, 0, 1), 0.7);
    s.open = approach(s.open, openTarget, openTarget > s.open ? 38 : 16, dt);
    s.bright = approach(s.bright, clamp((lv.bright - 0.1) / 0.4, 0, 1), 10, dt);

    const mic = st === 'dead' ? 0 : this.getMicLevel();
    s.micActive = approach(s.micActive, mic > 0.02 ? 1 : 0, mic > 0.02 ? 8 : 2, dt);

    // --- blinking -------------------------------------------------------
    if (t >= s.nextBlink) {
      s.blinkStart = t;
      s.nextBlink = t + (Math.random() < 0.15 ? 0.32 : 2.4 + Math.random() * 3.6);
    }
    const bt = t - s.blinkStart;
    let blink = bt >= 0 && bt < 0.16 ? (bt < 0.07 ? bt / 0.07 : 1 - (bt - 0.07) / 0.09) : 0;
    blink = Math.max(blink, s.dead * 0.92);

    // --- gaze -----------------------------------------------------------
    if (st === 'thinking') {
      s.tgx = -7;
      s.tgy = -8;
    } else if (st === 'dead') {
      s.tgx = 0;
      s.tgy = 3;
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
    const alive = 1 - s.dead;
    const listeningNod = st !== 'speaking' ? s.micActive * Math.sin(t * 3.2) * 1.6 : 0;
    const tiltTarget = alive * (5 * s.think + Math.sin(t * 0.7) * 1.4 + (st === 'speaking' ? Math.sin(t * 2.1) * s.open * 3 : 0)) - 4 * s.dead;
    s.tilt = approach(s.tilt, tiltTarget, 6, dt);
    s.dy = approach(s.dy, -s.open * 3.5 + listeningNod + 6 * s.dead, 10, dt);
    const quiet = st === 'speaking' ? 0 : 1;
    s.browL = approach(s.browL, -s.open * 5 - 7 * s.think - 3 * s.micActive * quiet, 12, dt);
    s.browR = approach(s.browR, -s.open * 5 + 1.5 * s.think - 3 * s.micActive * quiet, 12, dt);

    // --- apply ----------------------------------------------------------
    const e = this.el;
    e.head.setAttribute('transform', `translate(0 ${s.dy.toFixed(2)}) rotate(${s.tilt.toFixed(2)} 200 300)`);
    const breathe = 1 + Math.sin(t * 1.6) * 0.008 * alive;
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
    const cx = 200, cy = this.look.mouthY;
    // Brighter sounds (i/e) -> wider mouth; darker sounds (o/u) -> rounder.
    const w = 26 + 20 * bright + 6 * open;
    const h = 4 + 30 * open * (1.05 - 0.35 * bright);
    const l = cx - w / 2, r = cx + w / 2;
    const d = `M ${l.toFixed(1)} ${cy} Q ${cx} ${(cy - h * 0.32).toFixed(1)} ${r.toFixed(1)} ${cy} ` +
      `Q ${cx} ${(cy + h * 1.3).toFixed(1)} ${l.toFixed(1)} ${cy} Z`;
    e.mouth.setAttribute('d', d);
    e.clip.setAttribute('d', d);
    e.tongue.setAttribute('opacity', '1');
    e.tongue.setAttribute('cy', (cy + h * 0.78).toFixed(1));
    e.tongue.setAttribute('rx', (w * 0.3).toFixed(1));
    e.tongue.setAttribute('ry', (h * 0.32).toFixed(1));
  }
}

// Fake lip sync from text when there's no audio (voice off): syllable-like bursts.
export function fakeLevels() {
  const t = performance.now() / 1000;
  const syllable = Math.max(0, Math.sin(t * 9) * Math.sin(t * 2.3 + 1));
  return { rms: 0.11 * syllable, bright: 0.3 + 0.3 * Math.sin(t * 3.1) };
}
