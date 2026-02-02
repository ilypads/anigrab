// DOM Elements
const linkInput = document.getElementById('link-input');
const submitBtn = document.getElementById('submit-btn');
const inputError = document.getElementById('input-error');

const inputSection = document.getElementById('input-section');
const confirmSection = document.getElementById('confirm-section');
const statusSection = document.getElementById('status-section');

const mediaTitle = document.getElementById('media-title');
const mediaSize = document.getElementById('media-size');
const mediaSeeders = document.getElementById('media-seeders');
const mediaLeechers = document.getElementById('media-leechers');
const sizeRow = document.getElementById('size-row');
const seedersRow = document.getElementById('seeders-row');
const leechersRow = document.getElementById('leechers-row');

const cancelBtn = document.getElementById('cancel-btn');
const confirmBtn = document.getElementById('confirm-btn');

const progressSection = document.getElementById('progress-section');
const completeSection = document.getElementById('complete-section');
const errorSection = document.getElementById('error-section');
const dhtErrorSection = document.getElementById('dht-error-section');
const restartQbtBtn = document.getElementById('restart-qbt-btn');

// Post-processing elements
const postprocessSection = document.getElementById('postprocess-section');
const cleanNameInput = document.getElementById('clean-name-input');
const imageOptions = document.getElementById('image-options');
const noImagesMessage = document.getElementById('no-images-message');
const imageSearchInput = document.getElementById('image-search-input');
const searchImagesBtn = document.getElementById('search-images-btn');
const skipPostprocessBtn = document.getElementById('skip-postprocess');
const applyPostprocessBtn = document.getElementById('apply-postprocess');

// Plex mode elements
const plexModeToggle = document.getElementById('plex-mode-toggle');
const plexMetadata = document.getElementById('plex-metadata');
const plexYearSpan = document.getElementById('plex-year');
const plexSeasonInput = document.getElementById('plex-season');

const mullvadDot = document.getElementById('mullvad-dot');
const qbtDot = document.getElementById('qbt-dot');
const dhtNodes = document.getElementById('dht-nodes');

const newDownloadBtn = document.getElementById('new-download-btn');
const cancelDownloadBtn = document.getElementById('cancel-download-btn');

// State
let currentUrl = '';
let currentTorrentHash = '';
let eventSource = null;

// Post-processing state
let postprocessData = null;
let selectedImageUrl = null;
let selectedYear = null;
let imagesData = [];  // Store full image data for Plex year lookup
let detectedMediaType = 'series';  // 'series' or 'movie'

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    checkSystemStatus();
    setInterval(checkSystemStatus, 30000); // Check every 30 seconds

    // Check for active download in qBittorrent (works across browsers/refreshes)
    try {
        const response = await fetch('/api/active-download');
        const data = await response.json();
        if (data.active && data.hash) {
            resumeDownloadMonitoring(data.hash);
            return;
        }
    } catch (error) {
        console.error('Failed to check active downloads:', error);
    }

    // Fall back to localStorage for this browser session
    const savedHash = localStorage.getItem('activeDownloadHash');
    if (savedHash) {
        resumeDownloadMonitoring(savedHash);
    }
});

// Handle page visibility changes (user switching apps)
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && currentTorrentHash) {
        // Page became visible again - check if we need to reconnect
        if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
            resumeDownloadMonitoring(currentTorrentHash);
        }
    }
});

