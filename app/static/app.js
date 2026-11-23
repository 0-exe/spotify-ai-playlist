/* =====================================================
   Spotify AI Playlist Generator — Main App JS
   ===================================================== */

const API = {
  authStatus: () => fetch('/api/auth/status').then(r => r.json()),
  devices: () => fetch('/api/devices').then(r => r.json()),
  generate: (body) => fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(async r => {
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return r.json();
  }),
  setWeight: (artistId, artistName, weight) => fetch('/api/artists/weights', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artist_id: artistId, artist_name: artistName, weight }),
  }).then(r => r.json()),
  deleteWeight: (artistId) => fetch(`/api/artists/weights/${artistId}`, { method: 'DELETE' }),
  listWeights: () => fetch('/api/artists/weights').then(r => r.json()),
};

// ---- State ----
let currentTracks = [];
let currentStrategy = {};
let artistWeights = {}; // artistId -> weight
let pendingWeightUpdates = {}; // artistId -> {name, weight}
let isGenerating = false;

// ---- DOM refs ----
const $ = id => document.getElementById(id);
const promptInput   = $('prompt-input');
const limitInput    = $('limit-input');
const sortMode      = $('sort-mode');
const playlistName  = $('playlist-name');
const deviceSelect  = $('device-select');
const startPlayback = $('start-playback');
const generateBtn   = $('generate-btn');
const createBtn     = $('create-spotify-btn');
const refreshDevBtn = $('refresh-devices');
const emptyState    = $('empty-state');
const loadingState  = $('loading-state');
const resultsSection = $('results-section');
const trackList     = $('track-list');
const weightsList   = $('artist-weights-list');

// ---- Init ----
document.addEventListener('DOMContentLoaded', async () => {
  await checkConfig();
  await Promise.all([checkAuth(), loadWeights(), refreshDevices()]);

  generateBtn.addEventListener('click', onGenerate);
  createBtn.addEventListener('click', () => onCreateSpotify(false));
  refreshDevBtn.addEventListener('click', refreshDevices);
});

async function checkConfig() {
  const r = await fetch('/health').then(r => r.json()).catch(() => ({}));
  if (!r.spotify_configured || !r.gemini_configured) {
    $('config-warning').style.display = 'flex';
  }
}

async function checkAuth() {
  const { authenticated, user } = await API.authStatus().catch(() => ({ authenticated: false }));
  const section = $('auth-section');
  if (authenticated && user) {
    section.innerHTML = `
      <div class="user-pill">
        ${user.image ? `<img class="user-avatar" src="${user.image}" alt="" />` : '👤'}
        <span>${escHtml(user.display_name || user.id)}</span>
        <a href="/logout" class="muted small" style="margin-left:4px">Log out</a>
      </div>`;
  } else {
    section.innerHTML = `<a href="/login" class="btn btn-login btn-sm">Connect Spotify</a>`;
  }
}

async function refreshDevices() {
  refreshDevBtn.textContent = '⟳';
  try {
    const { devices } = await API.devices();
    deviceSelect.innerHTML = '<option value="">— Select device —</option>';
    (devices || []).forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.name;
      opt.textContent = `${deviceIcon(d.type)} ${d.name}${d.is_active ? ' ●' : ''}`;
      if (d.is_active) opt.selected = true;
      deviceSelect.appendChild(opt);
    });
    if (!devices || devices.length === 0) {
      deviceSelect.innerHTML = '<option value="">No devices found — open Spotify first</option>';
    }
  } catch (e) {
    deviceSelect.innerHTML = '<option value="">Not authenticated</option>';
  }
  refreshDevBtn.textContent = '↻';
}

function deviceIcon(type) {
  const icons = { Computer: '💻', Smartphone: '📱', Speaker: '🔊', TV: '📺', CastAudio: '🔊' };
  return icons[type] || '🎵';
}

async function loadWeights() {
  try {
    const list = await API.listWeights();
    artistWeights = {};
    list.forEach(w => { artistWeights[w.artist_id] = w.weight; });
    renderWeightsList(list);
  } catch (e) {
    console.warn('Could not load weights:', e);
  }
}

