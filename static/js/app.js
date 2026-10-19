// State Management
const state = {
    activeTab: 'tab-courses',
    courses: [],
    selectedIds: new Set(),
    currentYearFilter: '3',
    searchQuery: '',

    // Feedback State
    feedbackItems: [],
    completedFeedbackIds: new Set(),
    feedbackYearFilter: '3',
    feedbackSearchQuery: '',
    feedbackLoaded: false,

    // Attendance State
    attendanceReport: null,
    attendanceViewMode: 'course', // 'course' or 'sessions'
    attendanceStatusFilter: 'all', // 'all', 'present', 'absent'
    attendanceSearchQuery: '',
    attendanceLoaded: false,

    userConfig: {
        username: '',
        hasPassword: false,
        default_year: '3',
        remember: true,
        gemini_api_key: '',
        attendance_email: '',
        has_attendance_password: false,
        is_logged_in: false,
        local_ip: '127.0.0.1'
    },
    eventSource: null
};

// DOM Elements Cache
const elements = {
    // Header
    userBadge: document.getElementById('user-badge'),
    headerUsername: document.getElementById('header-username'),
    headerYear: document.getElementById('header-year'),
    lanChip: document.getElementById('lan-chip'),
    lanIpText: document.getElementById('lan-ip-text'),
    lanUrlDisplay: document.getElementById('lan-url-display'),
    btnOpenFolder: document.getElementById('btn-open-folder'),
    btnLogout: document.getElementById('btn-logout'),

    // Nav Tabs & Views
    mainNavTabs: document.getElementById('main-nav-tabs'),
    badgeFeedbackCount: document.getElementById('badge-feedback-count'),
    badgeAttendancePct: document.getElementById('badge-attendance-pct'),
    viewCourses: document.getElementById('view-courses'),
    viewFeedback: document.getElementById('view-feedback'),
    viewAttendance: document.getElementById('view-attendance'),
    viewSettings: document.getElementById('view-settings'),

    // Course Materials View
    yearTabs: document.getElementById('year-tabs'),
    countAll: document.getElementById('count-all'),
    count1: document.getElementById('count-1'),
    count2: document.getElementById('count-2'),
    count3: document.getElementById('count-3'),
    count4: document.getElementById('count-4'),
    countOther: document.getElementById('count-other'),
    courseSearch: document.getElementById('course-search'),
    btnSelectAll: document.getElementById('btn-select-all'),
    btnDeselectAll: document.getElementById('btn-deselect-all'),
    chkUseZip: document.getElementById('chk-use-zip'),
    btnDownloadSelected: document.getElementById('btn-download-selected'),
    downloadBtnText: document.getElementById('download-btn-text'),
    coursesLoading: document.getElementById('courses-loading'),
    coursesGrid: document.getElementById('courses-grid'),
    noCoursesState: document.getElementById('no-courses-state'),

    // Feedback View
    feedbackYearTabs: document.getElementById('feedback-year-tabs'),
    feedbackSearch: document.getElementById('feedback-search'),
    btnScanFeedback: document.getElementById('btn-scan-feedback'),
    btnAutofillAllFeedback: document.getElementById('btn-autofill-all-feedback'),
    feedbackEngineName: document.getElementById('feedback-engine-name'),
    feedbackLoading: document.getElementById('feedback-loading'),
    feedbackGrid: document.getElementById('feedback-grid'),
    noFeedbackState: document.getElementById('no-feedback-state'),

    // Attendance View
    attStudentRoll: document.getElementById('att-student-roll'),
    attStudentName: document.getElementById('att-student-name'),
    attStudentSem: document.getElementById('att-student-sem'),
    btnSyncAttendance: document.getElementById('btn-sync-attendance'),
    attOverallPct: document.getElementById('att-overall-pct'),
    attOverallStatus: document.getElementById('att-overall-status'),
    attAttendedCount: document.getElementById('att-attended-count'),
    attMissedCount: document.getElementById('att-missed-count'),
    attCoursesCount: document.getElementById('att-courses-count'),
    attCourseSearch: document.getElementById('att-course-search'),
    attCoursesTbody: document.getElementById('att-courses-tbody'),
    attSessionsTbody: document.getElementById('att-sessions-tbody'),
    attViewCourse: document.getElementById('att-view-course'),
    attViewSessions: document.getElementById('att-view-sessions'),
    attStatusPills: document.getElementById('att-status-pills'),

    // Settings View
    formFullSettings: document.getElementById('form-full-settings'),
    settingsAlert: document.getElementById('settings-alert'),
    setLmsUsername: document.getElementById('set-lms-username'),
    setLmsPassword: document.getElementById('set-lms-password'),
    setDefaultYear: document.getElementById('set-default-year'),
    setGeminiKey: document.getElementById('set-gemini-key'),
    setAttEmail: document.getElementById('set-att-email'),
    setAttPassword: document.getElementById('set-att-password'),

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
    // Tab Navigation
    elements.mainNavTabs.addEventListener('click', (e) => {
        const btn = e.target.closest('.nav-tab-btn');
        if (!btn) return;
        switchTab(btn.dataset.tab);
    });

    // Courses: Year Tabs & Search
    elements.yearTabs.addEventListener('click', (e) => {
        const tab = e.target.closest('.pill-tab');
        if (!tab) return;
        setYearFilter(tab.dataset.year);
    });

    elements.courseSearch.addEventListener('input', (e) => {
        state.searchQuery = e.target.value.toLowerCase().trim();
        renderCourseGrid();
    });

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

    elements.btnDownloadSelected.addEventListener('click', startDownload);
    if (elements.btnOpenFolder) elements.btnOpenFolder.addEventListener('click', openDownloadsFolder);
    if (elements.btnOpenDirDone) elements.btnOpenDirDone.addEventListener('click', openDownloadsFolder);

    // Feedback: Year Tabs, Search, Scan & Autofill All
    elements.feedbackYearTabs.addEventListener('click', (e) => {
        const tab = e.target.closest('.pill-tab');
        if (!tab) return;
        setFeedbackYearFilter(tab.dataset.fbyear);
    });

    elements.feedbackSearch.addEventListener('input', (e) => {
        state.feedbackSearchQuery = e.target.value.toLowerCase().trim();
        renderFeedbackGrid();
    });

    elements.btnScanFeedback.addEventListener('click', scanFeedback);
    elements.btnAutofillAllFeedback.addEventListener('click', autoFillAllFeedback);

    // Attendance: Subview switch & live sync
    document.querySelectorAll('.att-switch-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.att-switch-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.attendanceViewMode = btn.dataset.attview;
            if (state.attendanceViewMode === 'course') {
                elements.attViewCourse.classList.remove('hidden');
                elements.attViewSessions.classList.add('hidden');
            } else {
                elements.attViewCourse.classList.add('hidden');
                elements.attViewSessions.classList.remove('hidden');
            }
        });
    });

    elements.attStatusPills.addEventListener('click', (e) => {
        const tab = e.target.closest('.pill-tab');
        if (!tab) return;
        elements.attStatusPills.querySelectorAll('.pill-tab').forEach(b => b.classList.remove('active'));
        tab.classList.add('active');
        state.attendanceStatusFilter = tab.dataset.attstat;
        renderAttendanceSessions();
    });

    elements.attCourseSearch.addEventListener('input', (e) => {
        state.attendanceSearchQuery = e.target.value.toLowerCase().trim();
        renderAttendanceCourses();
    });

    elements.btnSyncAttendance.addEventListener('click', syncLiveAttendance);

    // Settings Form Submit
    elements.formFullSettings.addEventListener('submit', handleSettingsSubmit);
    elements.btnLogout.addEventListener('click', handleLogout);

    // Modals
    elements.btnClosePreview.addEventListener('click', () => elements.previewModal.classList.add('hidden'));
    elements.btnDismissDownload.addEventListener('click', () => elements.downloadModal.classList.add('hidden'));
    elements.btnCloseDlModal.addEventListener('click', () => elements.downloadModal.classList.add('hidden'));
}

