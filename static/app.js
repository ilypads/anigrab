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
let selectedYear = null;
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

// Plex mode toggle
plexModeToggle.addEventListener('change', () => {
    if (plexModeToggle.checked) {
        plexMetadata.classList.remove('hidden');
    } else {
        plexMetadata.classList.add('hidden');
    }
});

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

        // Store post-process data for the modal
        downloadPostprocessData = result;

        // Create a folder-like object to pass to the edit modal
        const folderData = {
            path: result.folder_path,
            name: data.name,
            detected_name: result.detected_name,
            detected_season: result.detected_season,
            media_type: result.media_type || 'series'
        };

        // Open the edit modal with download context
        openEditFolderModal(folderData, 'plex', 'download');

        // Pre-populate with AniList matches if available
        if (result.anilist_matches && result.anilist_matches.length > 0) {
            editImagesData = result.anilist_matches;
            displayEditMatches(result.anilist_matches);
        }

    } catch (error) {
        console.error('Post-process detection failed:', error);
        newDownloadBtn.classList.remove('hidden');
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
                    year: selectedYear
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
                    season: parseInt(plexSeasonInput.value) || 1
                })
            });
        } else {
            // Use regular apply endpoint
            response = await fetch('/api/post-process/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    folder_path: postprocessData.folder_path,
                    new_name: name
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


// ============================================================
// LIBRARY TOOLS FUNCTIONALITY
// ============================================================

// Library DOM Elements
const downloadViewBtn = document.getElementById('download-view-btn');
const libraryViewBtn = document.getElementById('library-view-btn');
const librarySection = document.getElementById('library-section');
const libraryPathInput = document.getElementById('library-path');
const scanLibraryBtn = document.getElementById('scan-library-btn');
const scanRecursive = document.getElementById('scan-recursive');
const scanForce = document.getElementById('scan-force');
const scanType = document.getElementById('scan-type');
const scanResults = document.getElementById('scan-results');
const scanPathDisplay = document.getElementById('scan-path-display');

// Tab elements
const folderTabs = document.querySelectorAll('.folder-tab');
const folderListCleaning = document.getElementById('folder-list-cleaning');
const folderListPlex = document.getElementById('folder-list-plex');

// Batch action elements
const selectAllBtn = document.getElementById('select-all-btn');
const deselectAllBtn = document.getElementById('deselect-all-btn');
const previewSelectedBtn = document.getElementById('preview-selected-btn');
const processSelectedBtn = document.getElementById('process-selected-btn');

// Modal elements
const previewModal = document.getElementById('preview-modal');
const previewContent = document.getElementById('preview-content');
const closePreviewBtn = document.getElementById('close-preview');
const cancelPreviewBtn = document.getElementById('cancel-preview-btn');
const confirmPreviewBtn = document.getElementById('confirm-preview-btn');

const editFolderModal = document.getElementById('edit-folder-modal');
const closeEditFolderBtn = document.getElementById('close-edit-folder');
const editOriginalName = document.getElementById('edit-original-name');
const editShowName = document.getElementById('edit-show-name');
const editSeason = document.getElementById('edit-season');
const editSeasonGroup = document.getElementById('edit-season-group');
const editYearDisplay = document.getElementById('edit-year-display');
const searchAnilistBtn = document.getElementById('search-anilist-btn');
const editImageOptions = document.getElementById('edit-image-options');
const editImageSearch = document.getElementById('edit-image-search');
const editSearchImagesBtn = document.getElementById('edit-search-images-btn');
const editPreviewContent = document.getElementById('edit-preview-content');
const cancelEditBtn = document.getElementById('cancel-edit-btn');
const saveEditBtn = document.getElementById('save-edit-btn');

// Processing options
const optPlexMode = document.getElementById('opt-plex-mode');
const optMoviesDir = document.getElementById('opt-movies-dir');

// Progress/Results elements
const libraryProgress = document.getElementById('library-progress');
const libraryProgressText = document.getElementById('library-progress-text');
const libraryProgressBar = document.getElementById('library-progress-bar');
const libraryProgressCount = document.getElementById('library-progress-count');
const libraryResults = document.getElementById('library-results');
const resultProcessed = document.getElementById('result-processed');
const resultFailed = document.getElementById('result-failed');
const resultSkipped = document.getElementById('result-skipped');
const resultsDetails = document.getElementById('results-details');
const libraryDoneBtn = document.getElementById('library-done-btn');

// Library state
let libraryScanData = null;
let currentTab = 'cleaning';
let editingFolder = null;
let editSelectedImageUrl = null;
let editSelectedYear = null;
let editImagesData = [];
let pendingBatchProcess = null;
let editContext = 'library';  // 'library' or 'download' - tracks where the edit modal was opened from
let downloadPostprocessData = null;  // Stores post-process data when editing from download context

// View toggle event listeners
downloadViewBtn.addEventListener('click', () => switchView('download'));
libraryViewBtn.addEventListener('click', () => switchView('library'));

function switchView(view) {
    if (view === 'download') {
        downloadViewBtn.classList.add('active');
        libraryViewBtn.classList.remove('active');
        inputSection.classList.remove('hidden');
        librarySection.classList.add('hidden');
        // Also show confirm/status sections if they were active
    } else {
        downloadViewBtn.classList.remove('active');
        libraryViewBtn.classList.add('active');
        inputSection.classList.add('hidden');
        confirmSection.classList.add('hidden');
        statusSection.classList.add('hidden');
        librarySection.classList.remove('hidden');
    }
}

// Scan library event listener
scanLibraryBtn.addEventListener('click', scanLibrary);

async function scanLibrary() {
    scanLibraryBtn.disabled = true;
    scanLibraryBtn.textContent = 'Scanning...';

    try {
        const response = await fetch('/api/library/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: libraryPathInput.value.trim() || '',
                recursive: scanRecursive.checked,
                type: scanType.value,
                force: scanForce.checked
            })
        });

        const data = await response.json();

        if (!data.success) {
            alert('Scan failed: ' + data.error);
            return;
        }

        libraryScanData = data.results;
        displayScanResults(data);

    } catch (error) {
        console.error('Scan failed:', error);
        alert('Scan failed: ' + error.message);
    } finally {
        scanLibraryBtn.disabled = false;
        scanLibraryBtn.textContent = 'Scan';
    }
}

