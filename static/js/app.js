// State Management
const state = {
    courses: [],
    selectedIds: new Set(),
    currentYearFilter: '3', // Default year
    searchQuery: '',
    userConfig: {
        username: '',
        hasPassword: false,
        defaultYear: '3',
        remember: true,
        isLoggedIn: false
    },
    eventSource: null
};

// DOM Elements
const elements = {
    // Header
    userBadge: document.getElementById('user-badge'),
    headerUsername: document.getElementById('header-username'),
    headerYear: document.getElementById('header-year'),
    btnOpenFolder: document.getElementById('btn-open-folder'),
    btnSettings: document.getElementById('btn-settings'),
    btnLogout: document.getElementById('btn-logout'),

    // Year Tabs
    yearTabs: document.getElementById('year-tabs'),
    countAll: document.getElementById('count-all'),
    count1: document.getElementById('count-1'),
    count2: document.getElementById('count-2'),
    count3: document.getElementById('count-3'),
    count4: document.getElementById('count-4'),
    countOther: document.getElementById('count-other'),

    // Controls
    courseSearch: document.getElementById('course-search'),
    btnSelectAll: document.getElementById('btn-select-all'),
    btnDeselectAll: document.getElementById('btn-deselect-all'),
    chkUseZip: document.getElementById('chk-use-zip'),
    btnDownloadSelected: document.getElementById('btn-download-selected'),
    downloadBtnText: document.getElementById('download-btn-text'),

    // Course Grid
    coursesLoading: document.getElementById('courses-loading'),
    coursesGrid: document.getElementById('courses-grid'),
    noCoursesState: document.getElementById('no-courses-state'),

    // Login Modal
    loginModal: document.getElementById('login-modal'),
    formLogin: document.getElementById('form-login'),
    loginUsername: document.getElementById('login-username'),
    loginPassword: document.getElementById('login-password'),
    loginYear: document.getElementById('login-year'),
    loginRemember: document.getElementById('login-remember'),
    loginErrorAlert: document.getElementById('login-error-alert'),
    btnSubmitLogin: document.getElementById('btn-submit-login'),

    // Settings Modal
    settingsModal: document.getElementById('settings-modal'),
    formSettings: document.getElementById('form-settings'),
    settingsUsername: document.getElementById('settings-username'),
    settingsPassword: document.getElementById('settings-password'),
    settingsYear: document.getElementById('settings-year'),
    settingsRemember: document.getElementById('settings-remember'),
    settingsSuccessAlert: document.getElementById('settings-success-alert'),
    btnCloseSettings: document.getElementById('btn-close-settings'),
    btnForgetCredentials: document.getElementById('btn-forget-credentials'),

    // Download Modal
    downloadModal: document.getElementById('download-modal'),
    dlStatusSubtitle: document.getElementById('dl-status-subtitle'),
    dlProgressBar: document.getElementById('dl-progress-bar'),
    dlProgressText: document.getElementById('dl-progress-text'),
    dlProgressPercent: document.getElementById('dl-progress-percent'),
    dlCurrentFile: document.getElementById('dl-current-file'),
    dlLogConsole: document.getElementById('dl-log-console'),
    dlSummaryBox: document.getElementById('dl-summary-box'),
    statDownloaded: document.getElementById('stat-downloaded'),
    statSkipped: document.getElementById('stat-skipped'),
    statFailed: document.getElementById('stat-failed'),
    btnOpenDirDone: document.getElementById('btn-open-dir-done'),
    btnDismissDownload: document.getElementById('btn-dismiss-download'),
    btnCloseDlModal: document.getElementById('btn-close-dl-modal'),

    // Preview Modal
    previewModal: document.getElementById('preview-modal'),
    previewCourseName: document.getElementById('preview-course-name'),
    previewCourseSub: document.getElementById('preview-course-sub'),
    previewBody: document.getElementById('preview-body'),
    btnClosePreview: document.getElementById('btn-close-preview')
};