// Tab Switching Handler
function switchTab(tabId) {
    state.activeTab = tabId;
    document.querySelectorAll('.nav-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });

    elements.viewCourses.classList.toggle('hidden', tabId !== 'tab-courses');
    elements.viewFeedback.classList.toggle('hidden', tabId !== 'tab-feedback');
    elements.viewAttendance.classList.toggle('hidden', tabId !== 'tab-attendance');
    elements.viewSettings.classList.toggle('hidden', tabId !== 'tab-settings');

    if (tabId === 'tab-feedback' && !state.feedbackLoaded) {
        fetchFeedback();
    } else if (tabId === 'tab-attendance' && !state.attendanceLoaded) {
        fetchAttendance();
    }
}

// Safe HTTP JSON fetchers that guarantee informative error handling
async function postJson(url, bodyObj = {}) {
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        body: JSON.stringify(bodyObj)
    });
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
        return await res.json();
    }
    const raw = await res.text();
    const clean = raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    throw new Error(`Server error (${res.status}): ${clean.slice(0, 150) || res.statusText}`);
}

async function getJson(url) {
    const res = await fetch(url, {
        headers: { 'Accept': 'application/json' }
    });
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
        return await res.json();
    }
    const raw = await res.text();
    const clean = raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    throw new Error(`Server error (${res.status}): ${clean.slice(0, 150) || res.statusText}`);
}