function displayScanResults(data) {
    scanResults.classList.remove('hidden');
    libraryProgress.classList.add('hidden');
    libraryResults.classList.add('hidden');

    // Update summary
    scanPathDisplay.textContent = data.results.path;
    document.getElementById('count-cleaning').textContent = `${data.counts.needs_cleaning} need cleaning`;
    document.getElementById('count-plex').textContent = `${data.counts.needs_plex_restructure} need Plex`;

    // Populate folder lists
    populateFolderList(folderListCleaning, data.results.needs_cleaning, 'cleaning');
    populateFolderList(folderListPlex, data.results.needs_plex_restructure, 'plex');

    // Show first non-empty tab
    if (data.counts.needs_cleaning > 0) {
        switchTab('cleaning');
    } else if (data.counts.needs_plex_restructure > 0) {
        switchTab('plex');
    }
}

function populateFolderList(container, folders, type) {
    container.innerHTML = '';

    if (!folders || folders.length === 0) {
        container.innerHTML = '<div class="empty-list-message">No folders found</div>';
        return;
    }

    folders.forEach((folder, index) => {
        const item = document.createElement('div');
        item.className = 'folder-item';
        item.dataset.index = index;
        item.dataset.type = type;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = true;
        checkbox.dataset.path = folder.path;

        const info = document.createElement('div');
        info.className = 'folder-info';

        const name = document.createElement('span');
        name.className = 'folder-name';
        name.textContent = folder.name;
        name.title = folder.path;

        const detected = document.createElement('span');
        detected.className = 'folder-detected';
        detected.innerHTML = `${folder.detected_name}`;
        if (type === 'plex' && folder.media_type) {
            detected.innerHTML += ` <span class="arrow">→</span> ${folder.media_type}`;
            if (folder.detected_season) {
                detected.innerHTML += ` S${folder.detected_season}`;
            }
        }

        info.appendChild(name);
        info.appendChild(detected);

        const editBtn = document.createElement('button');
        editBtn.className = 'folder-edit-btn';
        editBtn.textContent = 'Edit';
        editBtn.addEventListener('click', () => openEditFolderModal(folder, type));

        item.appendChild(checkbox);
        item.appendChild(info);
        item.appendChild(editBtn);
        container.appendChild(item);
    });
}

