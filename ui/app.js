/* ============================================================
   Waca — app.js
   UKDW's Personal Chatbot System
   Universitas Kristen Duta Wacana · Yogyakarta
   ============================================================ */

/* ── Config ──────────────────────────────────────────────────── */

/**
 * Base URL of the FastAPI backend.
 * Defaults to the same origin the page is served from.
 * In development, set this to "http://localhost:8000" if serving
 * the frontend separately from the backend.
 */
const API_URL = window.location.origin;

/**
 * Demo Mode — set to true to run the UI without a real backend.
 * Waca will use keyword-matched mock responses instead of calling
 * the FastAPI server. Useful for presentations or local UI testing.
 */
const DEMO_MODE = false;

/* ── Application State ───────────────────────────────────────── */
let conversationHistory = [];  // Accumulated turns sent to the backend for context
let isLoading           = false; // Prevents double-sends while a request is in flight
let rateLimitState      = { count: 0, remaining: null, limit: null, reset_at: null };

/* ── Startup ─────────────────────────────────────────────────── */
window.addEventListener('load', () => {
  checkHealth();
  checkRateLimit();
  document.getElementById('msgInput').focus();
});

/**
 * Ping GET /health on page load to verify backend connectivity.
 * Updates the header status indicator (green / orange / red dot).
 */
async function checkHealth() {
  if (DEMO_MODE) {
    document.getElementById('modeLabel').textContent  = 'Demo (mock)';
    document.getElementById('statusLabel').textContent = 'Demo Mode';
    return;
  }
  try {
    const r = await fetch(`${API_URL}/health`);
    setStatus(r.ok ? 'connected' : 'error');
  } catch {
    setStatus('offline');
  }
}

/**
 * Fetch current rate limit status from GET /rate-limit-status.
 * Updates the quota counter in the UI without using up a question.
 */
async function checkRateLimit() {
  if (DEMO_MODE) return;
  try {
    const r = await fetch(`${API_URL}/rate-limit-status`);
    if (r.ok) {
      const data = await r.json();
      updateQuotaUI(data);
    }
  } catch {
    // Quota display is non-critical — silently skip on error
  }
}

/**
 * Update rate limit info everywhere in the UI.
 * @param {{ count, remaining, limit, reset_at, allowed }} data
 */
function updateQuotaUI(data) {
  rateLimitState = data;

  const remaining = data.remaining ?? (data.limit - data.count);
  const limit     = data.limit;
  const pct       = limit > 0 ? Math.round((remaining / limit) * 100) : 100;

  const stateClass = (
    pct <= 0  ? 'quota-empty'   :
    pct <= 25 ? 'quota-warning' :
    pct <= 50 ? 'quota-low'     :
                'quota-ok'
  );

  // ── Angka tersisa (tebal, berwarna) ───────────────────────────────────────
  const countEl = document.getElementById('quotaCount');
  if (countEl) countEl.textContent = remaining;

  const totalEl = document.getElementById('quotaTotal');
  if (totalEl) totalEl.textContent = limit;

  // ── Badge wrapper — hanya ubah class warna untuk accent pada angka ────────
  const badge = document.getElementById('quotaBadge');
  if (badge) badge.className = `quota-badge ${stateClass}`;

  // ── Progress bar ──────────────────────────────────────────────────────────
  const bar = document.getElementById('quotaBar');
  if (bar) {
    bar.style.width = `${Math.max(pct, 0)}%`;
    bar.className   = `quota-fill ${stateClass}`;
  }

  // ── Tooltip di wrapper: "X dari Y sudah digunakan. Reset: ..." ───────────
  const wrap = document.getElementById('quotaWrap');
  if (wrap) wrap.title = `${data.count ?? 0} dari ${limit} pertanyaan digunakan hari ini. Reset: ${data.reset_at ?? '-'}`;

  // ── Disable input jika kuota habis ────────────────────────────────────────
  const input   = document.getElementById('msgInput');
  const sendBtn = document.getElementById('sendBtn');
  if (remaining <= 0) {
    if (input)   { input.disabled = true; input.placeholder = `Kuota habis — kembali pada ${data.reset_at}`; }
    if (sendBtn) sendBtn.disabled = true;
  } else {
    if (input)   { input.disabled = false; input.placeholder = 'Ketik pertanyaan Anda di sini…'; }
    if (sendBtn && !isLoading) sendBtn.disabled = false;
  }
}

/**
 * Update the header connection indicator.
 * @param {'connected'|'loading'|'error'|'offline'} state
 */
function setStatus(state) {
  const dot   = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');

  if (state === 'connected') {
    dot.style.background  = '#5dba6c';
    dot.style.animation   = 'pulse 2s infinite';
    label.textContent     = 'Connected';
  } else if (state === 'loading') {
    dot.style.background  = '#f09a50';
    dot.style.animation   = 'pulse 0.6s infinite';
    label.textContent     = 'Memproses…';
  } else {
    dot.style.background  = '#e05555';
    dot.style.animation   = 'none';
    label.textContent     = 'Offline — Demo Mode';
    const modeLabel = document.getElementById('modeLabel');
    if (modeLabel) modeLabel.textContent = 'Demo (offline)';
  }
}

/* ── Input Helpers ───────────────────────────────────────────── */

/**
 * Handle keyboard shortcuts inside the message textarea.
 * Enter → send; Shift+Enter → insert newline.
 * @param {KeyboardEvent} e
 */
function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

/**
 * Auto-expand the textarea height as the user types, capped at 120px.
 * @param {HTMLTextAreaElement} el
 */
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

/**
 * Populate the textarea with a sidebar suggestion and immediately send it.
 * @param {string} msg
 */
function sendSuggestion(msg) {
  const input   = document.getElementById('msgInput');
  input.value   = msg;
  autoResize(input);
  sendMessage();
}

/* ── Send Message ────────────────────────────────────────────── */

/**
 * Main dispatch function — triggered by Enter key or the send button.
 * Full pipeline:
 *   1. Read and validate the textarea value.
 *   2. Render the user's bubble immediately.
 *   3. Show the animated typing indicator.
 *   4. Call the API (or mock).
 *   5. Remove the typing indicator.
 *   6. Render the bot's bubble with chips and SQL block.
 *   7. Update the right-panel pipeline visualisation.
 */
