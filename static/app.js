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
    if (method !== 'GET') return api(method, path);
    const key = `GET:${path}`;
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

const _exercisePRWeights = {};

let _restTimerInterval = null;
let _restTimerSeconds = 0;
const DEFAULT_REST_SECONDS = 90;

function startRestTimer(seconds) {
    clearRestTimer();
    _restTimerSeconds = seconds;
    const bar = document.getElementById('rest-timer-bar');
    if (!bar) return;
    bar.style.display = 'flex';
    _updateRestDisplay();
    _restTimerInterval = setInterval(() => {
        _restTimerSeconds--;
        if (_restTimerSeconds <= 0) {
            clearRestTimer();
            showToast('Rest complete — next set!');
            return;
        }
        _updateRestDisplay();
    }, 1000);
}

function _updateRestDisplay() {
    const el = document.getElementById('rest-timer-countdown');
    if (el) {
        const m = Math.floor(_restTimerSeconds / 60);
        const s = _restTimerSeconds % 60;
        el.textContent = `${m}:${s.toString().padStart(2, '0')}`;
    }
}

function clearRestTimer() {
    clearInterval(_restTimerInterval);
    _restTimerInterval = null;
    const bar = document.getElementById('rest-timer-bar');
    if (bar) bar.style.display = 'none';
}

function adjustRestTimer(deltaSecs) {
    _restTimerSeconds = Math.max(5, _restTimerSeconds + deltaSecs);
    _updateRestDisplay();
}

/* ── Unit preferences ── */
const UNIT_KEY = 'bb_unit_pref';

function getUnitPref() {
    const stored = localStorage.getItem(UNIT_KEY);
    if (stored === 'imperial' || stored === 'metric') return stored;
    const langs = Array.from(navigator.languages || [navigator.language || 'en']);
    return langs.some(l => l === 'en-US') ? 'imperial' : 'metric';
}

function isImperial() { return getUnitPref() === 'imperial'; }

function toggleUnitPref() {
    localStorage.setItem(UNIT_KEY, isImperial() ? 'metric' : 'imperial');
    applyUnitLabels();
    const tok = getToken();
    if (!tok) return;
    const t = state.activeTab;
    if (t === 'profile') loadProfile();
    else if (t === 'progress') loadProgress();
    else if (t === 'workout') loadWorkout();
    else if (t === 'dashboard') loadDashboard();
}

const DARK_KEY = 'bb_dark_mode';

function isDarkMode() { return localStorage.getItem(DARK_KEY) === 'true'; }

function toggleDarkMode() {
    const next = !isDarkMode();
    localStorage.setItem(DARK_KEY, next ? 'true' : 'false');
    applyDarkMode();
}

function applyDarkMode() {
    const dark = isDarkMode();
    document.body.classList.toggle('dark', dark);
    const btn = document.getElementById('dark-mode-btn');
    if (btn) btn.textContent = dark ? '☀️ Light' : '🌙 Dark';
    const btn2 = document.getElementById('dash-dark-btn');
    if (btn2) btn2.textContent = dark ? '☀️ Light' : '🌙 Dark';
}

const BODY_NEUTRAL_KEY = 'bb_body_neutral';

function isBodyNeutral() { return localStorage.getItem(BODY_NEUTRAL_KEY) === 'true'; }

function toggleBodyNeutral() {
    localStorage.setItem(BODY_NEUTRAL_KEY, isBodyNeutral() ? 'false' : 'true');
    applyBodyNeutral();
}

function applyBodyNeutral() {
    const neutral = isBodyNeutral();
    const btn = document.getElementById('body-neutral-btn');
    if (btn) btn.textContent = neutral ? 'Show Scores' : 'Body-Neutral Mode';
    const bfCard = document.getElementById('stat-bf')?.closest('.stat-card');
    const scoreCard = document.getElementById('stat-score')?.closest('.stat-card');
    if (bfCard) bfCard.style.display = neutral ? 'none' : '';
    if (scoreCard) scoreCard.style.display = neutral ? 'none' : '';
}

function kgToLbs(kg)    { return Math.round(+kg * 2.20462); }         // whole lbs — no decimals
function lbsToKg(lbs)   { return Math.round(+lbs / 2.20462 * 100) / 100; }
function cmToIn(cm)     { return Math.round(+cm * 0.393701 * 10) / 10; }
function inToCm(inches) { return Math.round(+inches / 0.393701 * 10) / 10; }

function fmtWeight(kg) {
    if (kg == null || kg === '' || isNaN(+kg)) return '—';
    return isImperial() ? `${kgToLbs(+kg)} lbs` : `${parseFloat(+kg).toFixed(1)} kg`;
}
function fmtLength(cm) {
    if (cm == null || cm === '' || isNaN(+cm)) return '—';
    return isImperial() ? `${cmToIn(+cm)} in` : `${parseFloat(+cm).toFixed(1)} cm`;
}
function weightUnit() { return isImperial() ? 'lbs' : 'kg'; }
function lengthUnit() { return isImperial() ? 'in' : 'cm'; }

function applyUnitLabels() {
    applyDarkMode();
    applyBodyNeutral();
    const imp = isImperial();
    document.querySelectorAll('.unit-lbl-weight').forEach(el => { el.textContent = imp ? '(lbs)' : '(kg)'; });
    document.querySelectorAll('.unit-lbl-height').forEach(el => { el.textContent = imp ? '(in)' : '(cm)'; });
    document.querySelectorAll('.unit-lbl-length').forEach(el => { el.textContent = imp ? '(in)' : '(cm)'; });
    const hf = document.querySelector('[name="height_cm"]');
    if (hf) { hf.min = imp ? 40 : 100; hf.max = imp ? 100 : 250; hf.placeholder = imp ? '70' : '178'; }
    const wf = document.querySelector('[name="weight_kg"]');
    if (wf) { wf.min = imp ? 66 : 30; wf.max = imp ? 660 : 300; wf.placeholder = imp ? '187' : '85'; }
    const mw = document.getElementById('m-weight');
    if (mw) mw.placeholder = imp ? '185' : '85.0';
    const mwa = document.getElementById('m-waist');
    if (mwa) mwa.placeholder = imp ? '32' : '82';
    const mc = document.getElementById('m-chest');
    if (mc) mc.placeholder = imp ? '39' : '100';
    const ma = document.getElementById('m-arm');
    if (ma) ma.placeholder = imp ? '15' : '38';
    const btn = document.getElementById('unit-toggle-btn');
    if (btn) btn.textContent = imp ? '→ Metric' : '→ Imperial';
    const pul = document.getElementById('profile-unit-label');
    if (pul) pul.textContent = imp ? 'Imperial' : 'Metric';

    // Rebuild height select (value always stored in cm, display in user's unit)
    const hSel = document.getElementById('profile-height');
    if (hSel) {
        const prev = hSel.value;
        hSel.innerHTML = '<option value="">Select height…</option>';
        for (let cm = 140; cm <= 220; cm += 2) {
            const opt = document.createElement('option');
            opt.value = cm;
            if (imp) {
                const totalIn = cm * 0.393701;
                const ft = Math.floor(totalIn / 12);
                const inches = Math.round(totalIn % 12);
                opt.textContent = `${ft}'${inches}"`;
            } else {
                opt.textContent = `${cm} cm`;
            }
            hSel.appendChild(opt);
        }
        if (prev) hSel.value = prev;
    }

    // Rebuild weight select (value always stored in kg, display in user's unit)
    const wSel = document.getElementById('profile-weight');
    if (wSel) {
        const prev = wSel.value;
        wSel.innerHTML = '<option value="">Select weight…</option>';
        for (let kg = 40; kg <= 200; kg += 2.5) {
            const opt = document.createElement('option');
            opt.value = kg;
            opt.textContent = imp ? `${kgToLbs(kg)} lbs` : `${kg} kg`;
            wSel.appendChild(opt);
        }
        if (prev) wSel.value = prev;
    }

    // Age select
    const aSel = document.getElementById('profile-age');
    if (aSel && aSel.options.length <= 1) {
        for (let age = 16; age <= 80; age++) {
            const opt = document.createElement('option');
            opt.value = age;
            opt.textContent = `${age} years old`;
            aSel.appendChild(opt);
        }
    }
}

