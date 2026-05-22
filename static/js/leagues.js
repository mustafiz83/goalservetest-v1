let allLeagues = [];
let currentPage = 1;
let searchDebounce = null;
let paginationMeta = {};

const filterState = {
    liveOnly: false,
    heatmapOnly: false,
};
let sortMode = 'default';

function getPerPage() {
    return parseInt(document.getElementById('perPageSelect').value, 10) || 50;
}

function apiUrl() {
    const params = new URLSearchParams();
    if (filterState.liveOnly) params.set('live_only', 'true');
    if (filterState.heatmapOnly) params.set('heatmap_only', 'true');
    if (sortMode && sortMode !== 'default') params.set('sort', sortMode);
    const q = document.getElementById('searchInput').value.trim();
    const country = document.getElementById('countryFilter').value;
    if (q) params.set('q', q);
    if (country) params.set('country', country);
    params.set('page', String(currentPage));
    params.set('per_page', String(getPerPage()));
    return `/api/v1/leagues?${params.toString()}`;
}

function updateFilterButtonStates() {
    document.getElementById('btnLiveOnly')?.classList.toggle('active', filterState.liveOnly);
    document.getElementById('btnLiveOnly')?.setAttribute('aria-pressed', filterState.liveOnly ? 'true' : 'false');
    document.getElementById('btnHeatmapOnly')?.classList.toggle('active', filterState.heatmapOnly);
    document.getElementById('btnHeatmapOnly')?.setAttribute('aria-pressed', filterState.heatmapOnly ? 'true' : 'false');
    const nextOn = sortMode === 'next_match';
    document.getElementById('btnSortNextMatch')?.classList.toggle('active', nextOn);
    document.getElementById('btnSortNextMatch')?.setAttribute('aria-pressed', nextOn ? 'true' : 'false');
}

function bindFilterButtons() {
    document.getElementById('btnLiveOnly')?.addEventListener('click', () => {
        filterState.liveOnly = !filterState.liveOnly;
        updateFilterButtonStates();
        resetPageAndLoad();
    });
    document.getElementById('btnHeatmapOnly')?.addEventListener('click', () => {
        filterState.heatmapOnly = !filterState.heatmapOnly;
        updateFilterButtonStates();
        resetPageAndLoad();
    });
    document.getElementById('btnSortNextMatch')?.addEventListener('click', () => {
        sortMode = sortMode === 'next_match' ? 'default' : 'next_match';
        updateFilterButtonStates();
        resetPageAndLoad();
    });
}

async function loadLeagues() {
    const content = document.getElementById('content');
    const loadingNote = sortMode === 'next_match'
        ? 'Loading & sorting by next match time (may take 30–90s)…'
        : 'Loading leagues… (next matches for this page may take 20–40s)';
    content.innerHTML = `
        <div class="loading">
            <div class="spinner"></div>
            <p>${loadingNote}</p>
        </div>`;

    try {
        const response = await fetch(apiUrl());
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }
        allLeagues = data.leagues || [];
        if (data.filters) {
            filterState.liveOnly = !!data.filters.live_only;
            filterState.heatmapOnly = !!data.filters.heatmap_only;
        }
        if (data.sort) sortMode = data.sort;
        updateFilterButtonStates();

        paginationMeta = {
            total: data.total ?? 0,
            page: data.page ?? 1,
            per_page: data.per_page ?? getPerPage(),
            total_pages: data.total_pages ?? 1,
            has_prev: data.has_prev ?? false,
            has_next: data.has_next ?? false,
            heatmap_league_count: data.heatmap_league_count,
            sort: data.sort,
        };
        currentPage = paginationMeta.page;
        updateStatsLine(data);
        populateCountryFilter(data.countries || []);
        renderPagination();
        renderTable(allLeagues);
    } catch (err) {
        console.error(err);
        content.innerHTML = `<div class="error-box">⚠️ ${escapeHtml(err.message)}</div>`;
        document.getElementById('statsLine').textContent = 'Error loading catalog';
        document.getElementById('pagination').hidden = true;
    }
}

