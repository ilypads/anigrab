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
const addToQueueBtn = document.getElementById('add-to-queue-btn');

const progressSection = document.getElementById('progress-section');
const completeSection = document.getElementById('complete-section');
const errorSection = document.getElementById('error-section');
const dhtErrorSection = document.getElementById('dht-error-section');
const restartQbtBtn = document.getElementById('restart-qbt-btn');


const mullvadDot = document.getElementById('mullvad-dot');
const qbtDot = document.getElementById('qbt-dot');
const dhtNodes = document.getElementById('dht-nodes');

const newDownloadBtn = document.getElementById('new-download-btn');
const cancelDownloadBtn = document.getElementById('cancel-download-btn');

// Queue DOM elements
const queueSection = document.getElementById('queue-section');
const queueList = document.getElementById('queue-list');
const queueStartBtn = document.getElementById('queue-start-btn');
const queueStopBtn = document.getElementById('queue-stop-btn');
const queueClearBtn = document.getElementById('queue-clear-btn');
const queueSummary = document.getElementById('queue-summary');
const queueCount = document.getElementById('queue-count');
const queueStatus = document.getElementById('queue-status');

// State
let currentUrl = '';
let currentTorrentHash = '';
let eventSource = null;

// Queue state
let queueData = [];
let queueEventSource = null;
let isQueueProcessing = false;


// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    checkSystemStatus();
    setInterval(checkSystemStatus, 30000); // Check every 30 seconds

    // Initialize queue
    await loadQueue();
    connectQueueSSE();

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

addToQueueBtn.addEventListener('click', addToQueue);
newDownloadBtn.addEventListener('click', resetToInput);
cancelDownloadBtn.addEventListener('click', cancelDownload);
restartQbtBtn.addEventListener('click', restartQBittorrent);

// Queue event listeners
queueStartBtn.addEventListener('click', startQueueProcessing);
queueStopBtn.addEventListener('click', stopQueueProcessing);
queueClearBtn.addEventListener('click', clearCompletedFromQueue);


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
    newDownloadBtn.classList.remove('hidden');
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


// ============================================================
// QUEUE FUNCTIONALITY
// ============================================================

async function loadQueue() {
    try {
        const response = await fetch('/api/queue');
        const data = await response.json();

        if (data.success) {
            queueData = data.jobs;
            isQueueProcessing = data.processing;
            renderQueue();
        }
    } catch (error) {
        console.error('Failed to load queue:', error);
    }
}

function connectQueueSSE() {
    // Close existing connection
    if (queueEventSource) {
        queueEventSource.close();
    }

    queueEventSource = new EventSource('/api/queue/stream');

    queueEventSource.addEventListener('queue_update', (e) => {
        const data = JSON.parse(e.data);
        queueData = data.jobs;
        isQueueProcessing = data.processing;
        renderQueue();
    });

    queueEventSource.addEventListener('job_progress', (e) => {
        const data = JSON.parse(e.data);
        // Update specific job progress
        const job = queueData.find(j => j.id === data.id);
        if (job) {
            job.progress = data.progress;
            job.status = data.status;
            renderQueue();
        }
    });

    queueEventSource.addEventListener('job_complete', (e) => {
        const data = JSON.parse(e.data);
        const job = queueData.find(j => j.id === data.id);
        if (job) {
            job.status = 'complete';
            job.progress = 100;
            job.folder_path = data.folder_path;
            renderQueue();
        }
    });

    queueEventSource.addEventListener('job_error', (e) => {
        const data = JSON.parse(e.data);
        const job = queueData.find(j => j.id === data.id);
        if (job) {
            job.status = 'error';
            job.error_message = data.error;
            renderQueue();
        }
    });

    queueEventSource.onerror = () => {
        // Will auto-reconnect
        console.log('Queue SSE connection lost, will reconnect...');
    };
}