// Tab switching
folderTabs.forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
});

function switchTab(tab) {
    currentTab = tab;

    folderTabs.forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });

    folderListCleaning.classList.toggle('hidden', tab !== 'cleaning');
    folderListPlex.classList.toggle('hidden', tab !== 'plex');
}

// Select/Deselect all
selectAllBtn.addEventListener('click', () => {
    const currentList = getCurrentFolderList();
    currentList.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
});

deselectAllBtn.addEventListener('click', () => {
    const currentList = getCurrentFolderList();
    currentList.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
});

function getCurrentFolderList() {
    switch (currentTab) {
        case 'cleaning': return folderListCleaning;
        case 'plex': return folderListPlex;
        default: return folderListCleaning;
    }
}

function getSelectedFolders() {
    const currentList = getCurrentFolderList();
    const checkboxes = currentList.querySelectorAll('input[type="checkbox"]:checked');
    const paths = Array.from(checkboxes).map(cb => cb.dataset.path);

    let sourceData;
    switch (currentTab) {
        case 'cleaning':
            sourceData = libraryScanData.needs_cleaning;
            break;
        case 'plex':
            sourceData = libraryScanData.needs_plex_restructure;
            break;
        default:
            sourceData = libraryScanData.needs_cleaning;
    }

    return sourceData.filter(f => paths.includes(f.path));
}

// Preview selected
previewSelectedBtn.addEventListener('click', async () => {
    const selected = getSelectedFolders();
    if (selected.length === 0) {
        alert('No folders selected');
        return;
    }

    previewSelectedBtn.disabled = true;
    previewSelectedBtn.textContent = 'Loading...';

    try {
        const previews = await Promise.all(selected.map(async (folder) => {
            const mode = currentTab === 'plex' ? 'plex' : 'standard';
            const response = await fetch('/api/library/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    folder_path: folder.path,
                    mode: mode
                })
            });
            const data = await response.json();
            return { folder, preview: data.success ? data.preview : null, error: data.error };
        }));

        displayPreviewModal(previews);

    } catch (error) {
        console.error('Preview failed:', error);
        alert('Preview failed: ' + error.message);
    } finally {
        previewSelectedBtn.disabled = false;
        previewSelectedBtn.textContent = 'Preview Selected';
    }
});

function displayPreviewModal(previews) {
    previewContent.innerHTML = '';

    previews.forEach(({ folder, preview, error }) => {
        const div = document.createElement('div');
        div.className = 'preview-folder';

        const nameEl = document.createElement('div');
        nameEl.className = 'preview-folder-name';
        nameEl.textContent = folder.name;
        div.appendChild(nameEl);

        if (error) {
            const errorEl = document.createElement('div');
            errorEl.className = 'preview-change';
            errorEl.innerHTML = `<span class="preview-change-skip">Error: ${error}</span>`;
            div.appendChild(errorEl);
        } else if (preview) {
            // Folder rename
            if (preview.folder_rename) {
                const renameEl = document.createElement('div');
                renameEl.className = 'preview-change';
                renameEl.innerHTML = `
                    <span class="preview-change-type">Folder:</span>
                    <span class="preview-change-old">${preview.folder_rename.old}</span>
                    <span class="preview-arrow">→</span>
                    <span class="preview-change-new">${preview.folder_rename.new}</span>
                `;
                div.appendChild(renameEl);
            }

            // Structure changes
            if (preview.structure_changes && preview.structure_changes.length > 0) {
                preview.structure_changes.forEach(change => {
                    const changeEl = document.createElement('div');
                    changeEl.className = 'preview-change';
                    if (change.type === 'use_existing') {
                        changeEl.innerHTML = `<span class="preview-change-type">Using:</span><span class="preview-change-new">${change.path}</span>`;
                    } else {
                        changeEl.innerHTML = `<span class="preview-change-type">Create:</span><span class="preview-change-new">${change.path}</span>`;
                    }
                    div.appendChild(changeEl);
                });
            }

            // File renames
            if (preview.file_renames && preview.file_renames.length > 0) {
                preview.file_renames.slice(0, 5).forEach(file => {
                    const fileEl = document.createElement('div');
                    fileEl.className = 'preview-change';
                    if (file.action === 'skip') {
                        fileEl.innerHTML = `
                            <span class="preview-change-type">Skip:</span>
                            <span class="preview-change-skip">${file.old} (${file.reason})</span>
                        `;
                    } else {
                        fileEl.innerHTML = `
                            <span class="preview-change-type">File:</span>
                            <span class="preview-change-old">${file.old}</span>
                            <span class="preview-arrow">→</span>
                            <span class="preview-change-new">${file.new}</span>
                        `;
                    }
                    div.appendChild(fileEl);
                });

                if (preview.file_renames.length > 5) {
                    const moreEl = document.createElement('div');
                    moreEl.className = 'preview-change';
                    moreEl.innerHTML = `<span class="preview-change-type">...</span><span>and ${preview.file_renames.length - 5} more files</span>`;
                    div.appendChild(moreEl);
                }
            }

        }

        previewContent.appendChild(div);
    });

    pendingBatchProcess = previews.map(p => p.folder);
    previewModal.classList.remove('hidden');
}

