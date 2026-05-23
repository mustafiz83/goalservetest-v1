let matchData = {
    localteam_players: {},
    visitorteam_players: {}
};

let currentMatchId = '';
let currentSeason = '';
let heatmapLeagues = [];
let lastLiveHeatmapIds = [];
let lastLiveHeatmapMatches = [];

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = String(text ?? '');
    return div.innerHTML;
}

function normalizeHeatmapLiveMatches(data, season) {
    let matches = data.heatmap_live_matches || [];
    const ids = data.heatmap_live_match_ids || [];
    if (!matches.length && ids.length) {
        const seasonLabel = data.season_resolved || season || 'current season';
        matches = ids.map(id => ({
            match_id: String(id),
            season: seasonLabel,
            name: `Match ${id}`,
            home_team: '',
            away_team: '',
            status: 'live heatmap feed',
            in_fixtures_feed: false,
        }));
    }
    return matches;
}

function renderHeatmapLiveMatches(matches) {
    const section = document.getElementById('heatmap-live-section');
    const list = document.getElementById('heatmap-live-list');
    const empty = document.getElementById('heatmap-live-empty');
    if (!section || !list) return;

    section.hidden = false;
    list.innerHTML = '';

    if (!matches.length) {
        empty.hidden = false;
        return;
    }
    empty.hidden = true;

    matches.forEach(m => {
        const li = document.createElement('li');
        li.className = 'heatmap-live-item';
        li.dataset.matchId = m.match_id;
        const season = m.season || '—';
        const dateTime = [m.date, m.time].filter(Boolean).join(' ') || '—';
        const status = m.status ? `Status: ${m.status}` : '';
        const minute = m.minute ? `Min: ${m.minute}` : '';
        const score = m.score ? `Score: ${m.score}` : '';
        const inFeed = m.in_fixtures_feed
            ? 'Listed in loaded fixtures'
            : 'Live heatmap feed only (not in season fixtures list)';

        li.innerHTML = `
            <div class="match-title">${escapeHtml(m.name || m.match_id)}</div>
            <div class="match-meta">
                <span><strong>Season:</strong> ${escapeHtml(season)}</span>
                <span><strong>ID:</strong> ${escapeHtml(m.match_id)}</span>
                <span>${escapeHtml(dateTime)}</span>
                ${score ? `<span>${escapeHtml(score)}</span>` : ''}
                ${status ? `<span>${escapeHtml(status)}</span>` : ''}
                ${minute ? `<span>${escapeHtml(minute)}</span>` : ''}
            </div>
            <div class="match-meta">${escapeHtml(inFeed)}</div>
        `;

        li.addEventListener('click', () => {
            const select = document.getElementById('fixture-select');
            const opt = Array.from(select.options).find(o => o.value === m.match_id);
            if (opt) {
                select.value = m.match_id;
            }
            currentMatchId = m.match_id;
            loadHeatmap(getLeagueId(), m.match_id, currentSeason);
            document.querySelectorAll('.heatmap-live-item').forEach(el => el.classList.remove('selected'));
            li.classList.add('selected');
        });

        list.appendChild(li);
    });
}

function getLeagueId() {
    return document.getElementById('leagueSelect').value;
}

function getSeason() {
    return document.getElementById('seasonSelect').value;
}

function parseErrorDetail(detail) {
    if (!detail) return 'Unknown error';
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object') {
        const matches = detail.heatmap_live_matches || [];
        const ids = detail.heatmap_live_match_ids || [];
        let msg = detail.message || JSON.stringify(detail);
        if (matches.length) {
            msg += '\n\nLive heatmap matches:\n' + matches.map(m =>
                `• ${m.name || m.match_id} — season ${m.season || '—'}, ID ${m.match_id}`
            ).join('\n');
        } else if (ids.length) {
            msg += `\n\nLive heatmap match IDs right now: ${ids.join(', ')}`;
        }
        return msg;
    }
    return String(detail);
}

