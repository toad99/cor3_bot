// cor3.gg : écoute du WebSocket du jeu (socket.io + msgpack) depuis la console du navigateur (F12)
// ou un outil d'automatisation (Claude in Chrome : javascript_tool). Colle tout le fichier, puis joue.
//   window.__cor3ws                      trames décodées {t, dir, room, action, data}
//   __cor3ws.summary()                   nombre de trames par « sens salle:action »
//   __cor3ws.last('market', 'get.jobs')  dernière trame d'une action
// Les clés qui ressemblent à un secret (token, jwt, password, auth…) et les chaînes au format JWT
// sont masquées AVANT stockage : rien de sensible ne sort de la page par ce biais.
// Le crochet s'installe au premier envoi du client (au plus ~25 s : réponse au ping engine.io).
(() => {
  if (window.__cor3ws) return 'déjà installé';
  const frames = [];
  const SECRET = /token|jwt|secret|passw|cookie|^auth(?!or)|authoriz|rkey|tkey|api.?key/i;
  const JWT = /eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}/g;
  const td = new TextDecoder();

  function decode(buf) {
    const v = new DataView(buf), u8 = new Uint8Array(buf);
    let p = 0;
    const str = n => { const s = td.decode(u8.subarray(p, p + n)); p += n; return s; };
    const bin = n => { const b = u8.slice(p, p + n); p += n; return b; };
    const arr = n => { const a = []; for (let i = 0; i < n; i++) a.push(read()); return a; };
    const map = n => { const o = {}; for (let i = 0; i < n; i++) { const k = read(); o[k] = read(); } return o; };
    const u = (size) => { const x = size === 1 ? u8[p] : size === 2 ? v.getUint16(p) : v.getUint32(p); p += size; return x; };
    function ext(n) {
      const type = v.getInt8(p); p += 1;
      const data = bin(n), dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
      if (type === -1 && n === 4) return new Date(dv.getUint32(0) * 1000);                       // timestamp 32
      if (type === -1 && n === 8) {                                                             // timestamp 64
        const hi = dv.getUint32(0), lo = dv.getUint32(4);
        return new Date(((hi & 3) * 2 ** 32 + lo) * 1000 + (hi >>> 2) / 1e6);
      }
      if (type === -1 && n === 12) return new Date(Number(dv.getBigInt64(4)) * 1000 + dv.getUint32(0) / 1e6);
      return {ext: type, length: n};
    }
    function read() {
      const b = u8[p++];
      if (b <= 0x7f) return b;                          // positive fixint
      if (b <= 0x8f) return map(b & 0x0f);              // fixmap
      if (b <= 0x9f) return arr(b & 0x0f);              // fixarray
      if (b <= 0xbf) return str(b & 0x1f);              // fixstr
      if (b >= 0xe0) return b - 0x100;                  // negative fixint
      let x;
      switch (b) {
        case 0xc0: return null;
        case 0xc2: return false;
        case 0xc3: return true;
        case 0xc4: return bin(u(1));
        case 0xc5: return bin(u(2));
        case 0xc6: return bin(u(4));
        case 0xc7: return ext(u(1));
        case 0xc8: return ext(u(2));
        case 0xc9: return ext(u(4));
        case 0xca: x = v.getFloat32(p); p += 4; return x;
        case 0xcb: x = v.getFloat64(p); p += 8; return x;
        case 0xcc: return u(1);
        case 0xcd: return u(2);
        case 0xce: return u(4);
        case 0xcf: x = Number(v.getBigUint64(p)); p += 8; return x;
        case 0xd0: x = v.getInt8(p); p += 1; return x;
        case 0xd1: x = v.getInt16(p); p += 2; return x;
        case 0xd2: x = v.getInt32(p); p += 4; return x;
        case 0xd3: x = Number(v.getBigInt64(p)); p += 8; return x;
        case 0xd4: return ext(1);
        case 0xd5: return ext(2);
        case 0xd6: return ext(4);
        case 0xd7: return ext(8);
        case 0xd8: return ext(16);
        case 0xd9: return str(u(1));
        case 0xda: return str(u(2));
        case 0xdb: return str(u(4));
        case 0xdc: return arr(u(2));
        case 0xdd: return arr(u(4));
        case 0xde: return map(u(2));
        case 0xdf: return map(u(4));
      }
      throw new Error('octet msgpack inconnu 0x' + b.toString(16));
    }
    return read();
  }

  const scrub = x => {
    if (Array.isArray(x)) return x.map(scrub);
    if (x instanceof Uint8Array) return `<${x.length} octets>`;
    if (x instanceof Date) return x.toISOString();
    if (x && typeof x === 'object') {
      return Object.fromEntries(Object.entries(x).map(([k, val]) => [k, SECRET.test(k) ? '<masqué>' : scrub(val)]));
    }
    return typeof x === 'string' ? x.replace(JWT, '<jwt masqué>') : x;
  };

  async function record(dir, raw) {
    let buf = raw;
    if (raw instanceof Blob) buf = await raw.arrayBuffer();
    else if (ArrayBuffer.isView(raw)) buf = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength);
    if (!(buf instanceof ArrayBuffer)) return;          // trames texte engine.io (ping « 2 », pong « 3 »)
    const t = new Date().toISOString();
    try {
      const msg = decode(buf), d = msg && msg.data;
      const room = Array.isArray(d) && typeof d[0] === 'string' ? d[0] : null;
      const body = Array.isArray(d) ? d[1] : d;
      const evt = body && typeof body === 'object' && body.event;
      frames.push({t, dir, room, action: evt ? evt.action : null, data: scrub(evt ? body.data : body)});
    } catch (e) {
      frames.push({t, dir, error: String(e)});
    }
    if (frames.length > 5000) frames.shift();
  }

  const hooked = new WeakSet();
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    if (/socket\.io/.test(this.url)) {
      if (!hooked.has(this)) {
        hooked.add(this);
        this.addEventListener('message', ev => record('in', ev.data));
      }
      record('out', data);
    }
    return send.call(this, data);
  };
  frames.decode = decode;
  frames.scrub = scrub;
  frames.last = (room, action) => [...frames].reverse().find(f => (!room || f.room === room) && (!action || f.action === action));
  frames.summary = () => frames.reduce((c, f) => { const k = `${f.dir} ${f.room}:${f.action}`; c[k] = (c[k] || 0) + 1; return c; }, {});
  window.__cor3ws = frames;
  return 'écoute installée : joue, puis lis window.__cor3ws';
})();