function renderWeightsList(list) {
  if (!list || list.length === 0) {
    weightsList.innerHTML = '<p class="muted small center">No saved weights yet. Use sliders in preview.</p>';
    return;
  }
  weightsList.innerHTML = list.map(w => `
    <div class="weight-item" data-id="${w.artist_id}">
      <span class="artist-name" title="${escHtml(w.artist_name)}">${escHtml(w.artist_name)}</span>
      <input type="range" min="0" max="2" step="0.1" value="${w.weight}"
        oninput="onSidebarWeightChange('${w.artist_id}', '${escHtml(w.artist_name)}', this)" />
      <span class="weight-badge ${w.weight === 0 ? 'banned' : w.weight > 1 ? 'boosted' : ''}">${w.weight.toFixed(1)}</span>
      <button class="btn-icon" onclick="onDeleteWeight('${w.artist_id}')" title="Reset">✕</button>
    </div>
  `).join('');
}

async function onSidebarWeightChange(artistId, artistName, input) {
  const weight = parseFloat(input.value);
  const badge = input.closest('.weight-item').querySelector('.weight-badge');
  badge.textContent = weight.toFixed(1);
  badge.className = 'weight-badge' + (weight === 0 ? ' banned' : weight > 1 ? ' boosted' : '');

  artistWeights[artistId] = weight;
  pendingWeightUpdates[artistId] = { name: artistName, weight };
  debounceRecalc();
}

async function onDeleteWeight(artistId) {
  await API.deleteWeight(artistId);
  delete artistWeights[artistId];
  await loadWeights();
  if (currentTracks.length) recalcAndRender();
}

// ---- Generate ----
async function onGenerate() {
  const prompt = promptInput.value.trim();
  if (!prompt) { toast('Enter a mood or vibe description', 'error'); return; }
  if (isGenerating) return;

  // Flush pending weight updates before generating
  await flushWeightUpdates();

  isGenerating = true;
  generateBtn.disabled = true;
  generateBtn.innerHTML = '<span class="btn-icon">⟳</span> Generating...';
  emptyState.style.display = 'none';
  loadingState.style.display = 'flex';
  resultsSection.style.display = 'none';
  $('playlist-created').style.display = 'none';

  const loadingSub = $('loading-sub');
  const steps = [
    'Asking Gemini AI for search strategies...',
    'Searching Spotify tracks and playlists...',
    'Applying mood filters and scoring...',
    'Almost there...',
  ];
  let stepIdx = 0;
  const stepInterval = setInterval(() => {
    stepIdx = (stepIdx + 1) % steps.length;
    loadingSub.textContent = steps[stepIdx];
  }, 2200);

  try {
    const result = await API.generate({
      prompt,
      limit: parseInt(limitInput.value) || 40,
      sort_mode: sortMode.value,
      create_on_spotify: false,
      playlist_name: '',
      device_name: deviceSelect.value,
      start_playback: false,
    });

    clearInterval(stepInterval);
    currentTracks = result.tracks;
    currentStrategy = result.search_strategy;
    renderResults(result);
    createBtn.disabled = false;
    toast(`Found ${result.tracks.length} tracks!`, 'success');
  } catch (e) {
    clearInterval(stepInterval);
    toast(`Error: ${e.message}`, 'error');
    emptyState.style.display = 'flex';
  } finally {
    isGenerating = false;
    generateBtn.disabled = false;
    generateBtn.innerHTML = '<span class="btn-icon">✨</span> Generate Playlist';
    loadingState.style.display = 'none';
  }
}

