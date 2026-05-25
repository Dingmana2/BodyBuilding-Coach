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
    pendingFile: null,
    currentPlan: null,
    analyses: [],
};

/* ── API helpers ── */
async function api(method, path, body = null, isFormData = false) {
    const opts = { method };
    if (body) {
        if (isFormData) {
            opts.body = body;
        } else {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(body);
        }
    }
    const res = await fetch(`/api${path}`, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Request failed');
    }
    return res.json();
}

/* ── Tab navigation ── */
function showTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
    document.getElementById(`tab-${tab}`).classList.add('active');
    document.querySelector(`[data-tab="${tab}"]`).classList.add('active');

    const loaders = {
        dashboard: loadDashboard,
        analysis: loadAnalyses,
        plans: loadCurrentPlan,
        research: loadResearch,
        progress: loadProgress,
        profile: loadProfile,
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
    const [health, plan] = await Promise.all([
        cachedApi('GET', '/health').catch(() => ({ api_key_configured: false })),
        cachedApi('GET', '/plan/current').catch(() => null),
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
}

/* ── Photo Upload & Analysis ── */
function handleDrop(e) {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (file) previewFile(file);
}

function handleFileSelect(input) {
    if (input.files?.[0]) previewFile(input.files[0]);
}

function previewFile(file) {
    state.pendingFile = file;
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('preview-img').src = e.target.result;
        document.getElementById('drop-zone').style.display = 'none';
        document.getElementById('upload-preview').style.display = 'block';
    };
    reader.readAsDataURL(file);
}

function clearUpload() {
    state.pendingFile = null;
    document.getElementById('photo-input').value = '';
    document.getElementById('drop-zone').style.display = 'block';
    document.getElementById('upload-preview').style.display = 'none';
}

async function submitAnalysis() {
    if (!state.pendingFile) return;

    const file = state.pendingFile;
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    if (!allowed.includes(file.type)) {
        showToast('Please use a JPG, PNG, or WebP image.', 'error');
        return;
    }
    if (file.size > 20 * 1024 * 1024) {
        showToast('File too large. Max 20MB.', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

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
    const data = await cachedApi('GET', '/progress').catch(() => []);
    const container = document.getElementById('progress-content');

    if (data.length < 1) {
        container.innerHTML = '<p class="empty-state">No progress data yet. Upload at least two body photos to see your progress.</p>';
        return;
    }

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
}

/* ── Profile ── */
async function loadProfile() {
    const profile = await cachedApi('GET', '/profile').catch(() => ({}));
    if (!profile || !profile.age) return;

    const form = document.getElementById('profile-form');
    const fields = ['age', 'gender', 'height_cm', 'weight_kg', 'goal', 'training_experience', 'training_days_per_week', 'dietary_restrictions'];
    fields.forEach(field => {
        const el = form.querySelector(`[name="${field}"]`);
        if (el && profile[field] != null) el.value = profile[field];
    });
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
    };

    try {
        await api('POST', '/profile', data);
        invalidateCache('/profile', '/plan/current');
        showToast('Profile saved!');
    } catch (err) {
        showToast(`Save failed: ${err.message}`, 'error');
    }
}

/* ── Utilities ── */
function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/* ── Init ── */
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
});
