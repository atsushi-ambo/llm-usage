const COLORS = {
  claude: '#e8a07c',
  openai: '#2ec9a0',
  codex: '#3ddc8a',
  grok: '#9b8cff',
  cursor: '#5b9dff',
  gemini: '#e8c14a',
  openrouter: '#2dd4c0',
  cohere: '#3ba3e8',
  mistral: '#f07167',
  replicate: '#8b85f0',
  huggingface: '#e0c040',
};

const PREFS_KEY = 'llm-usage-ui';
let currentData = null;
let loadAbort = null;
let urlToken = null;

const $ = (id) => document.getElementById(id);

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}
const KNOWN_PROVIDERS = new Set([
  'claude', 'openai', 'codex', 'grok', 'cursor', 'gemini', 'openrouter',
  'cohere', 'mistral', 'replicate', 'huggingface',
]);
function safeProviderClass(p) {
  return KNOWN_PROVIDERS.has(p) ? p : 'unknown';
}
const ALLOWED_LINK_HOSTS = new Set([
  'claude.ai', 'console.anthropic.com',
  'chatgpt.com', 'platform.openai.com',
  'console.x.ai', 'x.ai', 'grok.com', 'www.grok.com',
  'cursor.com', 'www.cursor.com',
  'aistudio.google.com',
  'openrouter.ai',
  'cohere.com', 'dashboard.cohere.com',
  'mistral.ai', 'console.mistral.ai',
  'replicate.com',
  'huggingface.co',
]);
function safeUrl(u) {
  if (!u) return null;
  try {
    const parsed = new URL(String(u));
    if (parsed.protocol !== 'https:') return null;
    if (!ALLOWED_LINK_HOSTS.has(parsed.hostname)) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw);
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}
function savePrefs(partial) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...partial }));
  } catch { /* private mode / quota */ }
}

function fmtNum(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString();
}
function fmtCost(n, estimated) {
  if (n == null) return '—';
  return (estimated ? '~$' : '$') + Number(n).toFixed(2);
}
function fmtTok(n) {
  if (!n) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

/** One total-token value per day between periodStart and periodEnd
 * (inclusive), 0 for days with no DailyPoint — mirrors
 * llm_usage.history.daily_totals() in the CLI. */
function dailyTotals(daily, periodStart, periodEnd) {
  const byDay = {};
  (daily || []).forEach(d => {
    const tok = (d.input_tokens || 0) + (d.output_tokens || 0)
      + (d.cache_read_tokens || 0) + (d.cache_write_tokens || 0);
    byDay[d.day] = (byDay[d.day] || 0) + tok;
  });
  const out = [];
  const start = new Date(periodStart + 'T00:00:00Z').getTime();
  const end = new Date(periodEnd + 'T00:00:00Z').getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return out;
  for (let t = start; t <= end; t += 86_400_000) {
    out.push(byDay[new Date(t).toISOString().slice(0, 10)] || 0);
  }
  return out;
}

function renderSparkline(values, color) {
  if (!values.length || !values.some(v => v > 0)) return '';
  const max = Math.max(...values);
  const w = 240, h = 28;
  const slot = w / values.length;
  const barW = Math.max(0.5, slot - 1);
  const bars = values.map((v, i) => {
    const bh = Math.max(1, (v / max) * h);
    return `<rect x="${(i * slot).toFixed(1)}" y="${(h - bh).toFixed(1)}" `
      + `width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" rx="0.5"></rect>`;
  }).join('');
  return `
    <div class="sparkline-block">
      <div class="sparkline-label">Daily tokens</div>
      <svg class="sparkline-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="fill:${color}" role="img" aria-label="Daily tokens sparkline">
        ${bars}
      </svg>
    </div>`;
}

function clampPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return null;
  return Math.max(0, Math.min(100, v));
}
function pctClass(pct) {
  if (pct >= 90) return 'quota-hot';
  if (pct >= 70) return 'quota-warn';
  return 'quota-ok';
}
function barColor(pct, fallback) {
  if (pct >= 90) return 'var(--red)';
  if (pct >= 70) return 'var(--yellow)';
  return fallback || 'var(--accent)';
}
function fmtReset(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return String(iso).slice(0, 16);
  }
}