async function onCreateSpotify(startPlay) {
  if (!currentTracks.length) return;
  await flushWeightUpdates();

  createBtn.disabled = true;
  createBtn.innerHTML = '<span class="btn-icon">⟳</span> Creating...';

  try {
    const result = await API.generate({
      prompt: promptInput.value.trim(),
      limit: parseInt(limitInput.value) || 40,
      sort_mode: sortMode.value,
      create_on_spotify: true,
      playlist_name: playlistName.value.trim() || `🎵 ${promptInput.value.trim().slice(0, 40)}`,
      device_name: deviceSelect.value,
      start_playback: startPlayback.checked,
    });

    if (result.playlist) {
      const pl = result.playlist;
      $('playlist-created').style.display = 'flex';
      $('playlist-created-text').textContent =
        `Playlist "${pl.name}" created with ${pl.tracks_added} tracks.`;
      $('playlist-link').href = pl.url;
      toast('Playlist saved to Spotify! ✅', 'success');
    }

    currentTracks = result.tracks;
    renderResults(result);
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    createBtn.disabled = false;
    createBtn.innerHTML = '<span class="btn-icon">➕</span> Save to Spotify';
  }
}

// ---- Render ----
function renderResults(result) {
  loadingState.style.display = 'none';
  resultsSection.style.display = 'block';

  $('results-title').textContent = playlistName.value.trim() || `🎵 ${promptInput.value.trim().slice(0, 40)}`;
  $('results-meta').textContent =
    `${result.tracks.length} tracks · ${result.total_found} found · sorted by ${sortMode.options[sortMode.selectedIndex].text}`;
  $('mood-badge').textContent = result.mood_description || '';

  // Strategy tags
  const strategy = result.search_strategy || {};
  const tags = [
    ...(strategy.genres || []).map(g => `🎸 ${g}`),
    ...(strategy.decade_tags || []).map(d => `📅 ${d}`),
    `⚡ ${strategy.energy_level || 'medium'}`,
  ];
  $('strategy-content').innerHTML = tags.map(t =>
    `<span class="strategy-tag">${escHtml(t)}</span>`
  ).join('');

  renderTrackList(result.tracks);
}

function renderTrackList(tracks) {
  trackList.innerHTML = tracks.map((t, i) => {
    const w = t.artist_weight ?? artistWeights[t.artist_id] ?? 1.0;
    const popPct = Math.round(t.popularity);
    const scoreLabel = `${Math.round(t.final_score ?? t.popularity)}`;
    const badgeClass = w === 0 ? ' banned' : w > 1.0 ? ' boosted' : '';
    return `
    <div class="track-item" data-track-id="${t.id}" data-artist-id="${t.artist_id}" data-artist-name="${escHtml(t.artist_name)}">
      <span class="track-num">${i + 1}</span>
      ${t.album_image
        ? `<img class="track-cover" src="${t.album_image}" alt="" loading="lazy" />`
        : `<div class="track-cover-placeholder">🎵</div>`}
      <div class="track-info">
        <div class="track-name" title="${escHtml(t.name)}">${escHtml(t.name)}</div>
        <div class="track-artist">${escHtml(t.artist_name)}</div>
        ${t.preview_url
          ? `<audio src="${t.preview_url}" preload="none" style="display:none" id="preview-${t.id}"></audio>
             <button class="btn-icon" style="font-size:11px;padding:2px 6px;margin-top:3px" onclick="togglePreview('${t.id}')" title="Preview">▶</button>`
          : ''}
      </div>
      <div class="track-meta">
        <div class="popularity-bar" title="Popularity: ${popPct}">
          <div class="popularity-fill" style="width:${popPct}%"></div>
        </div>
        <span class="track-score">${popPct} pop · ${scoreLabel} score</span>
      </div>
      <div class="track-weight-col">
        <input type="range" min="0" max="2" step="0.1" value="${w.toFixed(1)}"
          oninput="onTrackWeightChange(this, '${t.artist_id}', '${escHtml(t.artist_name)}')"
          title="Artist weight for ${escHtml(t.artist_name)}" />
        <span class="mini-badge${badgeClass}" id="wb-${t.artist_id.replace(/[^a-z0-9]/gi, '_')}">${w.toFixed(1)}</span>
        ${t.spotify_url
          ? `<a href="${t.spotify_url}" target="_blank" class="btn-icon" style="font-size:11px;padding:3px 7px" title="Open in Spotify">↗</a>`
          : ''}
      </div>
    </div>`;
  }).join('');
}

// ---- Track weight slider (in preview list) ----
let recalcTimer = null;