function renderQueue() {
    // Show/hide queue section based on whether there are items
    if (queueData.length === 0) {
        queueSection.classList.add('hidden');
        return;
    }

    queueSection.classList.remove('hidden');

    // Render queue items
    queueList.innerHTML = '';
    queueData.forEach((job, index) => {
        const item = document.createElement('div');
        item.className = `queue-item ${job.status}`;
        item.dataset.id = job.id;

        const position = document.createElement('span');
        position.className = 'queue-position';
        if (job.status === 'complete') {
            position.innerHTML = '&#10003;';
        } else if (job.status === 'error') {
            position.innerHTML = '&#10007;';
        } else {
            position.textContent = index + 1;
        }

        const info = document.createElement('div');
        info.className = 'queue-info';

        const title = document.createElement('span');
        title.className = 'queue-title';
        title.textContent = job.title;
        title.title = job.title;

        const statusText = document.createElement('span');
        statusText.className = 'queue-status-text';
        if (job.status === 'downloading') {
            statusText.textContent = 'Downloading...';
        } else if (job.status === 'complete') {
            statusText.textContent = 'Complete';
        } else if (job.status === 'error') {
            statusText.textContent = job.error_message || 'Error';
        } else if (job.status === 'cancelled') {
            statusText.textContent = 'Cancelled';
        } else {
            statusText.textContent = 'Pending';
        }

        info.appendChild(title);
        info.appendChild(statusText);

        const progress = document.createElement('span');
        progress.className = 'queue-item-progress';
        if (job.status === 'downloading') {
            progress.textContent = `${Math.round(job.progress)}%`;
        } else if (job.status === 'complete') {
            progress.textContent = '100%';
        } else {
            progress.textContent = '-';
        }

        const actions = document.createElement('div');
        actions.className = 'queue-actions';

        if (job.status === 'error' || job.status === 'cancelled') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'queue-retry-btn';
            retryBtn.innerHTML = '&#8635;';
            retryBtn.title = 'Retry';
            retryBtn.addEventListener('click', () => retryJob(job.id));
            actions.appendChild(retryBtn);
        }

        const removeBtn = document.createElement('button');
        removeBtn.className = 'queue-remove-btn';
        removeBtn.innerHTML = '&times;';
        removeBtn.title = 'Remove from queue';
        removeBtn.addEventListener('click', () => removeFromQueue(job.id));
        actions.appendChild(removeBtn);

        item.appendChild(position);
        item.appendChild(info);
        item.appendChild(progress);
        item.appendChild(actions);
        queueList.appendChild(item);
    });

    // Update summary
    queueSummary.classList.remove('hidden');
    const pendingCount = queueData.filter(j => j.status === 'pending').length;
    const downloadingCount = queueData.filter(j => j.status === 'downloading').length;
    queueCount.textContent = `${queueData.length} item${queueData.length !== 1 ? 's' : ''}`;

    if (isQueueProcessing) {
        queueStatus.textContent = downloadingCount > 0 ? 'Processing' : 'Starting...';
        queueStatus.className = 'processing';
        queueStartBtn.classList.add('hidden');
        queueStopBtn.classList.remove('hidden');
    } else {
        queueStatus.textContent = pendingCount > 0 ? 'Idle' : 'Done';
        queueStatus.className = 'idle';
        queueStartBtn.classList.remove('hidden');
        queueStopBtn.classList.add('hidden');
    }
}

async function addToQueue() {
    if (!currentUrl) return;

    addToQueueBtn.disabled = true;
    addToQueueBtn.textContent = 'Adding...';

    try {
        const response = await fetch('/api/queue/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: currentUrl,
                title: mediaTitle.textContent
            })
        });

        const data = await response.json();

        if (data.success) {
            // Reset to input and show queue
            showSection('input');
            linkInput.value = '';
            currentUrl = '';

            // Auto-start the queue if not already processing
            if (!isQueueProcessing) {
                await startQueueProcessing();
            }
        } else {
            alert('Failed to add to queue: ' + data.error);
        }
    } catch (error) {
        console.error('Failed to add to queue:', error);
        alert('Failed to add to queue');
    } finally {
        addToQueueBtn.disabled = false;
        addToQueueBtn.textContent = 'Download';
    }
}

async function retryJob(jobId) {
    try {
        const response = await fetch('/api/queue/retry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: jobId })
        });

        const data = await response.json();

        if (data.success) {
            // Auto-start the queue if not already processing
            if (!isQueueProcessing) {
                await startQueueProcessing();
            }
        } else {
            alert('Failed to retry: ' + data.error);
        }
    } catch (error) {
        console.error('Failed to retry job:', error);
    }
}

async function removeFromQueue(jobId) {
    try {
        const response = await fetch('/api/queue/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: jobId })
        });

        const data = await response.json();

        if (!data.success) {
            alert('Failed to remove: ' + data.error);
        }
        // Queue will update via SSE
    } catch (error) {
        console.error('Failed to remove from queue:', error);
    }
}

async function startQueueProcessing() {
    queueStartBtn.disabled = true;

    try {
        const response = await fetch('/api/queue/start', {
            method: 'POST'
        });

        const data = await response.json();

        if (!data.success) {
            alert('Failed to start queue: ' + data.error);
        }
        // Queue will update via SSE
    } catch (error) {
        console.error('Failed to start queue:', error);
    } finally {
        queueStartBtn.disabled = false;
    }
}

async function stopQueueProcessing() {
    queueStopBtn.disabled = true;

    try {
        const response = await fetch('/api/queue/stop', {
            method: 'POST'
        });

        const data = await response.json();

        if (!data.success) {
            alert('Failed to stop queue: ' + data.error);
        }
        // Queue will update via SSE
    } catch (error) {
        console.error('Failed to stop queue:', error);
    } finally {
        queueStopBtn.disabled = false;
    }
}

async function clearCompletedFromQueue() {
    try {
        const response = await fetch('/api/queue/clear', {
            method: 'POST'
        });

        const data = await response.json();

        if (!data.success) {
            alert('Failed to clear: ' + data.error);
        }
        // Queue will update via SSE
    } catch (error) {
        console.error('Failed to clear queue:', error);
    }
}