// Initialize Application
async function initApp() {
    bindEvents();
    await loadInitialConfig();
}

// Bind User Interactions
function bindEvents() {
    // Year Tabs
    elements.yearTabs.addEventListener('click', (e) => {
        const tab = e.target.closest('.pill-tab');
        if (!tab) return;
        const year = tab.dataset.year;
        setYearFilter(year);
    });

    // Course Search
    elements.courseSearch.addEventListener('input', (e) => {
        state.searchQuery = e.target.value.toLowerCase().trim();
        renderCourseGrid();
    });

    // Select / Deselect All
    elements.btnSelectAll.addEventListener('click', () => {
        const visible = getFilteredCourses();
        visible.forEach(c => state.selectedIds.add(c.id));
        updateSelectionUI();
    });

    elements.btnDeselectAll.addEventListener('click', () => {
        const visible = getFilteredCourses();
        visible.forEach(c => state.selectedIds.delete(c.id));
        updateSelectionUI();
    });

    // Download Action
    elements.btnDownloadSelected.addEventListener('click', startDownload);

    // Open Downloads Folder
    elements.btnOpenFolder.addEventListener('click', openDownloadsFolder);
    elements.btnOpenDirDone.addEventListener('click', openDownloadsFolder);

    // Modals
    elements.btnSettings.addEventListener('click', openSettingsModal);
    elements.btnCloseSettings.addEventListener('click', closeSettingsModal);
    elements.btnLogout.addEventListener('click', handleLogout);

    elements.formLogin.addEventListener('submit', handleLoginSubmit);
    elements.formSettings.addEventListener('submit', handleSettingsSubmit);
    elements.btnForgetCredentials.addEventListener('click', handleForgetCredentials);

    elements.btnClosePreview.addEventListener('click', () => elements.previewModal.classList.add('hidden'));
    elements.btnDismissDownload.addEventListener('click', () => elements.downloadModal.classList.add('hidden'));
    elements.btnCloseDlModal.addEventListener('click', () => elements.downloadModal.classList.add('hidden'));
}

// Load Initial Configuration from Backend
async function loadInitialConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        state.userConfig = data;

        if (data.default_year) {
            state.currentYearFilter = data.default_year;
        }

        if (data.username) {
            elements.headerUsername.textContent = data.username;
            elements.loginUsername.value = data.username;
            elements.settingsUsername.value = data.username;
        }

        if (data.default_year) {
            elements.headerYear.textContent = `Year ${data.default_year}`;
            elements.loginYear.value = data.default_year;
            elements.settingsYear.value = data.default_year;
        }

        // Activate matching pill tab
        setYearFilter(state.currentYearFilter, false);

        // Fetch courses (auto-logs in if saved credentials exist)
        await fetchCourses();

    } catch (err) {
        console.error('Failed to load initial config:', err);
        showLoginModal();
    }
}

// Fetch Courses from Backend
async function fetchCourses(refresh = false) {
    elements.coursesLoading.classList.remove('hidden');
    elements.coursesGrid.classList.add('hidden');
    elements.noCoursesState.classList.add('hidden');

    try {
        const res = await fetch(`/api/courses?refresh=${refresh ? 1 : 0}`);
        const data = await res.json();

        if (!data.success) {
            if (data.require_login) {
                showLoginModal();
            } else {
                alert(data.message || 'Error fetching courses');
            }
            elements.coursesLoading.classList.add('hidden');
            return;
        }

        state.courses = data.courses || [];
        updateUserBadgeUI();
        updateYearCounts();
        renderCourseGrid();

        elements.coursesLoading.classList.add('hidden');
    } catch (err) {
        console.error('Error fetching courses:', err);
        elements.coursesLoading.classList.add('hidden');
        showLoginModal();
    }
}