async function resumeDownloadMonitoring(torrentHash) {
    // Check if torrent is still active in qBittorrent
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        if (!data.qbittorrent.connected) {
            localStorage.removeItem('activeDownloadHash');
            return;
        }

        // Start monitoring the torrent
        currentTorrentHash = torrentHash;
        showSection('status');
        resetStatus();
        updateStep('mullvad', 'success', 'Connected');
        updateStep('qbittorrent', 'success', 'Connected');
        updateStep('download', 'active', 'Resuming...');

        // Connect to progress endpoint
        const encodedHash = encodeURIComponent(torrentHash);
        eventSource = new EventSource(`/api/progress?hash=${encodedHash}`);

        eventSource.addEventListener('progress', (e) => {
            const progressData = JSON.parse(e.data);
            currentTorrentHash = progressData.hash;
            updateStep('download', 'active', `Downloading... ${progressData.progress}%`);
            showProgress(progressData);
        });

        eventSource.addEventListener('complete', (e) => {
            const progressData = JSON.parse(e.data);
            updateStep('download', 'connected', 'Complete!');
            showComplete(progressData);
            localStorage.removeItem('activeDownloadHash');
            eventSource.close();
        });

        eventSource.addEventListener('not_found', () => {
            // Torrent no longer exists
            localStorage.removeItem('activeDownloadHash');
            resetToInput();
            eventSource.close();
        });

        eventSource.addEventListener('error', (e) => {
            if (e.data) {
                const errorData = JSON.parse(e.data);
                showDownloadError(errorData.message);
            }
            localStorage.removeItem('activeDownloadHash');
            eventSource.close();
        });

        eventSource.onerror = () => {
            // Don't show error immediately - might just be reconnecting
            if (eventSource.readyState === EventSource.CLOSED) {
                // Will try to reconnect on next visibility change
            }
        };
    } catch (error) {
        console.error('Failed to resume download:', error);
        localStorage.removeItem('activeDownloadHash');
    }
}

// Event Listeners
submitBtn.addEventListener('click', handleSubmit);
linkInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleSubmit();
});

cancelBtn.addEventListener('click', () => {
    showSection('input');
    currentUrl = '';
});

confirmBtn.addEventListener('click', startDownload);
newDownloadBtn.addEventListener('click', resetToInput);
cancelDownloadBtn.addEventListener('click', cancelDownload);
restartQbtBtn.addEventListener('click', restartQBittorrent);

// Post-processing event listeners
skipPostprocessBtn.addEventListener('click', resetToInput);
applyPostprocessBtn.addEventListener('click', applyPostProcess);
searchImagesBtn.addEventListener('click', searchImages);
imageSearchInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') searchImages();
});

// Plex mode toggle
plexModeToggle.addEventListener('change', () => {
    if (plexModeToggle.checked) {
        plexMetadata.classList.remove('hidden');
        // Update year from selected image
        updatePlexYear();
    } else {
        plexMetadata.classList.add('hidden');
    }
});

function updatePlexYear() {
    // Find the selected image's year
    if (selectedImageUrl && imagesData.length > 0) {
        const selectedImg = imagesData.find(img => img.cover_url === selectedImageUrl);
        if (selectedImg && selectedImg.year) {
            selectedYear = selectedImg.year;
            plexYearSpan.textContent = selectedImg.year;
            return;
        }
    }
    selectedYear = null;
    plexYearSpan.textContent = '-';
}

// Functions
async function checkSystemStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        // Update Mullvad status
        mullvadDot.className = 'status-dot';
        if (data.mullvad.status === 'connected') {
            mullvadDot.classList.add('connected');
        } else if (data.mullvad.status === 'connecting') {
            mullvadDot.classList.add('connecting');
        } else {
            mullvadDot.classList.add('disconnected');
        }

        // Update qBittorrent status
        qbtDot.className = 'status-dot';
        if (data.qbittorrent.connected) {
            if (data.qbittorrent.dht_nodes > 0) {
                qbtDot.classList.add('connected');
                dhtNodes.textContent = `(${data.qbittorrent.dht_nodes} nodes)`;
                dhtNodes.className = 'dht-info connected';
            } else {
                qbtDot.classList.add('warning');
                dhtNodes.textContent = '(0 nodes)';
                dhtNodes.className = 'dht-info warning';
            }
        } else {
            qbtDot.classList.add('disconnected');
            dhtNodes.textContent = '';
        }
    } catch (error) {
        console.error('Failed to check system status:', error);
        mullvadDot.className = 'status-dot disconnected';
        qbtDot.className = 'status-dot disconnected';
        dhtNodes.textContent = '';
    }
}