/** Pull a normalized quota object from provider meta (any shape). */
function extractQuota(p) {
  const meta = p.meta || {};
  if (meta.quota && meta.quota.used_percent != null) {
    return {
      used_percent: clampPct(meta.quota.used_percent),
      label: meta.quota.label || 'Quota',
      plan: meta.quota.plan || null,
      resets_at: meta.quota.resets_at || null,
      windows: meta.quota.windows || [],
      burn: meta.quota.burn || null,
    };
  }
  // Grok billing snapshot (only present when raw meta is included)
  const sub = meta.subscription;
  if (sub && typeof sub === 'object') {
    if (sub.credit_usage_percent != null) {
      return {
        used_percent: clampPct(sub.credit_usage_percent),
        label: 'Weekly limit',
        plan: sub.subscription_tier || null,
        resets_at: (sub.period && sub.period.end) || null,
        windows: [],
        burn: null,
      };
    }
    if (sub.five_hour || sub.seven_day) {
      const windows = [];
      const push = (key, label) => {
        const b = sub[key];
        if (!b) return;
        let util = b.utilization != null ? b.utilization : b.utilization_pct;
        if (util == null && b.used != null && b.limit) util = b.used / b.limit;
        if (util == null) return;
        const pct = util <= 1 ? util * 100 : util;
        windows.push({
          key, label,
          used_percent: clampPct(pct),
          resets_at: b.resets_at || b.resetsAt || null,
        });
      };
      push('five_hour', '5-hour');
      push('seven_day', '7-day');
      push('seven_day_sonnet', '7-day Sonnet');
      push('seven_day_opus', '7-day Opus');
      const primary = windows.find(w => w.key === 'five_hour')
        || windows.find(w => w.key === 'seven_day')
        || windows[0];
      if (primary) {
        return {
          used_percent: primary.used_percent,
          label: primary.label + ' limit',
          plan: meta.plan_type || 'Claude',
          resets_at: primary.resets_at,
          windows,
          burn: null,
        };
      }
    }
    const primary = sub.rate_limit && sub.rate_limit.primary_window;
    if (primary && primary.used_percent != null) {
      let resets = null;
      if (primary.reset_at) {
        resets = new Date(primary.reset_at * 1000).toISOString();
      }
      return {
        used_percent: clampPct(primary.used_percent),
        label: 'Usage window',
        plan: sub.plan_type || meta.plan_type || null,
        resets_at: resets,
        windows: [],
        burn: null,
      };
    }
  }
  return null;
}

function burnChipClass(burn) {
  if (!burn) return 'quota-ok';
  if (burn.hits_label === 'exhausted' || burn.hits_before_reset === true) return 'quota-hot';
  if (burn.hits_label === 'ok till reset') return 'quota-ok';
  return 'quota-warn';
}

function renderQuotaBlock(quota, color) {
  if (!quota || quota.used_percent == null) return '';
  const pct = quota.used_percent;
  const cls = pctClass(pct);
  const fill = barColor(pct, color);
  const reset = fmtReset(quota.resets_at);
  const burn = quota.burn || null;
  const burnLine = burn && burn.summary
    ? `<div class="quota-burn ${burnChipClass(burn)}">${esc(burn.summary)}</div>`
    : '';
  const extraWindows = (quota.windows || []).filter(w =>
    w && w.used_percent != null && w.label !== (quota.label || '').replace(' limit','')
  );
  const windowRows = extraWindows.map(w => {
    const wp = clampPct(w.used_percent);
    if (wp == null) return '';
    const wBurn = w.burn;
    const wBurnTxt = wBurn && wBurn.hits_label && wBurn.hits_label !== 'idle'
      ? ` · <span class="${burnChipClass(wBurn)}">${esc(wBurn.hits_label)}</span>`
      : '';
    return `
      <div class="quota-meta" style="margin-top:.4rem">
        <span><strong>${esc(w.label)}</strong></span>
        <span class="${pctClass(wp)}">${Math.round(wp)}%${wBurnTxt}</span>
      </div>
      <div class="quota-track" style="height:7px" role="progressbar" aria-valuenow="${Math.round(wp)}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(w.label)}">
        <span class="quota-fill" style="width:${wp}%;background:${barColor(wp, color)}"></span>
      </div>`;
  }).join('');
  return `
    <div class="quota-block">
      <div class="quota-head">
        <div class="quota-pct ${cls}">
          ${Math.round(pct)}%<small>used</small>
        </div>
        <div class="quota-reset">
          ${esc(quota.label || 'Quota')}${reset ? `<br>Resets ${esc(reset)}` : ''}
          ${burn && burn.hits_label ? `<br><span class="${burnChipClass(burn)}">${esc(burn.hits_label)}</span>` : ''}
        </div>
      </div>
      <div class="quota-track" role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(quota.label || 'Quota')}">
        <span class="quota-fill" style="width:${pct}%;background:${fill}"></span>
      </div>
      <div class="quota-meta">
        <span>${quota.plan ? `Plan: <strong>${esc(quota.plan)}</strong>` : ''}</span>
        <span>${Math.round(100 - pct)}% remaining</span>
      </div>
      ${burnLine}
      ${windowRows}
    </div>
  `;
}

