/* ============================================================
   EmbyPulse 玩家社区 - 核心逻辑驱动 (高容错稳定版 + 全选修复)
   =========================================================== */
const __requestAssetVer = (window.REQUEST_ASSET_VER ? `?v=${window.REQUEST_ASSET_VER}` : '');
const __logoApp2Avif = `/static/img/logo-app-2.avif${__requestAssetVer}`;
const __logoApp2Webp = `/static/img/logo-app-2.webp${__requestAssetVer}`;
const __logoApp2Png = `/static/img/logo-app-2.png${__requestAssetVer}`;

window.LOGO_APP2_ASSETS = { avif: __logoApp2Avif, webp: __logoApp2Webp, png: __logoApp2Png };
window.logoApp2Fallback = function(img) {
    if (!img) return;
    const state = img.dataset.fallback || '';
    if (state === '') { img.dataset.fallback = 'webp'; img.src = __logoApp2Webp; return; }
    if (state === 'webp') { img.dataset.fallback = 'png'; img.src = __logoApp2Png; return; }
    img.onerror = null;
};
window.markRequestChartReady = function(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const wrap = el.closest('.chart-wrapper');
    if (wrap) wrap.classList.add('is-ready');
};



window.ensureRequestChartJs = (() => {
    let promise = null;
    return () => {
        if (window.Chart) return Promise.resolve();
        if (promise) return promise;
        promise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
            script.async = true;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('chart.js load failed'));
            document.head.appendChild(script);
        });
        return promise;
    };
})();
async function toBase64(url) { try { const res = await fetch(url); if (!res.ok) throw new Error(`HTTP ${res.status}`); const blob = await res.blob(); return new Promise((resolve) => { const reader = new FileReader(); reader.onloadend = () => resolve(reader.result); reader.readAsDataURL(blob); }); this.chartRendered.hour = true; } catch (e) { return null; } }
async function applyPhysicalBlur(base64Url) { return new Promise((resolve) => { const img = new Image(); img.onload = () => { const canvas = document.createElement('canvas'); const ctx = canvas.getContext('2d'); canvas.width = 400; canvas.height = 800; ctx.filter = 'blur(40px) brightness(0.4)'; const scale = Math.max(canvas.width / img.width, canvas.height / img.height); ctx.drawImage(img, (canvas.width / 2) - (img.width / 2) * scale, (canvas.height / 2) - (img.height / 2) * scale, img.width * scale, img.height * scale); resolve(canvas.toDataURL('image/jpeg', 0.8)); }; img.onerror = () => resolve(base64Url); img.src = base64Url; }); this.chartRendered.trend = true; }
window.tmdbCache = {};
window.fallbackPoster = async function(img, title) { if (img.getAttribute('data-fallback-done')) return; img.setAttribute('data-fallback-done', 'true'); img.dataset.fallback = ''; img.onerror = () => window.logoApp2Fallback(img); img.src = __logoApp2Avif; img.classList.add('opacity-30', 'object-contain', 'p-4'); if (!title || title === 'undefined' || title === 'null') return; try { const res = await fetch(`/api/requests/search?query=${encodeURIComponent(title)}`); const data = await res.json(); if (data.status === 'success' && data.data.length > 0) { const match = data.data.find(d => d.poster_path) || data.data[0]; if (match.poster_path) { img.src = match.poster_path; img.classList.remove('opacity-30', 'object-contain', 'p-4'); img.classList.add('object-cover'); } } } catch(e) {} };
window.fallbackReportPoster = async function(imgEl, title) { if(imgEl.getAttribute('data-fallback-done')) return; imgEl.setAttribute('data-fallback-done', 'true'); imgEl.dataset.fallback = ''; imgEl.onerror = () => window.logoApp2Fallback(imgEl); imgEl.src = __logoApp2Avif; imgEl.style.objectFit = "contain"; imgEl.style.padding = "20px"; try { const res = await fetch(`/api/requests/search?query=${encodeURIComponent(title)}`); const data = await res.json(); if (data.status === 'success' && data.data.length > 0) { const match = data.data.find(d => d.poster_path) || data.data[0]; if (match.poster_path) { const b64 = await toBase64(match.poster_path); if(b64) { imgEl.src = b64; imgEl.style.objectFit = "cover"; imgEl.style.padding = "0"; } } } } catch(e) {} };