// Update Year Pill Counts
function updateYearCounts() {
    let cAll = state.courses.length;
    let c1 = 0, c2 = 0, c3 = 0, c4 = 0, cOther = 0;

    state.courses.forEach(c => {
        if (c.year === 1) c1++;
        else if (c.year === 2) c2++;
        else if (c.year === 3) c3++;
        else if (c.year === 4) c4++;
        else cOther++;
    });

    elements.countAll.textContent = cAll;
    elements.count1.textContent = c1;
    elements.count2.textContent = c2;
    elements.count3.textContent = c3;
    elements.count4.textContent = c4;
    elements.countOther.textContent = cOther;
}

// Filter Courses
function getFilteredCourses() {
    return state.courses.filter(course => {
        // Year filter
        let matchesYear = true;
        if (state.currentYearFilter === 'all') {
            matchesYear = true;
        } else if (state.currentYearFilter === 'other') {
            matchesYear = course.year === null;
        } else {
            matchesYear = course.year === parseInt(state.currentYearFilter, 10);
        }

        // Search query filter
        let matchesSearch = true;
        if (state.searchQuery) {
            const name = (course.name || '').toLowerCase();
            const id = (course.id || '').toLowerCase();
            matchesSearch = name.includes(state.searchQuery) || id.includes(state.searchQuery);
        }

        return matchesYear && matchesSearch;
    });
}

// Switch Year Filter
function setYearFilter(year, shouldRender = true) {
    state.currentYearFilter = year;

    // Update active tab styles
    const tabs = elements.yearTabs.querySelectorAll('.pill-tab');
    tabs.forEach(tab => {
        if (tab.dataset.year === String(year)) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });

    if (shouldRender) {
        renderCourseGrid();
    }
}

// Render Course Cards
function renderCourseGrid() {
    const courses = getFilteredCourses();
    elements.coursesGrid.innerHTML = '';

    if (courses.length === 0) {
        elements.coursesGrid.classList.add('hidden');
        elements.noCoursesState.classList.remove('hidden');
        updateSelectionUI();
        return;
    }

    elements.noCoursesState.classList.add('hidden');
    elements.coursesGrid.classList.remove('hidden');

    courses.forEach(course => {
        const isSelected = state.selectedIds.has(course.id);
        const card = document.createElement('div');
        card.className = `course-card ${isSelected ? 'selected' : ''}`;
        card.dataset.id = course.id;

        // Parse code badge
        const match = course.name.match(/\b[A-Za-z]{3}\s*\d{3}/);
        const code = match ? match[0].toUpperCase() : 'COURSE';
        const cleanName = course.name.replace(/\b[A-Za-z]{3}\s*\d{3}/, '').trim() || course.name;

        card.innerHTML = `
            <div class="course-card-top">
                <div class="course-meta-tags">
                    <span class="tag-code">${code}</span>
                    <span class="tag-year">${course.year ? `Year ${course.year}` : 'General'}</span>
                </div>
                <div class="card-checkbox">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                </div>
            </div>
            <div class="course-title">${escapeHtml(cleanName)}</div>
            <div class="course-card-footer">
                <span class="course-id-label">ID: ${course.id}</span>
                <button class="btn btn-preview" data-preview-id="${course.id}">Preview Content ▾</button>
            </div>
        `;

        // Card click toggles selection
        card.addEventListener('click', (e) => {
            if (e.target.closest('.btn-preview')) {
                e.stopPropagation();
                openCoursePreview(course.id, course.name);
                return;
            }
            toggleCourseSelection(course.id);
        });

        elements.coursesGrid.appendChild(card);
    });

    updateSelectionUI();
}

// Toggle Course Selection
function toggleCourseSelection(id) {
    if (state.selectedIds.has(id)) {
        state.selectedIds.delete(id);
    } else {
        state.selectedIds.add(id);
    }

    const card = elements.coursesGrid.querySelector(`.course-card[data-id="${id}"]`);
    if (card) {
        card.classList.toggle('selected', state.selectedIds.has(id));
    }

    updateSelectionUI();
}