async function initHeatmapPage() {
    const leagueSelect = document.getElementById('leagueSelect');
    const hint = document.getElementById('heatmap-hint');
    hint.textContent = 'Loading leagues with heatmap feed…';

    try {
        const resp = await fetch('/api/v1/leagues?heatmap_only=true&per_page=200');
        const data = await resp.json();
        if (!resp.ok) throw new Error(parseErrorDetail(data.detail));

        heatmapLeagues = data.leagues || [];
        leagueSelect.innerHTML = '';

        if (!heatmapLeagues.length) {
            hint.textContent = 'No heatmap leagues found. Check Goalserve API key.';
            return;
        }

        const defaultId = window.HEATMAP_DEFAULT_LEAGUE || '1204';
        heatmapLeagues.forEach(lg => {
            const opt = document.createElement('option');
            opt.value = lg.league_id;
            opt.textContent = `${lg.name} (${lg.country}) — ${lg.league_id}`;
            if (lg.league_id === defaultId) opt.selected = true;
            leagueSelect.appendChild(opt);
        });

        hint.textContent =
            'Heatmap works only for matches listed in the live commentaries/_heatmap.xml feed (not finished season games).';

        leagueSelect.addEventListener('change', () => {
            populateSeasonsForLeague(leagueSelect.value);
            document.getElementById('fixture-select').innerHTML =
                '<option value="">-- Load fixtures above --</option>';
        });

        document.getElementById('seasonSelect').addEventListener('change', () => {
            document.getElementById('fixture-select').innerHTML =
                '<option value="">-- Load fixtures above --</option>';
        });

        document.getElementById('loadFixturesBtn').addEventListener('click', loadFixtures);
        document.getElementById('heatmapOnlyFixtures').addEventListener('change', () => {
            if (document.getElementById('fixture-select').options.length > 1) {
                loadFixtures();
            }
        });

        await populateSeasonsForLeague(getLeagueId());
        await loadFixtures();
    } catch (err) {
        console.error(err);
        hint.textContent = `Failed to load leagues: ${err.message}`;
    }
}

async function populateSeasonsForLeague(leagueId) {
    const seasonSelect = document.getElementById('seasonSelect');
    seasonSelect.innerHTML = '<option value="">Current season</option>';

    try {
        const resp = await fetch(`/api/v1/leagues/${encodeURIComponent(leagueId)}`);
        const lg = await resp.json();
        if (!resp.ok) return;

        const seasons = [...(lg.seasons_results || [])].reverse();
        seasons.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            seasonSelect.appendChild(opt);
        });
    } catch (err) {
        console.warn('Season list load failed', err);
    }
}