/* ── Profile chip selectors ── */
const DIET_OPTIONS = [
    'Vegetarian', 'Vegan', 'Gluten-free', 'Dairy-free', 'Nut allergy',
    'Halal', 'Kosher', 'No shellfish', 'No pork', 'Low-carb / Keto',
];
const INJURY_OPTIONS = [
    'Lower back pain', 'Knee injury', 'Shoulder impingement', 'Hip pain',
    'Wrist / elbow pain', 'Ankle injury', 'Neck pain', 'No injuries',
];

const ACUTE_INJURY_KEYWORDS = ['surgery', 'surgical', 'acl', 'torn', 'rupture', 'fracture', 'broken', 'herniated', 'disc', 'spinal', 'acute', 'recent injury', 'post-op', 'rehabilitation'];

function _buildProfileChips(containerId, options, activeSet) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    options.forEach(opt => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = opt;
        const isSelected = activeSet.has(opt.toLowerCase());
        btn.className = 'profile-chip' + (isSelected ? ' selected' : '');
        btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        btn.onclick = () => {
            btn.classList.toggle('selected');
            btn.setAttribute('aria-pressed', btn.classList.contains('selected') ? 'true' : 'false');
            _checkInjuryWarning();
        };
        container.appendChild(btn);
    });
}

function _checkInjuryWarning() {
    const chips = _getChipValues('injury-chips');
    const other = (document.getElementById('injury-other')?.value || '').toLowerCase();
    const allText = [...chips.map(c => c.toLowerCase()), other].join(' ');
    const hasAcute = ACUTE_INJURY_KEYWORDS.some(k => allText.includes(k));
    const hasAnyInjury = chips.length > 0 && !chips.includes('No injuries');
    let warn = document.getElementById('injury-safety-warn');
    if ((hasAcute || hasAnyInjury) && !chips.includes('No injuries')) {
        if (!warn) {
            warn = document.createElement('p');
            warn.id = 'injury-safety-warn';
            warn.className = 'banner-injury';
            warn.textContent = '⚠️ If you have an acute injury or recent surgery, consult a physician or physical therapist before starting any AI-generated training program.';
            document.getElementById('injury-chips')?.after(warn);
        }
    } else if (warn) {
        warn.remove();
    }
}

function _getChipValues(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    return Array.from(container.querySelectorAll('.profile-chip.selected')).map(b => b.textContent);
}

/* ── Browser notifications ── */
function _requestNotificationPerm() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

function _sendBrowserNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body, icon: '/static/favicon.ico' });
    }
}

/* ── Background task indicator ── */
let _bgTaskCount = 0;

function showBgTask(msg) {
    _bgTaskCount++;
    const bar = document.getElementById('bg-task-bar');
    const msgEl = document.getElementById('bg-task-msg');
    if (bar && msgEl) { msgEl.textContent = msg; bar.style.display = 'flex'; }
}

function hideBgTask(doneMsg) {
    _bgTaskCount = Math.max(0, _bgTaskCount - 1);
    if (_bgTaskCount > 0) return;
    const bar = document.getElementById('bg-task-bar');
    const msgEl = document.getElementById('bg-task-msg');
    const spinner = document.getElementById('bg-task-spinner');
    if (!bar || !msgEl) return;
    if (doneMsg) {
        _sendBrowserNotification('BB Coach AI', doneMsg);
        if (spinner) spinner.style.display = 'none';
        msgEl.textContent = doneMsg;
        setTimeout(() => {
            bar.style.display = 'none';
            if (spinner) spinner.style.display = '';
        }, 3000);
    } else {
        bar.style.display = 'none';
    }
}

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

function showOnboardingOverlay() {
    document.getElementById('onboarding-overlay').style.display = 'flex';
    obShowRegister();
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
    document.querySelectorAll('#bottom-tab-bar .bottom-tab').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tab);
    });
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
    const [health, plan, summary, checkins, mealsToday] = await Promise.all([
        cachedApi('GET', '/health').catch(() => ({ api_key_configured: false })),
        cachedApi('GET', '/plan/current').catch(() => null),
        cachedApi('GET', '/dashboard/summary').catch(() => null),
        cachedApi('GET', '/checkins?limit=7').catch(() => []),
        cachedApi('GET', '/meals/today').catch(() => ({ meals: [], totals: {} })),
    ]);

    document.getElementById('setup-banner').style.display =
        !health.api_key_configured ? 'block' : 'none';

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
    renderGettingStarted(plan, summary);
    _renderDashMacroSummary(mealsToday, plan);
    loadMemory();
}

function _renderDashMacroSummary(mealsToday, plan) {
    const container = document.getElementById('dash-macro-summary');
    if (!container) return;
    const t = mealsToday?.totals || {};
    const calTarget = plan?.diet_plan?.daily_calories || plan?.diet_macros?.calories;
    const protTarget = plan?.diet_plan?.macros?.protein_g || plan?.diet_macros?.protein_g;

    const calActual = t.calories || 0;
    const protActual = t.protein_g || 0;

    const calText = document.getElementById('dash-cal-text');
    const calFill = document.getElementById('dash-cal-fill');
    const protText = document.getElementById('dash-prot-text');
    const protFill = document.getElementById('dash-prot-fill');

    if (calText) calText.textContent = calTarget ? `${Math.round(calActual)} / ${calTarget} kcal` : `${Math.round(calActual)} kcal`;
    if (calFill) {
        const pct = calTarget ? Math.min(100, Math.round(calActual / +calTarget * 100)) : 0;
        calFill.style.width = '0%';
        setTimeout(() => { calFill.style.width = pct + '%'; }, 50);
    }

    if (protText) protText.textContent = protTarget ? `${Math.round(protActual)} / ${protTarget}g` : `${Math.round(protActual)}g`;
    if (protFill) {
        const pct = protTarget ? Math.min(100, Math.round(protActual / +protTarget * 100)) : 0;
        protFill.style.width = '0%';
        // Change 3: protein warning — red if below 80% of target
        const isLow = protTarget && protActual < +protTarget * 0.8;
        protFill.style.background = isLow ? '#ef4444' : 'var(--green)';
        setTimeout(() => { protFill.style.width = pct + '%'; }, 50);
    }

    container.style.display = 'block';
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
    // Check-in nudge only shows if workout nudge hasn't claimed the slot
    if (summary.last_checkin_date && summary.last_checkin_date < today) {
        const daysSince = Math.floor((Date.now() - new Date(summary.last_checkin_date)) / 86400000);
        if (daysSince >= 2 && nudge.style.display !== 'block') {
            nudge.textContent = `📊 Check in today to maintain your recovery data streak!`;
            nudge.style.background = 'rgba(59,130,246,0.1)';
            nudge.style.color = 'var(--blue)';
            nudge.style.display = 'block';
            hasContent = true;
        }
    }

    // Competition countdown overrides lapse nudges (higher priority)
    if (summary.days_to_show != null) {
        const d = summary.days_to_show;
        const compText = d <= 0
            ? '🏆 Show day! Good luck today!'
            : d <= 7 ? `🚨 ${d} day${d !== 1 ? 's' : ''} to show — peak week protocols active!`
            : d <= 30 ? `⚡ ${d} days to show — stay sharp!`
            : `📅 ${d} days to show — comp prep in progress`;
        nudge.textContent = compText;
        nudge.style.background = d <= 7 ? 'rgba(239,68,68,0.12)' : 'rgba(240,165,0,0.1)';
        nudge.style.color = d <= 7 ? '#ef4444' : 'var(--gold)';
        nudge.style.display = 'block';
        hasContent = true;
    }

    card.style.display = hasContent ? 'block' : 'none';
}

