const COLORS = {
  claude: '#d4a27f',
  openai: '#10a37f',
  codex: '#22c55e',
  grok: '#a78bfa',
  cursor: '#60a5fa',
  gemini: '#fbbf24',
  openrouter: '#2dd4bf',
};

const $ = (id) => document.getElementById(id);

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}
// Only these provider ids are used as CSS class names / lookups.
const KNOWN_PROVIDERS = new Set(['claude', 'openai', 'codex', 'grok', 'cursor', 'gemini', 'openrouter']);
function safeProviderClass(p) {
  return KNOWN_PROVIDERS.has(p) ? p : 'unknown';
}
// Only allow console links to known, official provider hosts.
const ALLOWED_LINK_HOSTS = new Set([
  'claude.ai', 'console.anthropic.com',
  'chatgpt.com', 'platform.openai.com',
  'console.x.ai', 'x.ai',
  'cursor.com', 'www.cursor.com',
  'aistudio.google.com',
  'openrouter.ai',
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

function fmtNum(n) {
  if (n == null || n === 0) return '—';
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
      <svg class="sparkline-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="fill:${color}">
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
    };
  }
  // Grok billing snapshot
  const sub = meta.subscription;
  if (sub && typeof sub === 'object') {
    if (sub.credit_usage_percent != null) {
      return {
        used_percent: clampPct(sub.credit_usage_percent),
        label: 'Weekly limit',
        plan: sub.subscription_tier || null,
        resets_at: (sub.period && sub.period.end) || null,
        windows: [],
      };
    }
    // Claude OAuth shape: five_hour / seven_day
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
      // Prefer 5-hour (what blocks mid-session); fall back to 7-day.
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
        };
      }
    }
    // Codex /wham/usage shape
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
      };
    }
  }
  return null;
}

function renderQuotaBlock(quota, color) {
  if (!quota || quota.used_percent == null) return '';
  const pct = quota.used_percent;
  const cls = pctClass(pct);
  const fill = barColor(pct, color);
  const reset = fmtReset(quota.resets_at);
  const extraWindows = (quota.windows || []).filter(w =>
    w && w.used_percent != null && w.label !== (quota.label || '').replace(' limit','')
  );
  const windowRows = extraWindows.map(w => {
    const wp = clampPct(w.used_percent);
    return `
      <div class="quota-meta" style="margin-top:.4rem">
        <span><strong>${esc(w.label)}</strong></span>
        <span class="${pctClass(wp)}">${Math.round(wp)}%</span>
      </div>
      <div class="quota-track" style="height:7px">
        <i class="quota-fill" style="width:${wp}%;background:${barColor(wp, color)}"></i>
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
        </div>
      </div>
      <div class="quota-track">
        <i class="quota-fill" style="width:${pct}%;background:${fill}"></i>
      </div>
      <div class="quota-meta">
        <span>${quota.plan ? `Plan: <strong>${esc(quota.plan)}</strong>` : ''}</span>
        <span>${Math.round(100 - pct)}% remaining</span>
      </div>
      ${windowRows}
    </div>
  `;
}

async function load(opts) {
  const days = $('days').value;
  const refresh = opts && opts.forceRefresh ? '&refresh=1' : '';
  $('status').textContent = 'Collecting…';
  try {
    const res = await fetch(`/api/usage?days=${days}${refresh}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    render(data);
    $('status').textContent =
      `${data.period_start} → ${data.period_end} · fetched ${new Date(data.generated_at).toLocaleTimeString()}`;
  } catch (e) {
    $('status').textContent = 'Error';
    $('providers').innerHTML = `<div class="empty" style="color:var(--yellow)">${esc(e.message)}</div>`;
  }
}

function render(data) {
  const totalCost = data.providers
    .map(p => p.cost_usd)
    .filter(v => v != null)
    .reduce((a, b) => a + b, 0);
  const hasCost = data.providers.some(p => p.cost_usd != null);
  const totalTok = data.providers.reduce(
    (a, p) => a + (p.input_tokens||0) + (p.output_tokens||0)
      + (p.cache_read_tokens||0) + (p.cache_write_tokens||0), 0
  );
  const totalReq = data.providers.reduce((a, p) => a + (p.requests||0), 0);
  const active = data.providers.filter(p => p.source !== 'unavailable').length;

  $('summary').innerHTML = `
    <div class="card"><div class="label">Combined cost</div><div class="value">${hasCost ? fmtCost(totalCost) : '—'}</div><div class="sub">where known</div></div>
    <div class="card"><div class="label">Total tokens</div><div class="value">${fmtTok(totalTok)}</div><div class="sub">${fmtNum(totalTok)} raw</div></div>
    <div class="card"><div class="label">Requests</div><div class="value">${fmtNum(totalReq)}</div><div class="sub">across providers</div></div>
    <div class="card"><div class="label">Active sources</div><div class="value">${active}/${data.providers.length}</div><div class="sub">configured or local</div></div>
  `;

  $('providers').innerHTML = data.providers.map(p => {
    const est = p.meta && p.meta.estimated;
    const quota = extractQuota(p);
    const models = (p.models || []).slice(0, 8);
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

    // Prefer short notes; hide long HTTP error dumps
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
      <article class="card provider ${providerClass}">
        <h2>
          <span>${esc(p.display_name)}</span>
          <span class="badge ${sourceClass}">${esc(p.source)}</span>
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
        </table></div>` : ''}
        ${notes ? `<div class="notes">${notes}</div>` : ''}
        ${consoleUrl ? `<div class="notes"><a href="${esc(consoleUrl)}" target="_blank" rel="noopener">Open console ↗</a></div>` : ''}
      </article>
    `;
  }).join('');
}

$('refresh').addEventListener('click', () => load({ forceRefresh: true }));
$('days').addEventListener('change', () => load());
load();