async function handleSubmit() {
    const url = linkInput.value.trim();

    if (!url) {
        showError('Please enter a URL');
        return;
    }

    setLoading(true);
    hideError();

    try {
        const response = await fetch('/api/parse-link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        const data = await response.json();

        if (!data.success) {
            showError(data.error);
            return;
        }

        // Store URL and show confirmation
        currentUrl = data.url;
        displayMediaInfo(data.info);
        showSection('confirm');
    } catch (error) {
        showError('Failed to parse link. Please try again.');
        console.error(error);
    } finally {
        setLoading(false);
    }
}

function displayMediaInfo(info) {
    mediaTitle.textContent = info.title || 'Unknown';

    if (info.size) {
        mediaSize.textContent = info.size;
        sizeRow.classList.remove('hidden');
    } else {
        sizeRow.classList.add('hidden');
    }

    if (info.seeders !== null) {
        mediaSeeders.textContent = info.seeders;
        seedersRow.classList.remove('hidden');
    } else {
        seedersRow.classList.add('hidden');
    }

    if (info.leechers !== null) {
        mediaLeechers.textContent = info.leechers;
        leechersRow.classList.remove('hidden');
    } else {
        leechersRow.classList.add('hidden');
    }
}

function startDownload() {
    showSection('status');
    resetStatus();

    // Close any existing event source
    if (eventSource) {
        eventSource.close();
    }

    // Start SSE connection
    const encodedUrl = encodeURIComponent(currentUrl);
    eventSource = new EventSource(`/api/download?url=${encodedUrl}`);

    eventSource.addEventListener('status', (e) => {
        const data = JSON.parse(e.data);
        console.log('Status:', data);
    });

    eventSource.addEventListener('mullvad', (e) => {
        const data = JSON.parse(e.data);
        updateStep('mullvad', data.status, data.message);

        // Update footer dot
        mullvadDot.className = 'status-dot';
        if (data.status === 'connected') {
            mullvadDot.classList.add('connected');
        } else if (data.status === 'connecting') {
            mullvadDot.classList.add('connecting');
        } else {
            mullvadDot.classList.add('disconnected');
        }
    });

    eventSource.addEventListener('qbittorrent', (e) => {
        const data = JSON.parse(e.data);
        updateStep('qbittorrent', data.connected ? 'connected' : 'error', data.message);

        // Update footer dot
        qbtDot.className = 'status-dot';
        if (data.connected) {
            qbtDot.classList.add('connected');
        } else {
            qbtDot.classList.add('disconnected');
        }
    });

    eventSource.addEventListener('progress', (e) => {
        const data = JSON.parse(e.data);
        currentTorrentHash = data.hash;
        // Save to localStorage so we can resume if user switches apps
        localStorage.setItem('activeDownloadHash', data.hash);
        updateStep('download', 'active', `Downloading... ${data.progress}%`);
        showProgress(data);
    });

    eventSource.addEventListener('complete', (e) => {
        const data = JSON.parse(e.data);
        updateStep('download', 'connected', 'Complete!');
        showComplete(data);
        localStorage.removeItem('activeDownloadHash');
        eventSource.close();
    });

    eventSource.addEventListener('dht_error', (e) => {
        const data = JSON.parse(e.data);
        showDhtError(data.message);
        localStorage.removeItem('activeDownloadHash');
        eventSource.close();
    });

    eventSource.addEventListener('error', (e) => {
        if (e.data) {
            const data = JSON.parse(e.data);
            showDownloadError(data.message);
            localStorage.removeItem('activeDownloadHash');
        }
        // Don't remove from localStorage on connection lost - might reconnect
        eventSource.close();
    });

    eventSource.onerror = () => {
        // Connection error
        if (eventSource.readyState === EventSource.CLOSED) {
            return; // Normal close
        }
        showDownloadError('Connection to server lost');
        eventSource.close();
    };
}

function updateStep(step, status, message) {
    const stepEl = document.getElementById(`step-${step}`);
    const icon = stepEl.querySelector('.icon');
    const messageEl = document.getElementById(
        step === 'mullvad' ? 'mullvad-message' :
        step === 'qbittorrent' ? 'qbt-message' : 'download-message'
    );

    // Update icon class - CSS handles the visual (colored circles)
    icon.className = 'icon';
    if (status === 'connected' || status === 'success') {
        icon.classList.add('success');
    } else if (status === 'connecting' || status === 'active') {
        icon.classList.add('active');
    } else if (status === 'error') {
        icon.classList.add('error');
    } else {
        icon.classList.add('pending');
    }

    messageEl.textContent = message;
}

function showProgress(data) {
    progressSection.classList.remove('hidden');
    completeSection.classList.add('hidden');
    errorSection.classList.add('hidden');

    document.getElementById('progress-name').textContent = data.name;
    document.getElementById('progress-bar').style.width = `${data.progress}%`;
    document.getElementById('progress-percent').textContent = `${data.progress}%`;
    document.getElementById('progress-speed').textContent = data.speed;
    document.getElementById('progress-eta').textContent = `ETA: ${data.eta}`;
    document.getElementById('progress-downloaded').textContent = data.downloaded;
    document.getElementById('progress-total').textContent = data.size;
}

function showComplete(data) {
    progressSection.classList.add('hidden');
    completeSection.classList.remove('hidden');
    errorSection.classList.add('hidden');

    document.getElementById('complete-name').textContent = data.name;

    // Start post-processing after a brief delay
    setTimeout(() => {
        startPostProcess(data);
    }, 1500);
}

function showDhtError(message) {
    progressSection.classList.add('hidden');
    completeSection.classList.add('hidden');
    errorSection.classList.add('hidden');
    dhtErrorSection.classList.remove('hidden');
    newDownloadBtn.classList.add('hidden');

    // Reset button state
    restartQbtBtn.disabled = false;
    restartQbtBtn.textContent = 'Restart qBittorrent';
    document.querySelector('.warning-hint').textContent = 'Restart qBittorrent to connect to the DHT network';
}

function showDownloadError(message) {
    progressSection.classList.add('hidden');
    completeSection.classList.add('hidden');
    dhtErrorSection.classList.add('hidden');
    errorSection.classList.remove('hidden');
    newDownloadBtn.classList.remove('hidden');

    document.getElementById('error-message').textContent = message;

    // Mark current step as error
    const downloadStep = document.getElementById('step-download');
    const icon = downloadStep.querySelector('.icon');
    icon.className = 'icon error';
}

function resetStatus() {
    // Reset all steps to pending
    ['mullvad', 'qbittorrent', 'download'].forEach(step => {
        updateStep(step, 'pending', 'Waiting...');
    });

    progressSection.classList.add('hidden');
    completeSection.classList.add('hidden');
    errorSection.classList.add('hidden');
    dhtErrorSection.classList.add('hidden');
    newDownloadBtn.classList.add('hidden');
}

function resetToInput() {
    showSection('input');
    linkInput.value = '';
    currentUrl = '';
    currentTorrentHash = '';
}

async function restartQBittorrent() {
    restartQbtBtn.disabled = true;
    restartQbtBtn.textContent = 'Restarting...';

    try {
        const response = await fetch('/api/restart-qbittorrent', {
            method: 'POST'
        });

        const data = await response.json();

        if (!data.success) {
            // Failed to restart
            restartQbtBtn.textContent = 'Restart qBittorrent';
            restartQbtBtn.disabled = false;
            document.querySelector('.warning-hint').textContent = data.message;
            return;
        }

        // Restart succeeded - now poll for DHT nodes for up to 3 minutes
        const maxWaitTime = 180; // 3 minutes in seconds
        const pollInterval = 5; // Check every 5 seconds
        let elapsed = 0;

        while (elapsed < maxWaitTime) {
            const remaining = maxWaitTime - elapsed;
            const mins = Math.floor(remaining / 60);
            const secs = remaining % 60;
            restartQbtBtn.textContent = `Waiting for DHT... ${mins}:${secs.toString().padStart(2, '0')}`;

            await new Promise(resolve => setTimeout(resolve, pollInterval * 1000));
            elapsed += pollInterval;

            // Check status
            const statusResp = await fetch('/api/status');
            const statusData = await statusResp.json();

            // Update footer status
            if (statusData.qbittorrent.connected) {
                dhtNodes.textContent = `(${statusData.qbittorrent.dht_nodes} nodes)`;
                if (statusData.qbittorrent.dht_nodes > 0) {
                    dhtNodes.className = 'dht-info connected';
                    qbtDot.className = 'status-dot connected';
                    // Success! Start the download
                    restartQbtBtn.textContent = 'Retrying download...';
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    startDownload();
                    return;
                }
            }
        }

        // Timed out after 3 minutes
        restartQbtBtn.textContent = 'Restart qBittorrent';
        restartQbtBtn.disabled = false;
        document.querySelector('.warning-hint').textContent =
            'Still 0 nodes after 3 minutes. Check Mullvad connection and try again.';

    } catch (error) {
        console.error('Failed to restart qBittorrent:', error);
        restartQbtBtn.textContent = 'Restart qBittorrent';
        restartQbtBtn.disabled = false;
        document.querySelector('.warning-hint').textContent = 'Failed to restart. Try again.';
    }
}

async function cancelDownload() {
    if (!currentTorrentHash) {
        showDownloadError('Cannot cancel: no torrent hash available');
        return;
    }

    // Close the event source to stop monitoring
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    cancelDownloadBtn.disabled = true;
    cancelDownloadBtn.textContent = 'Canceling...';

    try {
        const response = await fetch('/api/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hash: currentTorrentHash })
        });

        const data = await response.json();

        if (data.success) {
            resetToInput();
        } else {
            showDownloadError(data.message || 'Failed to cancel download');
        }
    } catch (error) {
        console.error('Failed to cancel download:', error);
        showDownloadError('Failed to cancel download');
    } finally {
        cancelDownloadBtn.disabled = false;
        cancelDownloadBtn.textContent = 'Cancel Download';
    }
}