function resolveDays() {
  const daysSelect = $('days').value;
  if (daysSelect !== 'custom') {
    return parseInt(daysSelect, 10) || 30;
  }
  const startDate = $('date-start').value;
  const endDate = $('date-end').value;
  if (!startDate || !endDate) return null;
  const start = new Date(startDate + 'T00:00:00Z');
  const end = new Date(endDate + 'T00:00:00Z');
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) {
    return null;
  }
  // API is lookback-from-today only: use (today - start) so the range is covered.
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  const days = Math.ceil((today - start) / 86_400_000) + 1;
  return Math.max(1, Math.min(365, days));
}

function sliceToCustomRange(data) {
  if ($('days').value !== 'custom') return data;
  const startDate = $('date-start').value;
  const endDate = $('date-end').value;
  if (!startDate || !endDate || endDate < startDate) return data;

  const providers = data.providers.map(p => {
    const daily = (p.daily || []).filter(d => d.day >= startDate && d.day <= endDate);
    if (!daily.length) {
      return { ...p, daily, period_start: startDate, period_end: endDate };
    }
    const sum = (key) => daily.reduce((a, d) => a + (d[key] || 0), 0);
    const costs = daily.map(d => d.cost_usd).filter(c => c != null);
    return {
      ...p,
      daily,
      input_tokens: sum('input_tokens'),
      output_tokens: sum('output_tokens'),
      cache_read_tokens: sum('cache_read_tokens'),
      cache_write_tokens: sum('cache_write_tokens'),
      requests: sum('requests'),
      cost_usd: costs.length ? costs.reduce((a, c) => a + c, 0) : p.cost_usd,
      period_start: startDate,
      period_end: endDate,
    };
  });
  const billed = providers
    .filter(p => p.cost_usd != null && !(p.meta && p.meta.estimated))
    .reduce((a, p) => a + p.cost_usd, 0);
  const estimated = providers
    .filter(p => p.cost_usd != null && p.meta && p.meta.estimated)
    .reduce((a, p) => a + p.cost_usd, 0);
  return {
    ...data,
    providers,
    period_start: startDate,
    period_end: endDate,
    billed_cost_usd: billed || null,
    estimated_cost_usd: estimated || null,
    has_estimated_cost: estimated > 0,
  };
}

function usageUrl(days, refresh) {
  const u = new URL('/api/usage', location.origin);
  u.searchParams.set('days', String(days));
  if (refresh) u.searchParams.set('refresh', '1');
  if (urlToken) u.searchParams.set('token', urlToken);
  return u;
}

function stripTokenFromAddressBar() {
  try {
    const here = new URL(location.href);
    if (!here.searchParams.has('token')) return;
    urlToken = here.searchParams.get('token') || urlToken;
    here.searchParams.delete('token');
    const next = here.pathname + (here.search ? here.search : '') + here.hash;
    history.replaceState({}, '', next);
  } catch { /* ignore */ }
}

function setLoading(on, message) {
  document.body.classList.toggle('is-loading', on);
  const btn = $('refresh');
  if (btn) {
    btn.disabled = on;
    btn.textContent = on ? 'Collecting…' : 'Refresh';
  }
  if (message) $('status').textContent = message;
  const main = $('main');
  if (main) main.setAttribute('aria-busy', on ? 'true' : 'false');
}

function relativeFetched(iso) {
  try {
    const t = new Date(iso).getTime();
    if (!Number.isFinite(t)) return '';
    const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (secs < 10) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    return new Date(iso).toLocaleTimeString();
  } catch {
    return '';
  }
}

async function load(opts) {
  const days = resolveDays();
  if (days == null) {
    $('status').textContent = 'Pick a start and end date';
    return;
  }

  if (loadAbort) loadAbort.abort();
  loadAbort = new AbortController();
  setLoading(true, 'Collecting…');
  try {
    const res = await fetch(usageUrl(days, opts && opts.forceRefresh), {
      signal: loadAbort.signal,
      cache: 'no-store',
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error) detail = String(body.error);
      } catch {
        /* non-JSON error body */
      }
      if (res.status === 403) {
        detail = 'Session expired — reopen the URL printed by `llm-usage dashboard`.';
      }
      throw new Error(detail.slice(0, 280));
    }
    const data = await res.json();
    currentData = data;
    render(sliceToCustomRange(data));
    const ago = relativeFetched(data.generated_at);
    $('status').textContent =
      `${data.period_start} → ${data.period_end}`
      + (ago ? ` · fetched ${ago}` : '');
  } catch (e) {
    if (e && e.name === 'AbortError') return;
    $('status').textContent = 'Error';
    const summary = $('summary');
    if (summary) summary.innerHTML = '';
    $('providers').innerHTML =
      `<div class="empty"><strong>Couldn’t load usage</strong>${esc(e.message)}</div>`;
  } finally {
    setLoading(false);
  }
}

function providerIsActive(p) {
  if (!p) return false;
  if (p.source && p.source !== 'unavailable') return true;
  if (p.requests || p.total_tokens || p.cost_usd) return true;
  if (extractQuota(p)) return true;
  return false;
}

