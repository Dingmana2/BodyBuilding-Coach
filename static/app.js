/* ── XSS guard ── */
function esc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/* ── Simple TTL cache for GET requests (30 s) ── */
const _cache = {};
const CACHE_TTL_MS = 30_000;

async function cachedApi(method, path) {
    const key = `${method}:${path}`;
    const hit = _cache[key];
    if (hit && Date.now() - hit.ts < CACHE_TTL_MS) return hit.data;
    const data = await api(method, path);
    _cache[key] = { data, ts: Date.now() };
    return data;
}

function invalidateCache(...paths) {
    paths.forEach(p => delete _cache[`GET:${p}`]);
}

/* ── State ── */
const state = {
    activeTab: 'dashboard',
    activePlanTab: 'workout',
    pendingFiles: [],
    currentPlan: null,
    analyses: [],
    activeSessionId: null,
    selectedExercise: null,
    sessionTimerInterval: null,
    sessionStartTime: null,
    _workoutExercises: [],
    todayCheckinId: null,
    latestCheckins: [],
};

/* ── Auth ── */
const AUTH_KEY = 'bb_auth_token';
const USER_KEY = 'bb_auth_user';

function getToken() { return localStorage.getItem(AUTH_KEY); }
function getStoredUser() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
}

function setAuth(token, user) {
    localStorage.setItem(AUTH_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    updateUserBadge(user);
    document.getElementById('auth-overlay').style.display = 'none';
    document.getElementById('onboarding-overlay').style.display = 'none';
    const drawerUser = document.getElementById('drawer-user');
    const drawerEmail = document.getElementById('drawer-email');
    if (drawerUser && drawerEmail) {
        drawerEmail.textContent = user.email || '';
        drawerUser.classList.add('visible');
    }
}

function clearAuth() {
    localStorage.removeItem(AUTH_KEY);
    localStorage.removeItem(USER_KEY);
    const drawerUser = document.getElementById('drawer-user');
    if (drawerUser) drawerUser.classList.remove('visible');
    showOnboardingOverlay();
}

function updateUserBadge(user) {
    const drawerUser = document.getElementById('drawer-user');
    const drawerEmail = document.getElementById('drawer-email');
    if (user && drawerUser && drawerEmail) {
        drawerEmail.textContent = user.email || '';
        drawerUser.classList.add('visible');
    }
}

function showAuthOverlay(mode = 'login') {
    document.getElementById('auth-overlay').style.display = 'flex';
    switchAuthTab(mode);
}

function showOnboardingOverlay() {
    document.getElementById('onboarding-overlay').style.display = 'flex';
    obGoToStep(0);
}

let _authMode = 'login';

function switchAuthTab(mode) {
    _authMode = mode;
    document.querySelectorAll('.auth-tab').forEach((el, i) => {
        el.classList.toggle('active', (i === 0 && mode === 'login') || (i === 1 && mode === 'register'));
    });
    document.getElementById('auth-submit-btn').textContent = mode === 'login' ? 'Sign In' : 'Create Account';
    document.getElementById('auth-error').style.display = 'none';
    const pw = document.getElementById('auth-password');
    pw.autocomplete = mode === 'login' ? 'current-password' : 'new-password';
}

async function submitAuth(e) {
    e.preventDefault();
    const email = document.getElementById('auth-email').value.trim();
    const password = document.getElementById('auth-password').value;
    const errEl = document.getElementById('auth-error');
    errEl.style.display = 'none';
    const btn = document.getElementById('auth-submit-btn');
    btn.disabled = true;
    btn.textContent = '…';
    try {
        const path = _authMode === 'login' ? '/auth/login' : '/auth/register';
        const data = await api('POST', path, { email, password });
        setAuth(data.token, data.user);
        await loadDashboard();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
        btn.disabled = false;
        btn.textContent = _authMode === 'login' ? 'Sign In' : 'Create Account';
    }
}

function signOut() {
    if (!confirm('Sign out?')) return;
    clearAuth();
}

async function initAuth() {
    const token = getToken();
    if (!token) { showOnboardingOverlay(); return; }
    const user = getStoredUser();
    if (user) { updateUserBadge(user); }
    try {
        const me = await api('GET', '/auth/me');
        updateUserBadge(me);
    } catch {
        clearAuth();
    }
}

/* ── API helpers ── */
async function api(method, path, body = null, isFormData = false) {
    const opts = { method, headers: {} };
    const token = getToken();
    if (token) opts.headers['Authorization'] = `Bearer ${token}`;

    if (body) {
        if (isFormData) {
            opts.body = body;
        } else {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
    }
    const res = await fetch(`/api${path}`, opts);
    if (res.status === 401 && path !== '/auth/login' && path !== '/auth/register') {
        clearAuth();
        throw new Error('Session expired — please sign in again.');
    }
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Request failed');
    }
    return res.json();
}

/* ── Hamburger nav drawer ── */
function toggleDrawer() {
    const drawer = document.getElementById('nav-drawer');
    const backdrop = document.getElementById('nav-drawer-backdrop');
    const isOpen = drawer.classList.contains('open');
    if (isOpen) {
        drawer.classList.remove('open');
        backdrop.classList.remove('open');
    } else {
        drawer.classList.add('open');
        backdrop.classList.add('open');
    }
}

function closeDrawer() {
    document.getElementById('nav-drawer').classList.remove('open');
    document.getElementById('nav-drawer-backdrop').classList.remove('open');
}

/* ── Tab navigation ── */
function showTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.drawer-nav-item').forEach(el => el.classList.remove('active'));
    document.getElementById(`tab-${tab}`).classList.add('active');
    const navEl = document.querySelector(`.drawer-nav-item[data-tab="${tab}"]`);
    if (navEl) navEl.classList.add('active');

    const loaders = {
        dashboard: loadDashboard,
        analysis: loadAnalyses,
        plans: loadCurrentPlan,
        workout: loadWorkout,
        research: loadResearch,
        progress: loadProgress,
        profile: loadProfile,
        nutrition: loadNutrition,
        reports: loadReports,
    };
    if (loaders[tab]) loaders[tab]();
}

function showPlanTab(tab, el) {
    state.activePlanTab = tab;
    document.querySelectorAll('.plan-tab').forEach(e => e.classList.remove('active'));
    document.querySelectorAll('.plan-section').forEach(e => e.style.display = 'none');
    el.classList.add('active');
    document.getElementById(`plan-${tab}`).style.display = 'block';
}

/* ── Loading & Toast ── */
function showLoading(msg = 'Working…') {
    document.getElementById('loading-message').textContent = msg;
    document.getElementById('loading-overlay').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loading-overlay').style.display = 'none';
}

function showToast(msg, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = `toast toast-${type}`;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 4000);
}

/* ── Dashboard ── */
async function loadDashboard() {
    const [health, plan, summary, checkins] = await Promise.all([
        cachedApi('GET', '/health').catch(() => ({ api_key_configured: false })),
        cachedApi('GET', '/plan/current').catch(() => null),
        cachedApi('GET', '/dashboard/summary').catch(() => null),
        cachedApi('GET', '/checkins?limit=7').catch(() => []),
    ]);

    document.getElementById('setup-banner').style.display =
        health.api_key_configured ? 'none' : 'block';

    state.currentPlan = plan;

    if (plan) {
        document.getElementById('stat-bf').textContent =
            plan.latest_analysis?.body_fat_estimate || '—';
        document.getElementById('stat-score').textContent =
            plan.latest_analysis?.overall_physique_score
                ? `${plan.latest_analysis.overall_physique_score}/10`
                : '—';
        document.getElementById('stat-cal').textContent =
            plan.diet_macros?.calories ? `${plan.diet_macros.calories} kcal` : '—';
        document.getElementById('stat-protein').textContent =
            plan.diet_macros?.protein_g ? `${plan.diet_macros.protein_g}g` : '—';

        if (plan.latest_analysis) {
            const card = document.getElementById('latest-analysis-card');
            card.style.display = 'block';
            document.getElementById('latest-analysis-content').innerHTML = `
                <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
                    <img src="${esc(plan.latest_analysis.photo_url)}" style="height:120px;border-radius:8px;object-fit:cover" />
                    <div>
                        <div class="stat-label">Last Analyzed</div>
                        <div>${esc(formatDate(plan.latest_analysis.created_at))}</div>
                        <div class="stat-label" style="margin-top:8px">Body Fat</div>
                        <div style="font-size:20px;font-weight:700;color:var(--gold)">${esc(plan.latest_analysis.body_fat_estimate)}</div>
                    </div>
                </div>
            `;
        }
    }

    if (summary?.avg_recovery_7d != null) {
        const avg = summary.avg_recovery_7d;
        const color = avg >= 70 ? 'var(--green)' : avg >= 50 ? 'var(--gold)' : 'var(--red)';
        const el = document.getElementById('stat-recovery');
        el.textContent = `${avg}/100`;
        el.style.color = color;
    }
    state.latestCheckins = checkins;
    renderRecoveryWidget(summary, checkins);
    renderRetentionWidget(summary);
    loadMemory();
}