function updateStatsLine(data) {
    const total = data.total ?? 0;
    const hm = data.heatmap_league_count ?? '—';
    const page = data.page ?? 1;
    const pages = data.total_pages ?? 1;
    const start = total ? (page - 1) * (data.per_page || getPerPage()) + 1 : 0;
    const end = Math.min(page * (data.per_page || getPerPage()), total);
    const liveLg = data.live_league_count ?? 0;
    const liveMx = data.live_match_total ?? 0;
    const updated = data.live_feed_updated ? ` · live feed ${data.live_feed_updated}` : '';
    const bits = [];
    if (filterState.liveOnly) bits.push('filter: live');
    if (filterState.heatmapOnly) bits.push('filter: heatmap');
    if (sortMode && sortMode !== 'default') bits.push(`sort: ${sortMode}`);
    const filterLabel = bits.length ? ` · ${bits.join(', ')}` : '';
    document.getElementById('statsLine').textContent =
        `${start}–${end} of ${total} leagues · page ${page}/${pages} · ${hm} heatmap · ${liveLg} live leagues / ${liveMx} matches${filterLabel}${updated}`;
}

function populateCountryFilter(countries) {
    const select = document.getElementById('countryFilter');
    const current = select.value;
    select.innerHTML = '<option value="">All countries</option>';
    countries.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c;
        opt.textContent = c;
        select.appendChild(opt);
    });
    if ([...select.options].some(o => o.value === current)) {
        select.value = current;
    }
    select.dataset.filled = '1';
}

function renderPagination() {
    const bar = document.getElementById('pagination');
    const { total_pages, page, has_prev, has_next, total } = paginationMeta;

    if (!total || total_pages <= 1) {
        bar.hidden = true;
        return;
    }

    bar.hidden = false;
    const pages = buildPageNumbers(page, total_pages);

    bar.innerHTML = `
        <button type="button" class="btn-page" data-action="first" ${has_prev ? '' : 'disabled'}>« First</button>
        <button type="button" class="btn-page" data-action="prev" ${has_prev ? '' : 'disabled'}>‹ Prev</button>
        ${pages.map(p => {
            if (p === '…') return '<span class="page-info">…</span>';
            const active = p === page ? ' active' : '';
            return `<button type="button" class="btn-page${active}" data-page="${p}">${p}</button>`;
        }).join('')}
        <button type="button" class="btn-page" data-action="next" ${has_next ? '' : 'disabled'}>Next ›</button>
        <button type="button" class="btn-page" data-action="last" ${has_next ? '' : 'disabled'}>Last »</button>
        <span class="page-info">Page ${page} of ${total_pages}</span>
    `;

    bar.querySelectorAll('[data-page]').forEach(btn => {
        btn.addEventListener('click', () => goToPage(parseInt(btn.dataset.page, 10)));
    });
    bar.querySelector('[data-action="first"]')?.addEventListener('click', () => goToPage(1));
    bar.querySelector('[data-action="prev"]')?.addEventListener('click', () => goToPage(page - 1));
    bar.querySelector('[data-action="next"]')?.addEventListener('click', () => goToPage(page + 1));
    bar.querySelector('[data-action="last"]')?.addEventListener('click', () => goToPage(total_pages));
}