// Update Action Button & Counter UI
function updateSelectionUI() {
    const count = state.selectedIds.size;
    elements.btnDownloadSelected.disabled = count === 0;
    elements.downloadBtnText.textContent = `Download Selected (${count})`;
}

// Course Content Preview
async function openCoursePreview(courseId, courseName) {
    elements.previewCourseName.textContent = courseName;
    elements.previewCourseSub.textContent = `Fetching modules from LMS...`;
    elements.previewBody.innerHTML = '<div class="spinner"></div>';
    elements.previewModal.classList.remove('hidden');

    try {
        const res = await fetch(`/api/course/${courseId}/preview`);
        const data = await res.json();

        if (!data.success) {
            elements.previewBody.innerHTML = `<div class="alert-box alert-error">${escapeHtml(data.message)}</div>`;
            return;
        }

        elements.previewCourseSub.textContent = `${data.total_activities} activities across ${data.sections.length} sections`;

        if (data.sections.length === 0 || data.total_activities === 0) {
            elements.previewBody.innerHTML = '<div class="empty-sub" style="text-align: center; padding: 20px;">No folder or resource activities found in this course.</div>';
            return;
        }

        let html = '';
        data.sections.forEach(sec => {
            if (sec.activities.length === 0) return;
            html += `
                <div class="preview-section-item">
                    <div class="preview-section-title">${escapeHtml(sec.title)}</div>
                    <div class="preview-activity-list">
            `;
            sec.activities.forEach(act => {
                html += `
                    <div class="preview-activity-item">
                        <span class="act-badge ${act.type}">${act.type}</span>
                        <span>${escapeHtml(act.name)}</span>
                    </div>
                `;
            });
            html += `</div></div>`;
        });

        elements.previewBody.innerHTML = html || '<div class="empty-sub">No downloadable files found.</div>';
    } catch (err) {
        elements.previewBody.innerHTML = `<div class="alert-box alert-error">Failed to load preview: ${err.message}</div>`;
    }
}

// Start Material Download with Real-Time SSE
async function startDownload() {
    if (state.selectedIds.size === 0) return;

    const courseIds = Array.from(state.selectedIds);
    const useZip = elements.chkUseZip.checked;

    // Reset download modal state
    elements.dlStatusSubtitle.textContent = `Starting download for ${courseIds.length} course(s)...`;
    elements.dlProgressBar.style.width = '0%';
    elements.dlProgressText.textContent = `Course 0 of ${courseIds.length}`;
    elements.dlProgressPercent.textContent = '0%';
    elements.dlCurrentFile.textContent = 'Contacting LMS...';
    elements.dlLogConsole.innerHTML = '';
    elements.dlSummaryBox.classList.add('hidden');
    elements.btnOpenDirDone.classList.add('hidden');
    elements.btnDismissDownload.classList.add('hidden');
    elements.btnCloseDlModal.disabled = true;

    elements.downloadModal.classList.remove('hidden');

    try {
        const startRes = await fetch('/api/download/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: courseIds,
                use_zip: useZip
            })
        });

        const startData = await startRes.json();
        if (!startData.success) {
            appendLog(startData.message, 'error');
            elements.btnCloseDlModal.disabled = false;
            return;
        }

        // Connect to SSE stream
        if (state.eventSource) {
            state.eventSource.close();
        }

        state.eventSource = new EventSource('/api/download/progress');

        state.eventSource.onmessage = (e) => {
            try {
                const ev = JSON.parse(e.data);
                handleDownloadEvent(ev, courseIds.length);
            } catch (err) {
                console.error('Error parsing SSE:', err);
            }
        };

        state.eventSource.onerror = (err) => {
            console.error('SSE Error:', err);
            if (state.eventSource) {
                state.eventSource.close();
            }
            elements.btnCloseDlModal.disabled = false;
        };

    } catch (err) {
        appendLog(`Error starting download: ${err.message}`, 'error');
        elements.btnCloseDlModal.disabled = false;
    }
}