function render(data) {
  const billed = data.billed_cost_usd;
  const estimated = data.estimated_cost_usd;
  const hasCost = billed != null || estimated != null;
  let costValue = '—';
  let costSub = 'where known';
  if (billed != null && estimated != null) {
    costValue = fmtCost(billed) + ' + ' + fmtCost(estimated, true);
    costSub = 'billed + estimated';
  } else if (estimated != null) {
    costValue = fmtCost(estimated, true);
    costSub = 'estimated (list price)';
  } else if (billed != null) {
    costValue = fmtCost(billed);
    costSub = 'billed';
  }
  const totalTok = data.providers.reduce(
    (a, p) => a + (p.input_tokens||0) + (p.output_tokens||0)
      + (p.cache_read_tokens||0) + (p.cache_write_tokens||0), 0
  );
  const totalReq = data.providers.reduce((a, p) => a + (p.requests||0), 0);
  const active = data.providers.filter(providerIsActive).length;

  const hottest = data.providers
    .map(p => ({ p, q: extractQuota(p) }))
    .filter(x => x.q && x.q.used_percent != null)
    .sort((a, b) => b.q.used_percent - a.q.used_percent)[0];
  const hottestCard = hottest
    ? `<div class="card">
        <div class="label">Hottest quota</div>
        <div class="value ${pctClass(hottest.q.used_percent)}">${Math.round(hottest.q.used_percent)}%</div>
        <div class="sub">${esc(hottest.p.display_name)}${hottest.q.burn && hottest.q.burn.hits_label ? ' · ' + esc(hottest.q.burn.hits_label) : ''}</div>
      </div>`
    : `<div class="card"><div class="label">Active sources</div><div class="value">${active}/${data.providers.length}</div><div class="sub">configured or local</div></div>`;

  const anyEstimated = data.has_estimated_cost
    || data.providers.some(p => p.meta && p.meta.estimated);
  $('summary').innerHTML = `
    <div class="card"><div class="label">Combined cost</div><div class="value">${hasCost ? costValue : '—'}</div><div class="sub">${costSub}</div></div>
    <div class="card"><div class="label">Total tokens</div><div class="value">${fmtTok(totalTok)}</div><div class="sub">${fmtNum(totalTok)} raw</div></div>
    <div class="card"><div class="label">Requests</div><div class="value">${fmtNum(totalReq)}</div><div class="sub">across providers</div></div>
    ${hottestCard}
  `;
  const foot = $('estimate-footnote');
  if (foot) {
    if (anyEstimated) {
      foot.hidden = false;
      const pricesAsOf = data.prices_as_of || null;
      foot.textContent = pricesAsOf
        ? `~ = estimated from public list prices (as of ${pricesAsOf}) — not an invoice.`
        : '~ = estimated from public list prices — not an invoice.';
    } else {
      foot.hidden = true;
      foot.textContent = '';
    }
  }

  renderBudgetAlerts(data);
  renderQuotaStrip(data);
  renderCostBreakdown(data);
  renderUsageChart(data);
  renderForecast(data);
  updateProviderFilter(data);
  renderProviders(data);
}