// Load Initial Configuration from Backend
async function loadInitialConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        state.userConfig = data;

        const defaultYear = data.default_year || '3';
        state.currentYearFilter = defaultYear;
        state.feedbackYearFilter = defaultYear;

        // Display Mobile LAN URL
        if (data.local_ip) {
            elements.lanIpText.textContent = `📱 ${data.local_ip}:5000`;
            elements.lanUrlDisplay.textContent = `http://${data.local_ip}:5000`;
        }

        // Fill Settings inputs
        if (data.username) {
            elements.headerUsername.textContent = data.username;
            elements.setLmsUsername.value = data.username;
        }
        elements.setDefaultYear.value = defaultYear;
        if (data.gemini_api_key) {
            elements.setGeminiKey.value = data.gemini_api_key;
            elements.feedbackEngineName.textContent = 'Gemini 3.5 Flash Lite AI';
        }
        if (data.attendance_email) {
            elements.setAttEmail.value = data.attendance_email;
        }

        updateUserBadgeUI();
        setYearFilter(defaultYear, false);
        setFeedbackYearFilter(defaultYear, false);

        // Preload Course Materials & Attendance in background
        await fetchCourses();
        fetchAttendance();
        fetchFeedback();

    } catch (err) {
        console.error('Failed to load initial config:', err);
    }
}

// ----------------------------------------------------------------------------
// 1. Courses Materials Logic
// ----------------------------------------------------------------------------

async function fetchCourses(refresh = false) {
    elements.coursesLoading.classList.remove('hidden');
    elements.coursesGrid.classList.add('hidden');
    elements.noCoursesState.classList.add('hidden');

    try {
        const res = await fetch(`/api/courses?refresh=${refresh ? 1 : 0}`);
        const data = await res.json();

        if (data.success) {
            state.courses = data.courses || [];
            updateYearPillCounts();
            renderCourseGrid();
        } else {
            elements.coursesLoading.classList.add('hidden');
            elements.noCoursesState.classList.remove('hidden');
        }
    } catch (err) {
        console.error('Error fetching courses:', err);
        elements.coursesLoading.classList.add('hidden');
        elements.noCoursesState.classList.remove('hidden');
    }
}

function setYearFilter(year, shouldRender = true) {
    state.currentYearFilter = year;
    document.querySelectorAll('#year-tabs .pill-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.year === year);
    });
    if (shouldRender) renderCourseGrid();
}

function getFilteredCourses() {
    return state.courses.filter(course => {
        let yearMatch = false;
        if (state.currentYearFilter === 'all') yearMatch = true;
        else if (state.currentYearFilter === 'other') yearMatch = (course.year === null);
        else yearMatch = (course.year === parseInt(state.currentYearFilter, 10));

        let searchMatch = true;
        if (state.searchQuery) {
            searchMatch = course.name.toLowerCase().includes(state.searchQuery) ||
                          course.id.toLowerCase().includes(state.searchQuery);
        }
        return yearMatch && searchMatch;
    });
}

function updateYearPillCounts() {
    const counts = { all: state.courses.length, 1: 0, 2: 0, 3: 0, 4: 0, other: 0 };
    state.courses.forEach(c => {
        if (c.year >= 1 && c.year <= 4) counts[c.year]++;
        else counts.other++;
    });

    elements.countAll.textContent = counts.all;
    elements.count1.textContent = counts[1];
    elements.count2.textContent = counts[2];
    elements.count3.textContent = counts[3];
    elements.count4.textContent = counts[4];
    elements.countOther.textContent = counts.other;
}