// Modal close handlers
closePreviewBtn.addEventListener('click', () => previewModal.classList.add('hidden'));
cancelPreviewBtn.addEventListener('click', () => previewModal.classList.add('hidden'));

// Process selected folders
processSelectedBtn.addEventListener('click', () => {
    const selected = getSelectedFolders();
    if (selected.length === 0) {
        alert('No folders selected');
        return;
    }
    processFolders(selected);
});

confirmPreviewBtn.addEventListener('click', () => {
    previewModal.classList.add('hidden');
    if (pendingBatchProcess && pendingBatchProcess.length > 0) {
        processFolders(pendingBatchProcess);
    }
});

async function processFolders(folders) {
    scanResults.classList.add('hidden');
    libraryProgress.classList.remove('hidden');
    libraryResults.classList.add('hidden');

    const mode = currentTab === 'plex' ? 'plex' : 'standard';
    const total = folders.length;

    // Update progress
    function updateProgress(current, total, text) {
        libraryProgressBar.style.width = `${(current / total) * 100}%`;
        libraryProgressCount.textContent = `${current}/${total}`;
        if (text) {
            libraryProgressText.textContent = text;
        }
    }

    updateProgress(0, total, 'Starting...');

    // Prepare folder configs
    const folderConfigs = folders.map(f => ({
        path: f.path,
        show_name: f.detected_name,
        season: f.detected_season
    }));

    try {
        const response = await fetch('/api/library/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folders: folderConfigs,
                mode: mode,
                movies_dir: optMoviesDir.value.trim() || null
            })
        });

        const data = await response.json();

        if (data.success) {
            displayResults(data.results);
        } else {
            alert('Processing failed: ' + data.error);
            scanResults.classList.remove('hidden');
            libraryProgress.classList.add('hidden');
        }

    } catch (error) {
        console.error('Processing failed:', error);
        alert('Processing failed: ' + error.message);
        scanResults.classList.remove('hidden');
        libraryProgress.classList.add('hidden');
    }
}

function displayResults(results) {
    libraryProgress.classList.add('hidden');
    libraryResults.classList.remove('hidden');

    resultProcessed.textContent = results.processed;
    resultFailed.textContent = results.failed;
    resultSkipped.textContent = results.skipped;

    resultsDetails.innerHTML = '';
    results.details.forEach(detail => {
        const item = document.createElement('div');
        item.className = `result-item ${detail.status}`;

        const pathParts = detail.path.split('/');
        const name = pathParts[pathParts.length - 1] || pathParts[pathParts.length - 2];

        if (detail.status === 'success') {
            item.textContent = `✓ ${name}`;
        } else if (detail.status === 'failed') {
            item.textContent = `✗ ${name}: ${detail.reason}`;
        } else {
            item.textContent = `○ ${name}: ${detail.reason}`;
        }

        resultsDetails.appendChild(item);
    });
}

libraryDoneBtn.addEventListener('click', () => {
    libraryResults.classList.add('hidden');
    // Rescan to refresh the list
    scanLibrary();
});

