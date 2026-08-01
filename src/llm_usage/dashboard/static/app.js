const COLORS = {
  claude: '#d4a27f',
  openai: '#10a37f',
  codex: '#22c55e',
  grok: '#a78bfa',
  cursor: '#60a5fa',
  gemini: '#fbbf24',
  openrouter: '#2dd4bf',
  cohere: '#00b4d8',
  mistral: '#ff6b35',
  replicate: '#6366f1',
  huggingface: '#ffd93d',
};

let currentData = null;

const $ = (id) => document.getElementById(id);

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}
// Only these provider ids are used as CSS class names / lookups.
const KNOWN_PROVIDERS = new Set([
  'claude', 'openai', 'codex', 'grok', 'cursor', 'gemini', 'openrouter',
  'cohere', 'mistral', 'replicate', 'huggingface',
]);
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

async function load(opts) {
  const days = resolveDays();
  const refresh = opts && opts.forceRefresh ? '&refresh=1' : '';
  if (days == null) {
    $('status').textContent = 'Pick a start and end date';
    return;
  }

  $('status').textContent = 'Collecting…';
  try {
    const res = await fetch(`/api/usage?days=${days}${refresh}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    currentData = data;
    render(data);
    $('status').textContent =
      `${data.period_start} → ${data.period_end} · fetched ${new Date(data.generated_at).toLocaleTimeString()}`;
  } catch (e) {
    $('status').textContent = 'Error';
    $('providers').innerHTML = `<div class="empty" style="color:var(--yellow)">${esc(e.message)}</div>`;
  }
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
  const active = data.providers.filter(p => p.source !== 'unavailable').length;

  const anyEstimated = data.has_estimated_cost
    || data.providers.some(p => p.meta && p.meta.estimated);
  const pricesAsOf = data.prices_as_of ? esc(data.prices_as_of) : null;
  $('summary').innerHTML = `
    <div class="card"><div class="label">Combined cost</div><div class="value">${hasCost ? costValue : '—'}</div><div class="sub">${costSub}</div></div>
    <div class="card"><div class="label">Total tokens</div><div class="value">${fmtTok(totalTok)}</div><div class="sub">${fmtNum(totalTok)} raw</div></div>
    <div class="card"><div class="label">Requests</div><div class="value">${fmtNum(totalReq)}</div><div class="sub">across providers</div></div>
    <div class="card"><div class="label">Active sources</div><div class="value">${active}/${data.providers.length}</div><div class="sub">configured or local</div></div>
  `;
  const foot = $('estimate-footnote');
  if (foot) {
    if (anyEstimated) {
      foot.hidden = false;
      foot.textContent = pricesAsOf
        ? `~ = estimated from public list prices (as of ${pricesAsOf}) — not an invoice.`
        : '~ = estimated from public list prices — not an invoice.';
    } else {
      foot.hidden = true;
      foot.textContent = '';
    }
  }

  renderBudgetAlerts(data);
  renderCostBreakdown(data);
  renderUsageChart(data);
  renderForecast(data);
  updateProviderFilter(data);
  renderProviders(data);
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
    <div class="budget-alert ${isWarning ? 'warning' : ''}">
      <strong>${isWarning ? 'Budget warning' : 'Budget exceeded'}</strong>
      <p>Current cost: ${fmtCost(totalCost, data.has_estimated_cost)} of ${fmtCost(budgetLimit, false)}</p>
      <p>${Math.round(ratio * 100)}% of monthly budget used (alert at ${Math.round(alertAt * 100)}%)</p>
    </div>
  `;
}

/** Horizontal bar breakdown (no external chart library — CSP-safe). */
function renderCostBreakdown(data) {
  const breakdown = $('cost-breakdown');
  if (!breakdown) return;
  const providersWithCost = data.providers
    .filter(p => p.cost_usd != null && p.cost_usd > 0)
    .sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0));

  if (providersWithCost.length === 0) {
    breakdown.innerHTML = '<p class="muted-note">No cost data available</p>';
    return;
  }

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
          <i class="quota-fill" style="width:${pct.toFixed(1)}%;background:${color}"></i>
        </div>
      </div>`;
  }).join('');
}

function renderUsageChart(data) {
  const host = $('usage-chart');
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
    host.innerHTML = '<p class="muted-note">No daily token data in this period</p>';
    return;
  }

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

  // Sparse x labels
  const labelEvery = Math.max(1, Math.ceil(n / 8));
  const labels = sortedDays.map((day, i) => {
    if (i % labelEvery !== 0 && i !== n - 1) return '';
    const x = padL + i * slot + slot / 2;
    const short = day.slice(5); // MM-DD
    return `<text x="${x.toFixed(1)}" y="${h - 8}" text-anchor="middle" `
      + `fill="#8b9bb0" font-size="10">${esc(short)}</text>`;
  }).join('');

  host.innerHTML = `
    <svg class="usage-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Daily tokens">
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="#1e2a3a"/>
      ${bars}
      ${labels}
    </svg>
    <div class="chart-caption">Daily tokens (all providers) · peak ${fmtTok(max)}</div>
  `;
}

function renderForecast(data) {
  const forecast = $('forecast');
  if (!forecast) return;
  const totalCost = (data.billed_cost_usd || 0) + (data.estimated_cost_usd || 0);

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
      + (p.source === 'unavailable' ? ' (n/a)' : '');
    filter.appendChild(option);
  });
  if ([...filter.options].some(o => o.value === currentValue)) {
    filter.value = currentValue;
  }
}

function renderProviders(data) {
  const providerFilter = ($('provider-filter') && $('provider-filter').value) || 'all';
  const sortBy = ($('sort-by') && $('sort-by').value) || 'cost';

  let providers = [...data.providers];
  if (providerFilter !== 'all') {
    providers = providers.filter(p => p.provider === providerFilter);
  }

  providers.sort((a, b) => {
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

  $('providers').innerHTML = providers.map(p => {
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
  }).join('') || '<div class="empty">No providers match this filter</div>';
}

function exportCSV() {
  if (!currentData) return;

  const rows = [
    ['Provider', 'Source', 'Requests', 'Input Tokens', 'Output Tokens', 'Total Tokens', 'Cost (USD)'].join(',')
  ];

  currentData.providers.forEach(p => {
    const cost = p.cost_usd != null
      ? ((p.meta && p.meta.estimated) ? '~' : '') + p.cost_usd.toFixed(2)
      : '';
    const total = (p.input_tokens || 0) + (p.output_tokens || 0)
      + (p.cache_read_tokens || 0) + (p.cache_write_tokens || 0);
    rows.push([
      `"${String(p.display_name).replace(/"/g, '""')}"`,
      p.source,
      p.requests || 0,
      p.input_tokens || 0,
      p.output_tokens || 0,
      total,
      cost,
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
  if (!currentData) return;
  const billed = currentData.billed_cost_usd;
  const estimated = currentData.estimated_cost_usd;
  const totalCost = (billed || 0) + (estimated || 0);
  const totalReq = currentData.providers.reduce((a, p) => a + (p.requests || 0), 0);
  const totalTok = currentData.providers.reduce(
    (a, p) => a + (p.input_tokens || 0) + (p.output_tokens || 0)
      + (p.cache_read_tokens || 0) + (p.cache_write_tokens || 0), 0
  );

  const printWindow = window.open('', '_blank');
  if (!printWindow) {
    $('status').textContent = 'Pop-up blocked — allow pop-ups to print';
    return;
  }

  let rows = '';
  currentData.providers.forEach(p => {
    const cost = p.cost_usd != null
      ? ((p.meta && p.meta.estimated) ? '~' : '') + p.cost_usd.toFixed(2)
      : '—';
    const total = (p.input_tokens || 0) + (p.output_tokens || 0)
      + (p.cache_read_tokens || 0) + (p.cache_write_tokens || 0);
    rows += `<tr>
      <td>${esc(p.display_name)}</td>
      <td>${esc(p.source)}</td>
      <td>${fmtNum(p.requests)}</td>
      <td>${fmtNum(p.input_tokens)}</td>
      <td>${fmtNum(p.output_tokens)}</td>
      <td>${fmtNum(total)}</td>
      <td>${esc(cost)}</td>
    </tr>`;
  });

  printWindow.document.write(`<!DOCTYPE html><html><head><title>LLM Usage Report</title>
    <style>
      body{font-family:system-ui,sans-serif;padding:24px;color:#111}
      table{width:100%;border-collapse:collapse;margin-top:16px}
      th,td{border:1px solid #ddd;padding:8px;text-align:left}
      th{background:#f2f2f2}
      .summary{margin:16px 0;padding:12px;background:#f9f9f9;border-radius:6px}
    </style></head><body>
    <h1>LLM Usage Report</h1>
    <p>Period: ${esc(currentData.period_start)} to ${esc(currentData.period_end)}</p>
    <p>Generated: ${esc(new Date(currentData.generated_at).toLocaleString())}</p>
    <div class="summary">
      <p><strong>Total cost:</strong> ${fmtCost(totalCost, currentData.has_estimated_cost)}</p>
      <p><strong>Requests:</strong> ${fmtNum(totalReq)}</p>
      <p><strong>Tokens:</strong> ${fmtNum(totalTok)}</p>
    </div>
    <table><thead><tr>
      <th>Provider</th><th>Source</th><th>Requests</th><th>In</th><th>Out</th><th>Total</th><th>Cost</th>
    </tr></thead><tbody>${rows}</tbody></table>
    </body></html>`);
  printWindow.document.close();
  printWindow.focus();
  printWindow.print();
}

$('refresh').addEventListener('click', () => load({ forceRefresh: true }));
$('days').addEventListener('change', () => {
  const isCustom = $('days').value === 'custom';
  $('date-start').hidden = !isCustom;
  $('date-end').hidden = !isCustom;
  if (isCustom && !$('date-end').value) {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - 29);
    $('date-end').value = end.toISOString().slice(0, 10);
    $('date-start').value = start.toISOString().slice(0, 10);
  }
  load();
});
$('date-start').addEventListener('change', () => load());
$('date-end').addEventListener('change', () => load());
if ($('provider-filter')) {
  $('provider-filter').addEventListener('change', () => {
    if (currentData) renderProviders(currentData);
  });
}
if ($('sort-by')) {
  $('sort-by').addEventListener('change', () => {
    if (currentData) renderProviders(currentData);
  });
}
if ($('export-csv')) $('export-csv').addEventListener('click', exportCSV);
if ($('export-pdf')) {
  $('export-pdf').textContent = 'Print / PDF';
  $('export-pdf').addEventListener('click', exportPrintable);
}
load();