function renderGettingStarted(plan, summary) {
    const card = document.getElementById('getting-started-card');
    if (!card) return;
    const hasAnalysis = !!plan?.latest_analysis;
    const hasPlan = !!(plan?.workout_plan || plan?.diet_plan);
    const hasCheckin = (summary?.streaks?.checkin ?? 0) > 0;
    if (hasAnalysis && hasPlan) { card.style.display = 'none'; return; }
    card.style.display = 'block';
    const step1 = document.getElementById('gs-num-1');
    const step2 = document.getElementById('gs-num-2');
    const step3 = document.getElementById('gs-num-3');
    if ((hasPlan || hasCheckin) && step1) { step1.textContent = '✓'; step1.className = 'gs-step-num done'; }
    if (hasAnalysis && step2) { step2.textContent = '✓'; step2.className = 'gs-step-num done'; }
    if (hasPlan && step3) { step3.textContent = '✓'; step3.className = 'gs-step-num done'; }
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

/* Change 5: Generic measurement chart SVG */
function _measurementChartSvg(entries, valueKey, w = 320, h = 80, color = '#f59e0b') {
    const valid = entries.filter(e => e[valueKey] != null).slice(0, 30).reverse();
    if (valid.length < 2) return '';
    const pad = 8;
    const vals = valid.map(e => +e[valueKey]);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;
    const xs = vals.map((_, i) => pad + (i / (vals.length - 1)) * (w - 2 * pad));
    const ys = vals.map(v => h - pad - ((v - min) / range) * (h - 2 * pad - 14));
    const pathD = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
    const lastIdx = vals.length - 1;
    const dots = xs.map((x, i) =>
        `<circle cx="${x.toFixed(1)}" cy="${ys[i].toFixed(1)}" r="3" fill="${color}" opacity="0.7"/>`
    ).join('');
    const labels = [0, lastIdx].map(i =>
        `<text x="${xs[i].toFixed(1)}" y="${(ys[i] - 6).toFixed(1)}" text-anchor="${i === 0 ? 'start' : 'end'}" font-size="10" fill="var(--text-muted)">${isImperial() ? cmToIn(vals[i]) + ' in' : vals[i].toFixed(1) + ' cm'}</text>`
    ).join('');
    const dateLabels = [0, lastIdx].map(i =>
        `<text x="${xs[i].toFixed(1)}" y="${(h - 1).toFixed(1)}" text-anchor="${i === 0 ? 'start' : 'end'}" font-size="9" fill="var(--text-muted)" opacity="0.6">${esc(valid[i].date?.slice(5) || '')}</text>`
    ).join('');
    return `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" style="overflow:visible">
        <path d="${pathD}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${dots}${labels}${dateLabels}
    </svg>`;
}

function _weightChartSvg(entries, w = 320, h = 80) {
    const valid = entries.filter(e => e.body_weight_kg != null).slice(0, 30).reverse();
    if (valid.length < 2) return '';
    const pad = 8;
    const vals = valid.map(e => +e.body_weight_kg);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = max - min || 1;
    const xs = vals.map((_, i) => pad + (i / (vals.length - 1)) * (w - 2 * pad));
    const ys = vals.map(v => h - pad - ((v - min) / range) * (h - 2 * pad - 14));
    const pathD = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
    const lastIdx = vals.length - 1;
    const trend = vals[lastIdx] < vals[0] ? '#22c55e' : vals[lastIdx] > vals[0] ? '#ef4444' : '#999';
    const dots = xs.map((x, i) =>
        `<circle cx="${x.toFixed(1)}" cy="${ys[i].toFixed(1)}" r="3" fill="${trend}" opacity="0.7"/>`
    ).join('');
    const labels = [0, lastIdx].map(i =>
        `<text x="${xs[i].toFixed(1)}" y="${(ys[i] - 6).toFixed(1)}" text-anchor="${i === 0 ? 'start' : 'end'}" font-size="10" fill="var(--text-muted)">${isImperial() ? kgToLbs(vals[i]) + ' lbs' : vals[i].toFixed(1) + ' kg'}</text>`
    ).join('');
    const dateLabels = [0, lastIdx].map(i =>
        `<text x="${xs[i].toFixed(1)}" y="${(h - 1).toFixed(1)}" text-anchor="${i === 0 ? 'start' : 'end'}" font-size="9" fill="var(--text-muted)" opacity="0.6">${esc(valid[i].date?.slice(5) || '')}</text>`
    ).join('');
    return `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" style="overflow:visible">
        <path d="${pathD}" fill="none" stroke="${trend}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${dots}${labels}${dateLabels}
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
    const sleepHrsEl = document.getElementById('ci-sleep-hrs');
    const sleepHrsValEl = document.getElementById('ci-sleep-hrs-val');
    if (sleepHrsEl && sleepHrsValEl) {
        const hrs = todayCheckin?.sleep_duration_hrs ?? 7.5;
        sleepHrsEl.value = hrs;
        sleepHrsValEl.textContent = hrs;
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
        sleep_duration_hrs: parseFloat(document.getElementById('ci-sleep-hrs')?.value) || null,
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
    if (btn) { btn.disabled = true; btn.textContent = 'Queued…'; }
    showBgTask('AI analyzing your physique… (20-40 sec)');
    api('POST', '/analyze', formData, true).then(async result => {
        invalidateCache('/plan/current', '/progress', '/analyses', '/dashboard/summary');
        clearUpload();
        renderAnalysisResult(result.analysis, result.created_at);
        document.getElementById('analysis-result').style.display = 'block';
        await loadAnalyses();
        // Silently refresh dashboard stats if user is there
        if (state.activeTab === 'dashboard') await loadDashboard();
        hideBgTask('✅ Analysis complete!');
        showToast('Analysis complete!');
    }).catch(err => {
        hideBgTask();
        showToast(`Analysis failed: ${err.message}`, 'error');
    }).finally(() => {
        if (btn) { btn.disabled = false; btn.textContent = 'Analyze Photo'; }
    });
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
            <li class="history-item" id="analysis-card-${a.id}">
                <img class="history-thumb" src="${esc(a.photo_url)}" alt="Photo" onclick="showHistoryAnalysis(${a.id})" style="cursor:pointer" />
                <div class="history-info" onclick="showHistoryAnalysis(${a.id})" style="cursor:pointer;flex:1">
                    <div class="history-bf">${esc(a.body_fat_estimate) || '—'}</div>
                    <div style="font-size:13px">Score: ${esc(a.overall_physique_score) || '—'}/10</div>
                    <div class="history-date">${esc(formatDate(a.created_at))}</div>
                </div>
                ${a.coach_message ? `<div style="font-size:12px;color:var(--text-muted);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:2">${esc(a.coach_message)}</div>` : ''}
                <button onclick="printAnalysis(${a.id})" style="background:none;border:none;cursor:pointer;font-size:16px;color:var(--text-muted);padding:4px;flex-shrink:0" aria-label="Print analysis">&#128424;</button>
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

/* ── Regen plan modal ── */
function openRegenModal() {
    document.getElementById('regen-plan-modal').style.display = 'flex';
}
function closeRegenModal() {
    document.getElementById('regen-plan-modal').style.display = 'none';
}
function confirmGeneratePlan() {
    closeRegenModal();
    generatePlan();
}

/* ── Plans ── */
async function generatePlan() {
    showBgTask('Building your personalized plan… (30-60 sec)');
    api('POST', '/plan/generate').then(plan => {
        state.currentPlan = {
            workout_plan: plan.workout_plan,
            diet_plan: plan.diet_plan,
            supplement_plan: plan.supplement_plan,
            coaching_notes: plan.coaching_notes,
        };
        invalidateCache('/plan/current');
        hideBgTask('✅ Plan ready!');
        showToast('Plan generated!');
        showTab('plans');
        renderPlan(plan);
    }).catch(err => {
        hideBgTask();
        const msg = err.message || '';
        if (msg.toLowerCase().includes('analysis') || msg.toLowerCase().includes('photo')) {
            showToast('Upload a physique photo first for the best plan. Go to the Analysis tab.', 'error');
            setTimeout(() => showTab('analysis'), 2000);
        } else {
            showToast(msg || 'Failed to generate plan.', 'error');
        }
    });
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
        coaching_notes: data.coaching_notes,
    });
}

function printPlan() {
    const sections = ['plan-workout', 'plan-diet', 'plan-supplements', 'plan-coaching'];
    const content = sections.map(id => {
        const el = document.getElementById(id);
        return el ? el.innerHTML : '';
    }).join('<hr style="margin:32px 0;border:none;border-top:1px solid #ccc">');
    const win = window.open('', '_blank');
    if (!win) { showToast('Allow pop-ups to print the plan.', 'error'); return; }
    win.document.write(`<!DOCTYPE html><html><head><title>BB Coach AI — My Plan</title>
    <style>
        :root { --gold: #e8b40a; --green: #22c55e; --text-muted: #666; --surface-2: #f5f5f5; --border: #ddd; --blue: #3b82f6; --red: #ef4444; }
        body { font-family: -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 32px; color: #111; font-size: 14px; line-height: 1.6; }
        h2, h3 { margin-top: 24px; }
        table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
        th, td { padding: 6px 10px; border: 1px solid #ddd; text-align: left; }
        th { background: #f5f5f5; }
        .macro-row { display: flex; gap: 20px; flex-wrap: wrap; margin: 16px 0; }
        .macro-card { text-align: center; padding: 12px 20px; background: #f9f9f9; border-radius: 8px; }
        .macro-value { font-size: 24px; font-weight: 800; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        @media print { body { padding: 0; } }
    </style></head><body>
    <h1>My Coaching Plan</h1>
    <p style="color:#666">Generated by BodyBuilding Coach AI · ${new Date().toLocaleDateString()}</p>
    <hr>
    ${content}
    </body></html>`);
    win.document.close();
    setTimeout(() => win.print(), 500);
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

        ${(diet.daily_calories && +diet.daily_calories < 1600) ? `
        <div class="banner-calorie-warn">
            ⚠️ <strong>Low calorie target (${esc(String(diet.daily_calories))} kcal):</strong> Targets below 1,600 kcal may be insufficient for most people. Please consult a registered dietitian before following this plan.
        </div>` : ''}
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
        <div class="banner-injury" style="margin-top:16px">⚠️ <strong>Medical disclaimer:</strong> These supplement recommendations are AI-generated and not a substitute for personalised medical advice. Dosing needs vary by individual. Consult a physician or registered dietitian before starting any supplement protocol, especially if you are on medication or have a health condition.</div>
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
        // Change 9: use relative time and add research-topic-card class for filtering
        return `
            <div class="research-topic research-topic-card">
                <div class="research-topic-header" onclick="togglePapers(${topicId})">
                    <div>
                        <div class="research-topic-title">${esc(topic.topic)}</div>
                        <div style="font-size:12px;color:var(--text-muted)">${esc(topic.papers?.length || 0)} papers • Updated ${esc(_relTime(topic.last_updated))}</div>
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
        cachedApi('GET', '/measurements?limit=30').catch(() => []),
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
    const chartEl = document.getElementById('weight-chart');
    if (chartEl) {
        if (data.length >= 2) {
            chartEl.innerHTML = _weightChartSvg(data);
            chartEl.style.display = 'block';
        } else {
            chartEl.style.display = 'none';
        }
    }

    // Change 5: Waist trend chart
    const waistEl = document.getElementById('waist-chart');
    const waistSvgEl = document.getElementById('waist-chart-svg');
    const waistData = data.filter(e => e.waist_cm != null);
    if (waistEl && waistSvgEl) {
        if (waistData.length >= 2) {
            waistSvgEl.innerHTML = _measurementChartSvg(waistData, 'waist_cm', 320, 80, '#f59e0b');
            waistEl.style.display = 'block';
        } else {
            waistEl.style.display = 'none';
        }
    }

    // Change 5: Avg arm trend chart (average of left_arm_cm and right_arm_cm)
    const armEl = document.getElementById('arm-chart');
    const armSvgEl = document.getElementById('arm-chart-svg');
    if (armEl && armSvgEl) {
        const armData = data.map(e => {
            const l = e.left_arm_cm != null ? +e.left_arm_cm : null;
            const r = e.right_arm_cm != null ? +e.right_arm_cm : null;
            if (l != null && r != null) return { ...e, avg_arm_cm: (l + r) / 2 };
            if (l != null) return { ...e, avg_arm_cm: l };
            if (r != null) return { ...e, avg_arm_cm: r };
            return { ...e, avg_arm_cm: null };
        }).filter(e => e.avg_arm_cm != null);
        if (armData.length >= 2) {
            armSvgEl.innerHTML = _measurementChartSvg(armData, 'avg_arm_cm', 320, 80, '#8b5cf6');
            armEl.style.display = 'block';
        } else {
            armEl.style.display = 'none';
        }
    }
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
                        <td style="padding:8px;text-align:right;color:var(--gold)">${fmtWeight(m.body_weight_kg)}</td>
                        <td style="padding:8px;text-align:right">${fmtLength(m.waist_cm)}</td>
                        <td style="padding:8px;text-align:right">${fmtLength(m.chest_cm)}</td>
                        <td style="padding:8px;text-align:right">${fmtLength(m.left_arm_cm)}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

async function logMeasurement(event) {
    event.preventDefault();
    let weight = parseFloat(document.getElementById('m-weight').value) || null;
    let waist  = parseFloat(document.getElementById('m-waist').value) || null;
    let chest  = parseFloat(document.getElementById('m-chest').value) || null;
    let arm    = parseFloat(document.getElementById('m-arm').value) || null;

    if (isImperial()) {
        if (weight) weight = lbsToKg(weight);
        if (waist)  waist  = inToCm(waist);
        if (chest)  chest  = inToCm(chest);
        if (arm)    arm    = inToCm(arm);
    }

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
        invalidateCache('/measurements?limit=30', '/dashboard/summary', '/progress', '/progress/plateaus');
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
            const disp = isImperial() ? kgToLbs(v) : v;
            return `<span style="font-size:${isLast ? '15px' : '13px'};font-weight:${isLast ? '700' : '400'};color:${isLast && ex.stalled ? '#ef4444' : 'var(--text-muted)'}">${disp}</span>`;
        }).join(' → ');

        return `
            <div style="padding:12px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
                <div>
                    <div style="font-weight:600;font-size:14px">${esc(ex.exercise)}</div>
                    <div style="font-size:12px;color:var(--text-muted);margin-top:3px">${trendDots} ${weightUnit()} est. 1RM</div>
                </div>
                <div style="display:flex;align-items:center;gap:12px">
                    <div style="text-align:right">
                        <div style="font-size:18px;font-weight:700;color:var(--gold)">${fmtWeight(ex.current_1rm)}</div>
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
    const [profile, me, goals, dashSummary] = await Promise.all([
        cachedApi('GET', '/profile').catch(() => ({})),
        api('GET', '/auth/me').catch(() => null),
        api('GET', '/goals').catch(() => []),
        cachedApi('GET', '/dashboard/summary').catch(() => null),
    ]);

    applyUnitLabels(); // ensures dropdowns are populated before we set values
    if (profile && Object.keys(profile).length) {
        const form = document.getElementById('profile-form');
        // Simple fields
        ['age', 'gender', 'goal', 'training_experience', 'training_days_per_week', 'show_date'].forEach(field => {
            const el = form.querySelector(`[name="${field}"]`);
            if (el && profile[field] != null) el.value = profile[field];
        });
        // Height: select value is always cm, find closest option
        const hSel = document.getElementById('profile-height');
        if (hSel && profile.height_cm) {
            const cm = parseFloat(profile.height_cm);
            let closest = null, minDiff = Infinity;
            Array.from(hSel.options).forEach(o => {
                if (!o.value) return;
                const diff = Math.abs(parseFloat(o.value) - cm);
                if (diff < minDiff) { minDiff = diff; closest = o.value; }
            });
            if (closest) hSel.value = closest;
        }
        // Weight: select value is always kg, find closest option
        const wSel = document.getElementById('profile-weight');
        if (wSel && profile.weight_kg) {
            const kg = parseFloat(profile.weight_kg);
            let closest = null, minDiff = Infinity;
            Array.from(wSel.options).forEach(o => {
                if (!o.value) return;
                const diff = Math.abs(parseFloat(o.value) - kg);
                if (diff < minDiff) { minDiff = diff; closest = o.value; }
            });
            if (closest) wSel.value = closest;
        }
        // Dietary restrictions chips
        const dietStr = (profile.dietary_restrictions || '').toLowerCase();
        const dietActive = new Set(DIET_OPTIONS.filter(o => dietStr.includes(o.toLowerCase())));
        _buildProfileChips('diet-chips', DIET_OPTIONS, dietActive);
        const otherDiet = DIET_OPTIONS.reduce((s, o) => s.replace(o.toLowerCase(), '').replace(',', '').trim(), dietStr);
        const dietOther = document.getElementById('diet-other');
        if (dietOther && otherDiet) dietOther.value = otherDiet;
        // Injury chips
        const injStr = (profile.injuries || '').toLowerCase();
        const injActive = new Set(INJURY_OPTIONS.filter(o => injStr.includes(o.toLowerCase())));
        _buildProfileChips('injury-chips', INJURY_OPTIONS, injActive);
        const otherInj = INJURY_OPTIONS.reduce((s, o) => s.replace(o.toLowerCase(), '').replace(',', '').trim(), injStr);
        const injOther = document.getElementById('injury-other');
        if (injOther && otherInj) injOther.value = otherInj;
        // Wire free-text to trigger warning
        injOther?.addEventListener('input', _checkInjuryWarning);
        _checkInjuryWarning();
    } else {
        // First load — build empty chips
        _buildProfileChips('diet-chips', DIET_OPTIONS, new Set());
        _buildProfileChips('injury-chips', INJURY_OPTIONS, new Set());
        applyUnitLabels();
    }

    // Change 6: merge goal_progress fields from dashboard summary into goals
    const gp = dashSummary?.goal_progress;
    const mergedGoals = goals.map(g => {
        if (g.is_active && gp) {
            return { ...g, ...gp };
        }
        return g;
    });
    renderGoals(mergedGoals);
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
    // Height and weight selects always store metric values directly
    const height_cm = parseFloat(form.height_cm.value) || null;
    const weight_kg = parseFloat(form.weight_kg.value) || null;
    // Collect dietary chips + other text
    const dietChips = _getChipValues('diet-chips');
    const dietOther = (document.getElementById('diet-other')?.value || '').trim();
    const dietAll = [...dietChips, ...(dietOther ? [dietOther] : [])].join(', ') || null;
    // Collect injury chips + other text
    const injChips = _getChipValues('injury-chips');
    const injOther = (document.getElementById('injury-other')?.value || '').trim();
    const injAll = [...injChips, ...(injOther ? [injOther] : [])].join(', ') || null;
    const data = {
        age: parseInt(form.age.value) || null,
        gender: form.gender.value || null,
        height_cm,
        weight_kg,
        goal: form.goal.value || null,
        training_experience: form.training_experience.value || null,
        training_days_per_week: parseInt(form.training_days_per_week.value) || null,
        dietary_restrictions: dietAll,
        injuries: injAll,
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
        if (g.target_weight_kg) parts.push(`→ ${fmtWeight(g.target_weight_kg)}`);
        if (g.target_bf_pct) parts.push(`→ ${g.target_bf_pct}% BF`);
        if (g.target_date) parts.push(`by ${esc(g.target_date)}`);
        const badge = g.is_active
            ? `<span style="background:rgba(34,197,94,0.12);color:#22c55e;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">ACTIVE</span>`
            : `<span style="background:rgba(100,100,100,0.12);color:var(--text-muted);padding:2px 8px;border-radius:4px;font-size:11px">inactive</span>`;

        // Change 6: Goal progress visualization
        let progressHtml = '';
        if (g.is_active && g.current_weight_kg != null && g.target_weight_kg != null && g.start_weight_kg != null) {
            const start = +g.start_weight_kg;
            const current = +g.current_weight_kg;
            const target = +g.target_weight_kg;
            const totalChange = target - start;
            const achieved = current - start;
            const pct = totalChange !== 0 ? Math.min(100, Math.max(0, Math.round(achieved / totalChange * 100))) : 0;
            const daysRemaining = g.days_remaining != null ? g.days_remaining : null;
            progressHtml = `
                <div style="margin-top:8px">
                    <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-bottom:3px">
                        <span>${fmtWeight(start)} start</span>
                        <span style="font-weight:600;color:var(--green)">${pct}% complete</span>
                        <span>${fmtWeight(target)} target</span>
                    </div>
                    <div style="height:6px;background:var(--surface-2);border-radius:3px;overflow:hidden">
                        <div style="height:100%;width:${pct}%;background:var(--green);border-radius:3px;transition:width 0.4s ease"></div>
                    </div>
                    <div style="margin-top:4px;font-size:12px;color:var(--text-muted)">
                        Current: <strong>${fmtWeight(current)}</strong>
                        ${daysRemaining != null ? `<span style="margin-left:8px;background:rgba(59,130,246,0.1);color:#3b82f6;padding:1px 6px;border-radius:4px;font-size:11px">${esc(String(daysRemaining))} days left</span>` : ''}
                    </div>
                </div>`;
        }

        return `
            <div style="padding:10px 0;border-bottom:1px solid var(--border)">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
                    <div>
                        <div style="font-weight:600;font-size:14px;text-transform:uppercase;letter-spacing:0.5px">${esc(g.goal_type)}</div>
                        ${parts.length ? `<div style="font-size:13px;color:var(--text-muted);margin-top:2px">${parts.join(' · ')}</div>` : ''}
                    </div>
                    ${badge}
                </div>
                ${progressHtml}
            </div>`;
    }).join('');
}

async function saveGoal(event) {
    event.preventDefault();
    const form = event.target;
    try {
        let target_weight_kg = parseFloat(form.target_weight_kg.value) || null;
        if (isImperial() && target_weight_kg) target_weight_kg = lbsToKg(target_weight_kg);
        await api('POST', '/goals', {
            goal_type: form.goal_type.value,
            target_weight_kg,
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
    const [today, recent, plan] = await Promise.all([
        api('GET', '/meals/today').catch(() => ({ meals: [], totals: {} })),
        api('GET', '/meals?limit=30').catch(() => []),
        cachedApi('GET', '/plan/current').catch(() => null),
    ]);

    const t = today.totals || {};
    document.getElementById('nt-calories').textContent = t.calories ? `${t.calories} kcal` : '—';
    document.getElementById('nt-protein').textContent = t.protein_g ? `${t.protein_g}g` : '—';
    document.getElementById('nt-carbs').textContent = t.carbs_g ? `${t.carbs_g}g` : '—';
    document.getElementById('nt-fat').textContent = t.fat_g ? `${t.fat_g}g` : '—';

    const calTarget = plan?.diet_plan?.daily_calories || plan?.diet_macros?.calories;
    const protTarget = plan?.diet_plan?.macros?.protein_g || plan?.diet_macros?.protein_g;
    const carbTarget = plan?.diet_plan?.macros?.carbs_g;
    const fatTarget = plan?.diet_plan?.macros?.fat_g;
    // Change 12: animate bars — set 0 first, then apply after 50ms
    function _setMacroBar(fillId, targetId, actual, target, unit, overrideColor) {
        const fill = document.getElementById(fillId);
        const lbl = document.getElementById(targetId);
        if (!fill || !lbl) return;
        if (target) {
            const pct = actual ? Math.min(100, Math.round(+actual / +target * 100)) : 0;
            fill.style.width = '0%';
            if (overrideColor) fill.style.background = overrideColor;
            lbl.textContent = actual ? `${actual} / ${target} ${unit}` : `Target: ${target} ${unit}`;
            setTimeout(() => { fill.style.width = pct + '%'; }, 50);
        } else {
            fill.style.width = '0%';
            lbl.textContent = '';
        }
    }
    _setMacroBar('nt-cal-fill', 'nt-cal-target', t.calories, calTarget, 'kcal');
    // Change 3: protein warning — red if below 80% of target
    const protIsLow = protTarget && t.protein_g != null && +t.protein_g < +protTarget * 0.8;
    _setMacroBar('nt-prot-fill', 'nt-prot-target', t.protein_g, protTarget, 'g', protIsLow ? '#ef4444' : undefined);
    _setMacroBar('nt-carb-fill', 'nt-carb-target', t.carbs_g, carbTarget, 'g');
    _setMacroBar('nt-fat-fill', 'nt-fat-target', t.fat_g, fatTarget, 'g');

    // Change 2: draw macro pie chart
    _drawMacroPie(t.protein_g || 0, t.carbs_g || 0, t.fat_g || 0);

    const todayList = document.getElementById('meals-today-list');
    if (today.meals.length) {
        todayList.innerHTML = today.meals.map(m => `
            <div style="padding:10px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
                <div style="flex:1">
                    <div style="font-weight:500;font-size:14px">${esc(m.description || 'Meal')}</div>
                    <div style="font-size:11px;color:var(--text-muted);margin-top:2px">
                        ${m.macro_source === 'estimated' ? '~ AI-estimated' : 'manual'}
                        ${_mealTime(m.logged_at) ? `<span style="margin-left:6px">${esc(_mealTime(m.logged_at))}</span>` : ''}
                    </div>
                </div>
                <div style="text-align:right;flex-shrink:0;font-size:13px">
                    ${m.calories ? `<span style="color:var(--text-muted)">${m.calories} kcal</span>` : ''}
                    ${m.protein_g ? `<span style="color:var(--gold);margin-left:8px">${m.protein_g}g P</span>` : ''}
                </div>
                <button onclick="deleteMeal(${m.id})" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:16px;flex-shrink:0;padding:4px" aria-label="Delete meal">🗑</button>
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

async function deleteMeal(mealId) {
    if (!confirm('Delete this meal?')) return;
    try {
        await api('DELETE', `/meals/${mealId}`);
        invalidateCache('/meals/today', '/meals?limit=30', '/dashboard/summary');
        await loadNutrition();
        showToast('Meal deleted.');
    } catch (e) {
        showToast(e.message || 'Failed to delete meal.', 'error');
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
    const lastKg = _exercisePRWeights[name];
    if (lastKg != null) {
        if (isImperial()) {
            document.getElementById('weight-input').value = Math.round(kgToLbs(lastKg) / 5) * 5;
        } else {
            document.getElementById('weight-input').value = Math.round(lastKg / 2.5) * 2.5;
        }
    }
    document.getElementById('log-set-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    document.querySelectorAll('.chip').forEach(c => {
        c.classList.toggle('active', c.textContent === name);
    });
    updatePRHint(name);
    updateWarmupSuggestions();
    const rpeInput = document.getElementById('rpe-input');
    const rirInput = document.getElementById('rir-input');
    if (rpeInput) rpeInput.value = '';
    if (rirInput) rirInput.value = '';
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

function markExerciseDone(name) {
    document.querySelectorAll('.chip').forEach(c => {
        if (c.textContent === name) {
            c.classList.add('done');
            c.setAttribute('aria-label', name + ' — done');
        }
    });
}

function adjustWeight(direction) {
    const step = isImperial() ? 5 : 2.5;
    const input = document.getElementById('weight-input');
    input.value = Math.max(0, Math.round((parseFloat(input.value || 0) + direction * step) * 10) / 10);
    updateWarmupSuggestions();
}

function adjustReps(delta) {
    const input = document.getElementById('reps-input');
    input.value = Math.max(1, parseInt(input.value || 1) + delta);
}

function openPlateCalc() {
    const modal = document.getElementById('plate-calc-modal');
    if (modal) modal.style.display = 'flex';
    const w = parseFloat(document.getElementById('weight-input')?.value) || 0;
    document.getElementById('plate-calc-input').value = w || '';
    calcPlates();
}

function closePlateCalc() {
    const modal = document.getElementById('plate-calc-modal');
    if (modal) modal.style.display = 'none';
}

function calcPlates() {
    const imp = isImperial();
    const barWeight = imp ? 45 : 20;
    const totalInput = parseFloat(document.getElementById('plate-calc-input').value) || 0;
    const totalKg = imp ? lbsToKg(totalInput) : totalInput;
    const perSide = (totalKg - (imp ? lbsToKg(barWeight) : barWeight)) / 2;

    const plateSizes = imp
        ? [45, 35, 25, 10, 5, 2.5].map(p => lbsToKg(p))
        : [25, 20, 15, 10, 5, 2.5, 1.25];
    const plateLabels = imp
        ? ['45', '35', '25', '10', '5', '2.5']
        : ['25', '20', '15', '10', '5', '2.5', '1.25'];

    const result = document.getElementById('plate-calc-result');
    if (perSide <= 0) {
        result.textContent = imp ? `Just the ${barWeight} lb bar.` : `Just the ${barWeight} kg bar.`;
        return;
    }

    let remaining = perSide;
    const plates = [];
    plateSizes.forEach((p, i) => {
        const count = Math.floor(remaining / p + 0.001);
        if (count > 0) { plates.push(`${count} × ${plateLabels[i]}${imp ? ' lb' : ' kg'}`); remaining -= count * p; }
    });
    const unit = imp ? 'lb' : 'kg';
    result.innerHTML = plates.length
        ? `<strong>Each side:</strong><br>${plates.join('<br>')}<br><small style="color:var(--text-muted)">Bar: ${barWeight} ${unit} · Total: ${imp ? totalInput : totalInput.toFixed(1)} ${unit}</small>`
        : `Just the bar.`;
}

function updateWarmupSuggestions() {
    const panel = document.getElementById('warmup-suggestions');
    if (!panel) return;
    const weightRaw = parseFloat(document.getElementById('weight-input')?.value) || 0;
    const weightKg = isImperial() ? lbsToKg(weightRaw) : weightRaw;
    if (weightKg < 40 || !state.selectedExercise) {
        panel.style.display = 'none';
        return;
    }
    const bar = 20;
    const sets = [
        { pct: 0, reps: 10, label: 'Bar' },
        { pct: 0.4, reps: 10, label: '40%' },
        { pct: 0.6, reps: 8, label: '60%' },
        { pct: 0.8, reps: 5, label: '80%' },
    ].map(({ pct, reps, label }) => {
        const kg = pct === 0 ? bar : Math.round(weightKg * pct / 2.5) * 2.5;
        const disp = isImperial() ? `${kgToLbs(kg)} lbs` : `${kg} kg`;
        return `<span style="font-size:12px;background:var(--surface-2);padding:4px 10px;border-radius:20px">${label}: ${disp} × ${reps}</span>`;
    });
    panel.innerHTML = `<div style="font-size:11px;color:var(--text-muted);font-weight:600;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px">Suggested warm-ups</div><div style="display:flex;flex-wrap:wrap;gap:6px">${sets.join('')}</div>`;
    panel.style.display = 'block';
}

function updatePRHint(name) {
    const hint = document.getElementById('last-1rm-hint');
    const prRow = document.querySelector(`.pr-row[data-exercise="${CSS.escape(name)}"]`);
    if (prRow) {
        hint.textContent = `Current PR: ${fmtWeight(prRow.dataset.estimated1rm)} est. 1RM`;
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
        const volStr = summary.total_volume_kg > 0 ? ` · ${fmtWeight(summary.total_volume_kg)} volume` : '';
        const streakStr = summary.workout_streak > 1 ? ` 🔥 ${summary.workout_streak}-day streak!` : '';
        showToast(`Session done! ${summary.set_count} sets${volStr}${streakStr}`);
        if ([7, 14, 30, 60, 90].includes(summary.workout_streak)) {
            setTimeout(() => showToast(`🏆 Milestone! ${summary.workout_streak}-day workout streak achieved!`, 'success'), 1500);
        }
        if (summary.next_session_targets) {
            _showNextSessionTargets(summary.next_session_targets);
        }
        invalidateCache('/sessions/history', '/prs', '/dashboard/summary');
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
    let weight = parseFloat(document.getElementById('weight-input').value);
    const reps = parseInt(document.getElementById('reps-input').value);
    if (!weight || weight <= 0 || !reps || reps <= 0) {
        showToast('Enter valid weight and reps.', 'error');
        return;
    }
    const weight_kg = isImperial() ? lbsToKg(weight) : weight;
    const logBtn = document.querySelector('#log-set-card .btn-primary');
    if (logBtn) { logBtn.disabled = true; logBtn.textContent = 'Saving…'; }
    try {
        const rpeVal = document.getElementById('rpe-input')?.value;
        const rirVal = document.getElementById('rir-input')?.value;
        const notesVal = document.getElementById('set-notes-input')?.value?.trim() || null;
        const result = await api('POST', `/sessions/${state.activeSessionId}/sets`, {
            exercise_name: state.selectedExercise,
            weight_kg,
            reps,
            rpe: rpeVal ? parseFloat(rpeVal) : null,
            rir: rirVal !== '' && rirVal !== undefined ? parseInt(rirVal) : null,
            set_notes: notesVal,
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
            <span class="set-detail">${fmtWeight(result.weight_kg)} × ${esc(result.reps)}</span>
            <span class="set-1rm">~${fmtWeight(result.estimated_1rm)} 1RM</span>
            ${result.rpe ? `<span class="set-rpe">RPE ${result.rpe}</span>` : ''}
            ${prBadge}
        `;
        ul.prepend(li);
        const markDoneBtn = document.getElementById('mark-exercise-done-btn');
        if (!markDoneBtn) {
            const btn = document.createElement('button');
            btn.id = 'mark-exercise-done-btn';
            btn.className = 'btn btn-ghost btn-sm';
            btn.style.cssText = 'font-size:12px;margin-top:10px;color:var(--green)';
            btn.textContent = '✓ Mark exercise done';
            btn.onclick = () => {
                markExerciseDone(state.selectedExercise);
                clearSelectedExercise();
                btn.remove();
            };
            document.getElementById('log-set-card').appendChild(btn);
        }
        document.getElementById('set-notes-input').value = '';
        document.getElementById('rpe-input').value = '';
        document.getElementById('rir-input').value = '';
        if (result.is_pr) {
            if (navigator.vibrate) navigator.vibrate([100, 50, 200]);
            showToast(`🏆 New PR on ${result.exercise_name}! ${fmtWeight(result.estimated_1rm)} est. 1RM`);
            li.classList.add('pr-celebration');
            setTimeout(() => li.classList.remove('pr-celebration'), 1200);
            invalidateCache('/prs');
            await loadPRs();
        }
        startRestTimer(DEFAULT_REST_SECONDS);
        document.getElementById('reps-input')?.focus();
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
            <span class="set-detail">${fmtWeight(s.weight_kg)} × ${esc(s.reps)}</span>
            <span class="set-1rm">~${fmtWeight(s.estimated_1rm)} 1RM</span>
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
            // Change 8: expandable sets drill-down
            const setsCount = s.sets?.length || 0;
            const setsDetailHtml = (s.sets || []).map(sl => `
                <div style="font-size:12px;display:flex;gap:8px;padding:3px 0;border-top:1px solid var(--border)">
                    <span style="flex:2">${esc(sl.exercise_name)}</span>
                    <span style="flex:1">${esc(sl.weight_kg)}kg</span>
                    <span style="flex:1">&#215;${esc(sl.reps)}</span>
                    <span style="flex:1;color:var(--text-muted)">${sl.estimated_1rm != null ? sl.estimated_1rm.toFixed(0) + 'kg' : ''}</span>
                    ${sl.rpe ? `<span style="flex:1;color:var(--text-muted)">RPE ${esc(sl.rpe)}</span>` : '<span style="flex:1"></span>'}
                </div>`).join('');
            // Change 11: session notes textarea
            const notesHtml = `
                <div style="margin-top:8px">
                    <textarea id="session-notes-${s.id}" placeholder="Session notes…" aria-label="Session notes" style="width:100%;font-size:12px;padding:6px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--text);resize:vertical;min-height:50px">${esc(s.notes || '')}</textarea>
                    <button onclick="saveSessionNotes(${s.id})" style="font-size:11px;margin-top:4px;padding:4px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;cursor:pointer;color:var(--text)">Save Notes</button>
                </div>`;
            return `
                <li class="session-history-item">
                    <div onclick="toggleSessionSets('ses-${s.id}')" style="cursor:pointer;display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
                        <div style="flex:1">
                            <div style="font-weight:600;font-size:14px">${esc(formatDate(s.started_at))}</div>
                            <div style="font-size:12px;color:var(--text-muted);margin-top:2px">
                                ${esc(s.set_count)} sets · ${fmtWeight(s.total_volume_kg)} volume${dur ? ` · ${esc(dur)}` : ''}
                            </div>
                            ${exStr ? `<div style="font-size:12px;color:var(--text-muted)">${esc(exStr)}</div>` : ''}
                            ${targetsHtml}
                        </div>
                        <span style="font-size:11px;color:var(--text-muted);flex-shrink:0">${setsCount} sets &#9658;</span>
                    </div>
                    <div id="ses-${s.id}" style="display:none;margin-top:10px">
                        ${setsDetailHtml}
                        ${notesHtml}
                    </div>
                </li>
            `;
        }).join('')
    }</ul>`;
}

function renderPRs(prs) {
    prs.forEach(r => { if (r.weight_kg != null) _exercisePRWeights[r.exercise_name] = +r.weight_kg; });
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
                            <td>${fmtWeight(r.weight_kg)}</td>
                            <td>${esc(r.reps)}</td>
                            <td style="color:var(--gold);font-weight:600">${fmtWeight(r.estimated_1rm)}</td>
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
    document.getElementById('session-pill').style.display = 'inline-flex';
    renderExerciseChips(state._workoutExercises);
}

function showNoSession() {
    document.getElementById('workout-no-session').style.display = 'block';
    document.getElementById('workout-active-session').style.display = 'none';
    document.getElementById('session-pill').style.display = 'none';
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
        const pill = document.getElementById('session-pill-time');
        if (pill) {
            const pm = Math.floor(elapsed / 60);
            const ps = elapsed % 60;
            pill.textContent = `${pm}:${ps.toString().padStart(2, '0')}`;
        }
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
    const insightsHtml = (r.ai_insights || []).map(i => `<li style="margin-bottom:8px">${esc(i)}</li>`).join('');
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

/* Change 4: Meal time helper */
function _mealTime(iso) {
    if (!iso) return '';
    try {
        return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
}

/* Change 7: Export data as CSV */
async function exportDataCSV() {
    try {
        showToast('Preparing export…');
        const [measurements, checkins, prs] = await Promise.all([
            api('GET', '/measurements?limit=500').catch(() => []),
            api('GET', '/checkins?limit=500').catch(() => []),
            api('GET', '/prs').catch(() => []),
        ]);

        const sections = [];

        if (checkins.length) {
            sections.push('Check-ins');
            sections.push('Date,Sleep,Energy,Soreness,Stress,Recovery,Sleep Hours');
            checkins.forEach(c => sections.push(
                `${c.date},${c.sleep_score},${c.energy_score},${c.soreness_score},${c.stress_score},${c.recovery_score},${c.sleep_duration_hrs ?? ''}`
            ));
            sections.push('');
        }

        if (measurements.length) {
            sections.push('Measurements');
            sections.push('Date,Weight(kg),Waist(cm),Chest(cm),L.Arm(cm),R.Arm(cm),Hips(cm),L.Thigh(cm),R.Thigh(cm)');
            measurements.forEach(m => sections.push(
                `${m.date},${m.body_weight_kg ?? ''},${m.waist_cm ?? ''},${m.chest_cm ?? ''},${m.left_arm_cm ?? ''},${m.right_arm_cm ?? ''},${m.hips_cm ?? ''},${m.left_thigh_cm ?? ''},${m.right_thigh_cm ?? ''}`
            ));
            sections.push('');
        }

        if (prs.length) {
            sections.push('Personal Records');
            sections.push('Exercise,Weight(kg),Reps,Est.1RM,Date');
            prs.forEach(p => sections.push(
                `"${(p.exercise_name || '').replace(/"/g, '""')}",${p.weight_kg},${p.reps},${p.estimated_1rm},${p.achieved_at ? p.achieved_at.slice(0, 10) : ''}`
            ));
        }

        const blob = new Blob([sections.join('\n')], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `bb-coach-data-${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('Export downloaded.');
    } catch (err) {
        showToast('Export failed: ' + (err.message || 'Unknown error'), 'error');
    }
}

/* Change 9: Research relative time */
function _relTime(isoStr) {
    if (!isoStr) return '';
    const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 86400000);
    if (diff === 0) return 'today';
    if (diff === 1) return 'yesterday';
    if (diff < 30) return `${diff} days ago`;
    if (diff < 365) return `${Math.floor(diff / 30)} mo ago`;
    return `${Math.floor(diff / 365)}y ago`;
}

/* Change 2: Macro pie chart */
function _drawMacroPie(protein_g, carbs_g, fat_g) {
    const pCal = protein_g * 4;
    const cCal = carbs_g * 4;
    const fCal = fat_g * 9;
    const total = pCal + cCal + fCal;
    if (!total) return;
    const svg = document.getElementById('macro-pie');
    if (!svg) return;
    const cx = 60, cy = 60, r = 50, gap = 2;
    const slices = [
        { cal: pCal, color: '#22c55e', label: 'Protein' },
        { cal: cCal, color: '#3b82f6', label: 'Carbs' },
        { cal: fCal, color: '#f59e0b', label: 'Fat' },
    ];
    let html = '';
    let startAngle = -Math.PI / 2;
    for (const s of slices) {
        if (!s.cal) continue;
        const sweep = (s.cal / total) * 2 * Math.PI;
        const endAngle = startAngle + sweep;
        const x1 = cx + r * Math.cos(startAngle);
        const y1 = cy + r * Math.sin(startAngle);
        const x2 = cx + r * Math.cos(endAngle);
        const y2 = cy + r * Math.sin(endAngle);
        const large = sweep > Math.PI ? 1 : 0;
        html += `<path d="M${cx},${cy} L${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 ${large},1 ${x2.toFixed(1)},${y2.toFixed(1)} Z" fill="${s.color}" opacity="0.85"/>`;
        startAngle = endAngle + (gap / (2 * Math.PI * r)) * 2 * Math.PI;
    }
    // Inner white circle for donut effect
    html += `<circle cx="${cx}" cy="${cy}" r="30" fill="var(--surface)"/>`;
    svg.innerHTML = html;
    // Legend (no esc needed — all values are internal constants or Math.round results)
    const legend = document.getElementById('macro-pie-legend');
    if (legend) {
        legend.innerHTML = slices.filter(s => s.cal).map(s =>
            `<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${s.color};margin-right:3px"></span>${s.label} ${Math.round(s.cal / total * 100)}%</span>`
        ).join('');
    }
    document.getElementById('macro-pie-container').style.display = 'block';
}

/* Change 8: Toggle session sets */
function toggleSessionSets(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

/* Change 11: Save session notes */
async function saveSessionNotes(sessionId) {
    const notes = document.getElementById(`session-notes-${sessionId}`)?.value || '';
    try {
        await api('PATCH', `/sessions/${sessionId}/notes`, { notes });
        showToast('Notes saved.');
    } catch (err) {
        showToast(err.message || 'Failed to save notes.', 'error');
    }
}

/* Change 10: Print individual analysis */
function printAnalysis(analysisId) {
    const card = document.getElementById(`analysis-card-${analysisId}`);
    if (!card) return;
    const win = window.open('', '_blank', 'width=800,height=600');
    if (!win) { showToast('Allow pop-ups to print the analysis.', 'error'); return; }
    // Clone and strip script/link tags before injecting into print window
    const clone = card.cloneNode(true);
    clone.querySelectorAll('script, link[rel="import"]').forEach(el => el.remove());
    const safeHtml = clone.innerHTML;
    win.document.write(`<!DOCTYPE html><html><head><title>Analysis</title>
    <style>
        body { font-family: sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; }
        h2 { margin-bottom: 4px; }
        .tag { display:inline-block; padding:2px 8px; border-radius:20px; font-size:11px; margin:2px; }
        .strength { background:#dcfce7; color:#166534; }
        .improve { background:#fef3c7; color:#92400e; }
        @media print { button { display:none; } }
    </style>
    </head><body>
    <button onclick="window.print()">Print</button>
    ${safeHtml}
    </body></html>`);
    win.document.close();
}

/* Change 9: Filter research by keyword */
function filterResearch() {
    const q = document.getElementById('research-search')?.value.toLowerCase() || '';
    document.querySelectorAll('.research-topic-card').forEach(card => {
        const text = card.textContent.toLowerCase();
        card.style.display = !q || text.includes(q) ? '' : 'none';
    });
}

function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/* ── Auth overlay panels ── */
function obShowRegister() {
    document.getElementById('ob-panel-register').classList.add('active');
    document.getElementById('ob-panel-login').classList.remove('active');
    document.getElementById('ob-auth-error').textContent = '';
}

function obShowLogin() {
    document.getElementById('ob-panel-login').classList.add('active');
    document.getElementById('ob-panel-register').classList.remove('active');
    document.getElementById('ob-login-error').textContent = '';
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
        document.getElementById('onboarding-overlay').style.display = 'none';
        showTab('profile');
        showToast('Account created! Fill in your profile to personalize your plan.');
    } catch (err) {
        errEl.textContent = err.message;
    }
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
        document.getElementById('onboarding-overlay').style.display = 'none';
        await loadDashboard();
    } catch (err) {
        errEl.textContent = err.message;
    }
}

/* ── Init ── */
document.addEventListener('DOMContentLoaded', async () => {
    applyUnitLabels();
    await initAuth();
    if (getToken()) {
        _requestNotificationPerm();
        loadDashboard();
    }

    document.addEventListener('keydown', e => {
        if (e.key === 'Enter' && state.activeSessionId && state.selectedExercise) {
            const activeEl = document.activeElement;
            if (activeEl && (activeEl.id === 'weight-input' || activeEl.id === 'reps-input')) {
                e.preventDefault();
                logSet();
            }
        }
    });
});