function onTrackWeightChange(input, artistId, artistName) {
  const weight = parseFloat(input.value);
  artistWeights[artistId] = weight;
  pendingWeightUpdates[artistId] = { name: artistName, weight };

  // Update all badges for this artist across the list
  document.querySelectorAll(`[data-artist-id="${artistId}"] input[type=range]`).forEach(el => {
    el.value = weight.toFixed(1);
  });
  const safeId = artistId.replace(/[^a-z0-9]/gi, '_');
  document.querySelectorAll(`#wb-${safeId}`).forEach(el => {
    el.textContent = weight.toFixed(1);
    el.className = 'mini-badge' + (weight === 0 ? ' banned' : weight > 1 ? ' boosted' : '');
  });

  // Also update sidebar sliders if visible
  const sidebarSlider = document.querySelector(`.weight-item[data-id="${artistId}"] input`);
  if (sidebarSlider) {
    sidebarSlider.value = weight.toFixed(1);
    const badge = sidebarSlider.closest('.weight-item').querySelector('.weight-badge');
    if (badge) {
      badge.textContent = weight.toFixed(1);
      badge.className = 'weight-badge' + (weight === 0 ? ' banned' : weight > 1 ? ' boosted' : '');
    }
  }

  debounceRecalc();
}

function debounceRecalc() {
  clearTimeout(recalcTimer);
  recalcTimer = setTimeout(recalcAndRender, 800);
}

async function recalcAndRender() {
  if (!currentTracks.length) return;
  await flushWeightUpdates();
  
  // Re-request generate with updated weights (server will re-score from cached results isn't possible,
  // so we do client-side recalc for instant feedback)
  const limit = parseInt(limitInput.value) || 40;
  const mode = sortMode.value;
  const recalculated = clientSideRecalc(currentTracks, artistWeights, mode, limit);
  renderTrackList(recalculated);
  await loadWeights();
}

function clientSideRecalc(tracks, weights, mode, limit) {
  // Annotate
  const annotated = tracks.map(t => ({
    ...t,
    artist_weight: weights[t.artist_id] ?? 1.0,
    final_score: t.popularity * (weights[t.artist_id] ?? 1.0),
  }));

  // Filter hard-banned
  const eligible = annotated.filter(t => t.artist_weight > 0);

  // Sort
  if (mode === 'hidden_gems') {
    eligible.sort((a, b) => {
      const aScore = a.popularity < 55 ? a.final_score : 0;
      const bScore = b.popularity < 55 ? b.final_score : 0;
      return bScore - aScore || a.popularity - b.popularity;
    });
  } else {
    eligible.sort((a, b) => b.final_score - a.final_score);
  }

  // Apply caps
  const counts = {};
  const result = [];
  for (const t of eligible) {
    const count = counts[t.artist_id] || 0;
    const cap = t.artist_weight < 0.5 ? 2 : (mode === 'balanced' ? 3 : limit);
    if (t.artist_weight < 0.5 && t.popularity < 70 && count >= 1) continue;
    if (count >= cap) continue;
    result.push(t);
    counts[t.artist_id] = count + 1;
    if (result.length >= limit) break;
  }

  currentTracks = result;
  return result;
}

async function flushWeightUpdates() {
  const updates = Object.entries(pendingWeightUpdates);
  if (!updates.length) return;
  pendingWeightUpdates = {};
  await Promise.all(
    updates.map(([artistId, { name, weight }]) =>
      API.setWeight(artistId, name, weight).catch(console.warn)
    )
  );
}

// ---- Audio preview ----
let currentAudio = null;
let currentPreviewId = null;

function togglePreview(trackId) {
  if (currentPreviewId === trackId) {
    // Pause
    currentAudio.pause();
    currentAudio = null;
    currentPreviewId = null;
    document.querySelectorAll('[id^="preview-btn-"]').forEach(b => b.textContent = '▶');
    return;
  }
  if (currentAudio) currentAudio.pause();

  const audio = document.getElementById(`preview-${trackId}`);
  if (!audio) return;
  audio.play();
  currentAudio = audio;
  currentPreviewId = trackId;
}

// ---- Toast ----
function toast(msg, type = 'info') {
  const container = $('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ---- Utils ----
function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
