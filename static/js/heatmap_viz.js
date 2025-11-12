// static/js/heatmap_viz.js

let matchData = {
    localteam_players: {},
    visitorteam_players: {}
};

let currentMatchId = ''; // Store the currently selected match ID
let currentSeason = ''; // Store the currently loaded season

function loadFixtures() {
    const leagueId = document.getElementById('leagueId').value;
    const season = document.getElementById('seasonInput').value.trim(); 
    const select = document.getElementById('fixture-select');
    
    currentSeason = season; // Update global season tracker

    select.innerHTML = '<option value="">Loading Fixtures...</option>';
    
    // Construct the API URL based on whether a season is provided
    let apiUrl = `/api/v1/fixtures/${leagueId}`;
    if (season) {
        // Use the new path parameter for season
        apiUrl = `/api/v1/fixtures/${leagueId}/${encodeURIComponent(season)}`;
    }

    fetch(apiUrl)
        .then(response => {
            if (!response.ok) {
                return response.json().then(error => {
                    throw new Error(error.detail || `HTTP Error: ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.error) { throw new Error(data.error); }
            
            // Add Season/League info to display
            const seasonDisplay = season ? ` (${season})` : ' (Current Season)';
            document.getElementById('league-display').textContent = data.league_name + seasonDisplay;
            
            const fixtures = data.fixtures;
            select.innerHTML = '';
            
            if (fixtures.length === 0) {
                 select.innerHTML = '<option value="">No Fixtures Found</option>';
                 return;
            }
            
            const defaultOption = document.createElement('option');
            defaultOption.value = "";
            defaultOption.textContent = `-- Select a Match --`;
            select.appendChild(defaultOption);

            // Get default match ID from hidden input
            const defaultMatchId = document.getElementById('matchId').value; 

            fixtures.forEach(fixture => {
                const option = document.createElement('option');
                option.value = fixture.match_id;
                option.textContent = fixture.display;
                
                // Automatically select the default match if it exists
                if (fixture.match_id === defaultMatchId) {
                    option.selected = true;
                    currentMatchId = fixture.match_id;
                }
                select.appendChild(option);
            });
            
            // Load the heatmap for the pre-selected match (if any)
            if (currentMatchId) {
                // Pass the currentSeason when loading the heatmap
                loadHeatmap(leagueId, currentMatchId, currentSeason);
            }
        })
        .catch(error => {
            console.error('Error fetching fixtures:', error);
            // Revert select content on error
            select.innerHTML = '<option value="">Error Loading Fixtures</option>';
            alert(`Error loading fixtures: ${error.message}. Check console for details.`);
            document.getElementById('league-display').textContent = 'Error Loading League Data';
        });
}

function loadHeatmapFromFixture() {
    const leagueId = document.getElementById('leagueId').value;
    const matchId = document.getElementById('fixture-select').value;
    currentMatchId = matchId; 
    
    if (matchId) {
        // Pass the currentSeason when loading the heatmap
        loadHeatmap(leagueId, matchId, currentSeason); 
    } else {
        // Clear all displays if nothing is selected
        document.getElementById('local-player-select').innerHTML = '<option value="">Select a Player</option>';
        document.getElementById('visitor-player-select').innerHTML = '<option value="">Select a Player</option>';
        clearHeatmap('local-team-heatmap');
        clearHeatmap('visitor-team-heatmap');
    }
}

function loadHeatmap(leagueId, matchId, season) { 
    // Construct the API URL, including season if provided
    let apiUrl = `/api/v1/heatmap/${leagueId}/${matchId}`;
    if (season) {
        apiUrl += `/${encodeURIComponent(season)}`;
    }
    
    // Reset Info Display
    document.getElementById('date-display').textContent = 'Loading Match Data...';
    document.getElementById('score-display').textContent = '';           
    document.getElementById('status-minute-display').textContent = '';    
    document.getElementById('local-team-name-display').textContent = 'Local Team';
    document.getElementById('visitor-team-name-display').textContent = 'Visitor Team';

    fetch(apiUrl)
        .then(response => {
            if (!response.ok) {
                return response.json().then(error => {
                    throw new Error(error.detail || `HTTP Error: ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.error) { throw new Error(data.error); }
            
            // 1. Display Match Info
            document.getElementById('date-display').textContent = `Match Date: ${data.match_date}`;
            
            // Display Score, Status, and Minute
            document.getElementById('local-team-name-display').textContent = data.localteam_name;
            document.getElementById('visitor-team-name-display').textContent = data.visitorteam_name;
            document.getElementById('score-display').textContent = `${data.localteam_name} ${data.final_score} ${data.visitorteam_name}`;
            
            let statusText = `Status: ${data.match_status}`;
            if (data.match_status === 'Live' && data.live_minute !== 'N/A') {
                statusText += ` (Min ${data.live_minute}')`;
            }
            document.getElementById('status-minute-display').textContent = statusText;

            // 2. Store and Populate Player Data
            matchData.localteam_players = data.localteam_players;
            matchData.visitorteam_players = data.visitorteam_players;
            
            populatePlayerSelect('local', data.localteam_players);
            populatePlayerSelect('visitor', data.visitorteam_players);

            // Clear initial heatmaps
            clearHeatmap('local-team-heatmap');
            clearHeatmap('visitor-team-heatmap');
        })
        .catch(error => {
            console.error('Error fetching heatmap data:', error);
            alert(`Error loading match data: ${error.message}. Check console for details.`);
            document.getElementById('date-display').textContent = 'Error Loading Match Data';
        });
}

function clearHeatmap(containerId) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    
    const message = document.createElement('div');
    message.style.textAlign = 'center';
    message.style.paddingTop = '150px'; 
    message.style.color = 'white';
    message.style.fontWeight = 'bold';
    message.innerText = `Select a player to view their heatmap.`;
    container.appendChild(message);
}


function populatePlayerSelect(team, players) {
    const selectElementId = `${team}-player-select`;
    const select = document.getElementById(selectElementId);
    
    select.innerHTML = '<option value="">Select a Player</option>';

    const playerIds = Object.keys(players).sort((a, b) => {
         const nameA = (players[a].name || '').toLowerCase();
         const nameB = (players[b].name || '').toLowerCase();
         if (nameA < nameB) return -1;
         if (nameA > nameB) return 1;
         return 0;
    }); 

    if (playerIds.length === 0) {
        const option = document.createElement('option');
        option.textContent = `No Players Found`;
        option.disabled = true;
        select.appendChild(option);
        return;
    }

    playerIds.forEach(id => {
        const player = players[id];
        const option = document.createElement('option');
        option.textContent = player.name; 
        option.value = id;
        select.appendChild(option);
    });
}

function displayPlayerHeatmap(team) {
    const selectElementId = `${team}-player-select`;
    const containerId = `${team}-team-heatmap`;
    const selectedPlayerId = document.getElementById(selectElementId).value;
    
    clearHeatmap(containerId);

    if (!selectedPlayerId) {
        return;
    }
    
    let playerData;
    if (team === 'local') {
        playerData = matchData.localteam_players[selectedPlayerId];
    } else {
        playerData = matchData.visitorteam_players[selectedPlayerId];
    }
    
    if (playerData && playerData.heatmap_data.length > 0) {
        renderHeatmap(containerId, playerData.heatmap_data);
    } else {
        const container = document.getElementById(containerId);
        container.innerHTML = '';
        const message = document.createElement('div');
        message.style.textAlign = 'center';
        message.style.paddingTop = '150px';
        message.style.color = 'yellow';
        message.style.fontWeight = 'bold';
        message.innerText = `No movement data found for selected player.`;
        container.appendChild(message);
    }
}

function renderHeatmap(containerId, heatmapData) {
    const container = document.getElementById(containerId);
    
    // Clear any previous heatmap/message
    container.innerHTML = ''; 

    const width = container.offsetWidth;
    const height = container.offsetHeight;

    if (height === 0 || width === 0) {
        console.error("Container dimensions are zero. Check CSS for .heatmap-canvas");
        return;
    }

    const heatmapInstance = h337.create({
        container: container,
        radius: 40,
        maxOpacity: .7,
        minOpacity: 0,
        blur: .75
    });
    
    const scaledData = heatmapData.map(point => {
        return {
            // Scale x (0-100) to container width
            x: Math.round(point.x * (width / 100)),
            // Scale y (0-100) to container height
            y: Math.round(point.y * (height / 100)),
            value: point.value
        };
    });

    const maxVal = scaledData.reduce((max, point) => Math.max(max, point.value), 0) || 1;

    heatmapInstance.setData({
        max: maxVal,
        data: scaledData
    });
}