function showSection(section) {
    inputSection.classList.add('hidden');
    confirmSection.classList.add('hidden');
    statusSection.classList.add('hidden');

    // Reset post-processing state
    postprocessSection.classList.add('hidden');
    postprocessData = null;
    selectedImageUrl = null;

    if (section === 'input') {
        inputSection.classList.remove('hidden');
    } else if (section === 'confirm') {
        confirmSection.classList.remove('hidden');
    } else if (section === 'status') {
        statusSection.classList.remove('hidden');
    }
}

function setLoading(loading) {
    submitBtn.disabled = loading;
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoader = submitBtn.querySelector('.btn-loader');

    if (loading) {
        btnText.classList.add('hidden');
        btnLoader.classList.remove('hidden');
    } else {
        btnText.classList.remove('hidden');
        btnLoader.classList.add('hidden');
    }
}

function showError(message) {
    inputError.textContent = message;
    inputError.classList.remove('hidden');
}

function hideError() {
    inputError.classList.add('hidden');
}

// Post-processing functions
async function startPostProcess(data) {
    try {
        const response = await fetch('/api/post-process/detect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                torrent_name: data.name,
                folder_path: data.folder_path || ''
            })
        });

        const result = await response.json();

        if (!result.success) {
            // If detection fails, just show the new download button
            newDownloadBtn.classList.remove('hidden');
            return;
        }

        // Store post-process data
        postprocessData = result;
        selectedImageUrl = null;
        selectedYear = null;
        detectedMediaType = result.media_type || 'series';

        // Show post-processing UI
        completeSection.classList.add('hidden');
        postprocessSection.classList.remove('hidden');

        // Set detected name
        cleanNameInput.value = result.detected_name;
        imageSearchInput.value = result.detected_name;

        // Update UI based on media type
        if (detectedMediaType === 'movie') {
            // For movies, hide season input and show movie indicator
            plexSeasonInput.parentElement.classList.add('hidden');
            document.querySelector('.plex-hint').textContent = 'Creates: Movie Name (Year)/Movie Name (Year).mkv';
        } else {
            // For series, show season input
            plexSeasonInput.parentElement.classList.remove('hidden');
            document.querySelector('.plex-hint').textContent = 'Creates: Show (Year)/Season XX/sXXeXX format';
        }

        // Show existing show info if found
        if (result.existing_show) {
            console.log('Found existing show:', result.existing_show);
        }

        // Set detected season for Plex mode
        if (result.detected_season) {
            plexSeasonInput.value = result.detected_season;
        } else {
            plexSeasonInput.value = 1;
        }

        // Display images
        displayImages(result.images);

    } catch (error) {
        console.error('Post-process detection failed:', error);
        newDownloadBtn.classList.remove('hidden');
    }
}