function loadFixtures() {
    const leagueId = getLeagueId();
    const season = getSeason();
    const heatmapOnly = document.getElementById('heatmapOnlyFixtures').checked;
    const select = document.getElementById('fixture-select');

    currentSeason = season;
    select.innerHTML = '<option value="">Loading fixtures…</option>';

    let apiUrl = `/api/v1/fixtures/${encodeURIComponent(leagueId)}`;
    if (season) {
        apiUrl += `/${encodeURIComponent(season)}`;
    }

    fetch(apiUrl)
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => {
                    throw new Error(parseErrorDetail(err.detail));
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.error) throw new Error(data.error);

            lastLiveHeatmapMatches = normalizeHeatmapLiveMatches(data, season);
            lastLiveHeatmapIds = lastLiveHeatmapMatches.map(m => m.match_id).filter(Boolean);

            let seasonDisplay = season ? ` (${season})` : ' (current season)';
            if (data.season_resolved && data.season_resolved !== season) {
                seasonDisplay = ` (${season} → ${data.season_resolved})`;
            } else if (data.season_resolved) {
                seasonDisplay = ` (${data.season_resolved})`;
            }

            const liveHint = lastLiveHeatmapMatches.length
                ? ` · ${lastLiveHeatmapMatches.length} live heatmap match(es) — see list below`
                : ' · no live heatmap matches in feed right now';

            document.getElementById('league-display').textContent =
                (data.league_name || 'League') + seasonDisplay + (data.feed ? ` [${data.feed}]` : '') + liveHint;

            renderHeatmapLiveMatches(lastLiveHeatmapMatches);

            if (data.note) console.info('Fixtures:', data.note);

            let fixtures = data.fixtures || [];
            if (heatmapOnly) {
                fixtures = fixtures.filter(f => f.heatmap_available);
            }

            select.innerHTML = '';

            if (!fixtures.length) {
                select.innerHTML = '';
                if (lastLiveHeatmapMatches.length) {
                    const hint = document.getElementById('heatmap-hint');
                    hint.textContent =
                        `${lastLiveHeatmapMatches.length} live heatmap match(es) in feed — select below or click a row in the list.`;
                    const defaultOption = document.createElement('option');
                    defaultOption.value = '';
                    defaultOption.textContent = '-- Select a live heatmap match --';
                    select.appendChild(defaultOption);
                    lastLiveHeatmapMatches.forEach(m => {
                        const option = document.createElement('option');
                        option.value = m.match_id;
                        option.textContent = `${m.name} · ${m.season} · ${m.match_id}`;
                        select.appendChild(option);
                    });
                    return;
                }
                const msg = heatmapOnly
                    ? 'No fixtures with live heatmap. Try current season or uncheck filter.'
                    : 'No fixtures found';
                select.innerHTML = `<option value="">${msg}</option>`;
                return;
            }

            const defaultOption = document.createElement('option');
            defaultOption.value = '';
            defaultOption.textContent = '-- Select a match --';
            select.appendChild(defaultOption);

            const defaultMatchId = document.getElementById('matchId').value;
            currentMatchId = '';

            fixtures.forEach(fixture => {
                const option = document.createElement('option');
                option.value = fixture.match_id;
                option.textContent = fixture.display || fixture.match_id;
                if (fixture.heatmap_available) {
                    option.dataset.heatmap = '1';
                }
                if (fixture.match_id === defaultMatchId && fixture.heatmap_available) {
                    option.selected = true;
                    currentMatchId = fixture.match_id;
                }
                select.appendChild(option);
            });

            if (!currentMatchId && heatmapOnly && fixtures.length === 1) {
                select.selectedIndex = 1;
                currentMatchId = fixtures[0].match_id;
            }

            if (currentMatchId) {
                loadHeatmap(leagueId, currentMatchId, currentSeason);
            }
        })
        .catch(error => {
            console.error('Error fetching fixtures:', error);
            select.innerHTML = '<option value="">Error loading fixtures</option>';
            alert(`Error loading fixtures: ${error.message}`);
            document.getElementById('league-display').textContent = 'Error loading league data';
            renderHeatmapLiveMatches([]);
        });
}

function loadHeatmapFromFixture() {
    const leagueId = getLeagueId();
    const matchId = document.getElementById('fixture-select').value;
    currentMatchId = matchId;

    if (matchId) {
        loadHeatmap(leagueId, matchId, currentSeason);
    } else {
        document.getElementById('local-player-select').innerHTML =
            '<option value="">Select a Player</option>';
        document.getElementById('visitor-player-select').innerHTML =
            '<option value="">Select a Player</option>';
        clearHeatmap('local-team-heatmap');
        clearHeatmap('visitor-team-heatmap');
    }
}

function loadHeatmap(leagueId, matchId, season) {
    let apiUrl = `/api/v1/heatmap/${encodeURIComponent(leagueId)}/${encodeURIComponent(matchId)}`;
    if (season) {
        apiUrl += `/${encodeURIComponent(season)}`;
    }

    document.getElementById('date-display').textContent = 'Loading match data…';
    document.getElementById('score-display').textContent = '';
    document.getElementById('status-minute-display').textContent = '';
    document.getElementById('local-team-name-display').textContent = 'Local Team';
    document.getElementById('visitor-team-name-display').textContent = 'Visitor Team';

    fetch(apiUrl)
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => {
                    throw new Error(parseErrorDetail(err.detail));
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.error) throw new Error(data.error);

            document.getElementById('date-display').textContent = `Match date: ${data.match_date}`;
            document.getElementById('local-team-name-display').textContent = data.localteam_name;
            document.getElementById('visitor-team-name-display').textContent = data.visitorteam_name;
            document.getElementById('score-display').textContent =
                `${data.localteam_name} ${data.final_score} ${data.visitorteam_name}`;

            let statusText = `Status: ${data.match_status}`;
            if (data.live_minute && data.live_minute !== 'N/A') {
                statusText += ` (min ${data.live_minute}')`;
            }
            if (data.heatmap_source) {
                statusText += ` · ${data.heatmap_source}`;
            }
            document.getElementById('status-minute-display').textContent = statusText;

            matchData.localteam_players = data.localteam_players;
            matchData.visitorteam_players = data.visitorteam_players;

            populatePlayerSelect('local', data.localteam_players);
            populatePlayerSelect('visitor', data.visitorteam_players);

            clearHeatmap('local-team-heatmap');
            clearHeatmap('visitor-team-heatmap');
        })
        .catch(error => {
            console.error('Error fetching heatmap data:', error);
            alert(`Error loading match data: ${error.message}`);
            document.getElementById('date-display').textContent = 'Heatmap not available for this match';
        });
}