function renderCourseGrid() {
    elements.coursesLoading.classList.add('hidden');
    const filtered = getFilteredCourses();

    if (filtered.length === 0) {
        elements.coursesGrid.classList.add('hidden');
        elements.noCoursesState.classList.remove('hidden');
        updateSelectionUI();
        return;
    }

    elements.noCoursesState.classList.add('hidden');
    elements.coursesGrid.classList.remove('hidden');
    elements.coursesGrid.innerHTML = '';

    filtered.forEach(course => {
        const isSelected = state.selectedIds.has(course.id);
        const card = document.createElement('div');
        card.className = `course-card ${isSelected ? 'selected' : ''}`;
        card.dataset.id = course.id;

        const yearLabel = course.year ? `Year ${course.year}` : 'General';
        const cleanName = escapeHtml(course.name);

        card.innerHTML = `
            <div class="course-card-top">
                <div class="course-meta-tags">
                    <span class="tag-year">${yearLabel}</span>
                </div>
                <div class="card-checkbox" role="checkbox" aria-checked="${isSelected ? 'true' : 'false'}" title="Select course for download">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                </div>
            </div>
            <div class="course-title" title="${cleanName}">${cleanName}</div>
            <div class="course-card-footer">
                <span class="course-id-label">#${course.id}</span>
                <button class="btn btn-ghost btn-sm btn-preview" data-id="${course.id}" title="Preview folder contents">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                        <circle cx="12" cy="12" r="3"></circle>
                    </svg>
                    <span>Preview</span>
                </button>
            </div>
        `;

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

function toggleCourseSelection(id) {
    if (state.selectedIds.has(id)) {
        state.selectedIds.delete(id);
    } else {
        state.selectedIds.add(id);
    }

    const card = document.querySelector(`.course-card[data-id="${id}"]`);
    if (card) {
        const isSel = state.selectedIds.has(id);
        card.classList.toggle('selected', isSel);
        const chk = card.querySelector('.card-checkbox');
        if (chk) chk.setAttribute('aria-checked', isSel ? 'true' : 'false');
    }
    updateSelectionUI();
}

function updateSelectionUI() {
    const count = state.selectedIds.size;
    elements.downloadBtnText.textContent = `Download Selected (${count})`;
    elements.btnDownloadSelected.disabled = (count === 0);

    // Synchronize all rendered course cards and checkboxes in the DOM
    document.querySelectorAll('.course-card').forEach(card => {
        const id = card.dataset.id;
        const isSel = state.selectedIds.has(id);
        card.classList.toggle('selected', isSel);
        const chk = card.querySelector('.card-checkbox');
        if (chk) chk.setAttribute('aria-checked', isSel ? 'true' : 'false');
    });
}

// ----------------------------------------------------------------------------
// 2. Feedback Forms Directory & Auto-Solver Logic
// ----------------------------------------------------------------------------

async function fetchFeedback() {
    elements.feedbackLoading.classList.remove('hidden');
    elements.feedbackGrid.innerHTML = '';
    elements.noFeedbackState.classList.add('hidden');

    try {
        const res = await fetch('/api/feedback');
        const data = await res.json();
        state.feedbackLoaded = true;

        if (data.success) {
            state.feedbackItems = data.feedback || [];
            state.completedFeedbackIds = new Set(data.completed_ids || []);
            elements.badgeFeedbackCount.textContent = state.feedbackItems.length;

            if (data.gemini_active) {
                elements.feedbackEngineName.textContent = 'Gemini 3.5 Flash Lite AI';
            } else {
                elements.feedbackEngineName.textContent = 'Smart Positive Heuristic';
            }

            renderFeedbackGrid();
        } else {
            elements.feedbackLoading.classList.add('hidden');
            elements.noFeedbackState.classList.remove('hidden');
        }
    } catch (err) {
        console.error('Error loading feedback forms:', err);
        elements.feedbackLoading.classList.add('hidden');
        elements.noFeedbackState.classList.remove('hidden');
    }
}

function setFeedbackYearFilter(year, shouldRender = true) {
    state.feedbackYearFilter = year;
    document.querySelectorAll('#feedback-year-tabs .pill-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.fbyear === year);
    });
    if (shouldRender) renderFeedbackGrid();
}

function getFilteredFeedback() {
    return state.feedbackItems.filter(item => {
        let yearMatch = false;
        if (state.feedbackYearFilter === 'all') yearMatch = true;
        else yearMatch = (String(item.course_year) === state.feedbackYearFilter);

        let searchMatch = true;
        if (state.feedbackSearchQuery) {
            searchMatch = item.name.toLowerCase().includes(state.feedbackSearchQuery) ||
                          item.course_name.toLowerCase().includes(state.feedbackSearchQuery) ||
                          item.id.includes(state.feedbackSearchQuery);
        }
        return yearMatch && searchMatch;
    });
}

function renderFeedbackGrid() {
    elements.feedbackLoading.classList.add('hidden');
    const filtered = getFilteredFeedback();

    if (filtered.length === 0) {
        elements.feedbackGrid.classList.add('hidden');
        elements.noFeedbackState.classList.remove('hidden');
        return;
    }

    elements.noFeedbackState.classList.add('hidden');
    elements.feedbackGrid.classList.remove('hidden');
    elements.feedbackGrid.innerHTML = '';

    filtered.forEach(fb => {
        const isCompleted = state.completedFeedbackIds.has(fb.id);
        const card = document.createElement('div');
        card.className = `feedback-card ${isCompleted ? 'completed' : ''}`;
        card.dataset.id = fb.id;

        const codeMatch = fb.course_name.match(/[A-Za-z]{3}\s*\d{3}/);
        const codeStr = codeMatch ? codeMatch[0] : `#${fb.course_id}`;

        card.innerHTML = `
            <div class="feedback-card-header">
                <div>
                    <div class="fb-course-code">[${codeStr}] • Year ${fb.course_year || 'General'}</div>
                    <div class="fb-title">${escapeHtml(fb.name)}</div>
                    <div class="fb-course-name">${escapeHtml(fb.course_name)}</div>
                </div>
                <span class="status-chip ${isCompleted ? 'completed' : 'pending'}">
                    ${isCompleted ? '✓ Completed' : 'Pending'}
                </span>
            </div>

            <div class="feedback-card-actions">
                <button class="btn btn-sm ${isCompleted ? 'btn-ghost' : 'btn-primary'} btn-fb-autofill" 
                        data-id="${fb.id}" 
                        data-url="${fb.url}" 
                        data-course="${escapeHtml(fb.course_name)}"
                        ${isCompleted ? 'disabled' : ''}>
                    ${isCompleted ? '✓ Done' : '⚡ Auto-Fill'}
                </button>
                <a href="${fb.url}" target="_blank" class="btn btn-secondary btn-sm" title="Open form in browser">
                    ↗ Open
                </a>
                <button class="btn btn-ghost btn-sm btn-fb-copy" data-url="${fb.url}" title="Copy URL">
                    ⧉ Copy
                </button>
            </div>
        `;

        card.querySelector('.btn-fb-autofill').addEventListener('click', function(e) {
            e.stopPropagation();
            autoFillSingleFeedback(fb, this);
        });

        card.querySelector('.btn-fb-copy').addEventListener('click', function(e) {
            e.stopPropagation();
            navigator.clipboard.writeText(fb.url);
            this.textContent = 'Copied!';
            setTimeout(() => { this.textContent = '⧉ Copy'; }, 1500);
        });

        elements.feedbackGrid.appendChild(card);
    });
}

async function autoFillSingleFeedback(fb, btn) {
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = '⏳ Filling...';

    try {
        const res = await fetch('/api/feedback/autofill', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: fb.id,
                url: fb.url,
                course_name: fb.course_name
            })
        });
        const data = await res.json();

        if (data.success) {
            state.completedFeedbackIds.add(fb.id);
            btn.textContent = '✓ Done';
            btn.className = 'btn btn-sm btn-ghost btn-fb-autofill';
            const card = document.querySelector(`.feedback-card[data-id="${fb.id}"]`);
            if (card) {
                card.classList.add('completed');
                const chip = card.querySelector('.status-chip');
                if (chip) {
                    chip.className = 'status-chip completed';
                    chip.textContent = '✓ Completed';
                }
            }
        } else {
            alert(`Auto-fill error: ${data.message}`);
            btn.disabled = false;
            btn.textContent = '⚠ Retry';
        }
    } catch (err) {
        alert(`Request failed: ${err.message}`);
        btn.disabled = false;
        btn.textContent = oldText;
    }
}