function displayImages(images) {
    imageOptions.innerHTML = '';
    imagesData = images || [];  // Store for Plex year lookup

    if (!images || images.length === 0) {
        noImagesMessage.classList.remove('hidden');
        return;
    }

    noImagesMessage.classList.add('hidden');

    images.forEach((img, index) => {
        const div = document.createElement('div');
        div.className = 'image-option';
        div.dataset.url = img.cover_url;

        const imgEl = document.createElement('img');
        imgEl.src = img.cover_url;
        imgEl.alt = img.title;
        imgEl.loading = 'lazy';

        const label = document.createElement('span');
        label.className = 'image-label';
        label.textContent = img.title;
        if (img.year) {
            label.textContent += ` (${img.year})`;
        }

        div.appendChild(imgEl);
        div.appendChild(label);

        // Click to select
        div.addEventListener('click', () => {
            // Remove selection from others
            document.querySelectorAll('.image-option').forEach(el => {
                el.classList.remove('selected');
            });
            // Select this one
            div.classList.add('selected');
            selectedImageUrl = img.cover_url;
            // Update Plex year if in Plex mode
            if (plexModeToggle.checked) {
                updatePlexYear();
            }
        });

        // Auto-select first image
        if (index === 0) {
            div.classList.add('selected');
            selectedImageUrl = img.cover_url;
        }

        imageOptions.appendChild(div);
    });

    // Update Plex year if already in Plex mode
    if (plexModeToggle.checked) {
        updatePlexYear();
    }
}