function renderRetentionWidget(summary) {
    if (!summary) return;
    const card = document.getElementById('retention-card');
    let hasContent = false;

    const workoutStreak = summary.streaks?.workout ?? 0;
    const checkinStreak = summary.streaks?.checkin ?? 0;

    if (workoutStreak > 0) {
        document.getElementById('workout-streak-val').textContent = `${workoutStreak} day${workoutStreak !== 1 ? 's' : ''}`;
        document.getElementById('workout-streak-sub').textContent = '🏋️ keep it up!';
        document.getElementById('workout-streak-block').style.display = 'block';
        hasContent = true;
    }
    if (checkinStreak > 0) {
        document.getElementById('checkin-streak-val').textContent = `${checkinStreak} day${checkinStreak !== 1 ? 's' : ''}`;
        document.getElementById('checkin-streak-sub').textContent = '📊 consistency wins';
        document.getElementById('checkin-streak-block').style.display = 'block';
        hasContent = true;
    }
    if (summary.badges_count > 0) {
        document.getElementById('badges-val').textContent = summary.badges_count;
        document.getElementById('badges-block').style.display = 'block';
        hasContent = true;
    }
    if (summary.sessions_this_week != null) {
        document.getElementById('sessions-week-val').textContent = summary.sessions_this_week;
        document.getElementById('sessions-week-block').style.display = 'block';
        hasContent = true;
    }

    const today = new Date().toISOString().slice(0, 10);
    const nudge = document.getElementById('lapse-nudge');
    if (summary.last_workout_date) {
        const daysSince = Math.floor((Date.now() - new Date(summary.last_workout_date)) / 86400000);
        if (daysSince >= 4) {
            nudge.textContent = `🔔 It's been ${daysSince} days since your last workout — your streak is at risk!`;
            nudge.style.background = 'rgba(239,68,68,0.1)';
            nudge.style.color = '#ef4444';
            nudge.style.display = 'block';
            hasContent = true;
        } else if (daysSince >= 2) {
            nudge.textContent = `💪 ${daysSince} days since last workout — keep the momentum going!`;
            nudge.style.background = 'rgba(240,165,0,0.1)';
            nudge.style.color = 'var(--gold)';
            nudge.style.display = 'block';
            hasContent = true;
        }
    }
    if (summary.last_checkin_date && summary.last_checkin_date < today) {
        const daysSince = Math.floor((Date.now() - new Date(summary.last_checkin_date)) / 86400000);
        if (daysSince >= 2 && !nudge.style.display?.includes('block')) {
            nudge.textContent = `📊 Check in today to maintain your recovery data streak!`;
            nudge.style.background = 'rgba(59,130,246,0.1)';
            nudge.style.color = 'var(--blue)';
            nudge.style.display = 'block';
            hasContent = true;
        }
    }

    if (summary.days_to_show != null) {
        const d = summary.days_to_show;
        const compNudge = document.getElementById('lapse-nudge');
        const compText = d <= 0
            ? '🏆 Show day! Good luck today!'
            : d <= 7 ? `🚨 ${d} day${d !== 1 ? 's' : ''} to show — peak week protocols active!`
            : d <= 30 ? `⚡ ${d} days to show — stay sharp!`
            : `📅 ${d} days to show — comp prep in progress`;
        compNudge.textContent = compText;
        compNudge.style.background = d <= 7 ? 'rgba(239,68,68,0.12)' : 'rgba(240,165,0,0.1)';
        compNudge.style.color = d <= 7 ? '#ef4444' : 'var(--gold)';
        compNudge.style.display = 'block';
        hasContent = true;
    }

    card.style.display = hasContent ? 'block' : 'none';
}

function _scoreColor(score) {
    return score >= 70 ? 'var(--green)' : score >= 50 ? 'var(--gold)' : 'var(--red)';
}

function _sparklineSvg(scores, w = 180, h = 56) {
    if (scores.length < 2) return '';
    const pad = 6;
    const n = scores.length;
    const xs = scores.map((_, i) => pad + (i / (n - 1)) * (w - 2 * pad));
    const ys = scores.map(v => h - pad - (v / 100) * (h - 2 * pad));
    const pathD = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
    const last = scores[scores.length - 1];
    const stroke = last >= 70 ? '#22c55e' : last >= 50 ? '#f0a500' : '#ef4444';
    const dots = xs.map((x, i) =>
        `<circle cx="${x.toFixed(1)}" cy="${ys[i].toFixed(1)}" r="3" fill="${stroke}" opacity="0.85"/>`
    ).join('');
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
        <path d="${pathD}" fill="none" stroke="${stroke}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
        ${dots}
    </svg>`;
}

function renderRecoveryWidget(summary, checkins) {
    const card = document.getElementById('recovery-card');
    card.style.display = 'block';

    const avg = summary?.avg_recovery_7d;
    if (avg != null) {
        const avgEl = document.getElementById('recovery-avg');
        avgEl.textContent = `${avg}/100`;
        avgEl.style.color = _scoreColor(avg);
    }

    if (checkins.length >= 2) {
        const ordered = [...checkins].reverse();
        const scores = ordered.map(c => c.recovery_score ?? 0);
        document.getElementById('recovery-sparkline').innerHTML = _sparklineSvg(scores);
    }

    const tip = checkins[0]?.coaching_tip;
    const tipEl = document.getElementById('recovery-tip');
    if (tip) {
        tipEl.textContent = tip;
        tipEl.style.display = 'block';
    } else {
        tipEl.style.display = 'none';
    }

    const today = new Date().toISOString().slice(0, 10);
    const todayCheckin = checkins.find(c => c.date === today);
    const btn = document.getElementById('checkin-btn');
    const badge = document.getElementById('checkin-done-badge');
    if (todayCheckin) {
        btn.textContent = "Edit Today's Check-In";
        badge.style.display = 'inline';
        state.todayCheckinId = todayCheckin.id;
    } else {
        btn.textContent = 'Log Check-In';
        badge.style.display = 'none';
        state.todayCheckinId = null;
    }
}

/* ── Check-In Form ── */
function openCheckinForm() {
    const today = new Date().toISOString().slice(0, 10);
    const todayCheckin = state.latestCheckins.find(c => c.date === today);
    const keys = ['sleep', 'energy', 'soreness', 'stress'];
    if (todayCheckin) {
        keys.forEach(k => {
            const val = todayCheckin[`${k}_score`] ?? 5;
            document.getElementById(`ci-${k}`).value = val;
            document.getElementById(`ci-${k}-val`).textContent = val;
        });
    } else {
        keys.forEach(k => {
            document.getElementById(`ci-${k}`).value = 5;
            document.getElementById(`ci-${k}-val`).textContent = 5;
        });
    }
    document.getElementById('checkin-form-panel').style.display = 'block';
    document.getElementById('checkin-btn').style.display = 'none';
}

function closeCheckinForm() {
    document.getElementById('checkin-form-panel').style.display = 'none';
    document.getElementById('checkin-btn').style.display = '';
}

async function submitCheckin() {
    const btn = document.getElementById('ci-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Submitting…';
    const payload = {
        sleep_score: parseInt(document.getElementById('ci-sleep').value),
        energy_score: parseInt(document.getElementById('ci-energy').value),
        soreness_score: parseInt(document.getElementById('ci-soreness').value),
        stress_score: parseInt(document.getElementById('ci-stress').value),
    };
    try {
        let result;
        if (state.todayCheckinId) {
            result = await api('PUT', `/checkins/${state.todayCheckinId}`, payload);
        } else {
            result = await api('POST', '/checkins', payload);
        }
        closeCheckinForm();
        showToast(`Recovery logged — score: ${result.recovery_score}/100`);
        invalidateCache('/dashboard/summary', '/checkins?limit=7');
        await loadDashboard();
    } catch (e) {
        showToast(e.message || 'Failed to save check-in.', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Submit';
    }
}

/* ── Photo Upload & Analysis ── */
function handleDrop(e) {
    e.preventDefault();
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length) previewFiles(files);
}

function handleFileSelect(input) {
    const files = Array.from(input.files || []);
    if (files.length) previewFiles(files);
}

function previewFiles(files) {
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    const valid = files.filter(f => allowed.includes(f.type)).slice(0, 5);
    if (!valid.length) { showToast('Please use JPG, PNG, or WebP images.', 'error'); return; }
    state.pendingFiles = valid;
    const strip = document.getElementById('preview-strip');
    strip.innerHTML = '';
    valid.forEach(f => {
        const img = document.createElement('img');
        img.style.cssText = 'height:80px;width:auto;border-radius:6px;object-fit:cover';
        img.src = URL.createObjectURL(f);
        strip.appendChild(img);
    });
    document.getElementById('analyze-btn').textContent =
        valid.length > 1 ? `Analyze ${valid.length} Photos` : 'Analyze Photo';
    document.getElementById('drop-zone').style.display = 'none';
    document.getElementById('upload-preview').style.display = 'block';
}

function clearUpload() {
    state.pendingFiles = [];
    document.getElementById('photo-input').value = '';
    document.getElementById('preview-strip').innerHTML = '';
    document.getElementById('drop-zone').style.display = 'block';
    document.getElementById('upload-preview').style.display = 'none';
}

async function submitAnalysis() {
    const files = state.pendingFiles;
    if (!files?.length) return;

    const formData = new FormData();
    for (const f of files) {
        if (f.size > 20 * 1024 * 1024) {
            showToast('One or more photos exceed the 20MB limit.', 'error');
            return;
        }
        formData.append('files', f);
    }

    const btn = document.getElementById('analyze-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Analyzing…'; }
    showLoading('Analyzing your physique with AI… This takes 20-40 seconds.');
    try {
        const result = await api('POST', '/analyze', formData, true);
        invalidateCache('/plan/current', '/progress', '/analyses');
        hideLoading();
        clearUpload();
        renderAnalysisResult(result.analysis, result.created_at);
        document.getElementById('analysis-result').style.display = 'block';
        await loadAnalyses();
        showToast('Analysis complete!');
    } catch (err) {
        hideLoading();
        showToast(`Analysis failed: ${err.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Analyze Photo'; }
    }
}