async function autoFillAllFeedback() {
    const pending = getFilteredFeedback().filter(fb => !state.completedFeedbackIds.has(fb.id));
    if (pending.length === 0) {
        alert('All visible feedback forms are already completed!');
        return;
    }

    if (!confirm(`Auto-fill and submit all ${pending.length} pending feedback forms with top positive answers?`)) {
        return;
    }

    elements.btnAutofillAllFeedback.disabled = true;
    let completedCount = 0;

    for (const fb of pending) {
        const cardBtn = document.querySelector(`.feedback-card[data-id="${fb.id}"] .btn-fb-autofill`);
        if (cardBtn) cardBtn.textContent = '⏳ Filling...';

        try {
            const res = await fetch('/api/feedback/autofill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: fb.id, url: fb.url, course_name: fb.course_name })
            });
            const data = await res.json();
            if (data.success) {
                completedCount++;
                state.completedFeedbackIds.add(fb.id);
            }
        } catch (e) {}
    }

    elements.btnAutofillAllFeedback.disabled = false;
    renderFeedbackGrid();
    alert(`Successfully completed ${completedCount} of ${pending.length} feedback forms!`);
}

async function scanFeedback() {
    elements.btnScanFeedback.disabled = true;
    elements.btnScanFeedback.textContent = 'Scanning LMS...';

    try {
        const data = await postJson('/api/feedback/scan', {});
        if (data.success) {
            state.feedbackItems = data.feedback || [];
            elements.badgeFeedbackCount.textContent = state.feedbackItems.length;
            renderFeedbackGrid();
            alert(`Scan finished: ${data.total_found} feedback forms identified.`);
        } else {
            alert(`Scan failed: ${data.message}`);
        }
    } catch (err) {
        alert(`Scan failed: ${err.message}`);
    } finally {
        elements.btnScanFeedback.disabled = false;
        elements.btnScanFeedback.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="23 4 23 10 17 10"></polyline>
                <polyline points="1 20 1 14 7 14"></polyline>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
            </svg>
            <span>Scan LMS Forms</span>
        `;
    }
}

// ----------------------------------------------------------------------------
// 3. Attendance Portal (FACTS-H) Logic
// ----------------------------------------------------------------------------

async function fetchAttendance() {
    try {
        const res = await fetch('/api/attendance');
        const data = await res.json();
        if (data.success) {
            state.attendanceLoaded = true;
            state.attendanceReport = data;
            renderAttendance();
        }
    } catch (err) {
        console.error('Attendance fetch error:', err);
    }
}

function renderAttendance() {
    const report = state.attendanceReport;
    if (!report) return;

    elements.attStudentRoll.textContent = report.student_roll || 'STUDENT';
    elements.attStudentName.textContent = report.student_name || 'STUDENT';
    elements.attStudentSem.textContent = `${report.student_semester || 'CSE · Semester 5'} • ${report.source}`;

    const pct = report.overall_pct || 0;
    elements.attOverallPct.textContent = `${pct.toFixed(1)}%`;
    elements.badgeAttendancePct.textContent = `${pct.toFixed(1)}%`;

    if (pct >= 85.0) {
        elements.attOverallPct.className = 'stat-card-val text-success';
        elements.attOverallStatus.textContent = 'EXCELLENT (Above 85% Target)';
        elements.badgeAttendancePct.className = 'tab-badge success';
    } else if (pct >= 80.0) {
        elements.attOverallPct.className = 'stat-card-val text-warning';
        elements.attOverallStatus.textContent = 'SAFE (Above 80% Requirement)';
        elements.badgeAttendancePct.className = 'tab-badge';
    } else {
        elements.attOverallPct.className = 'stat-card-val text-danger';
        elements.attOverallStatus.textContent = 'SHORTAGE (Below 80% Requirement)';
        elements.badgeAttendancePct.className = 'tab-badge danger';
    }

    elements.attAttendedCount.textContent = `${report.total_attended} / ${report.total_sessions}`;
    elements.attMissedCount.textContent = report.total_absent;
    elements.attCoursesCount.textContent = report.courses ? report.courses.length : 0;

    renderAttendanceCourses();
    renderAttendanceSessions();
}

function renderAttendanceCourses() {
    if (!state.attendanceReport || !state.attendanceReport.courses) return;
    const query = state.attendanceSearchQuery;
    const courses = state.attendanceReport.courses.filter(c => {
        if (!query) return true;
        return c.code.toLowerCase().includes(query) || c.name.toLowerCase().includes(query);
    });

    elements.attCoursesTbody.innerHTML = '';
    courses.forEach(c => {
        const tr = document.createElement('tr');
        const colorClass = c.pct >= 85.0 ? 'text-success' : (c.pct >= 80.0 ? 'text-warning' : 'text-danger');

        tr.innerHTML = `
            <td><span class="table-code">[${escapeHtml(c.code)}]</span></td>
            <td><strong>${escapeHtml(c.name)}</strong></td>
            <td>${c.attended} / ${c.total}</td>
            <td><span class="table-pct ${colorClass}">${c.pct.toFixed(1)}%</span></td>
        `;
        elements.attCoursesTbody.appendChild(tr);
    });
}

function formatSessionDateTime(dt) {
    if (!dt) return '';
    let parts = dt.split('·').map(p => p.trim()).filter(Boolean);
    let dateStr = parts[0] || '';
    if (dateStr.includes('T')) {
        const isoDate = dateStr.split('T')[0];
        const [y, m, d] = isoDate.split('-');
        if (y && m && d) {
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const mIdx = parseInt(m, 10);
            if (mIdx >= 1 && mIdx <= 12) {
                dateStr = `${parseInt(d, 10)} ${months[mIdx - 1]} ${y}`;
            }
        }
    }
    let timeStr = '';
    for (let i = 1; i < parts.length; i++) {
        const p = parts[i];
        if (p.includes(':') || p.includes('–') || p.includes('-')) {
            timeStr = p;
            break;
        }
    }
    if (dateStr && timeStr) {
        return `${dateStr} · ${timeStr}`;
    }
    return dateStr || timeStr || dt;
}

function renderAttendanceSessions() {
    if (!state.attendanceReport || !state.attendanceReport.recent_sessions) return;
    const filter = state.attendanceStatusFilter;
    const sessions = state.attendanceReport.recent_sessions.filter(s => {
        if (filter === 'present') return s.is_present;
        if (filter === 'absent') return !s.is_present;
        return true;
    });

    elements.attSessionsTbody.innerHTML = '';
    sessions.forEach(s => {
        const tr = document.createElement('tr');
        const badgeClass = s.is_present ? 'present' : 'absent';
        tr.innerHTML = `
            <td><span class="table-code">[${escapeHtml(s.code)}]</span></td>
            <td>${escapeHtml(s.course)}</td>
            <td>${escapeHtml(formatSessionDateTime(s.datetime_venue))}</td>
            <td><span class="badge-status ${badgeClass}">[ ${escapeHtml(s.status.toUpperCase())} ]</span></td>
        `;
        elements.attSessionsTbody.appendChild(tr);
    });
}

async function syncLiveAttendance() {
    elements.btnSyncAttendance.disabled = true;
    elements.btnSyncAttendance.textContent = 'Syncing...';

    try {
        const data = await postJson('/api/attendance/sync', {});
        if (data.success) {
            state.attendanceReport = data;
            renderAttendance();
            alert('Live attendance synchronized successfully with FACTS-H server!');
        } else {
            alert(`Sync failed: ${data.message}`);
        }
    } catch (err) {
        alert(`Error syncing attendance: ${err.message}`);
    } finally {
        elements.btnSyncAttendance.disabled = false;
        elements.btnSyncAttendance.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="23 4 23 10 17 10"></polyline>
                <polyline points="1 20 1 14 7 14"></polyline>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
            </svg>
            <span>Sync Live</span>
        `;
    }
}