async function sendMessage() {
  if (isLoading) return;

  const input = document.getElementById('msgInput');
  const msg   = input.value.trim();
  if (!msg) return;

  // Clear input immediately for responsive feel
  input.value        = '';
  input.style.height = 'auto';

  // Remove the welcome screen on first message
  const welcome = document.getElementById('welcomeScreen');
  if (welcome) welcome.remove();

  appendMessage('user', msg);
  conversationHistory.push({ role: 'user', content: msg });

  isLoading = true;
  document.getElementById('sendBtn').disabled = true;
  setStatus('loading');
  resetPipeline('idle');

  const typingId = showTyping();

  try {
    const result = DEMO_MODE
      ? await mockResponseWithAnimation(msg, typingId)
      : await callAPIStream(msg, typingId);

    conversationHistory.push({ role: 'assistant', content: result.reply });
    setStatus('connected');
    if (result.rate_limit) updateQuotaUI(result.rate_limit);

  } catch (err) {
    removeTyping(typingId);   // Bersihkan "..." jika error sebelum token pertama
    if (err.isRateLimit) {
      appendRateLimitMessage(err.rateLimitData);
      updateQuotaUI({ ...err.rateLimitData, allowed: false });
      resetPipeline('error');
    } else {
      appendBotMessage({
        reply: `⚠️ ${err.message || 'Koneksi gagal. Pastikan server Waca sedang berjalan.'}`,
        intent: 'error',
        entities: {},
        sql_query: null,
        raw_data: [],
        pipeline_steps: [{ stage: 'ERROR', status: 'error', detail: err.message }]
      });
      setStatus('offline');
      resetPipeline('error');
    }
  } finally {
    isLoading = false;
    document.getElementById('sendBtn').disabled = false;
  }
}

/**
 * POST the user message to Waca's FastAPI /chat endpoint.
 * Sends the last 10 conversation turns as history for multi-turn context.
 * @param {string} msg
 * @returns {Promise<Object>} Parsed JSON response.
 */
async function callAPI(msg) {
  const response = await fetch(`${API_URL}/chat`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message:    msg,
      history:    conversationHistory.slice(-10),
      session_id: 'waca-web-session'
    })
  });

  if (response.status === 429) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail || {};
    const rlErr  = new Error(typeof detail === 'string' ? detail : detail.message || 'Kuota habis');
    rlErr.isRateLimit    = true;
    rlErr.rateLimitData  = {
      count:     detail.count     ?? 0,
      remaining: detail.remaining ?? 0,
      limit:     detail.limit     ?? 0,
      reset_at:  detail.reset_at  ?? '',
      allowed:   false,
    };
    throw rlErr;
  }

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

/* ── Rate Limit Message ──────────────────────────────────────── */

/**
 * Render a stylised rate-limit-exceeded card in the chat.
 * @param {{ limit, count, remaining, reset_at }} data
 */