function renderAnalysisResult(analysis, createdAt) {
    if (createdAt) {
        document.getElementById('analysis-date').textContent = formatDate(createdAt);
    }

    const muscle = analysis.muscle_development || {};
    const muscleKeys = ['chest', 'back', 'shoulders', 'arms', 'legs', 'core'];

    const muscleHtml = muscleKeys.map(key => {
        const m = muscle[key] || {};
        const score = m.score || 0;
        return `
            <div class="muscle-item">
                <div class="muscle-name">${esc(key)}</div>
                <div class="muscle-score">${esc(score)}/10</div>
                <div class="score-bar"><div class="score-fill" style="width:${score * 10}%"></div></div>
                <div class="muscle-notes">${esc(m.notes)}</div>
            </div>
        `;
    }).join('');

    const strengthsHtml = (analysis.strengths || [])
        .map(s => `<span class="tag tag-green">${esc(s)}</span>`).join('');

    const improvementsHtml = (analysis.areas_to_improve || [])
        .map(i => `<span class="tag tag-orange">${esc(i)}</span>`).join('');

    document.getElementById('analysis-content').innerHTML = `
        <div class="analysis-grid">
            <div>
                <div class="section-label">Body Composition</div>
                <div style="display:flex;gap:24px;margin-bottom:16px">
                    <div>
                        <div class="stat-label">Body Fat Estimate</div>
                        <div style="font-size:24px;font-weight:700;color:var(--gold)">${esc(analysis.body_fat_estimate)}</div>
                        <div style="font-size:12px;color:var(--text-muted)">Confidence: ${esc(analysis.body_fat_confidence)}</div>
                    </div>
                    <div>
                        <div class="stat-label">Physique Score</div>
                        <div style="font-size:24px;font-weight:700;color:var(--gold)">${esc(analysis.overall_physique_score)}/10</div>
                    </div>
                </div>

                <div class="section-label">Strengths</div>
                <div class="tag-list" style="margin-bottom:16px">${strengthsHtml}</div>

                <div class="section-label">Priority Improvements</div>
                <div class="tag-list" style="margin-bottom:16px">${improvementsHtml}</div>

                ${analysis.symmetry_notes ? `
                    <div class="section-label">Symmetry</div>
                    <p style="font-size:14px;color:var(--text-muted);margin-bottom:12px">${esc(analysis.symmetry_notes)}</p>
                ` : ''}

                ${analysis.posture_notes ? `
                    <div class="section-label">Posture</div>
                    <p style="font-size:14px;color:var(--text-muted);margin-bottom:12px">${esc(analysis.posture_notes)}</p>
                ` : ''}

                ${analysis.coach_message ? `
                    <div class="coach-message">${esc(analysis.coach_message)}</div>
                ` : ''}

                <p style="font-size:11px;color:var(--text-muted);margin-top:12px">${esc(analysis.disclaimer)}</p>
            </div>
            <div>
                <div class="section-label">Muscle Development</div>
                <div class="muscle-grid">${muscleHtml}</div>
            </div>
        </div>
    `;
}

async function loadAnalyses() {
    const analyses = await cachedApi('GET', '/analyses').catch(() => []);
    state.analyses = analyses;
    const container = document.getElementById('analyses-list');

    if (!analyses.length) {
        container.innerHTML = '<p class="empty-state">No analyses yet.</p>';
        return;
    }

    container.innerHTML = `<ul class="analyses-history">${
        analyses.map(a => `
            <li class="history-item" onclick="showHistoryAnalysis(${a.id})">
                <img class="history-thumb" src="${esc(a.photo_url)}" alt="Photo" />
                <div class="history-info">
                    <div class="history-bf">${esc(a.body_fat_estimate) || '—'}</div>
                    <div style="font-size:13px">Score: ${esc(a.overall_physique_score) || '—'}/10</div>
                    <div class="history-date">${esc(formatDate(a.created_at))}</div>
                </div>
                ${a.coach_message ? `<div style="font-size:12px;color:var(--text-muted);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.coach_message)}</div>` : ''}
            </li>
        `).join('')
    }</ul>`;
}

function showHistoryAnalysis(id) {
    const analysis = state.analyses.find(a => a.id === id);
    if (!analysis || !analysis.raw_analysis) return;
    renderAnalysisResult(analysis.raw_analysis, analysis.created_at);
    document.getElementById('analysis-result').style.display = 'block';
    document.getElementById('analysis-result').scrollIntoView({ behavior: 'smooth' });
}

async function generateWeakPoints() {
    showLoading('Analyzing muscle imbalances against your training volume…');
    try {
        const result = await api('POST', '/analysis/weak-points');
        hideLoading();
        const weakHtml = (result.weak_points || [])
            .map(p => `<li style="margin-bottom:6px">${esc(p)}</li>`).join('');
        const volHtml = Object.entries(result.volume_recommendations || {})
            .map(([m, r]) => `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
                    <span style="font-weight:600;text-transform:capitalize">${esc(m)}</span>
                    <span style="color:var(--text-muted);font-size:13px">${esc(r)}</span>
                </div>`).join('');
        document.getElementById('weak-points-result').innerHTML = `
            ${result.priority_fix ? `
                <div style="background:rgba(240,165,0,0.08);border:1px solid rgba(240,165,0,0.3);border-radius:8px;padding:14px;margin-bottom:16px">
                    <div class="stat-label" style="margin-top:0;color:var(--gold)">Priority Fix</div>
                    <p style="margin-top:4px">${esc(result.priority_fix)}</p>
                </div>` : ''}
            ${weakHtml ? `
                <div class="section-label">Identified Weak Points</div>
                <ul style="margin:8px 0 16px 16px;color:var(--text-muted);font-size:14px">${weakHtml}</ul>` : ''}
            ${volHtml ? `
                <div class="section-label">Volume Recommendations</div>
                <div style="margin-bottom:8px">${volHtml}</div>` : ''}
        `;
        document.getElementById('weak-points-result').style.display = 'block';
    } catch (err) {
        hideLoading();
        showToast(`Weak-point analysis failed: ${err.message}`, 'error');
    }
}

/* ── Plans ── */
async function generatePlan() {
    showLoading('Generating your personalized plan based on analysis + latest research… (30-60 seconds)');
    try {
        const plan = await api('POST', '/plan/generate');
        state.currentPlan = {
            workout_plan: plan.workout_plan,
            diet_plan: plan.diet_plan,
            supplement_plan: plan.supplement_plan,
            coaching_notes: plan.coaching_notes,
        };
        invalidateCache('/plan/current');
        hideLoading();
        showToast('Plan generated!');
        showTab('plans');
        renderPlan(plan);
    } catch (err) {
        hideLoading();
        showToast(`Plan generation failed: ${err.message}`, 'error');
    }
}

async function loadCurrentPlan() {
    const data = await cachedApi('GET', '/plan/current').catch(() => null);
    if (!data || (!data.workout_plan && !data.diet_plan)) {
        document.getElementById('no-plan-message').style.display = 'block';
        document.getElementById('plan-tabs').style.display = 'none';
        return;
    }
    document.getElementById('no-plan-message').style.display = 'none';
    document.getElementById('plan-tabs').style.display = 'block';
    renderPlan({
        workout_plan: data.workout_plan,
        diet_plan: data.diet_plan,
        supplement_plan: data.supplement_plan,
    });
}

function renderPlan(plan) {
    if (plan.workout_plan) renderWorkoutPlan(plan.workout_plan);
    if (plan.diet_plan) renderDietPlan(plan.diet_plan);
    if (plan.supplement_plan) renderSupplementPlan(plan.supplement_plan);
    if (plan.coaching_notes) renderCoachingNotes(plan.coaching_notes);
    document.getElementById('plan-tabs').style.display = 'block';
    document.getElementById('no-plan-message').style.display = 'none';
}

function renderWorkoutPlan(workout) {
    // Bot-synced plans arrive as {text: "...", source: "telegram_bot"}
    if (workout.text) {
        document.getElementById('plan-workout').innerHTML =
            '<pre style="white-space:pre-wrap;font-size:14px;line-height:1.6">' + esc(workout.text) + '</pre>';
        return;
    }
    const days = (workout.days || []).map(day => {
        const exerciseRows = (day.exercises || []).map(ex => `
            <tr>
                <td><strong>${esc(ex.name)}</strong><br><small style="color:var(--text-muted)">${esc(ex.why)}</small></td>
                <td>${esc(ex.sets)}</td>
                <td>${esc(ex.reps)}</td>
                <td>${ex.rest_seconds ? `${esc(ex.rest_seconds)}s` : '—'}</td>
                <td style="color:var(--text-muted);font-size:13px">${esc(ex.notes)}</td>
            </tr>
        `).join('');

        return `
            <div class="day-section">
                <div class="day-header">
                    <span>${esc(day.day)}</span>
                    <span class="day-focus">${esc(day.focus)}</span>
                </div>
                <table>
                    <thead><tr>
                        <th>Exercise</th><th>Sets</th><th>Reps</th><th>Rest</th><th>Notes</th>
                    </tr></thead>
                    <tbody>${exerciseRows}</tbody>
                </table>
                ${day.volume_note ? `<p style="font-size:12px;color:var(--text-muted);margin-top:8px">${esc(day.volume_note)}</p>` : ''}
            </div>
        `;
    }).join('');

    const citations = (workout.research_citations || [])
        .map(c => `<div class="citation">${esc(c)}</div>`).join('');

    document.getElementById('plan-workout').innerHTML = `
        <h3>Training Program — ${esc(workout.weekly_split)}</h3>
        <p style="color:var(--text-muted);margin-bottom:20px;font-size:14px">${esc(workout.split_rationale)}</p>
        ${days}
        ${workout.progression_strategy ? `
            <div class="section-label">Progression Strategy</div>
            <p style="font-size:14px;margin-bottom:12px">${esc(workout.progression_strategy)}</p>
        ` : ''}
        ${workout.deload_protocol ? `
            <div class="section-label">Deload Protocol</div>
            <p style="font-size:14px;margin-bottom:12px">${esc(workout.deload_protocol)}</p>
        ` : ''}
        ${citations ? `<div class="section-label">Research Citations</div>${citations}` : ''}
    `;
}