async function searchImages() {
    const query = imageSearchInput.value.trim();
    if (!query) return;

    searchImagesBtn.disabled = true;
    searchImagesBtn.textContent = '...';

    try {
        const response = await fetch('/api/post-process/search-images', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const result = await response.json();

        if (result.success) {
            displayImages(result.images);
        }
    } catch (error) {
        console.error('Image search failed:', error);
    } finally {
        searchImagesBtn.disabled = false;
        searchImagesBtn.textContent = 'Search';
    }
}

async function applyPostProcess() {
    if (!postprocessData) return;

    applyPostprocessBtn.disabled = true;
    applyPostprocessBtn.textContent = 'Applying...';

    try {
        let response;
        const name = cleanNameInput.value.trim();

        if (plexModeToggle.checked && detectedMediaType === 'movie') {
            // Use movie restructure endpoint
            response = await fetch('/api/post-process/movie-restructure', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    folder_path: postprocessData.folder_path,
                    movie_name: name,
                    year: selectedYear,
                    image_url: selectedImageUrl
                })
            });
        } else if (plexModeToggle.checked) {
            // Use Plex series restructure endpoint
            response = await fetch('/api/post-process/plex-restructure', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    folder_path: postprocessData.folder_path,
                    show_name: name,
                    year: selectedYear,
                    season: parseInt(plexSeasonInput.value) || 1,
                    image_url: selectedImageUrl
                })
            });
        } else {
            // Use regular apply endpoint
            response = await fetch('/api/post-process/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    folder_path: postprocessData.folder_path,
                    new_name: name,
                    image_url: selectedImageUrl
                })
            });
        }

        const result = await response.json();

        if (result.success) {
            // Show success and reset
            postprocessSection.classList.add('hidden');
            completeSection.classList.remove('hidden');

            // Show the final name (with year if Plex mode)
            let displayName = name;
            if (plexModeToggle.checked && selectedYear) {
                displayName = `${name} (${selectedYear})`;
            }
            document.getElementById('complete-name').textContent = displayName;
            newDownloadBtn.classList.remove('hidden');
        } else {
            alert('Failed to apply changes: ' + result.error);
        }
    } catch (error) {
        console.error('Apply post-process failed:', error);
        alert('Failed to apply changes');
    } finally {
        applyPostprocessBtn.disabled = false;
        applyPostprocessBtn.textContent = 'Apply';
    }
}