function renderQuotaStrip(data) {
  const strip = $('quota-strip');
  const title = $('quota-strip-title');
  if (!strip) return;
  const rows = data.providers
    .map(p => ({ p, q: extractQuota(p) }))
    .filter(x => x.q && x.q.used_percent != null)
    .sort((a, b) => b.q.used_percent - a.q.used_percent);
  if (!rows.length) {
    strip.hidden = true;
    if (title) title.hidden = true;
    strip.innerHTML = '';
    return;
  }
  strip.hidden = false;
  if (title) title.hidden = false;
  strip.innerHTML = rows.map(({ p, q }) => {
    const cls = safeProviderClass(p.provider);
    const burn = q.burn && q.burn.hits_label ? esc(q.burn.hits_label) : esc(q.label || 'Quota');
    return `<button type="button" class="quota-chip ${cls}" data-jump="${esc(p.provider)}" aria-label="${esc(p.display_name)} ${Math.round(q.used_percent)}% used">
      <div class="qc-name">${esc(p.display_name)}</div>
      <div class="qc-pct ${pctClass(q.used_percent)}">${Math.round(q.used_percent)}%</div>
      <div class="qc-meta">${burn}</div>
    </button>`;
  }).join('');
  strip.querySelectorAll('[data-jump]').forEach(btn => {
    btn.addEventListener('click', () => {
      const el = document.getElementById(`provider-${btn.getAttribute('data-jump')}`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

function renderBudgetAlerts(data) {
  const alerts = $('budget-alerts');
  if (!alerts) return;
  const budgetLimit = Number(data.budget_limit);
  const threshold = Number(data.budget_alert_threshold);
  if (!Number.isFinite(budgetLimit) || budgetLimit <= 0) {
    alerts.innerHTML = '';
    return;
  }
  const alertAt = Number.isFinite(threshold) ? threshold : 0.9;
  const totalCost = (data.billed_cost_usd || 0) + (data.estimated_cost_usd || 0);
  const ratio = totalCost / budgetLimit;
  if (ratio < alertAt) {
    alerts.innerHTML = '';
    return;
  }
  const isWarning = totalCost < budgetLimit;
  alerts.innerHTML = `
    <div class="budget-alert ${isWarning ? 'warning' : ''}" role="alert">
      <strong>${isWarning ? 'Budget warning' : 'Budget exceeded'}</strong>
      <p>Current cost: ${fmtCost(totalCost, data.has_estimated_cost)} of ${fmtCost(budgetLimit, false)}</p>
      <p>${Math.round(ratio * 100)}% of monthly budget used (alert at ${Math.round(alertAt * 100)}%)</p>
    </div>
  `;
}

function renderCostBreakdown(data) {
  const breakdown = $('cost-breakdown');
  const section = $('cost-section');
  if (!breakdown) return;
  const providersWithCost = data.providers
    .filter(p => p.cost_usd != null && p.cost_usd > 0)
    .sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0));

  if (providersWithCost.length === 0) {
    breakdown.innerHTML = '';
    if (section) section.hidden = true;
    return;
  }
  if (section) section.hidden = false;

  const total = providersWithCost.reduce((a, p) => a + p.cost_usd, 0);
  breakdown.innerHTML = providersWithCost.map(p => {
    const pct = total > 0 ? (p.cost_usd / total) * 100 : 0;
    const color = COLORS[safeProviderClass(p.provider)] || '#5b9dff';
    return `
      <div class="cost-row">
        <div class="cost-row-head">
          <span>${esc(p.display_name)}</span>
          <span>${fmtCost(p.cost_usd, p.meta && p.meta.estimated)} · ${pct.toFixed(0)}%</span>
        </div>
        <div class="quota-track" style="height:8px">
          <span class="quota-fill" style="width:${pct.toFixed(1)}%;background:${color}"></span>
        </div>
      </div>`;
  }).join('');
}

function renderUsageChart(data) {
  const host = $('usage-chart');
  const section = $('trends-section');
  if (!host) return;

  const dailyData = {};
  data.providers.forEach(p => {
    (p.daily || []).forEach(d => {
      const tokens = (d.input_tokens || 0) + (d.output_tokens || 0)
        + (d.cache_read_tokens || 0) + (d.cache_write_tokens || 0);
      dailyData[d.day] = (dailyData[d.day] || 0) + tokens;
    });
  });

  const sortedDays = Object.keys(dailyData).sort();
  if (!sortedDays.length || !sortedDays.some(d => dailyData[d] > 0)) {
    host.innerHTML = '';
    if (section) section.hidden = true;
    return;
  }
  if (section) section.hidden = false;

  const values = sortedDays.map(day => dailyData[day]);
  const max = Math.max(...values, 1);
  const w = 640;
  const h = 220;
  const padL = 8;
  const padR = 8;
  const padT = 12;
  const padB = 28;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const n = values.length;
  const slot = plotW / n;
  const barW = Math.max(1, slot * 0.7);

  const bars = values.map((v, i) => {
    const bh = Math.max(v > 0 ? 2 : 0, (v / max) * plotH);
    const x = padL + i * slot + (slot - barW) / 2;
    const y = padT + plotH - bh;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" `
      + `height="${bh.toFixed(1)}" rx="1.5" fill="#5b9dff" opacity="0.9">`
      + `<title>${esc(sortedDays[i])}: ${fmtTok(v)} tokens</title></rect>`;
  }).join('');

  const labelEvery = Math.max(1, Math.ceil(n / 8));
  const labels = sortedDays.map((day, i) => {
    if (i % labelEvery !== 0 && i !== n - 1) return '';
    const x = padL + i * slot + slot / 2;
    const short = day.slice(5);
    return `<text x="${x.toFixed(1)}" y="${h - 8}" text-anchor="middle" `
      + `fill="#8b95a8" font-size="10">${esc(short)}</text>`;
  }).join('');

  host.innerHTML = `
    <svg class="usage-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Daily tokens">
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="#2a3344"/>
      ${bars}
      ${labels}
    </svg>
    <div class="chart-caption">Daily tokens (all providers) · peak ${fmtTok(max)}</div>
  `;
}

function renderForecast(data) {
  const forecast = $('forecast');
  const section = $('forecast-section');
  if (!forecast) return;
  const totalCost = (data.billed_cost_usd || 0) + (data.estimated_cost_usd || 0);
  if (!(totalCost > 0)) {
    forecast.innerHTML = '';
    if (section) section.hidden = true;
    return;
  }
  if (section) section.hidden = false;

  let daysInPeriod = 0;
  if (data.period_start && data.period_end) {
    const start = new Date(data.period_start + 'T00:00:00Z');
    const end = new Date(data.period_end + 'T00:00:00Z');
    daysInPeriod = Math.max(1, Math.round((end - start) / 86_400_000) + 1);
  } else {
    daysInPeriod = 30;
  }

  const avgDailyCost = totalCost / daysInPeriod;
  const forecastedMonthlyCost = avgDailyCost * 30;

  forecast.innerHTML = `
    <div class="card">
      <div class="label">Monthly forecast</div>
      <div class="value">${fmtCost(forecastedMonthlyCost, data.has_estimated_cost)}</div>
      <div class="sub">Linear estimate from ${daysInPeriod} days · $${avgDailyCost.toFixed(2)}/day avg</div>
    </div>
  `;
}

function updateProviderFilter(data) {
  const filter = $('provider-filter');
  if (!filter) return;
  const currentValue = filter.value;
  filter.innerHTML = '<option value="all">All providers</option>';
  data.providers.forEach(p => {
    const option = document.createElement('option');
    option.value = p.provider;
    option.textContent = p.display_name
      + (providerIsActive(p) ? '' : ' (inactive)');
    filter.appendChild(option);
  });
  if ([...filter.options].some(o => o.value === currentValue)) {
    filter.value = currentValue;
  }
}

function renderProviders(data) {
  const providerFilter = ($('provider-filter') && $('provider-filter').value) || 'all';
  const sortBy = ($('sort-by') && $('sort-by').value) || 'quota';
  const hideNa = $('hide-na') ? $('hide-na').checked : true;

  let providers = [...data.providers];
  if (providerFilter !== 'all') {
    providers = providers.filter(p => p.provider === providerFilter);
  }
  const hiddenInactive = hideNa
    ? providers.filter(p => !providerIsActive(p)).length
    : 0;
  if (hideNa) {
    providers = providers.filter(providerIsActive);
  }

  providers.sort((a, b) => {
    if (sortBy === 'quota') {
      const aq = extractQuota(a);
      const bq = extractQuota(b);
      const ap = aq && aq.used_percent != null ? aq.used_percent : -1;
      const bp = bq && bq.used_percent != null ? bq.used_percent : -1;
      if (bp !== ap) return bp - ap;
      return (b.cost_usd || 0) - (a.cost_usd || 0);
    }
    if (sortBy === 'cost') {
      return (b.cost_usd || 0) - (a.cost_usd || 0);
    }
    if (sortBy === 'tokens') {
      const aTokens = (a.input_tokens || 0) + (a.output_tokens || 0);
      const bTokens = (b.input_tokens || 0) + (b.output_tokens || 0);
      return bTokens - aTokens;
    }
    if (sortBy === 'requests') {
      return (b.requests || 0) - (a.requests || 0);
    }
    return 0;
  });

  if (!providers.length) {
    const hint = hiddenInactive
      ? `${hiddenInactive} inactive source${hiddenInactive === 1 ? '' : 's'} hidden — uncheck “Hide inactive” or run <code>llm-usage setup</code>.`
      : 'No providers match this filter.';
    $('providers').innerHTML = `<div class="empty"><strong>Nothing to show</strong>${hint}</div>`;
    return;
  }

  $('providers').innerHTML = providers.map(p => {
    const est = p.meta && p.meta.estimated;
    const quota = extractQuota(p);
    const allModels = p.models || [];
    const models = allModels.slice(0, 8);
    const extraModels = Math.max(0, allModels.length - models.length);
    const modelRows = models.map(m => {
      const name = esc(m.model);
      const shortName = m.model.length > 28 ? esc(m.model.slice(0, 26)) + '…' : name;
      return `
      <tr>
        <td title="${name}">${shortName}</td>
        <td>${fmtNum(m.requests)}</td>
        <td>${fmtTok(m.input_tokens)}</td>
        <td>${fmtTok(m.output_tokens)}</td>
        <td>${m.cost_usd != null ? fmtCost(m.cost_usd, est) : '—'}</td>
      </tr>`;
    }).join('');

    const shortNotes = (p.notes || []).filter(n => n.length < 180).slice(0, 2);
    const shortErrs = (p.errors || []).map(e => {
      if (e.includes('429')) return 'Quota/rate-limit probe returned 429 (try later)';
      if (e.includes('401')) return 'Auth rejected — re-login';
      return e.length > 120 ? e.slice(0, 117) + '…' : e;
    });
    const notes = [
      ...shortNotes,
      ...shortErrs.map(e => e),
    ].map(n => {
      const isErr = shortErrs.includes(n) || n.startsWith('⚠');
      return `<div class="${isErr ? 'err' : ''}">${isErr && !n.startsWith('⚠') ? '⚠ ' : ''}${esc(n)}</div>`;
    }).join('');

    const providerClass = safeProviderClass(p.provider);
    const sourceClass = /^[a-z_]+$/.test(String(p.source || '')) ? p.source : 'unavailable';
    const consoleUrl = p.meta && safeUrl(p.meta.console_url);

    return `
      <article class="card provider ${providerClass}" id="provider-${esc(p.provider)}">
        <h2>
          <span>${esc(p.display_name)}</span>
          <span class="badge ${sourceClass}">${esc(String(p.source || '').replace(/_/g, ' '))}</span>
        </h2>
        ${quota ? renderQuotaBlock(quota, COLORS[providerClass]) : ''}
        <div class="metrics">
          <div><span>Cost</span><strong>${fmtCost(p.cost_usd, est)}</strong></div>
          <div><span>Requests</span><strong>${fmtNum(p.requests)}</strong></div>
          <div><span>Input</span><strong>${fmtTok(p.input_tokens)}</strong></div>
          <div><span>Output</span><strong>${fmtTok(p.output_tokens)}</strong></div>
        </div>
        ${renderSparkline(
          dailyTotals(p.daily, data.period_start, data.period_end),
          COLORS[providerClass] || 'var(--accent)'
        )}
        ${models.length ? `<div class="models"><table>
          <thead><tr><th>Model</th><th>Req</th><th>In</th><th>Out</th><th>$</th></tr></thead>
          <tbody>${modelRows}</tbody>
        </table>${extraModels ? `<div class="notes">${extraModels} more model${extraModels === 1 ? '' : 's'}</div>` : ''}</div>` : ''}
        ${notes ? `<div class="notes">${notes}</div>` : ''}
        ${consoleUrl ? `<div class="notes"><a href="${esc(consoleUrl)}" target="_blank" rel="noopener noreferrer">Open console ↗</a></div>` : ''}
      </article>
    `;
  }).join('');
}

function csvCell(value) {
  let s = value == null ? '' : String(value);
  if (/^[=+\-@]/.test(s) || s.startsWith('\t')) s = "'" + s;
  if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function localYmd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function defaultCustomRange() {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 29);
  $('date-end').value = localYmd(end);
  $('date-start').value = localYmd(start);
}

function exportCSV() {
  if (!currentData) {
    $('status').textContent = 'Nothing to export yet';
    return;
  }
  const data = sliceToCustomRange(currentData);

  const rows = [
    ['Provider', 'Source', 'Quota %', 'Pace', 'Requests', 'Input Tokens', 'Output Tokens', 'Total Tokens', 'Cost (USD)'].join(',')
  ];

  data.providers.forEach(p => {
    const cost = p.cost_usd != null
      ? ((p.meta && p.meta.estimated) ? '~' : '') + p.cost_usd.toFixed(2)
      : '';
    const total = (p.input_tokens || 0) + (p.output_tokens || 0)
      + (p.cache_read_tokens || 0) + (p.cache_write_tokens || 0);
    const q = extractQuota(p);
    rows.push([
      csvCell(p.display_name),
      csvCell(p.source),
      q && q.used_percent != null ? q.used_percent.toFixed(1) : '',
      q && q.burn && q.burn.hits_label ? csvCell(q.burn.hits_label) : '',
      p.requests || 0,
      p.input_tokens || 0,
      p.output_tokens || 0,
      total,
      csvCell(cost),
    ].join(','));
  });

  const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `llm-usage-export-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function exportPrintable() {
  if (!currentData) {
    $('status').textContent = 'Nothing to print yet';
    return;
  }
  const data = sliceToCustomRange(currentData);
  const billed = data.billed_cost_usd;
  const estimated = data.estimated_cost_usd;
  const totalCost = (billed || 0) + (estimated || 0);
  const totalReq = data.providers.reduce((a, p) => a + (p.requests || 0), 0);
  const totalTok = data.providers.reduce(
    (a, p) => a + (p.input_tokens || 0) + (p.output_tokens || 0)
      + (p.cache_read_tokens || 0) + (p.cache_write_tokens || 0), 0
  );

  let rows = '';
  data.providers.forEach(p => {
    const cost = p.cost_usd != null
      ? ((p.meta && p.meta.estimated) ? '~' : '') + p.cost_usd.toFixed(2)
      : '—';
    const total = (p.input_tokens || 0) + (p.output_tokens || 0)
      + (p.cache_read_tokens || 0) + (p.cache_write_tokens || 0);
    const q = extractQuota(p);
    const quota = q && q.used_percent != null ? `${Math.round(q.used_percent)}%` : '—';
    rows += `<tr>
      <td>${esc(p.display_name)}</td>
      <td>${esc(p.source)}</td>
      <td>${esc(quota)}</td>
      <td>${fmtNum(p.requests)}</td>
      <td>${fmtNum(p.input_tokens)}</td>
      <td>${fmtNum(p.output_tokens)}</td>
      <td>${fmtNum(total)}</td>
      <td>${esc(cost)}</td>
    </tr>`;
  });

  const html = `<!DOCTYPE html><html><head><title>LLM Usage Report</title>
    <style>
      body{font-family:system-ui,sans-serif;padding:24px;color:#111}
      table{width:100%;border-collapse:collapse;margin-top:16px}
      th,td{border:1px solid #ddd;padding:8px;text-align:left}
      th{background:#f2f2f2}
      .summary{margin:16px 0;padding:12px;background:#f9f9f9;border-radius:6px}
    </style></head><body>
    <h1>LLM Usage Report</h1>
    <p>Period: ${esc(data.period_start)} to ${esc(data.period_end)}</p>
    <p>Generated: ${esc(new Date(data.generated_at).toLocaleString())}</p>
    <div class="summary">
      <p><strong>Total cost:</strong> ${fmtCost(totalCost, data.has_estimated_cost)}</p>
      <p><strong>Requests:</strong> ${fmtNum(totalReq)}</p>
      <p><strong>Tokens:</strong> ${fmtNum(totalTok)}</p>
    </div>
    <table><thead><tr>
      <th>Provider</th><th>Source</th><th>Quota</th><th>Requests</th><th>In</th><th>Out</th><th>Total</th><th>Cost</th>
    </tr></thead><tbody>${rows}</tbody></table>
    </body></html>`;

  const blob = new Blob([html], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  const printWindow = window.open(url, '_blank', 'noopener,noreferrer');
  if (!printWindow) {
    $('status').textContent = 'Pop-up blocked — allow pop-ups to print';
    URL.revokeObjectURL(url);
    return;
  }
  printWindow.addEventListener('load', () => {
    printWindow.focus();
    printWindow.print();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

function applyPrefsToControls() {
  const prefs = loadPrefs();
  if (prefs.days && ['7', '14', '30', '90', 'custom'].includes(String(prefs.days))) {
    $('days').value = String(prefs.days);
  }
  if (prefs.sort && ['quota', 'cost', 'tokens', 'requests'].includes(prefs.sort)) {
    $('sort-by').value = prefs.sort;
  }
  if (typeof prefs.hideNa === 'boolean' && $('hide-na')) {
    $('hide-na').checked = prefs.hideNa;
  }
  const isCustom = $('days').value === 'custom';
  $('custom-dates').classList.toggle('hidden', !isCustom);
  if (isCustom) {
    if (prefs.dateStart) $('date-start').value = prefs.dateStart;
    if (prefs.dateEnd) $('date-end').value = prefs.dateEnd;
    if (!$('date-end').value) defaultCustomRange();
  }
}

function persistControls() {
  savePrefs({
    days: $('days').value,
    sort: $('sort-by') && $('sort-by').value,
    hideNa: $('hide-na') ? $('hide-na').checked : true,
    dateStart: $('date-start').value,
    dateEnd: $('date-end').value,
  });
}

function isTypingTarget(el) {
  if (!el) return false;
  const tag = (el.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'select' || tag === 'textarea' || el.isContentEditable;
}

$('refresh').addEventListener('click', () => load({ forceRefresh: true }));
$('days').addEventListener('change', () => {
  const isCustom = $('days').value === 'custom';
  $('custom-dates').classList.toggle('hidden', !isCustom);
  if (isCustom && !$('date-end').value) defaultCustomRange();
  persistControls();
  load();
});
$('date-start').addEventListener('change', () => { persistControls(); load(); });
$('date-end').addEventListener('change', () => { persistControls(); load(); });
if ($('provider-filter')) {
  $('provider-filter').addEventListener('change', () => {
    if (currentData) renderProviders(sliceToCustomRange(currentData));
  });
}
if ($('sort-by')) {
  $('sort-by').addEventListener('change', () => {
    persistControls();
    if (currentData) renderProviders(sliceToCustomRange(currentData));
  });
}
if ($('hide-na')) {
  $('hide-na').addEventListener('change', () => {
    persistControls();
    if (currentData) renderProviders(sliceToCustomRange(currentData));
  });
}
if ($('export-csv')) $('export-csv').addEventListener('click', exportCSV);
if ($('export-pdf')) $('export-pdf').addEventListener('click', exportPrintable);

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (isTypingTarget(e.target)) return;
  if (e.key === 'r' || e.key === 'R') {
    e.preventDefault();
    load({ forceRefresh: true });
  } else if (e.key === '1') {
    $('days').value = '7'; persistControls(); load();
  } else if (e.key === '2') {
    $('days').value = '14'; persistControls(); load();
  } else if (e.key === '3') {
    $('days').value = '30'; persistControls(); load();
  } else if (e.key === '4') {
    $('days').value = '90'; persistControls(); load();
  }
});

stripTokenFromAddressBar();
applyPrefsToControls();
load();