document.addEventListener('alpine:init', () => {
    Alpine.data('dragScroll', () => ({ isDown: false, isDragging: false, startX: 0, scrollLeft: 0, start(e) { this.isDown = true; this.isDragging = false; this.startX = e.pageX - this.$el.offsetLeft; this.scrollLeft = this.$el.scrollLeft; }, end() { this.isDown = false; setTimeout(() => { this.isDragging = false; }, 50); }, move(e) { if (!this.isDown) return; this.isDragging = true; e.preventDefault(); const walk = (e.pageX - this.$el.offsetLeft - this.startX) * 1.5; this.$el.scrollLeft = this.scrollLeft - walk; } }));

    Alpine.data('requestApp', () => ({
        scrolled: false, lastScrollTop: 0, isScrollingDown: false, isLoaded: false, isLoggedIn: false, isDarkMode: false,
        userId: '', userName: '', expireDate: '未知', serverUrl: '', serverUrlLocal: '', serverUrlPublic: '', serverId: '', showServerUrl: false, loginForm: { username: '', password: '', captcha: '' }, captchaImage: '', isLoggingIn: false,
        currentTab: 'explore', searchQuery: '', isSearching: false, searchResults: [], recommendResults: [], recommendRow1: [], recommendRow2: [], recommendRow3: [], recommendLoaded: false,
        serverDashboard: null, serverLatest: [], serverTopRated: [], serverGenres: [], serverTopMovies: [], serverTopSeries: [],
        showcaseModal: { open: false, isLoading: false, data: null }, queueModal: { open: false, activeTab: 'request' }, myQueue: [], myRequestMap: {}, myFeedbacks: [],
        userStats: null, userBadges: [], userTrend: null, isStatsLoading: false, statsLoaded: false, charts: { hour: null, device: null, client: null, trend: null },
        chartVisibility: { hour: false, trend: false, device: false, client: false },
        chartRendered: { hour: false, trend: false, device: false, client: false },
        profileObserversInitialized: false,
        isModalOpen: false, activeItem: null, tvSeasons: [], isLoadingSeasons: false, isCheckingLocal: false, selectedSeasons: [], isSubmitting: false,
        toast: { show: false, message: '', type: 'success' }, feedbackModal: { open: false, itemName: '', posterPath: '', issueType: '缺少字幕', desc: '' }, feedbackIssues: ['缺少字幕', '字幕错位', '视频卡顿/花屏', '清晰度太低', '音轨无声/音画不同步', '其他问题'], isFeedbackSubmitting: false,
        posterStudio: { open: false, isLoading: false, isSaving: false, period: 'month', periodLabel: '本月 观影报告', data: null, useCoverBg: false, top1BgBase64: null, rankRows: [] },
        html2canvasPromise: null,

        async initTheme() { 
            this.isDarkMode = document.documentElement.classList.contains('dark'); 
            try { 
                const res = await fetch('/api/requests/check'); 
                const data = await res.json(); 
                if (data.status === 'success') { 
                    this.isLoggedIn = true; 
                    this.userId = data.user.Id; 
                    this.userName = data.user.Name; 
                    this.expireDate = data.user.expire_date; 
                    this.serverUrlLocal = data.server_url_local || ''; 
                    this.serverUrlPublic = data.server_url_public || ''; 
                    this.serverUrl = await this.pickBestServerUrl(); 
                    this.serverId = data.server_id || ''; 
                    this.loadServerData(); 
                } 
            } catch(e) {} 
            this.isLoaded = true; 
            this.refreshCaptcha();
            this.applyLoginBackground(!this.isLoggedIn);
        },
        handleScroll() { const st = window.pageYOffset || document.documentElement.scrollTop; this.scrolled = st > 50; this.isScrollingDown = st > this.lastScrollTop && st > 50; this.lastScrollTop = st <= 0 ? 0 : st; },
        toggleTheme() { this.isDarkMode = !this.isDarkMode; localStorage.setItem('ep_theme', this.isDarkMode ? 'dark' : 'light'); document.documentElement.classList.toggle('dark', this.isDarkMode); if (this.currentTab === 'profile' && this.statsLoaded) { this.chartRendered = { hour: false, trend: false, device: false, client: false }; setTimeout(() => this.renderCharts(), 150); } },
        showToast(msg, type = 'success') { this.toast = { show: true, message: msg, type }; setTimeout(() => this.toast.show = false, 3000); },
        async copyToClipboard(text) { try { await navigator.clipboard.writeText(text); } catch(e) { const input = document.createElement('input'); input.value = text; document.body.appendChild(input); input.select(); document.execCommand('copy'); document.body.removeChild(input); } },
        async login() { 
            if(!this.loginForm.username || !this.loginForm.password || !this.loginForm.captcha) return; 
            this.isLoggingIn = true; 
            try { 
                const res = await fetch('/api/requests/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(this.loginForm) }); 
                const data = await res.json(); 
                if (data.status === 'success') { 
                    const checkRes = await fetch('/api/requests/check'); 
                    const checkData = await checkRes.json(); 
                    if (checkData.status === 'success') { 
                        this.userId = checkData.user.Id; 
                        this.userName = checkData.user.Name; 
                        this.expireDate = checkData.user.expire_date; 
                        this.serverUrlLocal = checkData.server_url_local || ''; 
                        this.serverUrlPublic = checkData.server_url_public || ''; 
                        this.serverUrl = await this.pickBestServerUrl(); 
                        this.serverId = checkData.server_id || ''; 
                    } 
                    this.isLoggedIn = true; 
                    this.applyLoginBackground(false);
                    this.loadServerData(); 
                    this.showToast('登录成功'); 
                } else { 
                    this.showToast(data.message, 'error'); 
                    this.loginForm.captcha = ''; 
                    this.refreshCaptcha(); 
                } 
            } catch(e) { 
                this.showToast('网络错误', 'error'); 
            } 
            this.isLoggingIn = false; 
        },
        isPrivateHost(host) {
            if (!host) return false;
            if (host === 'localhost' || host === '127.0.0.1' || host === '::1') return true;
            if (/^10\./.test(host)) return true;
            if (/^192\.168\./.test(host)) return true;
            const m = host.match(/^172\.(\d+)\./);
            if (m) { const n = parseInt(m[1], 10); if (n >= 16 && n <= 31) return true; }
            return false;
        },
        async probeLocalEmby(localUrl) {
            if (!localUrl) return false;
            return new Promise((resolve) => {
                const img = new Image();
                const timer = setTimeout(() => resolve(false), 800);
                img.onload = () => { clearTimeout(timer); resolve(true); };
                img.onerror = () => { clearTimeout(timer); resolve(false); };
                img.src = `${localUrl.replace(/\/$/, '')}/web/favicon.ico?ts=${Date.now()}`;
            });
        },
        async pickBestServerUrl() {
            const localUrl = (this.serverUrlLocal || '').replace(/\/$/, '');
            const publicUrl = (this.serverUrlPublic || '').replace(/\/$/, '');
            if (!localUrl && !publicUrl) return '';
            if (!publicUrl) return localUrl;
            if (!localUrl) return publicUrl;
            const host = window.location.hostname;
            if (this.isPrivateHost(host)) return localUrl;
            const ok = await this.probeLocalEmby(localUrl);
            return ok ? localUrl : publicUrl;
        },
        async refreshCaptcha() { try { const res = await fetch('/api/captcha'); const data = await res.json(); if (data.status === 'success') this.captchaImage = data.image || ''; } catch(e) {} },
        async logout() { 
            try { 
                await fetch('/api/requests/logout', { method: 'POST' }); 
                this.isLoggedIn = false; 
                this.applyLoginBackground(true);
            } catch (e) {} 
        },
        getLoginBgUrl() {
            const pc = (window.REQUEST_LOGIN_BG_PC || '').trim();
            const mobile = (window.REQUEST_LOGIN_BG_MOBILE || '').trim();
            const isMobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
            return (isMobile ? mobile : pc) || pc || mobile || '';
        },
        applyLoginBackground(show) {
            const bgEl = document.getElementById('login-bg');
            const overlay = document.getElementById('login-bg-overlay');
            const base = document.getElementById('base-bg');
            if (!bgEl) return;
            const bgUrl = this.getLoginBgUrl();
            const blurValRaw = Number(window.REQUEST_LOGIN_BG_BLUR);
            const blurVal = Number.isNaN(blurValRaw) ? 0 : Math.max(0, Math.min(30, blurValRaw));
            if (!bgUrl || !show) {
                bgEl.style.opacity = '0';
                if (overlay) overlay.classList.add('hidden');
                if (base) base.style.opacity = '';
                return;
            }
            const isImg = bgEl.tagName === 'IMG';
            if (isImg) {
                bgEl.src = bgUrl;
                bgEl.style.imageRendering = blurVal > 0 ? 'auto' : 'auto';
            } else {
                bgEl.style.backgroundImage = `url('${bgUrl}')`;
                bgEl.style.backgroundRepeat = 'no-repeat';
                bgEl.style.backgroundPosition = 'center';
                bgEl.style.backgroundSize = 'cover';
            }
            {
                const filters = [];
                if (blurVal > 0) filters.push(`blur(${blurVal}px)`);
                if (filters.length > 0) {
                    // Only soften when user explicitly sets blur
                    filters.push('contrast(0.97)', 'saturate(0.9)');
                }
                bgEl.style.filter = filters.length > 0 ? filters.join(' ') : 'none';
            }
            bgEl.style.transform = blurVal > 0 ? 'scale(1.05)' : 'none';
            bgEl.style.opacity = '1';
            if (overlay) {
                overlay.style.backdropFilter = blurVal > 0 ? `blur(${blurVal}px)` : 'none';
                overlay.style.webkitBackdropFilter = blurVal > 0 ? `blur(${blurVal}px)` : 'none';
                overlay.style.background = blurVal > 0
                    ? 'linear-gradient(180deg, rgba(255,255,255,0.08), rgba(0,0,0,0.06))'
                    : 'transparent';
                overlay.classList.remove('hidden');
            }
            if (base) base.style.opacity = blurVal > 0 ? '0' : '';
        },

        async loadServerData() { 
            try { 
                const [dash, hub, lat, topM, topS] = await Promise.all([ 
                    fetch('/api/stats/dashboard?user_id=all').then(r => r.json()), 
                    fetch('/api/requests/hub_data').then(r => r.json()), 
                    // 🔥 这里将原来的 latest 替换成了带有安检门的 safe_latest
                    fetch('/api/requests/safe_latest?limit=15').then(r => r.json()), 
                    fetch('/api/requests/safe_top?category=Movie').then(r => r.json()), 
                    fetch('/api/requests/safe_top?category=Episode').then(r => r.json()) 
                ]); 
                if (dash.status === 'success') this.serverDashboard = dash.data; 
                if (hub.status === 'success') { this.serverTopRated = hub.data.top_rated; this.serverGenres = hub.data.genres; } 
                if (lat.status === 'success') this.serverLatest = lat.data; 
                if (topM.status === 'success') this.serverTopMovies = topM.data; 
                if (topS.status === 'success') this.serverTopSeries = topS.data; if (topS.status !== 'success' || !topS.data || topS.data.length === 0) { try { const fallbackS = await fetch('/api/stats/top_movies?user_id=all&category=Episode&sort_by=count').then(r => r.json()); if (fallbackS.status === 'success') this.serverTopSeries = fallbackS.data.slice(0, 10); } catch(e) {} } 
            } catch(e) {} 
            if (this.currentTab === 'request' && !this.recommendLoaded) this.loadRecommendations();
        },

        async loadRecommendations() {
            try { 
                const res = await fetch(`/api/requests/trending`); 
                const data = await res.json(); 
                if(data.status === 'success' && data.data && data.data.length > 0) { 
                    let validItems = data.data.sort(() => 0.5 - Math.random());
                    this.recommendResults = validItems;
                    const third = Math.ceil(validItems.length / 3);
                    this.recommendRow1 = validItems.slice(0, third);
                    this.recommendRow2 = validItems.slice(third, third * 2);
                    this.recommendRow3 = validItems.slice(third * 2);
                    this.recommendLoaded = true;
                } 
            } catch(e) { console.log("无热门数据"); }
        },

        switchTab(tab) { 
            this.currentTab = tab; 
            this.$nextTick(() => window.scrollTo(0, 0)); 
            if (tab === 'profile') { 
                // Profile tab uses x-if, DOM gets recreated. Reset chart state to ensure re-render.
                try { if (this.charts.hour) this.charts.hour.destroy(); } catch(e) {}
                try { if (this.charts.trend) this.charts.trend.destroy(); } catch(e) {}
                try { if (this.charts.device) this.charts.device.destroy(); } catch(e) {}
                try { if (this.charts.client) this.charts.client.destroy(); } catch(e) {}
                this.chartRendered = { hour: false, trend: false, device: false, client: false };
                this.chartVisibility = { hour: false, trend: false, device: false, client: false };
                this.profileObserversInitialized = false;
                this.setupProfileChartObservers(); 
                if (!this.statsLoaded) this.loadProfileStats(); 
                else setTimeout(() => this.renderCharts(), 150);
                // rAF 双重兜底：防止 DOM 重建后首次渲染未命中
                requestAnimationFrame(() => requestAnimationFrame(() => this.renderCharts()));
            } 
            if (tab === 'request' && !this.recommendLoaded) this.loadRecommendations(); 
        },

        setupProfileChartObservers() {
            if (this.profileObserversInitialized) return;
            const self = this;
            const setup = (id, key) => {
                const el = document.getElementById(id);
                if (!el) return;
                const trigger = () => { self.chartVisibility[key] = true; self.renderCharts(); };
                const observer = new IntersectionObserver((entries) => {
                    if (entries.some(e => e.isIntersecting)) {
                        observer.disconnect();
                        trigger();
                    }
                }, { rootMargin: '200px' });
                observer.observe(el);
                el.addEventListener('click', trigger, { once: true });
            };
            setup('profileHourChart', 'hour');
            setup('profileTrendChart', 'trend');
            setup('profileDeviceChart', 'device');
            setup('profileClientChart', 'client');
            this.profileObserversInitialized = true;
        },

        async openShowcaseModal(itemId, fallbackItem = null) { 
            const finalId = itemId || (fallbackItem ? fallbackItem.ItemId || fallbackItem.Id : ''); 
            this.showcaseModal.data = fallbackItem || { Name: '加载中...' }; 
            this.showcaseModal.open = true; 
            this.showcaseModal.isLoading = true; 
            document.body.style.overflow = 'hidden'; 
            try { 
                const res = await fetch(`/api/requests/item_info?item_id=${finalId}`); 
                if(res.ok) { 
                    const data = await res.json(); 
                    if (data.status === 'success') {
                        // 核心修复：合并数据，防止覆盖掉已有字段
                        this.showcaseModal.data = { ...fallbackItem, ...data.data }; 
                    }
                } 
            } catch(e) {} finally { this.showcaseModal.isLoading = false; } 
        },
        getEmbyItemUrl(item) {
            const base = (this.serverUrl || '').replace(/\/$/, '');
            const jumpId = item?.JumpId || item?.SeriesId || item?.Id || item?.ItemId;
            const sid = item?.ServerId || this.serverId || '';
            if (!base || !jumpId) return '';
            const serverParam = sid ? `&serverId=${sid}` : '';
            return `${base}/web/index.html#!/item?id=${jumpId}${serverParam}`;
        },
        openInEmby(item) {
            const url = this.getEmbyItemUrl(item);
            if (!url) { this.showToast('未配置 Emby 访问地址', 'error'); return; }
            window.open(url, '_blank');
        },
       closeShowcaseModal() { this.showcaseModal.open = false; document.body.style.overflow = ''; },
        openQueueModal(tab) { this.queueModal.activeTab = tab; this.queueModal.open = true; document.body.style.overflow = 'hidden'; if(tab === 'request') this.loadQueue(); else this.loadMyFeedback(); },
        closeQueueModal() { this.queueModal.open = false; document.body.style.overflow = ''; },

        async submitRequest() { if (this.activeItem.media_type === 'movie' && (this.activeItem.local_status === 2 || this.isRequestSubmitted(this.activeItem.tmdb_id, 0))) return; this.isSubmitting = true; const seasons = this.activeItem.media_type === 'tv' ? this.selectedSeasons.map(Number).filter(sn => !this.isRequestSubmitted(this.activeItem.tmdb_id, sn)) : [0]; if (this.activeItem.media_type === 'tv' && seasons.length === 0) { this.showToast('✅ 已提交申请，等待管理员处理'); this.isSubmitting = false; return; } const payload = { tmdb_id: this.activeItem.tmdb_id, media_type: this.activeItem.media_type, title: this.activeItem.title, year: this.activeItem.year, poster_path: this.activeItem.poster_path, overview: this.activeItem.overview, seasons }; try { const res = await fetch('/api/requests/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); const text = await res.text(); let data = {}; try { data = JSON.parse(text); } catch(e) { data = { message: text }; } if (res.ok && (data.status === 'success' || !data.detail)) { this.showToast('✅ ' + (data.message || '心愿已发送！')); this.closeModal(); this.openQueueModal('request'); } else { this.showToast('❌ ' + (data.message || data.detail || '提交异常'), 'error'); } } catch (e) { this.showToast('网络异常', 'error'); } finally { this.isSubmitting = false; } },
        openFeedbackModal(itemName, posterPath = '') { this.feedbackModal.itemName = itemName; this.feedbackModal.posterPath = posterPath; this.feedbackModal.issueType = '缺少字幕'; this.feedbackModal.desc = ''; this.feedbackModal.open = true; if(this.isModalOpen) this.closeModal(); if(this.showcaseModal.open) this.closeShowcaseModal(); },
        async submitFeedback() { this.isFeedbackSubmitting = true; try { const res = await fetch('/api/requests/feedback/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_name: this.feedbackModal.itemName, issue_type: this.feedbackModal.issueType, description: this.feedbackModal.desc, poster_path: this.feedbackModal.posterPath }) }); const text = await res.text(); let data = {}; try { data = JSON.parse(text); } catch(e) { data = { message: text }; } if (res.ok && (data.status === 'success' || !data.detail)) { this.showToast(data.message || '反馈成功'); this.feedbackModal.open = false; this.openQueueModal('feedback'); } else { this.showToast(data.message || data.detail || '报错失败', 'error'); } } catch(e) { this.showToast('网络错误', 'error'); } finally { this.isFeedbackSubmitting = false; } },
        async searchMedia() { if (!this.searchQuery.trim()) return; this.isSearching = true; if (this.currentTab !== 'request') this.currentTab = 'request'; window.scrollTo(0, 0); try { const res = await fetch(`/api/requests/search?query=${encodeURIComponent(this.searchQuery)}`); const data = await res.json(); if (data.status === 'success') { this.searchResults = data.data; if (data.data.length === 0) this.showToast('未找到结果', 'error'); } } catch (e) { this.showToast('网络错误', 'error'); } finally { this.isSearching = false; } },

        async loadProfileStats() { if (this.statsLoaded || !this.userId) return; this.isStatsLoading = true; try { const [stats, badges, trend] = await Promise.all([ fetch(`/api/stats/user_details?user_id=${this.userId}`).then(r => r.json()), fetch(`/api/stats/badges?user_id=${this.userId}`).then(r => r.json()), fetch(`/api/stats/trend?dimension=day&user_id=${this.userId}`).then(r => r.json()) ]); if (stats.status === 'success') this.userStats = stats.data; if (badges.status === 'success') this.userBadges = badges.data; if (trend.status === 'success') this.userTrend = trend.data; this.statsLoaded = true; this.chartRendered = { hour: false, trend: false, device: false, client: false }; this.renderCharts(); } catch(e) {} this.isStatsLoading = false; },

        renderCharts() {
            this.$nextTick(() => {
                if (!this.userStats) return;
                window.ensureRequestChartJs().then(() => {
                    try {
                    const isDark = this.isDarkMode; const textColor = isDark ? '#a1a1aa' : '#64748b'; 
                    const macaronColors = ['#10b981', '#3b82f6', '#8b5cf6', '#6366f1', '#14b8a6', '#64748b'];
                    const warmColors = ['#f43f5e', '#f59e0b', '#ec4899', '#f97316', '#d946ef', '#64748b'];
                    const forceRender = !this.chartVisibility.hour && !this.chartVisibility.trend && !this.chartVisibility.device && !this.chartVisibility.client;
                    
                    const hourlyData = this.userStats.hourly || {};
                    if ((this.chartVisibility.hour || forceRender) && !this.chartRendered.hour && document.getElementById('profileHourChart')) { if (this.charts.hour) this.charts.hour.destroy(); const ctx = document.getElementById('profileHourChart').getContext('2d'); let labels = [], values = []; for(let i=0; i<24; i++) { labels.push(String(i).padStart(2, '0')); values.push(hourlyData[String(i).padStart(2, '0')] || 0); } this.charts.hour = new Chart(ctx, { type: 'bar', data: { labels, datasets: [{ data: values, backgroundColor: isDark ? '#818cf8' : '#6366f1', borderRadius: 4 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: textColor, font: {size: 9} } }, y: { display: false } } } }); this.chartRendered.hour = true; if (window.markRequestChartReady) window.markRequestChartReady('profileHourChart'); }
                    
                    const trendData = this.userTrend || {};
                    if ((this.chartVisibility.trend || forceRender) && !this.chartRendered.trend && document.getElementById('profileTrendChart') && Object.keys(trendData).length > 0) { if (this.charts.trend) this.charts.trend.destroy(); const ctx = document.getElementById('profileTrendChart').getContext('2d'); const labels = Object.keys(trendData).map(k => k.substring(5)); const values = Object.values(trendData).map(v => Math.round(v/3600)); this.charts.trend = new Chart(ctx, { type: 'line', data: { labels, datasets: [{ data: values, borderColor: isDark ? '#38bdf8' : '#0ea5e9', backgroundColor: isDark ? 'rgba(56,189,248,0.15)' : 'rgba(14,165,233,0.15)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 0 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: textColor, maxTicksLimit: 6, font: {size: 9} } }, y: { display: false } } } }); this.chartRendered.trend = true; if (window.markRequestChartReady) window.markRequestChartReady('profileTrendChart'); }
                    
                    const devices = this.userStats.devices || [];
                    if ((this.chartVisibility.device || forceRender) && !this.chartRendered.device && document.getElementById('profileDeviceChart') && devices.length > 0) {
                        if (this.charts.device) this.charts.device.destroy(); const ctx = document.getElementById('profileDeviceChart').getContext('2d'); let labels = [], values = [], others = 0;
                        devices.forEach((d, i) => { let name = d.Device || d.device || d.name || d.Client || '未知'; let val = d.Plays || d.count || 0; if(i<4){ labels.push(name); values.push(val); } else { others += val; } });
                        if(others > 0){ labels.push('其他'); values.push(others); }
                        this.charts.device = new Chart(ctx, { type: 'doughnut', data: { labels, datasets: [{ data: values, backgroundColor: macaronColors, borderWidth: 2, borderColor: isDark ? '#000' : '#fff' }] }, options: { responsive: true, maintainAspectRatio: false, cutout: '65%', plugins: { legend: { position: 'right', labels: { boxWidth: 6, font: {size: 9}, color: textColor } } } } }); this.chartRendered.device = true; if (window.markRequestChartReady) window.markRequestChartReady('profileDeviceChart');
                    }
                    
                    const clients = this.userStats.clients || [];
                    if ((this.chartVisibility.client || forceRender) && !this.chartRendered.client && document.getElementById('profileClientChart') && clients.length > 0) {
                        if (this.charts.client) this.charts.client.destroy(); const ctx = document.getElementById('profileClientChart').getContext('2d'); let labels = [], values = [], others = 0;
                        clients.forEach((c, i) => { let name = c.Client || c.client || c.name || '未知'; let val = c.Plays || c.count || 0; if(i<4){ labels.push(name); values.push(val); } else { others += val; } });
                        if(others > 0){ labels.push('其他'); values.push(others); }
                        this.charts.client = new Chart(ctx, { type: 'doughnut', data: { labels, datasets: [{ data: values, backgroundColor: warmColors, borderWidth: 2, borderColor: isDark ? '#000' : '#fff' }] }, options: { responsive: true, maintainAspectRatio: false, cutout: '65%', plugins: { legend: { position: 'right', labels: { boxWidth: 6, font: {size: 9}, color: textColor } } } } }); this.chartRendered.client = true; if (window.markRequestChartReady) window.markRequestChartReady('profileClientChart');
                    }
                } catch (e) { console.error("图表数据异常保护", e); }
            });
            });
        },

        getMoviePct() { if (!this.userStats || !this.userStats.preference) return 50; const pref = this.userStats.preference; const total = pref.movie_plays + pref.episode_plays; if (total === 0) return 50; return Math.round((pref.movie_plays / total) * 100); },
        getPrefText() { const pct = this.getMoviePct(); if (pct === 50 && (!this.userStats || this.userStats.overview.total_plays === 0)) return "尚无观看记录，探索中..."; if (pct > 70) return "「沉浸长片爱好者，偏爱电影的光影」"; if (pct < 30) return "「剧情连贯控，追剧是最大乐趣」"; return "「雨露均沾，电影与剧集我全都要」"; },
        
        // 🔥 修复3：补上丢失的“全选/全消”函数引擎
        toggleSelectAllSeasons() {
            const availableSeasons = this.tvSeasons.filter(s => !s.exists_locally && !this.isSeasonSubmitted(s.season_number)).map(s => s.season_number);
            if (this.selectedSeasons.length === availableSeasons.length && availableSeasons.length > 0) {
                this.selectedSeasons = []; // 已经全选了，就执行全消
            } else {
                this.selectedSeasons = availableSeasons; // 否则执行全选未入库的季
            }
        },

        async openModal(item) { this.activeItem = item; this.isModalOpen = true; this.tvSeasons = []; this.selectedSeasons = []; document.body.style.overflow = 'hidden'; const queuePromise = this.loadQueue(); if (item.media_type === 'tv') { this.isLoadingSeasons = true; try { const res = await fetch(`/api/requests/tv/${item.tmdb_id}`); const data = await res.json(); if (data.status === 'success') { this.tvSeasons = data.seasons; if (this.tvSeasons.some(s => s.exists_locally)) this.activeItem.local_status = 2; } } catch (e) {} this.isLoadingSeasons = false; } else if (item.media_type === 'movie') { this.isCheckingLocal = true; try { const res = await fetch(`/api/requests/check/movie/${item.tmdb_id}`); const data = await res.json(); if (data.status === 'success' && data.exists) this.activeItem.local_status = 2; } catch(e) {} this.isCheckingLocal = false; } await queuePromise; this.pruneSelectedSeasons(); },
        closeModal() { this.isModalOpen = false; document.body.style.overflow = ''; },
        buildMyRequestMap(list) { const map = {}; (list || []).forEach(r => { if (r && r.tmdb_id !== undefined && r.tmdb_id !== null) map[`${r.tmdb_id}_${r.season || 0}`] = r.status; }); this.myRequestMap = map; },
        getRequestStatus(tmdbId, season = 0) { return this.myRequestMap[`${tmdbId}_${season}`]; },
        isRequestSubmitted(tmdbId, season = 0) { const status = this.getRequestStatus(tmdbId, season); return status !== undefined && status !== null && status !== 3; },
        getRequestStatusText(status) {
            if (status === 0) return '已提交申请';
            if (status === 1) return '已审批';
            if (status === 2) return '已完成';
            if (status === 4) return '管理员接单';
            if (status === 3) return '已拒绝，可重新提交';
            return '';
        },
        getRequestBadgeText(status) {
            if (status === 3) return '已拒绝';
            return this.getRequestStatusText(status);
        },
        getRequestStatusClass(status) {
            if (status === 0) return 'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300';
            if (status === 1) return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300';
            if (status === 2) return 'bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-300';
            if (status === 4) return 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300';
            if (status === 3) return 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-300';
            return 'bg-slate-100 text-slate-600 dark:bg-white/5 dark:text-zinc-400';
        },
        getSeasonRequestStatus(seasonNumber) { if (!this.activeItem) return undefined; return this.getRequestStatus(this.activeItem.tmdb_id, seasonNumber); },
        getSeasonStatusText(seasonNumber) { return this.getRequestBadgeText(this.getSeasonRequestStatus(seasonNumber)); },
        getSeasonStatusClass(seasonNumber) { return this.getRequestStatusClass(this.getSeasonRequestStatus(seasonNumber)); },
        getSeasonStatusTooltip(seasonNumber) {
            const status = this.getSeasonRequestStatus(seasonNumber);
            if (status === undefined || status === null) return '';
            if (status === 0) return '该季度已提交，等待管理员处理';
            if (status === 1) return '管理员已审批，正在安排';
            if (status === 2) return '已完成，资源可用';
            if (status === 4) return '管理员已接单，处理中';
            if (status === 3) return '该季度已被拒绝，可重新提交';
            return '';
        },
        getMovieStatusText() { return this.getRequestStatusText(this.getRequestStatus(this.activeItem?.tmdb_id, 0)); },
        isSeasonSubmitted(seasonNumber) { if (!this.activeItem) return false; return this.isRequestSubmitted(this.activeItem.tmdb_id, seasonNumber); },
        pruneSelectedSeasons() { if (this.activeItem?.media_type !== 'tv') return; const allowed = this.selectedSeasons.filter(sn => !this.isSeasonSubmitted(sn)); if (allowed.length !== this.selectedSeasons.length) this.selectedSeasons = allowed; },
        async loadQueue() { try { const res = await fetch('/api/requests/my'); const data = await res.json(); if (data.status === 'success') { this.myQueue = data.data; this.buildMyRequestMap(data.data); } } catch (e) {} },
        async loadMyFeedback() { try { const res = await fetch('/api/requests/feedback/my'); const data = await res.json(); if (data.status === 'success') this.myFeedbacks = data.data; } catch (e) {} },
        
        ensureHtml2Canvas() {
            if (window.html2canvas) return Promise.resolve();
            if (this.html2canvasPromise) return this.html2canvasPromise;
            this.html2canvasPromise = new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = 'https://cdn.bootcdn.net/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
                script.async = true;
                script.onload = () => resolve();
                script.onerror = () => reject(new Error('html2canvas load failed'));
                document.head.appendChild(script);
            });
            return this.html2canvasPromise;
        },
        async openMyPosterStudio() { this.posterStudio.open = true; document.body.style.overflow = 'hidden'; this.setMyPosterTheme('#1a1a1a', 'white', '#eab308'); this.ensureHtml2Canvas().catch(() => {}); await this.setMyPosterPeriod('month'); },
        closeMyPosterStudio() { this.posterStudio.open = false; document.body.style.overflow = ''; },
        setMyPosterTheme(bg, text, hl) { const canvas = document.getElementById('my-capture-target'); if(!canvas) return; canvas.style.setProperty('--p-theme-bg', bg); canvas.style.setProperty('--p-theme-text', text); canvas.style.setProperty('--p-theme-highlight', hl); this.posterStudio.useCoverBg = false; document.getElementById('my-poster-bg-img').style.opacity = '0'; if(text === '#333') { canvas.style.setProperty('--p-theme-pill-bg', '#e5e7eb'); canvas.style.setProperty('--p-theme-pill-text', '#1f2937'); canvas.style.setProperty('--p-theme-card', 'rgba(0,0,0,0.03)'); document.getElementById('my-poster-bg-gradient').style.background = 'transparent'; document.getElementById('my-p-footer').style.color = 'rgba(0,0,0,0.3)'; } else { canvas.style.setProperty('--p-theme-pill-bg', 'rgba(255,255,255,0.15)'); canvas.style.setProperty('--p-theme-pill-text', 'white'); canvas.style.setProperty('--p-theme-card', 'rgba(255,255,255,0.08)'); document.getElementById('my-poster-bg-gradient').style.background = 'linear-gradient(to bottom, rgba(0,0,0,0.1), rgba(0,0,0,0.5))'; document.getElementById('my-p-footer').style.color = 'rgba(255,255,255,0.4)'; } },
        toggleMyCoverBg() { this.posterStudio.useCoverBg = !this.posterStudio.useCoverBg; const bgImg = document.getElementById('my-poster-bg-img'); const canvas = document.getElementById('my-capture-target'); if(!this.posterStudio.useCoverBg) { bgImg.style.opacity = '0'; if(canvas.style.getPropertyValue('--p-theme-text') === '#333') document.getElementById('my-poster-bg-gradient').style.background = 'transparent'; else document.getElementById('my-poster-bg-gradient').style.background = 'linear-gradient(to bottom, rgba(0,0,0,0.1), rgba(0,0,0,0.5))'; } else { bgImg.style.opacity = '1'; canvas.style.setProperty('--p-theme-card', 'rgba(255,255,255,0.08)'); canvas.style.setProperty('--p-theme-pill-bg', 'rgba(255,255,255,0.15)'); canvas.style.setProperty('--p-theme-pill-text', 'white'); canvas.style.setProperty('--p-theme-text', 'white'); document.getElementById('my-poster-bg-gradient').style.background = 'linear-gradient(to bottom, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.8) 100%)'; document.getElementById('my-p-footer').style.color = 'rgba(255,255,255,0.5)'; if(this.posterStudio.top1BgBase64) bgImg.style.backgroundImage = `url('${this.posterStudio.top1BgBase64}')`; } },
        async setMyPosterPeriod(period) { this.posterStudio.period = period; const now = new Date(); const y = now.getFullYear(); const m = now.getMonth() + 1; if (period === 'year') this.posterStudio.periodLabel = `${y} 年度观影报告`; else if (period === 'month') this.posterStudio.periodLabel = `${y}年${m}月 观影报告`; else if (period === 'week') { const day = now.getDay() || 7; const start = new Date(now); start.setDate(now.getDate() - day + 1); const end = new Date(now); end.setDate(now.getDate() - day + 7); this.posterStudio.periodLabel = `${start.getMonth()+1}/${start.getDate()} - ${end.getMonth()+1}/${end.getDate()} 周报`; } else this.posterStudio.periodLabel = '历史全量 观影报告'; await this.loadMyPosterData(); },
        async loadMyPosterData() { this.posterStudio.isLoading = true; try { const avatarEl = document.getElementById('my-p-avatar'); const b64Avatar = await toBase64(`/api/proxy/user_image/${this.userId}`); if (b64Avatar && avatarEl) { avatarEl.style.backgroundImage = `url('${b64Avatar}')`; avatarEl.innerHTML = ''; } const res = await fetch(`/api/stats/poster_data?user_id=${this.userId}&period=${this.posterStudio.period}`); const json = await res.json(); const data = json.data; this.posterStudio.data = data; this.posterStudio.top1BgBase64 = null; if (data.plays > 0) { const list = data.top_list;
            this.posterStudio.rankRows = Array.from({ length: 7 }, (_, i) => ({ rank: i + 4, idx: i, item: list[i + 3] || null }));
            await this.$nextTick();
            for(let i=0; i<7; i++) { const imgEl = document.getElementById(`my-sm-img-${i}`); if(imgEl) { imgEl.removeAttribute('data-fallback-done'); imgEl.removeAttribute('src'); imgEl.style.objectFit = ""; imgEl.style.padding = ""; } }; const renderRank = async (rank, idx) => { if(list[idx]) { const realImg = document.getElementById(`my-rank${rank}-img`); if(!realImg) return; realImg.removeAttribute('data-fallback-done'); const b64 = await toBase64(`/api/proxy/smart_image?item_id=${list[idx].ItemId}&type=Primary`); if(b64) { realImg.src = b64; realImg.style.objectFit = "cover"; realImg.style.padding = "0"; if(rank === 1) { this.posterStudio.top1BgBase64 = await applyPhysicalBlur(b64); if(this.posterStudio.useCoverBg) document.getElementById('my-poster-bg-img').style.backgroundImage = `url('${this.posterStudio.top1BgBase64}')`; } } else { window.fallbackReportPoster(realImg, list[idx].ItemName); } } }; await Promise.all([renderRank(1, 0), renderRank(2, 1), renderRank(3, 2)]); const smPromises = []; const max = Math.min(list.length, 10); for(let i=3; i<max; i++) { smPromises.push((async () => { const b64 = await toBase64(`/api/proxy/smart_image?item_id=${list[i].ItemId}&type=Primary`); const imgEl = document.getElementById(`my-sm-img-${i-3}`); if(imgEl) { if(b64) { imgEl.src = b64; imgEl.style.objectFit = "cover"; } else window.fallbackReportPoster(imgEl, list[i].ItemName); } })()); } await Promise.all(smPromises); const area = document.getElementById('my-mood-area'); if(area) { area.innerHTML = ''; let html = ''; const mood = data.mood_data; if(mood) { if(mood.genres && mood.genres.length > 0) { const iconMap = {'剧情': '🎬', '喜剧': '😂', '动作': '⚔️', '科幻': '🛸', '悬疑': '🕵️‍♂️', '爱情': '❤️', '动画': '🦄', '恐怖': '👻', '犯罪': '🔪'}; let tagsHtml = ''; mood.genres.forEach(g => tagsHtml += `<div class="my-mood-tag-pill"><span>${iconMap[g]||'🏷️'}</span> <span>${g}</span></div>`); html += `<div class="my-mood-card"><div class="my-mood-title">观影基因重组</div><div class="my-mood-tags-container">${tagsHtml}</div></div>`; } if(mood.binge_day) html += `<div class="my-mood-card"><div class="my-mood-title">极度沉迷时刻</div><div class="my-mood-data-container"><div class="my-mood-data-box"><div class="my-mood-data-val">${mood.binge_day.date}</div><div class="my-mood-data-sub">这一天最疯狂</div></div><div class="my-mood-data-box"><div class="my-mood-data-val">${mood.binge_day.hours} H</div><div class="my-mood-data-sub">一口气看了</div></div></div></div>`; if(mood.late_night) html += `<div class="my-mood-card"><div class="my-mood-title">深夜刺客出没</div><div class="my-mood-data-container"><div class="my-mood-data-box" style="flex:1;"><div class="my-mood-data-val">凌晨 ${mood.late_night.time}</div><div class="my-mood-data-sub">正在看: ${mood.late_night.name}</div></div></div></div>`; } area.innerHTML = html; } } this.$nextTick(() => { const wrapper = document.getElementById('my-poster-preview-area'); const scaleWrapper = document.getElementById('my-scale-wrapper'); if(wrapper && scaleWrapper) { const scale = Math.min((wrapper.clientWidth - 40) / 400, 1); scaleWrapper.style.transform = `scale(${scale})`; } }); } catch(e) {} this.posterStudio.isLoading = false; },
        async saveMyPoster() { this.posterStudio.isSaving = true; const scaleWrapper = document.getElementById('my-scale-wrapper'); const oldT = scaleWrapper.style.transform; document.getElementById('my-poster-preview-area').scrollTo(0, 0); scaleWrapper.style.transform = 'none'; await new Promise(r => setTimeout(r, 500)); try { await this.ensureHtml2Canvas(); const canvas = await html2canvas(document.getElementById('my-capture-target'), { scale: 2, useCORS: true, backgroundColor: null, scrollY: 0, scrollX: 0 }); const link = document.createElement('a'); link.download = `EmbyPulse_${this.userName}.png`; link.href = canvas.toDataURL(); link.click(); this.showToast('海报已保存！'); } catch(e) { this.showToast('生成失败', 'error'); } finally { scaleWrapper.style.transform = oldT; this.posterStudio.isSaving = false; } }
    }));
});