function renderDietPlan(diet) {
    const macros = diet.macros || {};
    const meals = (diet.sample_day || []).map(meal => `
        <div style="margin-bottom:16px;padding:14px;background:var(--surface-2);border-radius:8px">
            <strong style="font-size:14px">${esc(meal.meal)}</strong>
            <ul style="margin:8px 0 8px 16px;color:var(--text-muted);font-size:13px">
                ${(meal.foods || []).map(f => `<li>${esc(f)}</li>`).join('')}
            </ul>
            ${meal.approx_macros ? `
                <div style="display:flex;gap:16px;font-size:12px;color:var(--text-muted)">
                    <span>${esc(meal.approx_macros.calories)} kcal</span>
                    <span>P: ${esc(meal.approx_macros.protein)}g</span>
                    <span>C: ${esc(meal.approx_macros.carbs)}g</span>
                    <span>F: ${esc(meal.approx_macros.fat)}g</span>
                </div>
            ` : ''}
        </div>
    `).join('');

    const citations = (diet.research_citations || [])
        .map(c => `<div class="citation">${esc(c)}</div>`).join('');

    const timing = diet.meal_timing || {};

    document.getElementById('plan-diet').innerHTML = `
        <h3>Nutrition Plan</h3>
        <p style="color:var(--text-muted);margin-bottom:20px;font-size:14px">${esc(diet.goal_phase)}</p>

        <div class="macro-row">
            <div class="macro-card">
                <div class="macro-value">${esc(diet.daily_calories) || '—'}</div>
                <div class="macro-label">Daily Calories</div>
            </div>
            <div class="macro-card">
                <div class="macro-value">${esc(macros.protein_g) || '—'}g</div>
                <div class="macro-label">Protein</div>
            </div>
            <div class="macro-card">
                <div class="macro-value">${esc(macros.carbs_g) || '—'}g</div>
                <div class="macro-label">Carbohydrates</div>
            </div>
            <div class="macro-card">
                <div class="macro-value">${esc(macros.fat_g) || '—'}g</div>
                <div class="macro-label">Fat</div>
            </div>
        </div>

        ${macros.macro_rationale ? `<p style="font-size:13px;color:var(--text-muted);margin-bottom:20px">${esc(macros.macro_rationale)}</p>` : ''}

        ${Object.keys(timing).length ? `
            <div class="section-label">Meal Timing</div>
            <div style="display:grid;gap:10px;margin-bottom:20px">
                ${timing.pre_workout ? `<div style="padding:12px;background:var(--surface-2);border-radius:8px"><strong style="font-size:13px">Pre-Workout</strong><p style="font-size:13px;color:var(--text-muted);margin-top:4px">${esc(timing.pre_workout)}</p></div>` : ''}
                ${timing.post_workout ? `<div style="padding:12px;background:var(--surface-2);border-radius:8px"><strong style="font-size:13px">Post-Workout</strong><p style="font-size:13px;color:var(--text-muted);margin-top:4px">${esc(timing.post_workout)}</p></div>` : ''}
                ${timing.before_bed ? `<div style="padding:12px;background:var(--surface-2);border-radius:8px"><strong style="font-size:13px">Before Bed</strong><p style="font-size:13px;color:var(--text-muted);margin-top:4px">${esc(timing.before_bed)}</p></div>` : ''}
            </div>
        ` : ''}

        ${meals ? `<div class="section-label">Sample Day</div>${meals}` : ''}

        ${diet.foods_to_prioritize?.length ? `
            <div class="section-label">Foods to Prioritize</div>
            <ul style="color:var(--text-muted);font-size:14px;margin-left:16px;margin-bottom:16px">
                ${diet.foods_to_prioritize.map(f => `<li>${esc(f)}</li>`).join('')}
            </ul>
        ` : ''}

        ${diet.foods_to_limit?.length ? `
            <div class="section-label">Foods to Limit</div>
            <ul style="color:var(--text-muted);font-size:14px;margin-left:16px;margin-bottom:16px">
                ${diet.foods_to_limit.map(f => `<li>${esc(f)}</li>`).join('')}
            </ul>
        ` : ''}

        ${diet.hydration ? `
            <div class="section-label">Hydration</div>
            <p style="font-size:14px;color:var(--text-muted);margin-bottom:16px">${esc(diet.hydration)}</p>
        ` : ''}

        ${citations ? `<div class="section-label">Research Citations</div>${citations}` : ''}
    `;
}

function renderSupplementPlan(supplements) {
    if (!Array.isArray(supplements) || !supplements.length) {
        document.getElementById('plan-supplements').innerHTML = '<p class="empty-state">No supplement plan available.</p>';
        return;
    }

    const rows = supplements.map(s => `
        <tr>
            <td>
                <span class="priority-badge">#${esc(s.priority) || '?'}</span>
                <strong style="margin-left:8px">${esc(s.name)}</strong>
            </td>
            <td>${esc(s.dose) || '—'}</td>
            <td>${esc(s.timing) || '—'}</td>
            <td class="grade-${esc((s.evidence_grade || 'c').toLowerCase())}">${esc(s.evidence_grade) || '?'}</td>
            <td style="font-size:13px;color:var(--text-muted)">${esc(s.benefit)}</td>
            <td style="font-size:12px;color:var(--text-muted)">${esc(s.cost_per_month)}</td>
        </tr>
    `).join('');

    document.getElementById('plan-supplements').innerHTML = `
        <h3>Supplement Stack</h3>
        <p style="color:var(--text-muted);font-size:13px;margin-bottom:16px">Evidence grades: A = strong RCT evidence | B = moderate evidence | C = limited evidence | D = insufficient evidence</p>
        <div style="overflow-x:auto">
            <table class="supp-table">
                <thead><tr>
                    <th>Supplement</th><th>Dose</th><th>Timing</th><th>Grade</th><th>Benefit</th><th>Cost/Month</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
        <p style="font-size:12px;color:var(--text-muted);margin-top:16px">Consult a healthcare provider before starting any new supplement protocol.</p>
    `;
}

function renderCoachingNotes(notes) {
    if (!notes) return;
    const lifestyle = (notes.lifestyle_factors || []).map(f => `<li style="margin-bottom:6px">${esc(f)}</li>`).join('');

    document.getElementById('plan-coaching').innerHTML = `
        <h3>Coaching Notes</h3>
        ${notes.biggest_priority ? `
            <div style="background:rgba(240,165,0,0.08);border:1px solid rgba(240,165,0,0.3);border-radius:8px;padding:16px;margin-bottom:20px">
                <div class="section-label" style="margin-top:0">Biggest Priority Right Now</div>
                <p>${esc(notes.biggest_priority)}</p>
            </div>
        ` : ''}
        ${lifestyle ? `
            <div class="section-label">Lifestyle Factors</div>
            <ul style="color:var(--text-muted);font-size:14px;margin-left:16px;margin-bottom:16px">${lifestyle}</ul>
        ` : ''}
        ${notes['12_week_expectations'] ? `
            <div class="section-label">12-Week Expectations</div>
            <p style="font-size:14px;color:var(--text-muted);margin-bottom:16px">${esc(notes['12_week_expectations'])}</p>
        ` : ''}
        ${notes.check_in_schedule ? `
            <div class="section-label">Check-In Schedule</div>
            <p style="font-size:14px;color:var(--text-muted);margin-bottom:16px">${esc(notes.check_in_schedule)}</p>
        ` : ''}
        ${notes.motivation ? `<div class="coach-message">${esc(notes.motivation)}</div>` : ''}
    `;
}

/* ── Research ── */
async function refreshResearch() {
    showLoading('Fetching latest papers from PubMed & Semantic Scholar… This may take 1-2 minutes.');
    try {
        const result = await api('POST', '/research/refresh');
        invalidateCache('/research');
        hideLoading();
        showToast(`Research updated: ${result.count} topics refreshed`);
        if (state.activeTab === 'research') await loadResearch();
    } catch (err) {
        hideLoading();
        showToast(`Research refresh failed: ${err.message}`, 'error');
    }
}