function clearHeatmap(containerId) {
    const container = document.getElementById(containerId);
    const children = Array.from(container.children);
    children.forEach(child => {
        if (child.tagName.toLowerCase() === 'canvas' || child.classList.contains('heatmap-message')) {
            container.removeChild(child);
        }
    });

    const message = document.createElement('div');
    message.classList.add('heatmap-message');
    message.style.textAlign = 'center';
    message.style.paddingTop = '150px';
    message.innerText = 'Select a player to view their heatmap.';
    container.appendChild(message);
}

function populatePlayerSelect(team, players) {
    const select = document.getElementById(`${team}-player-select`);
    select.innerHTML = '<option value="">Select a Player</option>';

    const playerIds = Object.keys(players).sort((a, b) => {
        const nameA = (players[a].name || '').toLowerCase();
        const nameB = (players[b].name || '').toLowerCase();
        return nameA.localeCompare(nameB);
    });

    if (!playerIds.length) {
        const option = document.createElement('option');
        option.textContent = 'No players found';
        option.disabled = true;
        select.appendChild(option);
        return;
    }

    playerIds.forEach(id => {
        const option = document.createElement('option');
        option.textContent = players[id].name;
        option.value = id;
        select.appendChild(option);
    });
}

function displayPlayerHeatmap(team) {
    const selectElementId = `${team}-player-select`;
    const containerId = `${team}-team-heatmap`;
    const selectedPlayerId = document.getElementById(selectElementId).value;

    clearHeatmap(containerId);
    if (!selectedPlayerId) return;

    const playerData =
        team === 'local'
            ? matchData.localteam_players[selectedPlayerId]
            : matchData.visitorteam_players[selectedPlayerId];

    if (playerData && playerData.heatmap_data && playerData.heatmap_data.length > 0) {
        renderHeatmap(containerId, playerData.heatmap_data);
    } else {
        const container = document.getElementById(containerId);
        container.querySelector('.heatmap-message').innerText =
            'No movement data for this player.';
        container.querySelector('.heatmap-message').style.color = 'yellow';
    }
}

function renderHeatmap(containerId, heatmapData) {
    const container = document.getElementById(containerId);
    const message = container.querySelector('.heatmap-message');
    if (message) container.removeChild(message);

    const width = container.offsetWidth;
    const height = container.offsetHeight;

    if (!height || !width) {
        console.error('Container dimensions are zero.');
        return;
    }

    const heatmapInstance = h337.create({
        container: container,
        radius: 40,
        maxOpacity: 0.7,
        minOpacity: 0,
        blur: 0.75
    });

    const pitchWidthUnits = 100;
    const pitchHeightUnits = 60;
    const scaleXFactor = width / pitchWidthUnits;
    const scaleYFactor = height / pitchHeightUnits;
    const scale = Math.min(scaleXFactor, scaleYFactor);
    const scaledPitchWidth = pitchWidthUnits * scale;
    const scaledPitchHeight = pitchHeightUnits * scale;
    const offsetX = (width - scaledPitchWidth) / 2;
    const offsetY = (height - scaledPitchHeight) / 2;

    const scaledData = heatmapData.map(point => {
        const pitchUnitX = point.x;
        const pitchUnitY = (100 - point.y) * (pitchHeightUnits / 100);
        return {
            x: Math.round(pitchUnitX * scale + offsetX),
            y: Math.round(pitchUnitY * scale + offsetY),
            value: point.value
        };
    });

    const maxVal = scaledData.reduce((max, point) => Math.max(max, point.value), 0) || 1;
    heatmapInstance.setData({ max: maxVal, data: scaledData });
}