// Handle Real-Time Download Events
function handleDownloadEvent(ev, totalCourses) {
    if (ev.type === 'start') {
        appendLog(ev.message, 'info');
    } else if (ev.type === 'course_start') {
        const pct = Math.round(((ev.course_index - 1) / totalCourses) * 100);
        elements.dlProgressBar.style.width = `${pct}%`;
        elements.dlProgressText.textContent = `Course ${ev.course_index} of ${totalCourses}`;
        elements.dlProgressPercent.textContent = `${pct}%`;
        elements.dlStatusSubtitle.textContent = `Processing ${ev.course_name}`;
        appendLog(`📂 Scanning course: ${ev.course_name}`, 'info');
    } else if (ev.type === 'folder_scan') {
        appendLog(`  📁 Folder: ${ev.folder_name} (${ev.section})`, 'info');
    } else if (ev.type === 'file_downloading') {
        elements.dlCurrentFile.textContent = ev.filename;
        appendLog(`  ⬇ Downloading: ${ev.filename}`, 'downloading');
    } else if (ev.type === 'file_success') {
        const sizeStr = ev.size_mb ? ` (${ev.size_mb} MB)` : '';
        appendLog(`  ✓ Saved: ${ev.filename}${sizeStr}`, 'success');
    } else if (ev.type === 'file_skipped') {
        appendLog(`  ⏭ Already saved: ${ev.filename}`, 'skipped');
    } else if (ev.type === 'file_error') {
        appendLog(`  ✗ Error: ${ev.filename} - ${ev.error || 'Failed'}`, 'error');
    } else if (ev.type === 'completed') {
        elements.dlProgressBar.style.width = '100%';
        elements.dlProgressPercent.textContent = '100%';
        elements.dlStatusSubtitle.textContent = 'All courses processed!';
        elements.dlCurrentFile.textContent = 'Complete';

        // Summary stats
        elements.statDownloaded.textContent = ev.downloaded || 0;
        elements.statSkipped.textContent = ev.skipped || 0;
        elements.statFailed.textContent = ev.failed || 0;
        elements.dlSummaryBox.classList.remove('hidden');

        elements.btnOpenDirDone.classList.remove('hidden');
        elements.btnDismissDownload.classList.remove('hidden');
        elements.btnCloseDlModal.disabled = false;

        appendLog('✨ All downloads completed successfully!', 'success');

        if (state.eventSource) {
            state.eventSource.close();
        }
    } else if (ev.type === 'error') {
        appendLog(`Error: ${ev.message}`, 'error');
        elements.btnCloseDlModal.disabled = false;
        if (state.eventSource) {
            state.eventSource.close();
        }
    }
}

// Append line to download console
function appendLog(text, type = 'info') {
    const line = document.createElement('div');
    line.className = `log-line ${type}`;
    line.textContent = text;
    elements.dlLogConsole.appendChild(line);
    elements.dlLogConsole.scrollTop = elements.dlLogConsole.scrollHeight;
}

// Open Downloads Folder in Windows Explorer
async function openDownloadsFolder() {
    try {
        const res = await fetch('/api/open-downloads', { method: 'POST' });
        const data = await res.json();
        if (!data.success) {
            alert(`Could not open folder: ${data.message}`);
        }
    } catch (err) {
        alert(`Failed to open folder: ${err.message}`);
    }
}

// Login & Settings Handlers
function showLoginModal() {
    elements.loginErrorAlert.classList.add('hidden');
    elements.loginModal.classList.remove('hidden');
}