function buildPageNumbers(current, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const pages = new Set([1, total, current, current - 1, current + 1]);
    const sorted = [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
    const out = [];
    let prev = 0;
    for (const p of sorted) {
        if (prev && p - prev > 1) out.push('…');
        out.push(p);
        prev = p;
    }
    return out;
}

function goToPage(page) {
    currentPage = Math.max(1, page);
    loadLeagues();
    document.getElementById('content').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resetPageAndLoad() {
    currentPage = 1;
    loadLeagues();
}

function renderTable(leagues) {
    const content = document.getElementById('content');
    if (!leagues.length) {
        content.innerHTML = '<div class="empty-box">No leagues match your filters.</div>';
        return;
    }

    const rows = leagues.map(league => {
        const hmId = league.heatmap_league_id;
        const seasonsHtml = formatSeasons(league.seasons || []);
        const nextHtml = formatNextMatches(league.next_matches || []);

        return `
            <tr>
                <td>
                    <div class="league-name">${escapeHtml(league.name)}</div>
                    <div class="league-country">${escapeHtml(league.country || '—')}</div>
                    ${league.current_season ? `<div class="feed-hint">Current: ${escapeHtml(league.current_season)}</div>` : ''}
                </td>
                <td class="id-cell">${escapeHtml(league.league_id)}</td>
                <td class="id-cell">
                    ${hmId
                        ? `${escapeHtml(hmId)}<div class="feed-hint">${escapeHtml(league.heatmap_feed_path || '')}</div>`
                        : '<span class="id-none">—</span>'}
                </td>
                <td>${hmId ? '<span class="badge badge-yes">Yes</span>' : '<span class="badge badge-no">No</span>'}</td>
                <td>${formatLiveBadge(league)}</td>
                <td class="next-matches-cell">${nextHtml}</td>
                <td class="seasons-cell">${seasonsHtml}</td>
                <td class="actions">
                    <a href="/?league=${encodeURIComponent(league.league_id)}" title="Open heatmap page">Heatmap UI</a>
                    <a href="/api/v1/fixtures/${encodeURIComponent(league.league_id)}" target="_blank" rel="noopener">Fixtures API</a>
                </td>
            </tr>`;
    }).join('');

    content.innerHTML = `
        <div class="league-table-wrap">
            <table class="league-table">
                <thead>
                    <tr>
                        <th>League</th>
                        <th>League ID</th>
                        <th>Heatmap league ID</th>
                        <th>Heatmap</th>
                        <th>Live now</th>
                        <th>Next match(es)</th>
                        <th>Seasons (results)</th>
                        <th>Links</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;

    content.querySelectorAll('.seasons-more').forEach(btn => {
        btn.addEventListener('click', () => {
            const panel = btn.nextElementSibling;
            if (!panel) return;
            const open = panel.hidden;
            panel.hidden = !open;
            btn.textContent = open ? 'Hide seasons' : btn.dataset.moreLabel;
        });
    });
}

function formatNextMatches(matches) {
    if (!matches.length) {
        return '<span class="id-none">No upcoming in current feed</span>';
    }
    return matches.map(m => {
        const live = m.is_live ? '<span class="badge badge-live">LIVE</span> ' : '';
        const score = m.score ? ` <span class="next-score">${escapeHtml(m.score)}</span>` : '';
        const status = m.status ? ` <span class="next-status">(${escapeHtml(m.status)})</span>` : '';
        return `
            <div class="next-match-row">
                ${live}
                <span class="next-date">${escapeHtml(m.date || '')} ${escapeHtml(m.time || '')}</span>
                <span class="next-teams">${escapeHtml(m.home_team)} vs ${escapeHtml(m.away_team)}</span>${score}${status}
                <span class="feed-hint">id ${escapeHtml(m.match_id || '')}</span>
            </div>`;
    }).join('');
}

function formatLiveBadge(league) {
    if (league.has_live_match) {
        const n = league.live_match_count || 0;
        return `<span class="badge badge-live" title="${n} live match(es)">Live · ${n}</span>`;
    }
    return '<span class="badge badge-no">—</span>';
}

function formatSeasons(seasons) {
    if (!seasons.length) {
        return '<span class="id-none">No seasons in feed</span>';
    }
    if (seasons.length <= 4) {
        return `<span class="seasons-preview">${escapeHtml(seasons.join(', '))}</span>`;
    }
    const preview = seasons.slice(-4).join(', ');
    return `
        <span class="seasons-preview">…${escapeHtml(preview)}</span>
        <button type="button" class="seasons-more" data-more-label="Show all ${seasons.length} seasons">Show all ${seasons.length} seasons</button>
        <div class="seasons-expanded" hidden>${escapeHtml(seasons.join(' · '))}</div>`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = String(text ?? '');
    return div.innerHTML;
}

document.getElementById('refreshBtn').addEventListener('click', () => loadLeagues());
document.getElementById('searchInput').addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(resetPageAndLoad, 300);
});
document.getElementById('countryFilter').addEventListener('change', resetPageAndLoad);
document.getElementById('perPageSelect').addEventListener('change', resetPageAndLoad);

bindFilterButtons();
updateFilterButtonStates();
loadLeagues();