// Edit Folder Modal
function openEditFolderModal(folder, type, context = 'library') {
    editingFolder = { ...folder, type };
    editSelectedImageUrl = null;
    editSelectedYear = null;
    editImagesData = [];
    editContext = context;

    editOriginalName.textContent = folder.name;
    editShowName.value = folder.detected_name;
    editImageSearch.value = folder.detected_name;
    editYearDisplay.textContent = '-';

    // Update modal title based on context
    const modalTitle = editFolderModal.querySelector('.modal-header h3');
    if (context === 'download') {
        modalTitle.textContent = 'Organize Download';
        // Update cancel button behavior for download context
        cancelEditBtn.textContent = 'Skip';
    } else {
        modalTitle.textContent = 'Edit Folder';
        cancelEditBtn.textContent = 'Cancel';
    }

    // Show/hide season based on media type
    if (type === 'plex' && folder.media_type === 'series') {
        editSeasonGroup.classList.remove('hidden');
        editSeason.value = folder.detected_season || 1;
    } else if (type === 'plex' && folder.media_type === 'movie') {
        editSeasonGroup.classList.add('hidden');
    } else {
        editSeasonGroup.classList.add('hidden');
    }

    // Clear image options (will be populated later for download context)
    if (context !== 'download') {
        editImageOptions.innerHTML = '<div class="empty-list-message">Searching AniList...</div>';
    }

    // Clear preview
    updateEditPreview();

    editFolderModal.classList.remove('hidden');

    // Auto-search AniList to get year
    if (context !== 'download') {
        autoSearchAniList(folder.detected_name);
    }
}