async function handleLoginSubmit(e) {
    e.preventDefault();
    const username = elements.loginUsername.value.trim();
    const password = elements.loginPassword.value.trim();
    const defaultYear = elements.loginYear.value;
    const remember = elements.loginRemember.checked;

    elements.btnSubmitLogin.disabled = true;
    elements.btnSubmitLogin.querySelector('.btn-text').classList.add('hidden');
    elements.btnSubmitLogin.querySelector('.btn-spinner').classList.remove('hidden');
    elements.loginErrorAlert.classList.add('hidden');

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                password,
                default_year: defaultYear,
                remember
            })
        });

        const data = await res.json();
        if (!data.success) {
            elements.loginErrorAlert.textContent = data.message || 'Login failed. Please check credentials.';
            elements.loginErrorAlert.classList.remove('hidden');
            return;
        }

        // Login success
        elements.loginModal.classList.add('hidden');
        state.userConfig.username = username;
        state.userConfig.defaultYear = defaultYear;
        state.userConfig.remember = remember;
        state.userConfig.isLoggedIn = true;

        elements.headerUsername.textContent = username;
        elements.headerYear.textContent = `Year ${defaultYear}`;
        setYearFilter(defaultYear, false);

        await fetchCourses(true);

    } catch (err) {
        elements.loginErrorAlert.textContent = `Connection error: ${err.message}`;
        elements.loginErrorAlert.classList.remove('hidden');
    } finally {
        elements.btnSubmitLogin.disabled = false;
        elements.btnSubmitLogin.querySelector('.btn-text').classList.remove('hidden');
        elements.btnSubmitLogin.querySelector('.btn-spinner').classList.add('hidden');
    }
}

function openSettingsModal() {
    elements.settingsSuccessAlert.classList.add('hidden');
    elements.settingsUsername.value = state.userConfig.username || '';
    elements.settingsPassword.value = '';
    elements.settingsYear.value = state.userConfig.defaultYear || '3';
    elements.settingsRemember.checked = state.userConfig.remember !== false;
    elements.settingsModal.classList.remove('hidden');
}

function closeSettingsModal() {
    elements.settingsModal.classList.add('hidden');
}

async function handleSettingsSubmit(e) {
    e.preventDefault();
    const username = elements.settingsUsername.value.trim();
    const password = elements.settingsPassword.value.trim();
    const defaultYear = elements.settingsYear.value;
    const remember = elements.settingsRemember.checked;

    try {
        const res = await fetch('/api/settings/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                password,
                default_year: defaultYear,
                remember
            })
        });

        const data = await res.json();
        if (data.success) {
            state.userConfig.username = username;
            state.userConfig.defaultYear = defaultYear;
            state.userConfig.remember = remember;

            elements.headerUsername.textContent = username;
            elements.headerYear.textContent = `Year ${defaultYear}`;
            setYearFilter(defaultYear);

            elements.settingsSuccessAlert.textContent = 'Settings and credentials saved successfully!';
            elements.settingsSuccessAlert.classList.remove('hidden');

            setTimeout(() => {
                closeSettingsModal();
            }, 1200);
        }
    } catch (err) {
        alert(`Failed to save settings: ${err.message}`);
    }
}

async function handleForgetCredentials() {
    if (!confirm('Are you sure you want to remove your saved login credentials from this PC?')) return;

    try {
        await fetch('/api/settings/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: '',
                password: '',
                default_year: '3',
                remember: false
            })
        });

        state.userConfig.username = '';
        state.userConfig.hasPassword = false;
        state.userConfig.remember = false;

        closeSettingsModal();
        showLoginModal();
    } catch (err) {
        alert(`Error clearing credentials: ${err.message}`);
    }
}

async function handleLogout() {
    if (!confirm('Log out from LMS?')) return;
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        state.courses = [];
        state.selectedIds.clear();
        renderCourseGrid();
        showLoginModal();
    } catch (err) {
        console.error('Logout error:', err);
    }
}

function updateUserBadgeUI() {
    if (state.userConfig.username) {
        elements.headerUsername.textContent = state.userConfig.username;
        elements.headerYear.textContent = `Year ${state.userConfig.defaultYear || '3'}`;
        elements.userBadge.classList.remove('hidden');
        elements.btnLogout.classList.remove('hidden');
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Start
document.addEventListener('DOMContentLoaded', initApp);