function appendRateLimitMessage(data) {
  const messages = document.getElementById('messages');

  const card = document.createElement('div');
  card.className = 'msg bot rate-limit-card';
  card.innerHTML = `
    <div class="rl-icon">🚫</div>
    <div class="rl-body">
      <strong>Batas pertanyaan harian tercapai</strong>
      <p>Anda telah menggunakan <strong>${data.count}/${data.limit}</strong> pertanyaan hari ini.</p>
      <p class="rl-reset">🕛 Kuota akan direset pada <strong>${data.reset_at}</strong></p>
    </div>
  `;
  messages.appendChild(card);
  card.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

/* ── Render Messages ─────────────────────────────────────────── */

/**
 * Append a plain text bubble (user or bot) without chips or SQL block.
 * Used for the user's messages and error messages.
 * @param {'user'|'bot'} role
 * @param {string} text
 */
function appendMessage(role, text) {
  const messages = document.getElementById('messages');

  const div         = document.createElement('div');
  div.className     = `msg ${role}`;

  const avatar      = document.createElement('div');
  avatar.className  = 'msg-avatar';
  avatar.textContent = role === 'user' ? 'YOU' : '🎓';

  const body        = document.createElement('div');
  body.className    = 'msg-body';

  const bubble      = document.createElement('div');
  bubble.className  = 'bubble';
  bubble.innerHTML  = formatText(text);

  body.appendChild(bubble);
  if (role === 'user') { div.appendChild(body); div.appendChild(avatar); }
  else                 { div.appendChild(avatar); div.appendChild(body); }

  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

/**
 * Append a full bot message: bubble + pipeline stage chips + optional SQL block.
 * @param {Object} result - The ChatResponse object from the API or mock.
 */
function appendBotMessage(result) {
  const messages    = document.getElementById('messages');

  const div         = document.createElement('div');
  div.className     = 'msg bot';

  const avatar      = document.createElement('div');
  avatar.className  = 'msg-avatar';
  avatar.textContent = '🎓';

  const body        = document.createElement('div');
  body.className    = 'msg-body';

  // Main reply bubble
  const bubble      = document.createElement('div');
  bubble.className  = 'bubble';
  bubble.innerHTML  = formatText(result.reply);
  body.appendChild(bubble);

  // Pipeline stage indicator chips
  const chips       = document.createElement('div');
  chips.className   = 'pipeline-chips';

  if (result.intent && result.intent !== 'error') {
    chips.appendChild(makeChip('m1', `M1 · ${result.intent}`));
  }
  if (result.sql_query) {
    chips.appendChild(makeChip('sql', `SQL · ${result.raw_data?.length ?? 0} rows`));
  }
  const resOk = result.pipeline_steps?.find(
    s => s.stage === 'RESPONSE_LAYER' && s.status === 'success'
  );
  if (resOk) chips.appendChild(makeChip('res', 'RES · ok'));
  if (result.pipeline_steps?.some(s => s.status === 'error')) {
    chips.appendChild(makeChip('err', 'Error'));
  }

  body.appendChild(chips);

  // Collapsible SQL block (click header to expand/collapse)
  if (result.sql_query) {
    const sqlBlock      = document.createElement('div');
    sqlBlock.className  = 'sql-block';
    sqlBlock.innerHTML  = `
      <div class="sql-block-header" onclick="this.parentElement.classList.toggle('open')">
        <span>🗃</span>
        <span>SQL Query</span>
        <span class="sql-toggle"></span>
      </div>
      <div class="sql-code">${escHtml(formatSQL(result.sql_query))}</div>
    `;
    body.appendChild(sqlBlock);
  }

  div.appendChild(avatar);
  div.appendChild(body);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

/**
 * Create a small coloured pipeline stage chip element.
 * @param {'m1'|'sql'|'res'|'err'} type
 * @param {string} label
 * @returns {HTMLElement}
 */
function makeChip(type, label) {
  const chip      = document.createElement('div');
  chip.className  = `chip ${type}`;
  chip.innerHTML  = `<div class="chip-dot"></div>${escHtml(label)}`;
  return chip;
}

/* ── Typing Indicator ────────────────────────────────────────── */

/**
 * Show an animated three-dot typing indicator while Waca is processing.
 * @returns {string} The element's ID so it can be removed later.
 */
function showTyping() {
  const messages    = document.getElementById('messages');

  const div         = document.createElement('div');
  div.className     = 'msg bot';
  div.id            = 'typing-' + Date.now();

  const avatar      = document.createElement('div');
  avatar.className  = 'msg-avatar';
  avatar.textContent = '🎓';

  const body        = document.createElement('div');
  body.className    = 'msg-body';

  const indicator   = document.createElement('div');
  indicator.className = 'typing-indicator active';
  indicator.innerHTML = `
    <div class="typing-dot"></div>
    <div class="typing-dot"></div>
    <div class="typing-dot"></div>
  `;

  body.appendChild(indicator);
  div.appendChild(avatar);
  div.appendChild(body);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;

  return div.id;
}

/**
 * Remove the typing indicator once the response has arrived.
 * @param {string} id - The typing indicator element ID.
 */
function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

/* ── Pipeline Panel ──────────────────────────────────────────── */

/**
 * Reset all pipeline stage indicators to idle or loading state.
 * Called before each new request so the panel resets visually.
 * @param {'idle'|'loading'|'error'} state
 */
function resetPipeline(state) {
  ['m1', 'sql', 'res'].forEach(key => {
    const num  = document.getElementById(`num-${key}`);
    const step = document.getElementById(`step-${key}`);
    const conn = document.getElementById(`conn-${key}`);
    if (!num) return;

    if (state === 'loading') {
      // Legacy: semua stage loading serentak (dipakai saat error reset)
      num.className  = 'pipe-num loading';
      if (step) step.className = 'pipe-step-header';
      if (conn) conn.className = 'pipe-connector';
    } else {
      // idle / error: reset ke state default (tidak ada animasi loading)
      num.className  = 'pipe-num';
      if (step) step.className = 'pipe-step-header';
      if (conn) conn.className = 'pipe-connector';
    }
  });
  ['intentDetail', 'sqlDetail', 'dataDetail'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
}

/**
 * Update the right pipeline panel after a successful API response.
 * Marks each stage as success/error and populates the detail blocks.
 * @param {Object} result - ChatResponse object.
 */
function updatePipelinePanel(result) {
  const steps = result.pipeline_steps || [];

  steps.forEach(step => {
    const keyMap = {
      M1_ORCHESTRATION: 'm1',
      SQL_RETRIEVAL:    'sql',
      RESPONSE_LAYER:   'res'
    };
    const key = keyMap[step.stage];
    if (!key) return;

    const num    = document.getElementById(`num-${key}`);
    const stepEl = document.getElementById(`step-${key}`);
    const conn   = document.getElementById(`conn-${key}`);
    if (!num) return;

    if (step.status === 'success') {
      num.className    = 'pipe-num success';
      if (stepEl) stepEl.className = 'pipe-step-header success';
      if (conn)   conn.className   = 'pipe-connector active';
    } else if (step.status === 'error') {
      num.className    = 'pipe-num error';
      if (stepEl) stepEl.className = 'pipe-step-header error';
    }
  });

  // Intent & entities detail block
  if (result.intent) {
    document.getElementById('intentDetail').style.display = '';
    let html = `
      <div class="key-val">
        <span class="kv-key">intent</span>
        <span class="kv-val" style="color:#5dba6c">${escHtml(result.intent)}</span>
      </div>
    `;
    if (result.entities && Object.keys(result.entities).length > 0) {
      html += `<div style="margin-top:6px;color:var(--text-muted);font-size:10px;letter-spacing:0.5px">ENTITIES</div>`;
      for (const [k, v] of Object.entries(result.entities)) {
        html += `
          <div class="key-val">
            <span class="kv-key">${escHtml(k)}</span>
            <span class="kv-val">${escHtml(String(v))}</span>
          </div>`;
      }
    } else {
      html += `<div class="key-val"><span class="kv-key">entities</span><span class="kv-val" style="color:var(--text-muted)">none</span></div>`;
    }
    document.getElementById('intentBody').innerHTML = html;
  }

  // SQL query detail block
  if (result.sql_query) {
    document.getElementById('sqlDetail').style.display = '';
    document.getElementById('sqlBody').textContent     = formatSQL(result.sql_query);
  }

  // Retrieved data row count badge
  if (result.raw_data && result.raw_data.length > 0) {
    document.getElementById('dataDetail').style.display = '';
    const count = result.raw_data.length;
    document.getElementById('dataBody').innerHTML =
      `<span class="rows-badge">${count} row${count !== 1 ? 's' : ''} returned</span>`;
  }
}

/* ── LaTeX → Unicode Converter ───────────────────────────────── */

/**
 * Konversi notasi LaTeX yang umum dihasilkan LLM menjadi karakter Unicode.
 *
 * Menangani tiga format sekaligus:
 *   1. $$\command$$   — display math (blok)
 *   2. $\command$     — inline math
 *   3. \command       — bare command tanpa dollar sign
 *
 * Perintah yang tidak dikenali dibiarkan apa adanya.
 *
 * @param {string} text - Teks mentah dari LLM.
 * @returns {string}    - Teks dengan notasi LaTeX sudah dikonversi ke Unicode.
 */
function convertLatex(text) {
  if (!text) return text;

  // ── Tabel pemetaan perintah LaTeX → Unicode ─────────────────────────────
  const LATEX_MAP = {
    // ── Panah ───────────────────────────────────────────────────────────────
    rightarrow:       '→',   to:              '→',
    leftarrow:        '←',   gets:            '←',
    leftrightarrow:   '↔',
    Rightarrow:       '⇒',
    Leftarrow:        '⇐',
    Leftrightarrow:   '⇔',
    uparrow:          '↑',
    downarrow:        '↓',
    nearrow:          '↗',
    searrow:          '↘',
    swarrow:          '↙',
    nwarrow:          '↖',
    mapsto:           '↦',

    // ── Perbandingan ─────────────────────────────────────────────────────────
    geq:    '≥',  ge:    '≥',
    leq:    '≤',  le:    '≤',
    neq:    '≠',  ne:    '≠',
    approx: '≈',
    equiv:  '≡',
    sim:    '∼',
    simeq:  '≃',
    cong:   '≅',
    propto: '∝',

    // ── Operator aritmatika ───────────────────────────────────────────────────
    pm:     '±',
    mp:     '∓',
    times:  '×',
    div:    '÷',
    cdot:   '·',
    ast:    '*',
    circ:   '∘',
    oplus:  '⊕',
    otimes: '⊗',
    sqrt:   '√',

    // ── Kalkulus & analisis ───────────────────────────────────────────────────
    infty:   '∞',
    partial: '∂',
    nabla:   '∇',
    int:     '∫',
    oint:    '∮',
    sum:     'Σ',
    prod:    'Π',
    coprod:  '∐',

    // ── Huruf Yunani kecil ────────────────────────────────────────────────────
    alpha:   'α',  beta:    'β',  gamma:   'γ',
    delta:   'δ',  epsilon: 'ε',  varepsilon: 'ε',
    zeta:    'ζ',  eta:     'η',  theta:   'θ',
    vartheta:'ϑ',  iota:    'ι',  kappa:   'κ',
    lambda:  'λ',  mu:      'μ',  nu:      'ν',
    xi:      'ξ',  pi:      'π',  varpi:   'ϖ',
    rho:     'ρ',  varrho:  'ϱ',  sigma:   'σ',
    varsigma:'ς',  tau:     'τ',  upsilon: 'υ',
    phi:     'φ',  varphi:  'φ',  chi:     'χ',
    psi:     'ψ',  omega:   'ω',

    // ── Huruf Yunani kapital ──────────────────────────────────────────────────
    Gamma:   'Γ',  Delta:   'Δ',  Theta:   'Θ',
    Lambda:  'Λ',  Xi:      'Ξ',  Pi:      'Π',
    Sigma:   'Σ',  Upsilon: 'Υ',  Phi:     'Φ',
    Psi:     'Ψ',  Omega:   'Ω',

    // ── Teori himpunan & logika ───────────────────────────────────────────────
    in:       '∈',  notin:    '∉',
    ni:       '∋',
    subset:   '⊂',  supset:   '⊃',
    subseteq: '⊆',  supseteq: '⊇',
    cup:      '∪',  cap:      '∩',
    setminus: '∖',  emptyset: '∅',  varnothing: '∅',
    forall:   '∀',  exists:   '∃',  nexists: '∄',
    neg:      '¬',  lnot:     '¬',
    land:     '∧',  lor:      '∨',
    wedge:    '∧',  vee:      '∨',
    oplus:    '⊕',  models:   '⊨',
    vdash:    '⊢',

    // ── Tanda baca & spesial ─────────────────────────────────────────────────
    ldots:     '…',  cdots:     '⋯',  dots:  '…',  vdots: '⋮',  ddots: '⋱',
    bullet:    '•',
    star:      '★',
    dagger:    '†',
    ddagger:   '‡',
    checkmark: '✓',
    times:     '×',
    langle:    '⟨',  rangle:    '⟩',
    lfloor:    '⌊',  rfloor:    '⌋',
    lceil:     '⌈',  rceil:     '⌉',
    vert:      '|',  Vert:      '‖',

    // ── Aksara lain ───────────────────────────────────────────────────────────
    ell:    'ℓ',  Re:    'ℜ',  Im:    'ℑ',
    hbar:   'ℏ',  aleph: 'ℵ',  wp:    '℘',
  };

  // Fungsi pengganti: ubah \cmd ke simbol, atau biarkan jika tidak dikenal
  function replaceCmd(_, cmd) {
    return LATEX_MAP[cmd] !== undefined ? LATEX_MAP[cmd] : `\\${cmd}`;
  }

  // 1. $$...$$ (display math) — hapus delimeter, konversi perintah di dalamnya
  text = text.replace(/\$\$([^$]+?)\$\$/gs, (_, inner) =>
    inner.trim().replace(/\\([A-Za-z]+)/g, replaceCmd)
  );

  // 2. $...$ (inline math) — hapus delimeter, konversi perintah
  text = text.replace(/\$([^$\n]+?)\$/g, (_, inner) =>
    inner.trim().replace(/\\([A-Za-z]+)/g, replaceCmd)
  );

  // 3. Bare \command (tanpa dollar sign) — konversi perintah yang berdiri sendiri
  //    Hanya cocokkan \cmd yang diikuti non-huruf (spasi, tanda baca, akhir baris)
  //    agar tidak merusak path file seperti C:\Users atau escape string
  text = text.replace(/\\([A-Za-z]+)(?=[^A-Za-z]|$)/g, replaceCmd);

  return text;
}



/**
 * Render bot reply text (Markdown) to safe HTML.
 *
 * Supported block elements:
 *   ###/##/# Heading   → <h3> / <h2> / <h1>
 *   ---                → <hr>
 *   | table |          → <table>
 *   - item / * item    → <ul><li>
 *   1. item            → <ol><li>
 *   > blockquote       → <blockquote>
 *   ```code block```   → <pre><code>
 *   blank line         → paragraph break
 *
 * Inline (applied inside block text):
 *   **bold**           → <strong>
 *   *italic*           → <em>
 *   `code`             → <code>
 *
 * XSS-safe: all raw text is entity-escaped before markup is applied.
 *
 * @param {string} text - Raw LLM reply string.
 * @returns {string} HTML string safe for innerHTML.
 */
function formatText(text) {
  if (!text) return '';

  // ── Konversi notasi LaTeX → simbol Unicode ────────────────────────────────
  // Dipanggil SEBELUM parsing Markdown agar notasi seperti $\rightarrow$,
  // \geq, $\times$ dst. sudah menjadi karakter Unicode sebelum di-escape HTML.
  text = convertLatex(text);

  // ── Inline formatter (applied to individual text segments) ────────────────
  function inlineFormat(raw) {
    return esc(raw)
      // **bold** and __bold__
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/__(.+?)__/g, '<strong>$1</strong>')
      // *italic* and _italic_  (not inside word boundaries to avoid false hits)
      .replace(/\*([^*\n]+?)\*/g, '<em>$1</em>')
      .replace(/_([^_\n]+?)_/g, '<em>$1</em>')
      // `inline code`
      .replace(/`([^`\n]+?)`/g, '<code class="inline-code">$1</code>');
  }

  // ── HTML entity escaper ───────────────────────────────────────────────────
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Split into lines and parse blocks ────────────────────────────────────
  const lines  = text.split('\n');
  const blocks = [];   // Each block: { type, lines[] }

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block  ```
    if (/^```/.test(line)) {
      const lang  = line.slice(3).trim();
      const body  = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        body.push(lines[i]);
        i++;
      }
      blocks.push({ type: 'code', lang, body });
      i++; // skip closing ```
      continue;
    }

    // Heading
    const hMatch = line.match(/^(#{1,3})\s+(.+)/);
    if (hMatch) {
      blocks.push({ type: 'heading', level: hMatch[1].length, text: hMatch[2] });
      i++; continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push({ type: 'hr' });
      i++; continue;
    }

    // Table row (starts and ends with |, or at least contains |...|)
    if (/^\|.+\|/.test(line.trim())) {
      const tableLines = [];
      while (i < lines.length && /^\|.+\|/.test(lines[i].trim())) {
        tableLines.push(lines[i]);
        i++;
      }
      blocks.push({ type: 'table', lines: tableLines });
      continue;
    }

    // Unordered list item  (-, *, or •)
    if (/^(\s*)([-*•])\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^(\s*)([-*•])\s+/.test(lines[i])) {
        const indent = lines[i].match(/^(\s*)/)[1].length;
        const content = lines[i].replace(/^\s*[-*•]\s+/, '');
        items.push({ indent, content });
        i++;
      }
      blocks.push({ type: 'ul', items });
      continue;
    }

    // Ordered list item  (1. 2. etc.)
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      blocks.push({ type: 'ol', items });
      continue;
    }

    // Blockquote
    if (/^>\s?/.test(line)) {
      const parts = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        parts.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      blocks.push({ type: 'blockquote', lines: parts });
      continue;
    }

    // Blank line → paragraph separator
    if (line.trim() === '') {
      blocks.push({ type: 'blank' });
      i++; continue;
    }

    // Plain paragraph text — accumulate consecutive non-special lines
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^#{1,3}\s/.test(lines[i]) &&
      !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim()) &&
      !/^\|.+\|/.test(lines[i].trim()) &&
      !/^(\s*)([-*•])\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s/.test(lines[i]) &&
      !/^>\s?/.test(lines[i]) &&
      !/^```/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length) {
      blocks.push({ type: 'para', lines: paraLines });
    }
  }

  // ── Render blocks to HTML ─────────────────────────────────────────────────
  const parts = [];

  for (const block of blocks) {
    switch (block.type) {

      case 'heading': {
        const tag = `h${block.level + 2}`;  // ### → h5, ## → h4, # → h3 (visual sizing)
        parts.push(`<${tag} class="md-heading md-h${block.level}">${inlineFormat(block.text)}</${tag}>`);
        break;
      }

      case 'hr':
        parts.push('<hr class="md-hr">');
        break;

      case 'code': {
        const langAttr = block.lang ? ` data-lang="${esc(block.lang)}"` : '';
        const codeHtml = block.body.map(l => esc(l)).join('\n');
        parts.push(`<pre class="md-pre"${langAttr}><code>${codeHtml}</code></pre>`);
        break;
      }

      case 'table': {
        const rows = block.lines;
        // Separator row is a row that matches /^[\s|:-]+$/
        const isSep = r => /^[\s|:\-]+$/.test(r);

        // Collect header row (first non-sep row), separator, then body rows
        let header = null;
        const body = [];
        let foundSep = false;
        for (const row of rows) {
          if (isSep(row)) { foundSep = true; continue; }
          const cells = row.split('|').slice(1, -1).map(c => c.trim());
          if (!header && !foundSep) { header = cells; }
          else                      { body.push(cells); }
        }

        let html = '<div class="md-table-wrap"><table class="md-table">';
        if (header) {
          html += '<thead><tr>' + header.map(c => `<th>${inlineFormat(c)}</th>`).join('') + '</tr></thead>';
        }
        if (body.length) {
          html += '<tbody>' + body.map(r =>
            '<tr>' + r.map(c => `<td>${inlineFormat(c)}</td>`).join('') + '</tr>'
          ).join('') + '</tbody>';
        }
        html += '</table></div>';
        parts.push(html);
        break;
      }

      case 'ul': {
        // Support two indent levels
        let html = '<ul class="md-ul">';
        let prevIndent = 0;
        for (const item of block.items) {
          if (item.indent > prevIndent) html += '<ul class="md-ul md-ul-nested">';
          if (item.indent < prevIndent) html += '</ul>';
          html += `<li>${inlineFormat(item.content)}</li>`;
          prevIndent = item.indent;
        }
        if (prevIndent > 0) html += '</ul>';
        html += '</ul>';
        parts.push(html);
        break;
      }

      case 'ol': {
        const items = block.items.map(it => `<li>${inlineFormat(it)}</li>`).join('');
        parts.push(`<ol class="md-ol">${items}</ol>`);
        break;
      }

      case 'blockquote': {
        const inner = block.lines.map(l => inlineFormat(l)).join('<br>');
        parts.push(`<blockquote class="md-blockquote">${inner}</blockquote>`);
        break;
      }

      case 'para': {
        const content = block.lines.map(l => inlineFormat(l)).join('<br>');
        parts.push(`<p class="md-para">${content}</p>`);
        break;
      }

      case 'blank':
        // Blank lines between blocks are handled by CSS margins; skip
        break;
    }
  }

  return parts.join('\n');
}

/**
 * Pretty-print a SQL string by adding newlines before keywords.
 * Normalises whitespace first to handle multi-space/tab indentation.
 * @param {string} sql
 * @returns {string}
 */
function formatSQL(sql) {
  return sql
    .replace(/\s+/g, ' ')
    .replace(
      /(SELECT|FROM|WHERE|ORDER BY|LIMIT|AND|OR|UNION ALL|INSERT|UPDATE|DELETE)/gi,
      '\n$1'
    )
    .trim();
}

/**
 * Escape HTML special characters to prevent XSS when inserting
 * API-returned strings directly into innerHTML.
 * @param {string} str
 * @returns {string}
 */
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ── Streaming API ───────────────────────────────────────────── */

/**
 * Panggil POST /chat/stream dan proses event SSE secara real-time.
 *
 * Alur:
 *  1. Buka koneksi SSE ke /chat/stream.
 *  2. Setiap event stage_start  → animasikan stage pipeline sebagai "loading".
 *  3. Setiap event stage_done   → tandai stage berhasil/gagal + isi detail panel.
 *  4. Event pertama "token"     → hapus typing indicator "...", buat bubble streaming.
 *  5. Token berikutnya          → tambahkan teks ke bubble (efek typewriter).
 *  6. Event "done"              → finalisasi bubble (tambah chips + SQL block).
 *
 * @param {string} msg        - Pesan pengguna.
 * @param {string} typingId   - ID elemen typing indicator ("...") untuk dihapus.
 * @returns {Promise<Object>} - Objek hasil lengkap (sama dengan /chat response).
 */
async function callAPIStream(msg, typingId) {
  const response = await fetch(`${API_URL}/chat/stream`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message:    msg,
      history:    conversationHistory.slice(-10),
      session_id: 'waca-web-session'
    })
  });

  // Rate limit dan error HTTP terjadi SEBELUM stream dimulai
  if (response.status === 429) {
    const body   = await response.json().catch(() => ({}));
    const detail = body.detail || {};
    const rlErr  = new Error(typeof detail === 'string' ? detail : detail.message || 'Kuota habis');
    rlErr.isRateLimit   = true;
    rlErr.rateLimitData = {
      count:     detail.count     ?? 0,
      remaining: detail.remaining ?? 0,
      limit:     detail.limit     ?? 0,
      reset_at:  detail.reset_at  ?? '',
      allowed:   false,
    };
    throw rlErr;
  }
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }

  // ── Baca stream SSE ──────────────────────────────────────────────────────────
  const reader  = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer    = '';

  let streamWrapper   = null;   // Elemen div.msg.bot untuk bubble streaming
  let streamTextEl    = null;   // Elemen tempat teks diupdate secara bertahap
  let accumulatedText = '';     // Akumulasi seluruh token yang diterima
  let typingRemoved   = false;  // Flag agar removeTyping hanya dipanggil sekali
  let result          = {};     // Objek hasil final dari event "done"

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // Decode chunk dan tambahkan ke buffer
    buffer += decoder.decode(value, { stream: true });

    // Pisahkan event-event lengkap (diakhiri \n\n)
    const events = buffer.split('\n\n');
    buffer = events.pop();  // Simpan bagian yang belum lengkap

    for (const eventText of events) {
      // Cari baris "data: ..."
      const dataMatch = eventText.match(/^data: (.+)$/m);
      if (!dataMatch) continue;

      let data;
      try { data = JSON.parse(dataMatch[1]); } catch { continue; }

      switch (data.type) {

        case 'stage_start':
          // Stage mulai diproses → tampilkan animasi loading di pipeline panel
          setPipelineStageLoading(data.stage);
          break;

        case 'stage_done':
          // Stage selesai → tandai dan isi detail panel
          setPipelineStageDone(data.stage, data.status, data);
          break;

        case 'token':
          // Token pertama tiba → ganti typing indicator dengan bubble streaming
          if (!typingRemoved) {
            removeTyping(typingId);
            typingRemoved = true;
            const created = createStreamingBubble();
            streamWrapper = created.wrapper;
            streamTextEl  = created.textEl;
          }
          // Tambahkan token ke teks dan render ulang dengan kursor berkedip
          accumulatedText += data.text;
          streamTextEl.innerHTML = formatText(accumulatedText) +
            '<span class="stream-cursor" aria-hidden="true"></span>';
          document.getElementById('messages').scrollTop =
            document.getElementById('messages').scrollHeight;
          break;

        case 'done':
          // Semua token diterima → finalisasi bubble
          result = data;
          if (streamWrapper && streamTextEl) {
            // Hapus kursor berkedip, render teks final
            streamTextEl.innerHTML = formatText(data.reply);
            finalizeStreamingBubble(streamWrapper, data);
          } else {
            // Edge case: tidak ada token sama sekali (reply kosong atau error cepat)
            if (!typingRemoved) { removeTyping(typingId); typingRemoved = true; }
            appendBotMessage(data);
            updatePipelinePanel(data);
          }
          break;

        case 'error':
          throw new Error(data.message || 'Stream error dari server.');
      }
    }
  }

  return result;
}

/**
 * Tandai satu stage pipeline sebagai "sedang berjalan" (animasi loading).
 * Dipanggil saat event SSE stage_start diterima.
 * @param {'M1_ORCHESTRATION'|'SQL_RETRIEVAL'|'RESPONSE_LAYER'} stage
 */
function setPipelineStageLoading(stage) {
  const keyMap = { M1_ORCHESTRATION: 'm1', SQL_RETRIEVAL: 'sql', RESPONSE_LAYER: 'res' };
  const key    = keyMap[stage];
  if (!key) return;

  const num = document.getElementById(`num-${key}`);
  if (num) num.className = 'pipe-num loading';
}

/**
 * Tandai satu stage pipeline sebagai selesai (success/error) dan isi detail panel.
 * Dipanggil saat event SSE stage_done diterima.
 *
 * @param {'M1_ORCHESTRATION'|'SQL_RETRIEVAL'|'RESPONSE_LAYER'} stage
 * @param {'success'|'error'} status
 * @param {Object} data  - Payload event (berisi intent, entities, sql_query, dll.)
 */
function setPipelineStageDone(stage, status, data) {
  const keyMap = { M1_ORCHESTRATION: 'm1', SQL_RETRIEVAL: 'sql', RESPONSE_LAYER: 'res' };
  const key    = keyMap[stage];
  if (!key) return;

  const num    = document.getElementById(`num-${key}`);
  const stepEl = document.getElementById(`step-${key}`);
  const conn   = document.getElementById(`conn-${key}`);

  if (status === 'success') {
    if (num)    num.className    = 'pipe-num success';
    if (stepEl) stepEl.className = 'pipe-step-header success';
    if (conn)   conn.className   = 'pipe-connector active';
  } else {
    if (num)    num.className    = 'pipe-num error';
    if (stepEl) stepEl.className = 'pipe-step-header error';
  }

  // ── Isi detail panel sesuai stage ──────────────────────────────────────────
  if (stage === 'M1_ORCHESTRATION' && data.intent) {
    document.getElementById('intentDetail').style.display = '';
    let html = `
      <div class="key-val">
        <span class="kv-key">intent</span>
        <span class="kv-val" style="color:#5dba6c">${escHtml(data.intent)}</span>
      </div>`;
    const ents = data.entities || {};
    if (Object.keys(ents).length > 0) {
      html += `<div style="margin-top:6px;color:var(--text-muted);font-size:10px;letter-spacing:0.5px">ENTITIES</div>`;
      for (const [k, v] of Object.entries(ents)) {
        html += `<div class="key-val">
          <span class="kv-key">${escHtml(k)}</span>
          <span class="kv-val">${escHtml(String(v))}</span>
        </div>`;
      }
    } else {
      html += `<div class="key-val"><span class="kv-key">entities</span>
        <span class="kv-val" style="color:var(--text-muted)">none</span></div>`;
    }
    document.getElementById('intentBody').innerHTML = html;
  }

  if (stage === 'SQL_RETRIEVAL') {
    if (data.sql_query) {
      document.getElementById('sqlDetail').style.display = '';
      document.getElementById('sqlBody').textContent     = formatSQL(data.sql_query);
    }
    if (data.row_count > 0) {
      document.getElementById('dataDetail').style.display = '';
      const c = data.row_count;
      document.getElementById('dataBody').innerHTML =
        `<span class="rows-badge">${c} row${c !== 1 ? 's' : ''} returned</span>`;
    }
  }
}

/**
 * Buat bubble kosong di area chat untuk diisi secara streaming.
 * Mengembalikan referensi ke wrapper div dan elemen teks agar bisa diupdate.
 *
 * @returns {{ wrapper: HTMLElement, textEl: HTMLElement }}
 */
function createStreamingBubble() {
  const messages = document.getElementById('messages');

  const wrapper      = document.createElement('div');
  wrapper.className  = 'msg bot';

  const avatar       = document.createElement('div');
  avatar.className   = 'msg-avatar';
  avatar.textContent = '🎓';

  const body         = document.createElement('div');
  body.className     = 'msg-body';

  const bubble       = document.createElement('div');
  bubble.className   = 'bubble bubble-streaming';

  const textEl       = document.createElement('div');
  textEl.className   = 'bubble-stream-text';
  bubble.appendChild(textEl);
  body.appendChild(bubble);

  wrapper.appendChild(avatar);
  wrapper.appendChild(body);
  messages.appendChild(wrapper);
  messages.scrollTop = messages.scrollHeight;

  // Simpan referensi internal untuk finalizeStreamingBubble
  wrapper._streamBody   = body;
  wrapper._streamBubble = bubble;

  return { wrapper, textEl };
}

/**
 * Finalisasi bubble streaming setelah semua token diterima.
 * Menghapus class streaming, menambahkan pipeline chips, dan SQL block.
 *
 * @param {HTMLElement} wrapper - Elemen wrapper yang dikembalikan oleh createStreamingBubble.
 * @param {Object}      data    - Payload event "done" dari stream.
 */
function finalizeStreamingBubble(wrapper, data) {
  const body   = wrapper._streamBody;
  const bubble = wrapper._streamBubble;

  // Hapus state streaming (kursor, style khusus)
  bubble.classList.remove('bubble-streaming');

  // ── Tambahkan pipeline chips ────────────────────────────────────────────────
  const chips = document.createElement('div');
  chips.className = 'pipeline-chips';

  if (data.intent && data.intent !== 'error') {
    chips.appendChild(makeChip('m1', `M1 · ${data.intent}`));
  }
  if (data.sql_query) {
    chips.appendChild(makeChip('sql', `SQL · ${data.raw_data?.length ?? 0} rows`));
  }
  const resOk = data.pipeline_steps?.find(
    s => s.stage === 'RESPONSE_LAYER' && s.status === 'success'
  );
  if (resOk) chips.appendChild(makeChip('res', 'RES · ok'));
  if (data.pipeline_steps?.some(s => s.status === 'error')) {
    chips.appendChild(makeChip('err', 'Error'));
  }
  body.appendChild(chips);

  // ── Tambahkan SQL block yang bisa di-expand ─────────────────────────────────
  if (data.sql_query) {
    const sqlBlock     = document.createElement('div');
    sqlBlock.className = 'sql-block';
    sqlBlock.innerHTML = `
      <div class="sql-block-header" onclick="this.parentElement.classList.toggle('open')">
        <span>🗃</span>
        <span>SQL Query</span>
        <span class="sql-toggle"></span>
      </div>
      <div class="sql-code">${escHtml(formatSQL(data.sql_query))}</div>`;
    body.appendChild(sqlBlock);
  }

  document.getElementById('messages').scrollTop =
    document.getElementById('messages').scrollHeight;
}

/**
 * Versi Demo Mode dari callAPIStream.
 * Mensimulasikan animasi pipeline secara bertahap lalu streaming teks.
 *
 * @param {string} msg       - Pesan pengguna.
 * @param {string} typingId  - ID typing indicator untuk dihapus.
 * @returns {Promise<Object>}
 */
async function mockResponseWithAnimation(msg, typingId) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // Dapatkan mock response (sudah include delay 1.4s)
  const result = await mockResponse(msg);

  // ── Animasi pipeline panel ──────────────────────────────────────────────────
  setPipelineStageLoading('M1_ORCHESTRATION');
  await sleep(350);
  setPipelineStageDone('M1_ORCHESTRATION', 'success', {
    intent:   result.intent,
    entities: result.entities,
  });

  setPipelineStageLoading('SQL_RETRIEVAL');
  await sleep(250);
  setPipelineStageDone('SQL_RETRIEVAL', 'success', {
    sql_query: result.sql_query,
    row_count: result.raw_data?.length ?? 0,
  });

  setPipelineStageLoading('RESPONSE_LAYER');
  await sleep(200);
  setPipelineStageDone('RESPONSE_LAYER', 'success', {});

  // ── Ganti "..." dengan bubble streaming ────────────────────────────────────
  removeTyping(typingId);
  const { wrapper, textEl } = createStreamingBubble();

  let accumulated = '';
  const words     = result.reply.split(' ');
  for (const word of words) {
    accumulated += word + ' ';
    textEl.innerHTML = formatText(accumulated) +
      '<span class="stream-cursor" aria-hidden="true"></span>';
    document.getElementById('messages').scrollTop =
      document.getElementById('messages').scrollHeight;
    await sleep(20);
  }

  // Finalisasi
  textEl.innerHTML = formatText(result.reply);
  finalizeStreamingBubble(wrapper, result);

  return result;
}



/**
 * Simulate the full three-stage pipeline locally.
 * Returns a mock ChatResponse object matching the real API shape.
 * Triggered when DEMO_MODE = true or when the backend is unreachable.
 * @param {string} msg
 * @returns {Promise<Object>}
 */
async function mockResponse(msg) {
  await new Promise(r => setTimeout(r, 1400));  // Simulate network latency

  const m = msg.toLowerCase();
  let intent = 'general', entities = {}, reply = '';

  if (m.includes('scholarship') || m.includes('beasiswa') || m.includes('kip')) {
    intent   = 'scholarship_info';
    entities = { scholarship_type: 'merit', degree_level: 'S1' };
    reply    = `Berikut beasiswa yang tersedia di **UKDW**:\n\n**KIP-Kuliah** — Rp 950.000/bulan selama 48 bulan. Untuk mahasiswa dari keluarga kurang mampu. Menanggung UKT penuh.\n\n**Beasiswa Prestasi Akademik UKDW** — Rp 750.000/bulan. Minimum IPK 3,75. Dapat diperpanjang tiap tahun.\n\n**Beasiswa Perusahaan Mitra** — Rp 1.500.000/bulan untuk Teknik Informatika & Elektro.\n\nHubungi **beasiswa@ukdw.ac.id** untuk informasi pendaftaran!`;

  } else if (m.includes('ukt') || m.includes('biaya') || m.includes('fee')) {
    intent   = 'fee_info';
    entities = { program_name: 'Informatics Engineering', fee_type: 'UKT' };
    reply    = `Biaya UKT **Teknik Informatika S1** UKDW 2024/2025:\n\n  • Kelompok 1: **Rp 500.000**\n  • Kelompok 3: **Rp 3.000.000**\n  • Kelompok 5: **Rp 7.000.000**\n  • Kelompok 7: **Rp 10.000.000**\n  • Kelompok 8: **Rp 12.500.000**\n\nPengelompokan UKT berdasarkan penghasilan orang tua. Hubungi **keuangan@ukdw.ac.id** untuk detail lebih lanjut.`;

  } else if (m.includes('snbt') || m.includes('mandiri') || m.includes('daftar') || m.includes('registr')) {
    intent   = 'registration_info';
    entities = { registration_type: 'SNBT' };
    reply    = `**Pendaftaran SNBT 2025/2026 UKDW:**\n\n  • Pendaftaran: **1 April 2025** → 30 April 2025\n  • Pengumuman: **15 Juni 2025**\n  • Biaya pendaftaran: Rp 200.000\n  • Syarat: WNI, usia ≤ 25 tahun, nilai UTBK valid\n\nDaftar di: **snpmb.bppp.kemdikbud.go.id**`;

  } else if (m.includes('exchange') || m.includes('pertukaran') || m.includes('japan') || m.includes('jepang') || m.includes('iisma')) {
    intent   = 'exchange_program_info';
    entities = { country: 'Japan' };
    reply    = `**Summer Research Program — Kyoto University (Jepang)**\n\n  • Durasi: 6 minggu (Juli–Agustus)\n  • Kuota: **6 mahasiswa** | Min IPK: 3,30\n  • Pendanaan: Akomodasi ditanggung Kyoto University\n  • Syarat bahasa: TOEFL iBT 72+\n  • Deadline: **30 April 2025**\n\nCek juga program **IISMA** — didanai penuh oleh Kemdikbud! Hubungi **exchange@ukdw.ac.id**.`;

  } else if (m.includes('uts') || m.includes('ujian') || m.includes('jadwal') || m.includes('semester') || m.includes('krs')) {
    intent   = 'calendar_info';
    entities = { event_type: 'exam', semester: 'Ganjil' };
    reply    = `**Kalender Akademik UKDW 2024/2025 (Semester Ganjil):**\n\n  • PKKMB: **19–23 Agustus 2024**\n  • Input KRS: **26 Ags – 6 Sep 2024**\n  • Kuliah Mulai: **2 September 2024**\n  • UTS: **28 Okt – 8 Nov 2024**\n  • UAS: **23 Des 2024 – 3 Jan 2025**\n\nKalender lengkap di **academic.ukdw.ac.id/calendar**`;

  } else if (m.includes('program') || m.includes('jurusan') || m.includes('fakultas') || m.includes('teknik') || m.includes('engineering')) {
    intent   = 'program_info';
    entities = { faculty: 'Faculty of Engineering' };
    reply    = `**Program Studi Fakultas Teknik UKDW:**\n\n  • **Teknik Informatika** (S1) — Akreditasi Unggul, 4 tahun\n  • **Teknik Elektro** (S1) — Akreditasi Unggul, 4 tahun\n  • **Teknik Sipil** (S1) — Akreditasi A, 4 tahun\n  • **Teknik Informatika** (S2) — 2 tahun, fokus riset\n  • **Data Science** (S2) — 2 tahun, bahasa pengantar Inggris\n\nKunjungi **teknik.ukdw.ac.id** untuk kurikulum lengkap.`;

  } else {
    intent = 'general';
    reply  = `**Halo! Saya Waca** 🎓 — asisten chatbot pribadi **UKDW (Universitas Kristen Duta Wacana)**.\n\nSaya bisa membantu Anda dengan:\n\n  • **Pendaftaran** — SNBT, Mandiri, Transfer, Internasional\n  • **Beasiswa** — KIP-Kuliah, prestasi, mitra industri\n  • **Exchange Program** — IISMA, NUS, TU Munich, Erasmus+\n  • **Biaya Kuliah** — UKT per kelompok, SPP, biaya lab\n  • **Kalender Akademik** — UTS/UAS, KRS, wisuda\n  • **Program Studi** — fakultas, jurusan, akreditasi\n\nAda yang bisa saya bantu?`;
  }

  const sqlQuery = `SELECT * FROM ${intent.replace('_info', 's')} WHERE active = TRUE LIMIT 5`;

  return {
    reply,
    intent,
    entities,
    sql_query:      sqlQuery,
    raw_data:       [{ mock: true, intent, rows: 3 }],
    pipeline_steps: [
      { stage: 'M1_ORCHESTRATION', status: 'success', detail: `Intent: ${intent}` },
      { stage: 'SQL_RETRIEVAL',    status: 'success', detail: '3 rows fetched' },
      { stage: 'RESPONSE_LAYER',   status: 'success', detail: 'Response generated' }
    ]
  };
}