async function autoSearchAniList(query) {
    if (!query) return;

    try {
        const response = await fetch('/api/anilist/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const result = await response.json();

        if (result.success && result.matches.length > 0) {
            editImagesData = result.matches;
            displayEditMatches(result.matches);
        } else {
            editImageOptions.innerHTML = '<div class="empty-list-message">No results found. Try searching manually.</div>';
        }
    } catch (error) {
        console.error('Auto AniList search failed:', error);
        editImageOptions.innerHTML = '<div class="empty-list-message">Search failed. Click "Search AniList" to try again.</div>';
    }
}

closeEditFolderBtn.addEventListener('click', () => closeEditModal());
cancelEditBtn.addEventListener('click', () => closeEditModal());

function closeEditModal() {
    editFolderModal.classList.add('hidden');

    // If we're in download context and user skips, show the new download button
    if (editContext === 'download') {
        newDownloadBtn.classList.remove('hidden');
        editContext = 'library';  // Reset context
    }
}

// Search AniList for metadata (title, year, format)
searchAnilistBtn.addEventListener('click', async () => {
    const query = editShowName.value.trim();
    if (!query) return;

    searchAnilistBtn.disabled = true;
    searchAnilistBtn.textContent = '...';

    try {
        const response = await fetch('/api/anilist/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const result = await response.json();

        if (result.success && result.matches.length > 0) {
            editImagesData = result.matches;
            displayEditMatches(result.matches);
        } else {
            editImageOptions.innerHTML = '<div class="empty-list-message">No results found</div>';
        }
    } catch (error) {
        console.error('AniList search failed:', error);
    } finally {
        searchAnilistBtn.disabled = false;
        searchAnilistBtn.textContent = 'Search AniList';
    }
});

editSearchImagesBtn.addEventListener('click', async () => {
    const query = editImageSearch.value.trim();
    if (!query) return;

    editSearchImagesBtn.disabled = true;
    editSearchImagesBtn.textContent = '...';

    try {
        const response = await fetch('/api/anilist/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const result = await response.json();

        if (result.success && result.matches.length > 0) {
            editImagesData = result.matches;
            displayEditMatches(result.matches);
        }
    } catch (error) {
        console.error('AniList search failed:', error);
    } finally {
        editSearchImagesBtn.disabled = false;
        editSearchImagesBtn.textContent = 'Search';
    }
});

function displayEditMatches(matches) {
    editImageOptions.innerHTML = '';

    matches.slice(0, 8).forEach((match, index) => {
        const div = document.createElement('div');
        div.className = 'match-option';

        const label = document.createElement('span');
        label.className = 'match-label';
        label.textContent = match.title;
        if (match.year) {
            label.textContent += ` (${match.year})`;
        }
        if (match.format) {
            label.textContent += ` [${match.format}]`;
        }

        div.appendChild(label);

        div.addEventListener('click', () => {
            editImageOptions.querySelectorAll('.match-option').forEach(el => {
                el.classList.remove('selected');
            });
            div.classList.add('selected');
            editSelectedYear = match.year;
            editYearDisplay.textContent = match.year || '-';
            // Update show name to official title
            editShowName.value = match.title;
            updateEditPreview();
        });

        // Auto-select first match
        if (index === 0) {
            div.classList.add('selected');
            editSelectedYear = match.year;
            editYearDisplay.textContent = match.year || '-';
            updateEditPreview();
        }

        editImageOptions.appendChild(div);
    });
}

// Update edit preview when inputs change
editShowName.addEventListener('input', updateEditPreview);
editSeason.addEventListener('input', updateEditPreview);

// Dry run preview button
const refreshPreviewBtn = document.getElementById('refresh-preview-btn');
const editDryRunContent = document.getElementById('edit-dry-run-content');

refreshPreviewBtn.addEventListener('click', fetchDryRunPreview);

function updateEditPreview() {
    if (!editingFolder) return;

    const name = editShowName.value.trim();
    const season = parseInt(editSeason.value) || 1;
    const year = editSelectedYear;

    let previewText = '';

    if (editingFolder.type === 'plex') {
        if (editingFolder.media_type === 'movie') {
            if (year) {
                previewText = `${name} (${year})/${name} (${year}).mkv`;
            } else {
                previewText = `${name}/${name}.mkv`;
            }
        } else {
            const seasonStr = `Season ${String(season).padStart(2, '0')}`;
            const epFormat = `s${String(season).padStart(2, '0')}e##`;
            if (year) {
                previewText = `${name} (${year})/${seasonStr}/${name} (${year}) - ${epFormat}.mkv`;
            } else {
                previewText = `${name}/${seasonStr}/${name} - ${epFormat}.mkv`;
            }
        }
    } else {
        previewText = `Folder: ${name}\nFiles: ${name} - ##.mkv`;
    }

    editPreviewContent.textContent = previewText;

    // Hide dry run content when preview changes (stale data)
    editDryRunContent.classList.add('hidden');
}

async function fetchDryRunPreview() {
    if (!editingFolder) return;

    refreshPreviewBtn.disabled = true;
    refreshPreviewBtn.textContent = '...';

    try {
        const mode = editingFolder.type === 'plex' ? 'plex' : (editingFolder.type === 'images' ? 'images_only' : 'standard');
        const response = await fetch('/api/library/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                folder_path: editingFolder.path,
                mode: mode,
                show_name: editShowName.value.trim(),
                year: editSelectedYear,
                season: parseInt(editSeason.value) || 1,
                skip_images: !editSelectedImageUrl
            })
        });

        const data = await response.json();

        if (data.success) {
            displayDryRunPreview(data.preview);
        } else {
            editDryRunContent.innerHTML = `<div class="preview-error">Error: ${data.error}</div>`;
            editDryRunContent.classList.remove('hidden');
        }
    } catch (error) {
        console.error('Dry run failed:', error);
        editDryRunContent.innerHTML = `<div class="preview-error">Failed to fetch preview</div>`;
        editDryRunContent.classList.remove('hidden');
    } finally {
        refreshPreviewBtn.disabled = false;
        refreshPreviewBtn.textContent = 'Dry Run';
    }
}