async function loadResearch() {
    const data = await cachedApi('GET', '/research').catch(() => []);
    const container = document.getElementById('research-list');

    if (!data.length) {
        container.innerHTML = '<p class="empty-state">No research loaded. Click "Refresh Research" to fetch the latest papers.</p>';
        return;
    }

    container.innerHTML = data.map(topic => {
        const papersHtml = (topic.papers || []).slice(0, 8).map(p => `
            <li class="paper-item">
                <div class="paper-title">${esc(p.title)}</div>
                <div class="paper-meta">
                    ${p.authors?.length ? esc(p.authors.slice(0, 2).join(', ')) : ''}
                    ${p.year ? `• ${esc(p.year)}` : ''}
                    ${p.journal ? `• <em>${esc(p.journal)}</em>` : ''}
                    ${p.citation_count ? `• ${esc(p.citation_count)} citations` : ''}
                    ${p.source ? `• ${esc(p.source)}` : ''}
                </div>
                ${p.url ? `<a class="paper-link" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">Read paper →</a>` : ''}
            </li>
        `).join('');

        const topicId = topic.id;
        return `
            <div class="research-topic">
                <div class="research-topic-header" onclick="togglePapers(${topicId})">
                    <div>
                        <div class="research-topic-title">${esc(topic.topic)}</div>
                        <div style="font-size:12px;color:var(--text-muted)">${esc(topic.papers?.length || 0)} papers • Updated ${esc(formatDate(topic.last_updated))}</div>
                    </div>
                    <span class="toggle-papers" id="toggle-${topicId}">Show papers ▼</span>
                </div>
                ${topic.summary ? `<div class="research-summary">${esc(topic.summary)}</div>` : ''}
                <ul class="paper-list" id="papers-${topicId}" style="display:none">${papersHtml}</ul>
            </div>
        `;
    }).join('');
}

function togglePapers(topicId) {
    const list = document.getElementById(`papers-${topicId}`);
    const toggle = document.getElementById(`toggle-${topicId}`);
    if (list.style.display === 'none') {
        list.style.display = 'block';
        toggle.textContent = 'Hide papers ▲';
    } else {
        list.style.display = 'none';
        toggle.textContent = 'Show papers ▼';
    }
}

/* ── Progress ── */
async function loadProgress() {
    const [data, plateaus, measurements] = await Promise.all([
        cachedApi('GET', '/progress').catch(() => []),
        cachedApi('GET', '/progress/plateaus').catch(() => []),
        api('GET', '/measurements?limit=30').catch(() => []),
    ]);

    const container = document.getElementById('progress-content');
    if (data.length < 1) {
        container.innerHTML = '<p class="empty-state">No progress data yet. Upload at least two body photos to see your progress.</p>';
        document.getElementById('comparison-card').style.display = 'none';
    } else {
        const cards = data.map((entry, i) => `
            <div class="progress-card">
                <img src="${esc(entry.photo_url)}" alt="Progress photo ${i + 1}" loading="lazy" />
                <div class="progress-card-info">
                    <div class="progress-date">${esc(formatDate(entry.created_at))}</div>
                    <div class="progress-bf">${esc(entry.body_fat_estimate) || '—'}</div>
                    <div style="font-size:13px;color:var(--text-muted)">Score: ${esc(entry.overall_physique_score) || '—'}/10</div>
                </div>
            </div>
        `).join('');
        container.innerHTML = `<div class="progress-grid">${cards}</div>`;

        if (data.length >= 2) {
            const first = data[0];
            const last = data[data.length - 1];
            const bfChange = (first.body_fat_estimate && last.body_fat_estimate)
                ? `${esc(first.body_fat_estimate)} → ${esc(last.body_fat_estimate)}`
                : null;
            const scoreChange = (first.overall_physique_score && last.overall_physique_score)
                ? `${esc(first.overall_physique_score)} → ${esc(last.overall_physique_score)}/10`
                : null;
            const _photoCard = (label, entry) => `
                <div style="flex:1;min-width:160px;max-width:260px;text-align:center">
                    <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:1px">${label}</div>
                    <img src="${esc(entry.photo_url)}" style="width:100%;border-radius:8px;object-fit:cover;aspect-ratio:3/4" loading="lazy" />
                    <div style="font-size:12px;color:var(--text-muted);margin-top:6px">${esc(formatDate(entry.created_at))}</div>
                    <div style="font-size:14px;font-weight:600;color:var(--gold)">${esc(entry.body_fat_estimate) || '—'}</div>
                </div>`;
            document.getElementById('comparison-content').innerHTML =
                _photoCard('Before', first) +
                `<div style="flex:0;display:flex;align-items:center;padding:0 8px;font-size:22px;color:var(--text-muted)">→</div>` +
                _photoCard('Now', last) +
                (bfChange || scoreChange ? `
                    <div style="flex:1;min-width:120px;display:flex;flex-direction:column;justify-content:center;gap:12px">
                        ${bfChange ? `<div><div class="stat-label">Body Fat</div><div style="font-size:14px;color:var(--green)">${bfChange}</div></div>` : ''}
                        ${scoreChange ? `<div><div class="stat-label">Physique Score</div><div style="font-size:14px;color:var(--gold)">${scoreChange}</div></div>` : ''}
                    </div>` : '');
            document.getElementById('comparison-card').style.display = 'block';
        } else {
            document.getElementById('comparison-card').style.display = 'none';
        }
    }

    renderPlateaus(plateaus);
    renderMeasurements(measurements);
}

function renderMeasurements(data) {
    const el = document.getElementById('measurements-history');
    if (!data.length) {
        el.innerHTML = '<p class="empty-state">No measurements logged yet.</p>';
        return;
    }
    el.innerHTML = `
        <table style="width:100%;font-size:13px;border-collapse:collapse">
            <thead>
                <tr style="color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px">
                    <th style="padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)">Date</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Weight</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Waist</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Chest</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Arm</th>
                </tr>
            </thead>
            <tbody>
                ${data.map(m => `
                    <tr style="border-bottom:1px solid var(--border)">
                        <td style="padding:8px">${esc(m.date)}</td>
                        <td style="padding:8px;text-align:right;color:var(--gold)">${m.body_weight_kg != null ? `${m.body_weight_kg}kg` : '—'}</td>
                        <td style="padding:8px;text-align:right">${m.waist_cm != null ? `${m.waist_cm}cm` : '—'}</td>
                        <td style="padding:8px;text-align:right">${m.chest_cm != null ? `${m.chest_cm}cm` : '—'}</td>
                        <td style="padding:8px;text-align:right">${m.left_arm_cm != null ? `${m.left_arm_cm}cm` : '—'}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function logMeasurement(event) {
    event.preventDefault();
    const weight = parseFloat(document.getElementById('m-weight').value) || null;
    const waist = parseFloat(document.getElementById('m-waist').value) || null;
    const chest = parseFloat(document.getElementById('m-chest').value) || null;
    const arm = parseFloat(document.getElementById('m-arm').value) || null;

    if (!weight && !waist && !chest && !arm) {
        showToast('Enter at least one measurement.', 'error');
        return;
    }
    try {
        await api('POST', '/measurements', {
            body_weight_kg: weight, waist_cm: waist, chest_cm: chest,
            left_arm_cm: arm, right_arm_cm: arm,
        });
        ['m-weight', 'm-waist', 'm-chest', 'm-arm'].forEach(id => { document.getElementById(id).value = ''; });
        showToast('Measurements logged!');
        invalidateCache('/measurements?limit=30', '/dashboard/summary');
        const updated = await api('GET', '/measurements?limit=30').catch(() => []);
        renderMeasurements(updated);
    } catch (e) {
        showToast(e.message || 'Failed to log measurements.', 'error');
    }
}

function renderPlateaus(data) {
    const container = document.getElementById('plateaus-content');
    if (!data.length) {
        container.innerHTML = '<p class="empty-state">No strength data yet. Log workouts to see trends.</p>';
        return;
    }

    container.innerHTML = data.map(ex => {
        const badge = ex.stalled
            ? `<span style="background:rgba(239,68,68,0.15);color:#ef4444;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">PLATEAU</span>`
            : `<span style="background:rgba(34,197,94,0.12);color:#22c55e;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">PROGRESSING</span>`;

        const trendDots = ex.weekly_trend.map((v, i) => {
            const isLast = i === ex.weekly_trend.length - 1;
            return `<span style="font-size:${isLast ? '15px' : '13px'};font-weight:${isLast ? '700' : '400'};color:${isLast && ex.stalled ? '#ef4444' : 'var(--text-muted)'}">${v}</span>`;
        }).join(' → ');

        return `
            <div style="padding:12px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
                <div>
                    <div style="font-weight:600;font-size:14px">${esc(ex.exercise)}</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-top:3px">${trendDots} kg est. 1RM</div>
                </div>
                <div style="display:flex;align-items:center;gap:12px">
                    <div style="text-align:right">
                        <div style="font-size:18px;font-weight:700;color:var(--gold)">${esc(ex.current_1rm)}kg</div>
                        <div style="font-size:11px;color:var(--text-muted)">current est. 1RM</div>
                    </div>
                    ${badge}
                </div>
            </div>
        `;
    }).join('');
}

/* ── Profile ── */
async function loadProfile() {
    const [profile, me, goals] = await Promise.all([
        cachedApi('GET', '/profile').catch(() => ({})),
        api('GET', '/auth/me').catch(() => null),
        api('GET', '/goals').catch(() => []),
    ]);

    if (profile && profile.age) {
        const form = document.getElementById('profile-form');
        const fields = ['age', 'gender', 'height_cm', 'weight_kg', 'goal', 'training_experience', 'training_days_per_week', 'dietary_restrictions', 'show_date'];
        fields.forEach(field => {
            const el = form.querySelector(`[name="${field}"]`);
            if (el && profile[field] != null) el.value = profile[field];
        });
    }

    renderGoals(goals);
    loadBadgesAndStreaks();

    // Telegram link status
    if (me) {
        if (me.telegram_linked) {
            document.getElementById('telegram-unlinked-status').style.display = 'none';
            document.getElementById('telegram-linked-status').style.display = '';
            document.getElementById('telegram-linked-email').textContent = me.email;
        } else {
            document.getElementById('telegram-unlinked-status').style.display = '';
            document.getElementById('telegram-linked-status').style.display = 'none';
        }
    }
}

async function linkTelegram() {
    const code = (document.getElementById('telegram-link-code').value || '').trim().toUpperCase();
    const errEl = document.getElementById('telegram-link-error');
    errEl.style.display = 'none';
    if (!code) { errEl.textContent = 'Enter the code from /link in Telegram.'; errEl.style.display = ''; return; }

    try {
        const res = await api('POST', '/auth/link-telegram', { code });
        showToast('Telegram linked successfully!');
        invalidateCache('/auth/me');
        await loadProfile();
    } catch (err) {
        const msg = err.message || 'Invalid or expired code.';
        errEl.textContent = msg;
        errEl.style.display = '';
        // Remove any stale sign-out helper from a previous attempt
        const prev = document.getElementById('telegram-signout-btn');
        if (prev) prev.remove();
        // Stale session: server was restarted and wiped the DB — guide the user to re-register
        if (msg.toLowerCase().includes('account not found') || msg.toLowerCase().includes('sign out')) {
            const btn = document.createElement('button');
            btn.id = 'telegram-signout-btn';
            btn.textContent = 'Sign Out & Register Again';
            btn.className = 'btn btn-secondary';
            btn.style.cssText = 'margin-top:8px;width:100%';
            btn.onclick = () => signOut();
            errEl.after(btn);
        }
    }
}

async function saveProfile(event) {
    event.preventDefault();
    const form = event.target;
    const data = {
        age: parseInt(form.age.value) || null,
        gender: form.gender.value || null,
        height_cm: parseFloat(form.height_cm.value) || null,
        weight_kg: parseFloat(form.weight_kg.value) || null,
        goal: form.goal.value || null,
        training_experience: form.training_experience.value || null,
        training_days_per_week: parseInt(form.training_days_per_week.value) || null,
        dietary_restrictions: form.dietary_restrictions.value || null,
        show_date: form.show_date?.value || null,
    };

    try {
        await api('POST', '/profile', data);
        invalidateCache('/profile', '/plan/current');
        showToast('Profile saved!');
    } catch (err) {
        showToast(`Save failed: ${err.message}`, 'error');
    }
}

/* ── Goals ── */
function renderGoals(goals) {
    const el = document.getElementById('goals-list');
    if (!goals.length) {
        el.innerHTML = '<p class="empty-state">No goals set yet.</p>';
        return;
    }
    el.innerHTML = goals.map(g => {
        const parts = [];
        if (g.target_weight_kg) parts.push(`→ ${g.target_weight_kg}kg`);
        if (g.target_bf_pct) parts.push(`→ ${g.target_bf_pct}% BF`);
        if (g.target_date) parts.push(`by ${esc(g.target_date)}`);
        const badge = g.is_active
            ? `<span style="background:rgba(34,197,94,0.12);color:#22c55e;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">ACTIVE</span>`
            : `<span style="background:rgba(100,100,100,0.12);color:var(--text-muted);padding:2px 8px;border-radius:4px;font-size:11px">inactive</span>`;
        return `
            <div style="padding:10px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:10px">
                <div>
                    <div style="font-weight:600;font-size:14px;text-transform:uppercase;letter-spacing:0.5px">${esc(g.goal_type)}</div>
                    ${parts.length ? `<div style="font-size:13px;color:var(--text-muted);margin-top:2px">${parts.join(' · ')}</div>` : ''}
                </div>
                ${badge}
            </div>`;
    }).join('');
}

async function saveGoal(event) {
    event.preventDefault();
    const form = event.target;
    try {
        await api('POST', '/goals', {
            goal_type: form.goal_type.value,
            target_weight_kg: parseFloat(form.target_weight_kg.value) || null,
            target_bf_pct: parseFloat(form.target_bf_pct.value) || null,
            target_date: form.target_date.value || null,
        });
        showToast('Goal set!');
        form.target_weight_kg.value = '';
        form.target_bf_pct.value = '';
        form.target_date.value = '';
        invalidateCache('/goals');
        const goals = await api('GET', '/goals').catch(() => []);
        renderGoals(goals);
    } catch (e) {
        showToast(e.message || 'Failed to save goal.', 'error');
    }
}

/* ── Nutrition ── */
async function loadNutrition() {
    const [today, recent] = await Promise.all([
        api('GET', '/meals/today').catch(() => ({ meals: [], totals: {} })),
        api('GET', '/meals?limit=30').catch(() => []),
    ]);

    const t = today.totals || {};
    document.getElementById('nt-calories').textContent = t.calories ? `${t.calories} kcal` : '—';
    document.getElementById('nt-protein').textContent = t.protein_g ? `${t.protein_g}g` : '—';
    document.getElementById('nt-carbs').textContent = t.carbs_g ? `${t.carbs_g}g` : '—';
    document.getElementById('nt-fat').textContent = t.fat_g ? `${t.fat_g}g` : '—';

    const todayList = document.getElementById('meals-today-list');
    if (today.meals.length) {
        todayList.innerHTML = today.meals.map(m => `
            <div style="padding:10px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
                <div>
                    <div style="font-weight:500;font-size:14px">${esc(m.description || 'Meal')}</div>
                    <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${m.macro_source === 'estimated' ? '~ AI-estimated' : 'manual'}</div>
                </div>
                <div style="text-align:right;flex-shrink:0;font-size:13px">
                    ${m.calories ? `<span style="color:var(--text-muted)">${m.calories} kcal</span>` : ''}
                    ${m.protein_g ? `<span style="color:var(--gold);margin-left:8px">${m.protein_g}g P</span>` : ''}
                </div>
            </div>`).join('');
    } else {
        todayList.innerHTML = '<p class="empty-state">No meals logged today.</p>';
    }

    const histList = document.getElementById('meals-history-list');
    if (recent.length) {
        histList.innerHTML = recent.map(m => `
            <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:8px">
                <div>
                    <div style="font-size:13px;font-weight:500">${esc(m.description || 'Meal')}</div>
                    <div style="font-size:11px;color:var(--text-muted)">${esc(m.date)}</div>
                </div>
                <div style="font-size:12px;color:var(--text-muted);text-align:right">
                    ${m.calories ? `${m.calories} kcal` : ''}${m.protein_g ? ` · ${m.protein_g}g P` : ''}
                </div>
            </div>`).join('');
    } else {
        histList.innerHTML = '<p class="empty-state">No meals logged yet.</p>';
    }
}

async function logMeal(event) {
    event.preventDefault();
    const btn = document.getElementById('meal-log-btn');
    btn.disabled = true;
    btn.textContent = 'Logging…';

    const desc = document.getElementById('meal-desc').value.trim();
    const kcal = parseFloat(document.getElementById('meal-kcal').value) || null;
    const prot = parseFloat(document.getElementById('meal-protein').value) || null;
    const carbs = parseFloat(document.getElementById('meal-carbs').value) || null;
    const fat = parseFloat(document.getElementById('meal-fat').value) || null;

    if (!desc && kcal === null && prot === null) {
        showToast('Enter a description or macros.', 'error');
        btn.disabled = false;
        btn.textContent = 'Log Meal';
        return;
    }
    try {
        const result = await api('POST', '/meals', { description: desc, calories: kcal, protein_g: prot, carbs_g: carbs, fat_g: fat });
        ['meal-desc', 'meal-kcal', 'meal-protein', 'meal-carbs', 'meal-fat'].forEach(id => { document.getElementById(id).value = ''; });
        const src = result.macro_source === 'estimated' ? ' (AI-estimated)' : '';
        const pStr = result.protein_g ? ` · ${result.protein_g}g protein` : '';
        showToast(`Meal logged${pStr}${src}`);
        invalidateCache('/dashboard/summary');
        await loadNutrition();
    } catch (e) {
        showToast(e.message || 'Failed to log meal.', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Log Meal';
    }
}

/* ── Coach Memory ── */
async function loadMemory() {
    const memories = await api('GET', '/memory?limit=20').catch(() => []);
    const card = document.getElementById('memory-card');
    const list = document.getElementById('memory-list');
    if (!memories.length) {
        card.style.display = 'none';
        return;
    }
    card.style.display = 'block';
    const typeColor = { pr: 'var(--gold)', recovery: 'var(--green)', note: 'var(--blue)', observation: 'var(--text-muted)' };
    list.innerHTML = memories.map(m => `
        <div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;gap:10px;align-items:flex-start">
            <span style="background:rgba(255,255,255,0.07);color:${typeColor[m.memory_type] || 'var(--text-muted)'};padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600;flex-shrink:0;text-transform:uppercase">${esc(m.memory_type)}</span>
            <div>
                <div style="font-size:13px">${esc(m.content)}</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${esc(formatDate(m.created_at))}</div>
            </div>
        </div>`).join('');
}

async function addMemory(event) {
    event.preventDefault();
    const input = document.getElementById('memory-input');
    const content = input.value.trim();
    if (!content) return;
    try {
        await api('POST', '/memory', { content, memory_type: 'note' });
        input.value = '';
        showToast('Note added to coach memory.');
        await loadMemory();
    } catch (e) {
        showToast(e.message || 'Failed to add note.', 'error');
    }
}

/* ── Badges & Streaks ── */
async function loadBadgesAndStreaks() {
    const [badges, streaks] = await Promise.all([
        api('GET', '/badges').catch(() => []),
        api('GET', '/streaks').catch(() => ({})),
    ]);
    renderBadges(badges);
    renderStreakDetail(streaks);
}

function renderBadges(badges) {
    const el = document.getElementById('badges-earned');
    if (!badges.length) {
        el.innerHTML = '<p class="empty-state">No badges yet — complete check-in and workout streaks to earn them.</p>';
        return;
    }
    const _badgeLabel = t => t.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    el.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:8px">` +
        badges.map(b => `
            <div style="background:rgba(240,165,0,0.12);border:1px solid rgba(240,165,0,0.3);border-radius:8px;padding:8px 12px;text-align:center;min-width:100px">
                <div style="font-size:20px">🏅</div>
                <div style="font-size:12px;font-weight:600;color:var(--gold);margin-top:4px">${esc(_badgeLabel(b.badge_type))}</div>
                <div style="font-size:10px;color:var(--text-muted);margin-top:2px">${esc(b.earned_at.slice(0, 10))}</div>
            </div>`).join('') +
        `</div>`;
}

function renderStreakDetail(streaks) {
    const el = document.getElementById('streaks-detail');
    const entries = Object.entries(streaks);
    if (!entries.length) { el.innerHTML = ''; return; }
    el.innerHTML = `
        <table style="width:100%;font-size:13px;border-collapse:collapse;margin-top:14px">
            <thead>
                <tr style="color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px">
                    <th style="padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)">Type</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Current</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Best</th>
                    <th style="padding:6px 8px;text-align:right;border-bottom:1px solid var(--border)">Total Days</th>
                </tr>
            </thead>
            <tbody>
                ${entries.map(([type, s]) => `
                    <tr style="border-bottom:1px solid var(--border)">
                        <td style="padding:8px;font-weight:500;text-transform:capitalize">${esc(type.replace('_', ' '))}</td>
                        <td style="padding:8px;text-align:right;color:var(--gold);font-weight:700">${s.current_streak}</td>
                        <td style="padding:8px;text-align:right">${s.longest_streak}</td>
                        <td style="padding:8px;text-align:right;color:var(--text-muted)">${s.total_days_active}</td>
                    </tr>`).join('')}
            </tbody>
        </table>`;
}

/* ── Workout Logger ── */
async function loadWorkout() {
    const [active, history, prs, planData] = await Promise.all([
        api('GET', '/sessions/active').catch(() => ({ session: null, sets: [] })),
        api('GET', '/sessions/history').catch(() => []),
        api('GET', '/prs').catch(() => []),
        cachedApi('GET', '/plan/current').catch(() => null),
    ]);

    const exercises = [];
    if (planData?.workout_plan?.days) {
        for (const day of planData.workout_plan.days) {
            for (const ex of (day.exercises || [])) {
                if (ex.name && !exercises.includes(ex.name)) exercises.push(ex.name);
            }
        }
    }
    state._workoutExercises = exercises;

    renderSessionHistory(history);
    renderPRs(prs);

    const lastTargets = history[0]?.next_session_targets;
    _showNextSessionTargets(lastTargets || null);

    if (active.session) {
        state.activeSessionId = active.session.id;
        state.sessionStartTime = new Date(active.session.started_at);
        showActiveSession();
        renderSessionSets(active.sets);
        startSessionTimer();
    } else {
        state.activeSessionId = null;
        showNoSession();
    }
}

function renderExerciseChips(exercises) {
    const container = document.getElementById('exercise-chips');
    if (!exercises.length) {
        container.innerHTML = '<span style="color:var(--text-muted);font-size:13px">Generate a plan to get exercise suggestions, or type one below.</span>';
        return;
    }
    container.innerHTML = exercises.map(name =>
        `<button class="chip" onclick="selectExercise(${JSON.stringify(name)})">${esc(name)}</button>`
    ).join('');
}

function selectExercise(name) {
    state.selectedExercise = name;
    document.getElementById('selected-exercise-name').textContent = name;
    document.getElementById('log-set-card').style.display = 'block';
    document.getElementById('log-set-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    document.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', c.textContent === name);
    });
    updatePRHint(name);
}

function selectCustomExercise() {
    const val = document.getElementById('custom-exercise-input').value.trim();
    if (val) selectExercise(val);
}

function clearSelectedExercise() {
    state.selectedExercise = null;
    document.getElementById('log-set-card').style.display = 'none';
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
}

function adjustWeight(delta) {
    const input = document.getElementById('weight-input');
    input.value = Math.max(0, Math.round((parseFloat(input.value || 0) + delta) * 10) / 10);
}

function adjustReps(delta) {
    const input = document.getElementById('reps-input');
    input.value = Math.max(1, parseInt(input.value || 1) + delta);
}

function updatePRHint(name) {
    const hint = document.getElementById('last-1rm-hint');
    const prRow = document.querySelector(`.pr-row[data-exercise="${CSS.escape(name)}"]`);
    if (prRow) {
        hint.textContent = `Current PR: ${prRow.dataset.estimated1rm}kg est. 1RM`;
    } else {
        hint.textContent = '';
    }
}

async function startSession() {
    try {
        const s = await api('POST', '/sessions/start', {});
        state.activeSessionId = s.id;
        state.sessionStartTime = new Date(s.started_at);
        showActiveSession();
        renderSessionSets([]);
        startSessionTimer();
        showToast('Session started!');
    } catch (err) {
        showToast(`Failed to start session: ${err.message}`, 'error');
    }
}

async function endSession() {
    if (!state.activeSessionId) return;
    try {
        const summary = await api('POST', `/sessions/${state.activeSessionId}/end`, {});
        stopSessionTimer();
        state.activeSessionId = null;
        state.selectedExercise = null;
        showNoSession();
        const volStr = summary.total_volume_kg > 0 ? ` · ${summary.total_volume_kg}kg volume` : '';
        const streakStr = summary.workout_streak > 1 ? ` 🔥 ${summary.workout_streak}-day streak!` : '';
        showToast(`Session done! ${summary.set_count} sets${volStr}${streakStr}`);
        if ([7, 14, 30, 60, 90].includes(summary.workout_streak)) {
            setTimeout(() => showToast(`🏆 Milestone! ${summary.workout_streak}-day workout streak achieved!`, 'success'), 1500);
        }
        if (summary.next_session_targets) {
            _showNextSessionTargets(summary.next_session_targets);
        }
        invalidateCache('/sessions/history');
        await loadWorkout();
    } catch (err) {
        showToast(`Failed to end session: ${err.message}`, 'error');
    }
}

function _showNextSessionTargets(targets) {
    const card = document.getElementById('next-session-targets-card');
    const text = document.getElementById('next-session-targets-text');
    if (!targets) { card.style.display = 'none'; return; }
    text.textContent = targets;
    card.style.display = 'block';
}

async function logSet() {
    if (!state.activeSessionId || !state.selectedExercise) return;
    const weight = parseFloat(document.getElementById('weight-input').value);
    const reps = parseInt(document.getElementById('reps-input').value);
    if (!weight || weight <= 0 || !reps || reps <= 0) {
        showToast('Enter valid weight and reps.', 'error');
        return;
    }
    const logBtn = document.querySelector('#log-set-card .btn-primary');
    if (logBtn) { logBtn.disabled = true; logBtn.textContent = 'Saving…'; }
    try {
        const result = await api('POST', `/sessions/${state.activeSessionId}/sets`, {
            exercise_name: state.selectedExercise,
            weight_kg: weight,
            reps,
        });
        const container = document.getElementById('session-sets-list');
        if (container.querySelector('.empty-state')) {
            container.innerHTML = '<ul class="set-log-list"></ul>';
        }
        const ul = container.querySelector('ul');
        const prBadge = result.is_pr ? '<span class="pr-badge">🏆 PR</span>' : '';
        const li = document.createElement('li');
        li.className = 'set-log-item';
        li.innerHTML = `
            <span class="set-exercise">${esc(result.exercise_name)}</span>
            <span class="set-detail">${esc(result.weight_kg)}kg × ${esc(result.reps)}</span>
            <span class="set-1rm">~${esc(result.estimated_1rm)}kg 1RM</span>
            ${prBadge}
        `;
        ul.prepend(li);
        if (result.is_pr) {
            showToast(`🏆 New PR on ${result.exercise_name}! ${result.estimated_1rm}kg est. 1RM`);
            invalidateCache('/prs');
            await loadPRs();
        }
    } catch (err) {
        showToast(`Log failed: ${err.message}`, 'error');
    } finally {
        if (logBtn) { logBtn.disabled = false; logBtn.textContent = 'Log Set'; }
    }
}

async function loadPRs() {
    const prs = await api('GET', '/prs').catch(() => []);
    renderPRs(prs);
}

function renderSessionSets(sets) {
    const container = document.getElementById('session-sets-list');
    if (!sets.length) {
        container.innerHTML = '<p class="empty-state" style="padding:16px 0">No sets yet. Select an exercise above.</p>';
        return;
    }
    const items = [...sets].reverse().map(s => `
        <li class="set-log-item">
            <span class="set-exercise">${esc(s.exercise_name)}</span>
            <span class="set-detail">${esc(s.weight_kg)}kg × ${esc(s.reps)}</span>
            <span class="set-1rm">~${esc(s.estimated_1rm)}kg 1RM</span>
        </li>
    `).join('');
    container.innerHTML = `<ul class="set-log-list">${items}</ul>`;
}

function renderSessionHistory(history) {
    const container = document.getElementById('session-history-list');
    if (!history.length) {
        container.innerHTML = '<p class="empty-state">No sessions yet.</p>';
        return;
    }
    container.innerHTML = `<ul class="session-history">${
        history.map(s => {
            const dur = s.ended_at
                ? Math.round((new Date(s.ended_at) - new Date(s.started_at)) / 60000) + ' min'
                : '';
            const exStr = s.exercises.slice(0, 4).join(', ') + (s.exercises.length > 4 ? '…' : '');
            const targetsHtml = s.next_session_targets
                ? `<div style="margin-top:6px;font-size:12px;color:var(--gold);white-space:pre-line">${esc(s.next_session_targets)}</div>`
                : '';
            return `
                <li class="session-history-item">
                    <div style="font-weight:600;font-size:14px">${esc(formatDate(s.started_at))}</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-top:2px">
                        ${esc(s.set_count)} sets · ${esc(s.total_volume_kg)}kg volume${dur ? ` · ${esc(dur)}` : ''}
                    </div>
                    ${exStr ? `<div style="font-size:12px;color:var(--text-muted)">${esc(exStr)}</div>` : ''}
                    ${targetsHtml}
                </li>
            `;
        }).join('')
    }</ul>`;
}

function renderPRs(prs) {
    const container = document.getElementById('prs-list');
    if (!prs.length) {
        container.innerHTML = '<p class="empty-state">No personal records yet.</p>';
        return;
    }
    container.innerHTML = `
        <div style="overflow-x:auto">
            <table style="width:100%">
                <thead><tr>
                    <th>Exercise</th><th>Weight</th><th>Reps</th><th>Est. 1RM</th><th>Date</th>
                </tr></thead>
                <tbody>
                    ${prs.map(r => `
                        <tr class="pr-row" data-exercise="${esc(r.exercise_name)}" data-estimated1rm="${esc(r.estimated_1rm)}">
                            <td><strong>${esc(r.exercise_name)}</strong></td>
                            <td>${esc(r.weight_kg)}kg</td>
                            <td>${esc(r.reps)}</td>
                            <td style="color:var(--gold);font-weight:600">${esc(r.estimated_1rm)}kg</td>
                            <td style="color:var(--text-muted);font-size:13px">${esc(formatDate(r.achieved_at))}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function showActiveSession() {
    document.getElementById('workout-no-session').style.display = 'none';
    document.getElementById('workout-active-session').style.display = 'block';
    renderExerciseChips(state._workoutExercises);
}

function showNoSession() {
    document.getElementById('workout-no-session').style.display = 'block';
    document.getElementById('workout-active-session').style.display = 'none';
    clearInterval(state.sessionTimerInterval);
}

function startSessionTimer() {
    clearInterval(state.sessionTimerInterval);
    state.sessionTimerInterval = setInterval(() => {
        if (!state.sessionStartTime) return;
        const elapsed = Math.floor((Date.now() - state.sessionStartTime) / 1000);
        const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
        const s = String(elapsed % 60).padStart(2, '0');
        const el = document.getElementById('session-timer');
        if (el) el.textContent = `${m}:${s}`;
    }, 1000);
}

function stopSessionTimer() {
    clearInterval(state.sessionTimerInterval);
    state.sessionTimerInterval = null;
}

/* ── Reports ── */
async function generateReport() {
    showLoading('Generating your weekly AI coaching report… (15-30 seconds)');
    try {
        const report = await api('POST', '/reports/generate');
        invalidateCache('/reports');
        hideLoading();
        showToast('Weekly report generated!');
        await loadReports();
    } catch (err) {
        hideLoading();
        showToast(`Report failed: ${err.message}`, 'error');
    }
}

const _reportsById = {};

function printReport(id) {
    const r = _reportsById[id];
    if (!r) return;
    const win = window.open('', '_blank', 'width=800,height=700');
    const insightsHtml = (r.ai_insights || []).map(i => `<li style="margin-bottom:8px">${i}</li>`).join('');
    win.document.write(`<!DOCTYPE html><html><head><title>Weekly Report — Week of ${r.week_start}</title>
    <style>body{font-family:sans-serif;max-width:700px;margin:40px auto;color:#111}
    h1{font-size:22px}h2{font-size:16px;margin-top:24px;border-bottom:1px solid #ddd}
    .stats{display:flex;gap:32px;margin:16px 0}.stat{text-align:center}
    .stat-label{font-size:11px;color:#888;text-transform:uppercase}.stat-val{font-size:22px;font-weight:700}
    .focus{background:#fffbe6;border:1px solid #f0c000;border-radius:8px;padding:14px;margin-top:16px}
    @media print{body{margin:20px}}</style></head><body>
    <h1>Weekly Coaching Report</h1>
    <p style="color:#666">Week of ${esc(r.week_start)} ${r.adherence_rating ? `· <strong>${esc(r.adherence_rating)}</strong>` : ''}</p>
    <div class="stats">
        ${r.sessions_count != null ? `<div class="stat"><div class="stat-val">${r.sessions_count}</div><div class="stat-label">Sessions</div></div>` : ''}
        ${r.avg_recovery != null ? `<div class="stat"><div class="stat-val">${r.avg_recovery}/100</div><div class="stat-label">Avg Recovery</div></div>` : ''}
        ${r.avg_protein_g != null ? `<div class="stat"><div class="stat-val">${r.avg_protein_g}g</div><div class="stat-label">Avg Protein</div></div>` : ''}
        ${r.prs_count ? `<div class="stat"><div class="stat-val">${r.prs_count}</div><div class="stat-label">New PRs</div></div>` : ''}
    </div>
    ${insightsHtml ? `<h2>Coaching Insights</h2><ul>${insightsHtml}</ul>` : ''}
    ${r.next_week_focus ? `<div class="focus"><strong>Next Week Focus:</strong><br>${esc(r.next_week_focus)}</div>` : ''}
    </body></html>`);
    win.document.close();
    win.print();
}

async function loadReports() {
    const reports = await cachedApi('GET', '/reports').catch(() => []);
    const container = document.getElementById('reports-list');
    if (!reports.length) {
        container.innerHTML = '<p class="empty-state">No reports yet. Reports are auto-generated every Sunday for Pro users, or click "Generate Report Now" above.</p>';
        return;
    }
    reports.forEach(r => { _reportsById[r.id] = r; });
    container.innerHTML = reports.map(r => {
        const insightsHtml = (r.ai_insights || [])
            .map(i => `<li style="margin-bottom:6px;color:var(--text-muted);font-size:14px">${esc(i)}</li>`).join('');
        const ratingColor = r.adherence_rating === 'Excellent' ? 'var(--green)'
            : r.adherence_rating === 'Good' ? 'var(--gold)'
            : r.adherence_rating ? 'var(--red)' : 'var(--text-muted)';
        return `
            <div class="card" style="margin-bottom:16px">
                <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:14px">
                    <div>
                        <div style="font-weight:700;font-size:16px">Week of ${esc(r.week_start)}</div>
                        <div style="font-size:12px;color:var(--text-muted);margin-top:2px">${esc(formatDate(r.created_at))}</div>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px">
                        ${r.adherence_rating ? `<span style="padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600;background:rgba(240,165,0,0.1);color:${ratingColor}">${esc(r.adherence_rating)}</span>` : ''}
                        <button class="btn btn-ghost btn-sm" onclick="printReport(${r.id})">PDF</button>
                    </div>
                </div>
                <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:14px">
                    ${r.sessions_count != null ? `<div><div class="stat-label">Sessions</div><div style="font-size:18px;font-weight:700">${esc(r.sessions_count)}</div></div>` : ''}
                    ${r.avg_recovery != null ? `<div><div class="stat-label">Avg Recovery</div><div style="font-size:18px;font-weight:700;color:${r.avg_recovery >= 70 ? 'var(--green)' : r.avg_recovery >= 50 ? 'var(--gold)' : 'var(--red)'}">${esc(r.avg_recovery)}/100</div></div>` : ''}
                    ${r.avg_protein_g != null ? `<div><div class="stat-label">Avg Protein</div><div style="font-size:18px;font-weight:700">${esc(r.avg_protein_g)}g</div></div>` : ''}
                    ${r.prs_count ? `<div><div class="stat-label">New PRs</div><div style="font-size:18px;font-weight:700;color:var(--gold)">${esc(r.prs_count)}</div></div>` : ''}
                </div>
                ${insightsHtml ? `<div class="section-label">Coaching Insights</div><ul style="margin:8px 0 12px 16px">${insightsHtml}</ul>` : ''}
                ${r.next_week_focus ? `
                    <div style="background:rgba(240,165,0,0.07);border:1px solid rgba(240,165,0,0.25);border-radius:8px;padding:12px">
                        <div class="stat-label" style="margin-top:0;color:var(--gold)">Next Week Focus</div>
                        <p style="font-size:14px;margin-top:4px">${esc(r.next_week_focus)}</p>
                    </div>` : ''}
            </div>
        `;
    }).join('');
}

/* ── Utilities ── */
function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/* ── Onboarding wizard ── */
const _obState = { goal: null, exp: null, days: null };
let _obCurrentStep = 0;

const OB_STEPS = ['ob-step-0', 'ob-step-1', 'ob-step-2', 'ob-step-3', 'ob-step-4', 'ob-step-5', 'ob-step-6'];

function obGoToStep(stepIndex) {
    _obCurrentStep = stepIndex;
    OB_STEPS.forEach((id, i) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('active', i === stepIndex);
    });
    const loginStep = document.getElementById('ob-step-login');
    if (loginStep) loginStep.classList.remove('active');
    for (let i = 0; i <= 6; i++) {
        const dot = document.getElementById(`dot-${i}`);
        if (dot) dot.classList.toggle('active', i === stepIndex);
    }
}

function obNext() { obGoToStep(_obCurrentStep + 1); }
function obSkip() { obGoToStep(_obCurrentStep + 1); }

function obShowLogin() {
    OB_STEPS.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    });
    document.getElementById('ob-step-login').classList.add('active');
}

function obSelectChip(el, group) {
    const container = el.closest('.ob-chips');
    container.querySelectorAll('.ob-chip').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    _obState[group] = el.dataset.val;
}

async function obLogin() {
    const email = document.getElementById('ob-login-email').value.trim();
    const password = document.getElementById('ob-login-password').value;
    const errEl = document.getElementById('ob-login-error');
    errEl.textContent = '';
    if (!email || !password) { errEl.textContent = 'Please fill in all fields.'; return; }
    try {
        const data = await api('POST', '/auth/login', { email, password });
        setAuth(data.token, data.user);
        await loadDashboard();
    } catch (err) {
        errEl.textContent = err.message;
    }
}

async function obCreateAccount() {
    const email = document.getElementById('ob-email').value.trim();
    const password = document.getElementById('ob-password').value;
    const errEl = document.getElementById('ob-auth-error');
    errEl.textContent = '';
    if (!email || !password) { errEl.textContent = 'Please fill in all fields.'; return; }
    if (password.length < 8) { errEl.textContent = 'Password must be at least 8 characters.'; return; }
    try {
        const data = await api('POST', '/auth/register', { email, password });
        setAuth(data.token, data.user);
        obGoToStep(2);
    } catch (err) {
        errEl.textContent = err.message;
    }
}

async function obSaveBasicProfile() {
    const age = document.getElementById('ob-age').value;
    const gender = document.getElementById('ob-gender').value;
    const height = document.getElementById('ob-height').value;
    const weight = document.getElementById('ob-weight').value;
    const body = {};
    if (age) body.age = parseInt(age);
    if (gender) body.gender = gender;
    if (height) body.height_cm = parseFloat(height);
    if (weight) body.weight_kg = parseFloat(weight);
    if (Object.keys(body).length > 0) {
        await api('POST', '/profile', body).catch(() => {});
        invalidateCache('/profile');
    }
    obGoToStep(3);
}

async function obSaveGoals() {
    const body = {};
    if (_obState.goal) body.goal = _obState.goal;
    if (_obState.exp) body.training_experience = _obState.exp;
    if (_obState.days) body.training_days_per_week = parseInt(_obState.days);
    if (Object.keys(body).length > 0) {
        await api('POST', '/profile', body).catch(() => {});
        invalidateCache('/profile');
    }
    obGoToStep(4);
}

async function obSaveDiet() {
    const diet = document.getElementById('ob-diet').value.trim();
    if (diet) {
        await api('POST', '/profile', { dietary_restrictions: diet }).catch(() => {});
        invalidateCache('/profile');
    }
    obGoToStep(5);
}

async function obLinkTelegram() {
    const code = document.getElementById('ob-link-code').value.trim().toUpperCase();
    const statusEl = document.getElementById('ob-link-status');
    statusEl.textContent = '';
    if (!code) { statusEl.style.color = 'var(--red)'; statusEl.textContent = 'Enter the code from /link in Telegram.'; return; }
    try {
        await api('POST', '/auth/link-telegram', { code });
        invalidateCache('/auth/me');
        statusEl.style.color = 'var(--green)';
        statusEl.textContent = '✅ Linked! Now type /sync in Telegram to import your history, then refresh the dashboard.';
        setTimeout(() => obGoToStep(6), 2500);
    } catch (err) {
        statusEl.style.color = 'var(--red)';
        statusEl.textContent = err.message;
    }
}

function obFinish() {
    document.getElementById('ob-finish-msg').textContent = 'Your coaching dashboard is ready. You can link Telegram anytime from the Profile tab.';
    obGoToStep(6);
}

async function obEnterApp() {
    document.getElementById('onboarding-overlay').style.display = 'none';
    await loadDashboard();
}

/* ── Init ── */
document.addEventListener('DOMContentLoaded', async () => {
    await initAuth();
    const token = getToken();
    if (token) loadDashboard();
});