// ----------------------------------------------------------------------------
// 4. Settings & Account Logic
// ----------------------------------------------------------------------------

async function handleSettingsSubmit(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-save-full-settings');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    const payload = {
        username: elements.setLmsUsername.value.trim(),
        password: elements.setLmsPassword.value.trim(),
        default_year: elements.setDefaultYear.value,
        gemini_api_key: elements.setGeminiKey.value.trim(),
        attendance_email: elements.setAttEmail.value.trim(),
        attendance_password: elements.setAttPassword.value.trim(),
        remember: true
    };

    try {
        const res = await fetch('/api/settings/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            elements.settingsAlert.textContent = '✓ Configuration saved successfully!';
            elements.settingsAlert.classList.remove('hidden');
            setTimeout(() => elements.settingsAlert.classList.add('hidden'), 3500);

            state.userConfig.username = payload.username;
            state.userConfig.default_year = payload.default_year;
            updateUserBadgeUI();
            setYearFilter(payload.default_year);
            setFeedbackYearFilter(payload.default_year);
        } else {
            alert(`Error: ${data.message}`);
        }
    } catch (err) {
        alert(`Save failed: ${err.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save All Settings';
    }
}

async function handleLogout() {
    if (!confirm('Log out from LMS session?')) return;
    try {
        await postJson('/api/auth/logout', {});
        state.courses = [];
        state.selectedIds.clear();
        renderCourseGrid();
        alert('Logged out successfully.');
    } catch (err) {
        console.error('Logout error:', err);
    }
}

function updateUserBadgeUI() {
    if (state.userConfig.username) {
        elements.headerUsername.textContent = state.userConfig.username;
        elements.headerYear.textContent = `Year ${state.userConfig.default_year || '3'}`;
        elements.userBadge.classList.remove('hidden');
        elements.btnLogout.classList.remove('hidden');
    }
}

// ----------------------------------------------------------------------------
// 5. Material Downloads & SSE Progress Streaming
// ----------------------------------------------------------------------------

async function startDownload() {
    const courseIds = Array.from(state.selectedIds);
    if (courseIds.length === 0) return;

    elements.downloadModal.classList.remove('hidden');
    elements.dlStatusSubtitle.textContent = 'Initializing downloads...';
    elements.dlProgressBar.style.width = '0%';
    elements.dlProgressText.textContent = `Course 0 of ${courseIds.length}`;
    elements.dlProgressPercent.textContent = '0%';
    elements.dlCurrentFile.textContent = 'Connecting...';
    elements.dlLogConsole.innerHTML = '<div class="log-line info">Preparing connection to LMS server...</div>';
    elements.dlSummaryBox.classList.add('hidden');
    elements.btnOpenDirDone.classList.add('hidden');
    elements.btnDismissDownload.classList.add('hidden');
    elements.btnCloseDlModal.disabled = true;

    try {
        const res = await fetch('/api/download/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_ids: courseIds,
                use_zip: elements.chkUseZip.checked
            })
        });
        const data = await res.json();
        if (!data.success) {
            alert(data.message || 'Error starting download');
            elements.downloadModal.classList.add('hidden');
            return;
        }
        listenToDownloadProgress();
    } catch (err) {
        alert(`Download start failed: ${err.message}`);
        elements.downloadModal.classList.add('hidden');
    }
}

function listenToDownloadProgress() {
    if (state.eventSource) state.eventSource.close();
    state.eventSource = new EventSource('/api/download/progress');

    let totalCourses = 1;
    let currentCourseIndex = 0;

    state.eventSource.onmessage = (e) => {
        const event = JSON.parse(e.data);
        handleSSEEvent(event);
    };

    state.eventSource.onerror = () => {
        if (state.eventSource) state.eventSource.close();
    };

    function handleSSEEvent(ev) {
        const log = elements.dlLogConsole;
        function appendLog(text, type = 'info') {
            const line = document.createElement('div');
            line.className = `log-line ${type}`;
            line.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
            log.appendChild(line);
            log.scrollTop = log.scrollHeight;
        }

        switch (ev.type) {
            case 'start':
                totalCourses = ev.total_courses || 1;
                appendLog(ev.message, 'info');
                break;

            case 'course_start':
                currentCourseIndex = ev.course_index;
                elements.dlProgressText.textContent = `Course ${currentCourseIndex} of ${totalCourses}`;
                const pct = Math.round(((currentCourseIndex - 1) / totalCourses) * 100);
                elements.dlProgressBar.style.width = `${pct}%`;
                elements.dlProgressPercent.textContent = `${pct}%`;
                elements.dlCurrentFile.textContent = ev.course_name;
                appendLog(`> Syncing course [${ev.course_id}]: ${ev.course_name}`, 'info');
                break;

            case 'file_downloading':
                elements.dlCurrentFile.textContent = `Downloading: ${ev.filename}`;
                break;

            case 'file_success':
                appendLog(`✓ Downloaded: ${ev.filename} (${ev.size_mb ? ev.size_mb + 'MB' : 'saved'})`, 'success');
                break;

            case 'file_skipped':
                appendLog(`• Skipped (Already up to date): ${ev.filename}`, 'warning');
                break;

            case 'file_error':
                appendLog(`✕ Failed: ${ev.filename} (${ev.error})`, 'error');
                break;

            case 'completed':
                if (state.eventSource) state.eventSource.close();
                elements.dlProgressBar.style.width = '100%';
                elements.dlProgressPercent.textContent = '100%';
                elements.dlCurrentFile.textContent = 'All selected materials synced successfully!';
                elements.statDownloaded.textContent = ev.downloaded;
                elements.statSkipped.textContent = ev.skipped;
                elements.statFailed.textContent = ev.failed;
                elements.dlSummaryBox.classList.remove('hidden');
                elements.btnOpenDirDone.classList.remove('hidden');
                elements.btnDismissDownload.classList.remove('hidden');
                elements.btnCloseDlModal.disabled = false;
                appendLog(`All operations finished: ${ev.downloaded} downloaded, ${ev.skipped} skipped, ${ev.failed} failed.`, 'success');
                break;

            case 'error':
                if (state.eventSource) state.eventSource.close();
                appendLog(`Error: ${ev.message}`, 'error');
                elements.btnCloseDlModal.disabled = false;
                break;
        }
    }
}

async function openDownloadsFolder() {
    try {
        await postJson('/api/open-downloads', {});
    } catch (e) {}
}

async function openCoursePreview(courseId, courseName) {
    elements.previewCourseName.textContent = courseName;
    elements.previewCourseSub.textContent = `Module ID: #${courseId} • Fetching syllabus contents...`;
    elements.previewBody.innerHTML = '<div class="spinner"></div>';
    elements.previewModal.classList.remove('hidden');

    try {
        const res = await fetch(`/api/course/${courseId}/preview`);
        const data = await res.json();
        if (!data.success) {
            elements.previewBody.innerHTML = `<div class="empty-state"><div class="empty-title">${data.message || 'Error loading preview'}</div></div>`;
            return;
        }

        elements.previewCourseSub.textContent = `${data.sections.length} Sections • ${data.total_activities} Materials found`;
        elements.previewBody.innerHTML = '';

        data.sections.forEach(sec => {
            const secDiv = document.createElement('div');
            secDiv.className = 'preview-section-item';
            const actsHtml = sec.activities.map(act => `
                <div class="preview-activity-item">
                    <span class="act-badge ${act.type}">${act.type}</span>
                    <span>${escapeHtml(act.name)}</span>
                </div>
            `).join('');

            secDiv.innerHTML = `
                <div class="preview-section-title">${escapeHtml(sec.title || 'General Section')}</div>
                <div class="preview-activity-list">${actsHtml || '<div class="preview-activity-item text-muted">No materials</div>'}</div>
            `;
            elements.previewBody.appendChild(secDiv);
        });
    } catch (err) {
        elements.previewBody.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
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

// Start Application
document.addEventListener('DOMContentLoaded', initApp);