function displayDryRunPreview(preview) {
    editDryRunContent.innerHTML = '';

    if (!preview) {
        editDryRunContent.innerHTML = '<div class="preview-change">No changes detected</div>';
        editDryRunContent.classList.remove('hidden');
        return;
    }

    // Folder rename
    if (preview.folder_rename) {
        const renameEl = document.createElement('div');
        renameEl.className = 'preview-change';
        renameEl.innerHTML = `
            <span class="preview-change-type">Folder:</span>
            <span class="preview-change-old">${preview.folder_rename.old}</span>
            <span class="preview-arrow">→</span>
            <span class="preview-change-new">${preview.folder_rename.new}</span>
        `;
        editDryRunContent.appendChild(renameEl);
    }

    // Structure changes
    if (preview.structure_changes && preview.structure_changes.length > 0) {
        preview.structure_changes.forEach(change => {
            const changeEl = document.createElement('div');
            changeEl.className = 'preview-change';
            if (change.type === 'use_existing') {
                changeEl.innerHTML = `<span class="preview-change-type">Using:</span><span class="preview-change-new">${change.path}</span>`;
            } else {
                changeEl.innerHTML = `<span class="preview-change-type">Create:</span><span class="preview-change-new">${change.path}</span>`;
            }
            editDryRunContent.appendChild(changeEl);
        });
    }

    // File renames
    if (preview.file_renames && preview.file_renames.length > 0) {
        preview.file_renames.slice(0, 10).forEach(file => {
            const fileEl = document.createElement('div');
            fileEl.className = 'preview-change';
            if (file.action === 'skip') {
                fileEl.innerHTML = `
                    <span class="preview-change-type">Skip:</span>
                    <span class="preview-change-skip">${file.old} (${file.reason})</span>
                `;
            } else {
                fileEl.innerHTML = `
                    <span class="preview-change-type">File:</span>
                    <span class="preview-change-old">${file.old}</span>
                    <span class="preview-arrow">→</span>
                    <span class="preview-change-new">${file.new}</span>
                `;
            }
            editDryRunContent.appendChild(fileEl);
        });

        if (preview.file_renames.length > 10) {
            const moreEl = document.createElement('div');
            moreEl.className = 'preview-change';
            moreEl.innerHTML = `<span class="preview-change-type">...</span><span>and ${preview.file_renames.length - 10} more files</span>`;
            editDryRunContent.appendChild(moreEl);
        }
    }

    editDryRunContent.classList.remove('hidden');
}

// Save and process single folder
saveEditBtn.addEventListener('click', async () => {
    if (!editingFolder) return;

    saveEditBtn.disabled = true;
    saveEditBtn.textContent = 'Processing...';

    try {
        const mode = editingFolder.type === 'plex' ? 'plex' : (editingFolder.type === 'images' ? 'images_only' : 'standard');
        const name = editShowName.value.trim();
        const season = parseInt(editSeason.value) || 1;

        let endpoint, body;

        if (mode === 'plex' && editingFolder.media_type === 'movie') {
            endpoint = '/api/post-process/movie-restructure';
            body = {
                folder_path: editingFolder.path,
                movie_name: name,
                year: editSelectedYear,
                movies_dir: optMoviesDir.value.trim() || null
            };
        } else if (mode === 'plex') {
            endpoint = '/api/post-process/plex-restructure';
            body = {
                folder_path: editingFolder.path,
                show_name: name,
                year: editSelectedYear,
                season: season
            };
        } else {
            endpoint = '/api/post-process/apply';
            body = {
                folder_path: editingFolder.path,
                new_name: name
            };
        }

        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const result = await response.json();

        if (result.success) {
            editFolderModal.classList.add('hidden');

            if (editContext === 'download') {
                // Show completion message for download context
                const name = editShowName.value.trim();
                let displayName = name;
                if (editSelectedYear) {
                    displayName = `${name} (${editSelectedYear})`;
                }
                document.getElementById('complete-name').textContent = displayName;
                completeSection.classList.remove('hidden');
                newDownloadBtn.classList.remove('hidden');
                editContext = 'library';  // Reset context
            } else {
                // Rescan to refresh the list for library context
                scanLibrary();
            }
        } else {
            alert('Processing failed: ' + result.error);
        }

    } catch (error) {
        console.error('Processing failed:', error);
        alert('Processing failed: ' + error.message);
    } finally {
        saveEditBtn.disabled = false;
        saveEditBtn.textContent = 'Save & Process';
    }
});

// Close modal when clicking outside
previewModal.addEventListener('click', (e) => {
    if (e.target === previewModal) {
        previewModal.classList.add('hidden');
    }
});

editFolderModal.addEventListener('click', (e) => {
    if (e.target === editFolderModal) {
        editFolderModal.classList.add('hidden');
    }
});
