let allMatches = [];
let filteredLeagueId = null;
let refreshInterval = null;

async function loadLiveMatches() {
    try {
        const contentDiv = document.getElementById('content');
        contentDiv.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading live matches...</p></div>';

        const response = await fetch('/api/v1/football/live');
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to load matches');
        }

        allMatches = data.matches || [];
        updateLastUpdated(data.updated);
        displayMatches();

    } catch (error) {
        console.error('Error loading matches:', error);
        document.getElementById('content').innerHTML = 
            `<div class="error">⚠️ ${error.message}</div>`;
    }
}

function displayMatches() {
    const contentDiv = document.getElementById('content');
    
    let matches = allMatches;
    
    if (filteredLeagueId) {
        matches = allMatches.filter(m => m.league.id === filteredLeagueId);
    }

    document.getElementById('matchCount').textContent = `Total Matches: ${matches.length}`;

    if (matches.length === 0) {
        contentDiv.innerHTML = '<div class="no-matches">No matches found</div>';
        return;
    }

    const matchesHtml = matches.map(match => createMatchCard(match)).join('');
    contentDiv.innerHTML = `<div class="matches-grid">${matchesHtml}</div>`;
}

function createMatchCard(match) {
    const stats = match.stats || {};
    const possession = stats.possession || {};
    const homePos = possession.home || 50;
    const awayPos = possession.away || 50;
    const events = match.events || [];

    const statusLabel = getStatusLabel(match.status);
    const statusClass = getStatusClass(match.status);

    const recentEvents = events.slice(0, 4).map(event => createEventElement(event)).join('');

    return `
        <div class="match-card">
            <div class="league-info">
                <span class="league-badge">${match.league.is_cup ? '🏆' : '⚽'} ${match.league.name}</span>
                <span>${match.date} ${match.time}</span>
            </div>

            <div class="match-status ${statusClass}">${statusLabel}</div>

            <div class="match-score">
                <div class="team">
                    <div class="team-name">${match.home_team.name}</div>
                    <div class="time-info">${match.home_team.id}</div>
                </div>
                <div class="score-display">${match.home_team.goals} - ${match.away_team.goals}</div>
                <div class="team">
                    <div class="team-name">${match.away_team.name}</div>
                    <div class="time-info">${match.away_team.id}</div>
                </div>
            </div>

            <div class="stats-grid">
                ${createStatBox('Possession', possession.home || 0, possession.away || 0)}
                ${createStatBox('Shots on Target', stats.shots_on_target?.home || 0, stats.shots_on_target?.away || 0)}
                ${createStatBox('Corners', stats.corners?.home || 0, stats.corners?.away || 0)}
                ${createStatBox('Yellow Cards', stats.yellow_cards?.home || 0, stats.yellow_cards?.away || 0)}
                ${createStatBox('Attacks', stats.attacks?.home || 0, stats.attacks?.away || 0)}
                ${createStatBox('Dangerous Attacks', stats.dangerous_attacks?.home || 0, stats.dangerous_attacks?.away || 0)}
            </div>

            <div class="possession-bar">
                <div class="possession-label">
                    <span>Possession</span>
                    <span><span style="color: #60a5fa">${homePos}%</span> - <span style="color: #f87171">${awayPos}%</span></span>
                </div>
                <div class="possession-bar-container">
                    <div class="possession-home" style="width: ${homePos}%"></div>
                    <div class="possession-away" style="width: ${awayPos}%"></div>
                </div>
            </div>

            ${recentEvents ? `
                <div class="events-list">
                    <div class="events-title">Recent Events</div>
                    ${recentEvents}
                </div>
            ` : ''}
        </div>
    `;
}

function createStatBox(label, homeValue, awayValue) {
    const isHomeLead = homeValue > awayValue;
    const leadClass = homeValue > awayValue ? 'home-lead' : (awayValue > homeValue ? 'away-lead' : '');

    return `
        <div class="stat-box ${leadClass}">
            <div class="stat-label">${label}</div>
            <div class="stat-values">
                <span class="stat-home">${homeValue}</span>
                <span class="stat-away">${awayValue}</span>
            </div>
        </div>
    `;
}

function createEventElement(event) {
    const eventType = event.type.toLowerCase();
    const eventClass = getEventClass(eventType);
    const eventIcon = getEventIcon(eventType);
    const teamName = event.team === 'home' ? 'H' : 'A';

    return `
        <div class="event ${eventClass}">
            <div class="event-icon">${eventIcon}</div>
            <div class="event-content">
                <div class="event-player">${event.player || 'Event'}</div>
                <div class="event-team">${teamName} • ${event.assist ? 'Assist: ' + event.assist : ''}</div>
            </div>
            <span class="event-minute">${event.minute}'</span>
        </div>
    `;
}

function getStatusLabel(status) {
    const statusMap = {
        'FT': '🏁 Full Time',
        'HT': '⏸ Half Time',
        'PST': '🏁 Post-Match',
        'NOT': '⏳ Not Started',
        '45': '⏸ Half Time',
        '90': '🏁 Full Time'
    };
    return statusMap[status] || `🔴 ${status}'`;
}

function getStatusClass(status) {
    if (status === 'FT' || status === 'PST' || status === '90') return 'finished';
    if (status === 'HT' || status === '45') return 'halftime';
    return '';
}

function getEventClass(type) {
    const classes = {
        'goal': 'goal',
        'yellowcard': 'yellow',
        'redcard': 'red',
        'substitution': 'sub'
    };
    return classes[type] || '';
}

function getEventIcon(type) {
    const icons = {
        'goal': '⚽',
        'yellowcard': '🟨',
        'redcard': '🟥',
        'substitution': '🔄'
    };
    return icons[type] || '•';
}

function updateLastUpdated(timestamp) {
    document.getElementById('lastUpdated').textContent = `Last updated: ${timestamp || 'Just now'}`;
}

document.getElementById('filterBtn').addEventListener('click', () => {
    const leagueId = document.getElementById('leagueFilter').value.trim();
    if (leagueId) {
        filteredLeagueId = leagueId;
        displayMatches();
    }
});

document.getElementById('clearFilterBtn').addEventListener('click', () => {
    document.getElementById('leagueFilter').value = '';
    filteredLeagueId = null;
    displayMatches();
});

document.getElementById('refreshBtn').addEventListener('click', loadLiveMatches);

// Initial load
loadLiveMatches();

// // Auto-refresh every 30 seconds
// setInterval(loadLiveMatches, 30000);