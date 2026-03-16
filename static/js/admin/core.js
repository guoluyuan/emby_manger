// ==========================================
        // 全局基础逻辑 (底栏、侧栏、搜索)

        const ensureAdminChartJs = (() => {
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
        window.ensureAdminChartJs = ensureAdminChartJs;

        const __adminAssetVer = window.EP_ASSET_VER ? `?v=${window.EP_ASSET_VER}` : '';
        const __logoAppAvif = `/static/img/logo-app.avif${__adminAssetVer}`;
        const __logoAppWebp = `/static/img/logo-app.webp${__adminAssetVer}`;
        const __logoAppPng = `/static/img/logo-app.png${__adminAssetVer}`;
        window.markAdminChartReady = function(id) {
            const el = document.getElementById(id);
            if (!el) return;
            const wrap = el.closest('.chart-wrapper');
            if (wrap) wrap.classList.add('is-ready');
        };



        // ==========================================
        function toggleBottomSheet() {
            const sheet = document.getElementById('bottom-sheet'); const overlay = document.getElementById('bottom-sheet-overlay');
            if (sheet.classList.contains('translate-y-full')) {
                overlay.classList.remove('hidden'); void overlay.offsetWidth; overlay.classList.remove('opacity-0'); sheet.classList.remove('translate-y-full');
            } else {
                overlay.classList.add('opacity-0'); sheet.classList.add('translate-y-full'); setTimeout(() => overlay.classList.add('hidden'), 400);
            }
        }
        function toggleTheme() {
            const isDark = document.documentElement.classList.toggle('dark'); localStorage.theme = isDark ? 'dark' : 'light'; window.dispatchEvent(new Event('theme-changed'));
        }
        function syncThemeSwitch() {
            const isDark = document.documentElement.classList.contains('dark');
            const cb = document.getElementById('theme-toggle-cb');
            if (cb) cb.checked = isDark;
        }
        function toggleThemeSync() { toggleTheme(); syncThemeSwitch(); }
        document.addEventListener('DOMContentLoaded', syncThemeSwitch);
        
        const globalEmbyBaseUrl = (window.EP_CONFIG && window.EP_CONFIG.embyBaseUrl) || ""; const globalEmbyServerId = (window.EP_CONFIG && window.EP_CONFIG.embyServerId) || "";
        
        // ==========================================
        // 🔥 顶部工具面板逻辑 (快捷工具 / 通知 / 热播)
        // ==========================================
        let isNotifyPanelOpen = false;
        let isToolsPanelOpen = false;

        function toggleLiveDropdown() {
            const panel = document.getElementById('global-live-panel');
            if (panel.classList.contains('hidden')) {
                if(isNotifyPanelOpen) toggleNotifyDropdown();
                if(isToolsPanelOpen) toggleToolsDropdown();
                panel.classList.remove('hidden'); void panel.offsetWidth; panel.classList.remove('opacity-0', 'scale-95'); panel.classList.add('opacity-100', 'scale-100'); fetchGlobalLive(); 
            } else { panel.classList.remove('opacity-100', 'scale-100'); panel.classList.add('opacity-0', 'scale-95'); setTimeout(() => panel.classList.add('hidden'), 200); }
        }

        function toggleNotifyDropdown() {
            const panel = document.getElementById('notify-panel');
            if (isNotifyPanelOpen) { 
                panel.classList.remove('opacity-100', 'scale-100'); panel.classList.add('opacity-0', 'scale-95'); 
                setTimeout(() => panel.classList.add('hidden'), 200); isNotifyPanelOpen = false; 
            } else { 
                const livePanel = document.getElementById('global-live-panel');
                if(livePanel && !livePanel.classList.contains('hidden')) toggleLiveDropdown();
                if(isToolsPanelOpen) toggleToolsDropdown();

                panel.classList.remove('hidden'); void panel.offsetWidth; panel.classList.remove('opacity-0', 'scale-95'); panel.classList.add('opacity-100', 'scale-100'); 
                isNotifyPanelOpen = true; fetchNotifications(); 
            }
        }

        function toggleToolsDropdown() {
            const panel = document.getElementById('quick-tools-panel');
            if (isToolsPanelOpen) { 
                panel.classList.remove('opacity-100', 'scale-100'); panel.classList.add('opacity-0', 'scale-95'); 
                setTimeout(() => panel.classList.add('hidden'), 200); isToolsPanelOpen = false; 
            } else { 
                if(isNotifyPanelOpen) toggleNotifyDropdown();
                const livePanel = document.getElementById('global-live-panel');
                if(livePanel && !livePanel.classList.contains('hidden')) toggleLiveDropdown();

                panel.classList.remove('hidden'); void panel.offsetWidth; panel.classList.remove('opacity-0', 'scale-95'); panel.classList.add('opacity-100', 'scale-100'); 
                isToolsPanelOpen = true; syncThemeSwitch();
            }
        }

        document.addEventListener('click', (e) => {
            const notifyPanel = document.getElementById('notify-panel'); const notifyBell = document.querySelector('.notify-bell-btn');
            if (isNotifyPanelOpen && !notifyPanel.contains(e.target) && !notifyBell.contains(e.target)) toggleNotifyDropdown();
            
            const livePanel = document.getElementById('global-live-panel'); const liveBtn = document.getElementById('global-live-btn');
            if (livePanel && !livePanel.classList.contains('hidden') && !livePanel.contains(e.target) && !liveBtn.contains(e.target)) toggleLiveDropdown();

            const toolsPanel = document.getElementById('quick-tools-panel'); const toolsBtn = document.querySelector('.tools-menu-btn');
            if (isToolsPanelOpen && toolsPanel && toolsBtn && !toolsPanel.contains(e.target) && !toolsBtn.contains(e.target)) toggleToolsDropdown();
        });

        // ==========================================
        // 热播代码
        // ==========================================
        async function fetchGlobalLive() {
            try {
                const res = await fetch('/api/stats/live'); const json = await res.json();
                const listContainer = document.getElementById('global-live-list'); const badge = document.getElementById('global-live-badge'); const statusText = document.getElementById('global-live-status-text');
                if(json.status === 'success' && json.data.length > 0) {
                    badge.classList.remove('hidden'); statusText.innerText = `${json.data.length} 设备在线`; let html = '';
                    const formatTicks = (ticks) => {
                        if (!ticks) return '00:00'; let totalSeconds = Math.floor(ticks / 10000000); let h = Math.floor(totalSeconds / 3600); let m = Math.floor((totalSeconds % 3600) / 60); let s = totalSeconds % 60;
                        if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`; return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
                    };
                    json.data.forEach(s => {
                        const item = s.NowPlayingItem || {}; const playState = s.PlayState || {}; const imgTargetId = item.PrimaryImageItemId || item.SeriesId || item.ParentId || s.ItemId || item.Id; const jumpId = item.Id || s.ItemId; const rawTitle = item.SeriesName ? `${item.SeriesName} - ${item.Name}` : (item.Name || '未知内容'); const safeTitleForUrl = encodeURIComponent(rawTitle); const imgUrl = `/api/proxy/smart_image?item_id=${imgTargetId}&type=Primary&name=${safeTitleForUrl}`;
                        let percentage = 0; let currentStr = "00:00"; let totalStr = "00:00";
                        if (item.RunTimeTicks && item.RunTimeTicks > 0) { percentage = Math.round((playState.PositionTicks / item.RunTimeTicks) * 100); currentStr = formatTicks(playState.PositionTicks); totalStr = formatTicks(item.RunTimeTicks); }
                        html += `<div onclick="if(globalEmbyBaseUrl) window.open('${globalEmbyBaseUrl}/web/index.html#!/item?id=${jumpId}&serverId=${globalEmbyServerId}')" class="flex gap-3 p-3 hover:bg-gray-50/50 dark:hover:bg-white/5 rounded-xl transition-colors cursor-pointer group"><div class="w-12 h-16 rounded-lg overflow-hidden flex-shrink-0 bg-gray-100 dark:bg-gray-800 relative shadow-sm border border-gray-200/50 dark:border-white/5"><img src="${imgUrl}" onerror="this.src='/static/img/favicon.png'" class="w-full h-full object-cover"></div><div class="flex flex-col justify-center flex-1 min-w-0"><div class="flex items-center justify-between mb-1.5"><span class="text-[11px] font-bold text-brand-500 truncate mr-2">${s.UserName || s.User || '未知'}</span><span class="text-[10px] font-mono text-gray-500 dark:text-gray-400 font-medium shrink-0 bg-gray-100 dark:bg-apple-hoverDark px-1.5 py-0.5 rounded shadow-sm border border-transparent dark:border-white/5">${currentStr} / ${totalStr}</span></div><h4 class="text-[13px] font-bold text-gray-800 dark:text-gray-200 truncate mb-2 group-hover:text-brand-500 transition-colors">${rawTitle}</h4><div class="flex items-center gap-2"><div class="flex-1 bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 overflow-hidden shadow-inner"><div class="bg-brand-500 h-1.5 rounded-full transition-all duration-1000" style="width: ${percentage}%"></div></div></div></div></div>`;
                    });
                    listContainer.innerHTML = html;
                } else { badge.classList.add('hidden'); statusText.innerText = `空闲中`; listContainer.innerHTML = `<div class="py-12 text-center text-xs text-gray-400 font-medium"><i class="fa-solid fa-satellite text-3xl mb-3 opacity-20 block"></i>当前暂无播放</div>`; }
            } catch(e) { console.error(e); }
        }

        // ==========================================
        // 通知代码
        // ==========================================
        let lastNotifyIds = [];
        async function fetchNotifications() { try { const res = await fetch('/api/notifications'); const json = await res.json(); if (json.success) { updateNotifyUI(json.unread_count, json.items); } } catch(e) { } }
        function updateNotifyUI(count, items) {
            const badgeD = document.getElementById('notify-badge-desktop'); const badgeM = document.getElementById('notify-badge-mobile'); const countText = document.getElementById('notify-count-text');
            if (count > 0) { if(badgeD) badgeD.classList.remove('hidden'); if(badgeM) badgeM.classList.remove('hidden'); countText.textContent = count > 99 ? '99+' : count; countText.classList.remove('hidden'); } 
            else { if(badgeD) badgeD.classList.add('hidden'); if(badgeM) badgeM.classList.add('hidden'); countText.classList.add('hidden'); }
            let html = ''; let newIds = [];
            if (!items || items.length === 0) { html = '<div class="text-center py-10 text-gray-400 text-xs font-medium">📭 暂无通知，一切安好</div>'; } 
            else { 
                items.forEach(item => { 
                    newIds.push(item.id); const isUnread = item.is_read === 0; const bgClass = isUnread ? 'bg-gray-50/80 dark:bg-white/5' : 'hover:bg-gray-50/50 dark:hover:bg-white/5 opacity-75'; const dot = isUnread ? '<span class="inline-block w-2 h-2 bg-red-500 rounded-full mr-2 shrink-0"></span>' : '';
                    let iconHtml = '<i class="fa-solid fa-bell text-gray-400 dark:text-gray-300"></i>'; let iconBg = 'bg-gray-100 dark:bg-gray-800';
                    if (item.type === 'risk') { iconHtml = '<i class="fa-solid fa-shield-halved text-red-500"></i>'; iconBg = 'bg-red-50 dark:bg-red-500/10'; } else if (item.type === 'request') { iconHtml = '<i class="fa-solid fa-clapperboard text-brand-500"></i>'; iconBg = 'bg-brand-50 dark:bg-brand-500/10'; } else if (item.type === 'system') { iconHtml = '<i class="fa-solid fa-gear text-indigo-500"></i>'; iconBg = 'bg-indigo-50 dark:bg-indigo-500/10'; }
                    const timeStr = item.created_at ? item.created_at.substring(5, 16).replace('-', '/') : ''; 
                    html += `<div onclick="handleNotiClick(${item.id}, '${item.action_url || ''}')" class="flex gap-3 p-2.5 ${bgClass} rounded-xl transition cursor-pointer group"><div class="w-10 h-10 rounded-full ${iconBg} flex justify-center items-center shrink-0">${iconHtml}</div><div class="flex-1 min-w-0 flex flex-col justify-center"><div class="text-[13px] font-medium text-gray-800 dark:text-gray-200 truncate flex items-center">${dot}${item.title}</div><div class="text-[11px] text-gray-500 mt-1 line-clamp-1">${item.message}</div></div><div class="text-[10px] text-gray-400 mt-1 shrink-0 whitespace-nowrap">${timeStr}</div></div>`; 
                }); 
            }
            document.getElementById('notify-list').innerHTML = html; 
            if (lastNotifyIds.length > 0) { items.forEach(item => { if (!lastNotifyIds.includes(item.id) && item.is_read === 0) { showGlobalToast(item); } }); } lastNotifyIds = newIds;
        }

        function showGlobalToast(item) {
            const container = document.getElementById('global-toast-container'); const toast = document.createElement('div');
            toast.className = "flex items-center gap-3 bg-white/95 dark:bg-apple-cardDark/95 backdrop-blur-2xl border border-gray-100 dark:border-white/5 p-3 rounded-2xl shadow-xl transform translate-x-full opacity-0 transition-all duration-400 pointer-events-auto w-[280px] md:w-80 cursor-pointer";
            toast.onclick = () => { handleNotiClick(item.id, item.action_url); };
            let iconHtml = '<i class="fa-solid fa-bell text-white"></i>'; let bgClass = 'bg-brand-500';
            if (item.type === 'risk') bgClass = 'bg-red-500'; else if (item.type === 'system') bgClass = 'bg-indigo-500';
            toast.innerHTML = `<div class="w-10 h-10 rounded-full ${bgClass} flex items-center justify-center shrink-0 shadow-inner">${iconHtml}</div><div class="flex-1 min-w-0"><div class="text-[10px] font-bold text-gray-500 dark:text-gray-400 mb-0.5">新通知</div><div class="text-[13px] font-medium text-gray-800 dark:text-gray-200 truncate">${item.title}</div></div>`;
            container.appendChild(toast); setTimeout(() => { toast.classList.remove('translate-x-full', 'opacity-0'); toast.classList.add('translate-x-0', 'opacity-100'); }, 50); 
            setTimeout(() => { if(toast.parentElement) { toast.classList.remove('translate-x-0', 'opacity-100'); toast.classList.add('translate-x-full', 'opacity-0'); setTimeout(() => toast.remove(), 400); } }, 6000);
        }

        function handleNotiClick(id, url) { fetch('/api/notifications/read', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id}) }).then(() => { if (url && url !== 'undefined') window.location.href = url; else fetchNotifications(); }); }
        function markAllAsRead() { fetch('/api/notifications/read', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({}) }).then(() => fetchNotifications()); }
        function clearAllNotifications() { if(!confirm("⚠️ 确定要清空所有系统通知吗？此操作不可恢复。")) return; fetch('/api/notifications/clear', { method: 'DELETE' }).then(res => res.json()).then(data => { if (data.success) { fetchNotifications(); if(!document.getElementById('notify-history-modal').classList.contains('hidden')) { fetchHistoryNotifications(); } } }); }

        function openHistoryModal() { if(isNotifyPanelOpen) toggleNotifyDropdown(); const modal = document.getElementById('notify-history-modal'); const container = document.getElementById('notify-history-container'); modal.classList.remove('hidden'); void modal.offsetWidth; modal.classList.remove('opacity-0'); container.classList.remove('scale-95'); fetchHistoryNotifications(); }
        function closeHistoryModal() { const modal = document.getElementById('notify-history-modal'); const container = document.getElementById('notify-history-container'); modal.classList.add('opacity-0'); container.classList.add('scale-95'); setTimeout(() => modal.classList.add('hidden'), 200); }

        function fetchHistoryNotifications() {
            fetch('/api/notifications?limit=100&history=true').then(res => res.json()).then(data => {
                const list = document.getElementById('notify-history-list');
                if (data.success && data.items.length > 0) {
                    let html = '';
                    data.items.forEach(item => {
                        const isUnread = item.is_read === 0; const bgClass = isUnread ? 'bg-white dark:bg-apple-cardDark shadow-sm' : 'hover:bg-gray-50/80 dark:hover:bg-white/5 opacity-80'; const dot = isUnread ? '<span class="inline-block w-2 h-2 bg-red-500 rounded-full mr-2 shrink-0"></span>' : '';
                        let iconHtml = '<i class="fa-solid fa-bell text-gray-400 dark:text-gray-300"></i>'; let iconBg = 'bg-gray-100 dark:bg-gray-800';
                        if (item.type === 'risk') { iconHtml = '<i class="fa-solid fa-shield-halved text-red-500"></i>'; iconBg = 'bg-red-50 dark:bg-red-500/10'; } else if (item.type === 'request') { iconHtml = '<i class="fa-solid fa-clapperboard text-brand-500"></i>'; iconBg = 'bg-brand-50 dark:bg-brand-500/10'; } else if (item.type === 'system') { iconHtml = '<i class="fa-solid fa-gear text-indigo-500"></i>'; iconBg = 'bg-indigo-50 dark:bg-indigo-500/10'; }
                        html += `<div class="flex gap-4 p-4 rounded-[16px] transition cursor-default border border-transparent dark:border-white/5 ${bgClass}"><div class="w-12 h-12 rounded-full ${iconBg} flex justify-center items-center shrink-0">${iconHtml}</div><div class="flex-1 min-w-0 flex flex-col justify-center"><div class="text-[14px] font-bold text-gray-800 dark:text-gray-200 flex items-center">${dot}${item.title}</div><div class="text-[12px] text-gray-500 mt-1">${item.message}</div></div><div class="flex flex-col items-end justify-between shrink-0 pl-4"><div class="text-[11px] font-mono text-gray-400 mb-2">${item.created_at}</div>${item.action_url ? `<a href="${item.action_url}" class="text-[11px] text-brand-500 hover:text-brand-600 font-medium bg-brand-50 hover:bg-brand-100 dark:bg-brand-500/10 dark:hover:bg-brand-500/20 px-3 py-1.5 rounded-lg transition-colors">立即处理</a>` : ''}</div></div>`;
                    }); list.innerHTML = html;
                } else { list.innerHTML = '<div class="text-center py-20 text-gray-400 text-sm"><i class="fa-regular fa-folder-open text-4xl mb-3 block opacity-20"></i>暂无历史记录</div>'; }
            });
        }

        // ==========================================
        // 全局更新提示
        // ==========================================
        let __updateCheckDisabled = null;
        let __updateHasUpdate = false;
        let __updateChecking = false;
        let __updateLastCheckedAt = 0;
        let __updateToastShown = false;

        function handleUpdateClick() {
            window.location.href = '/settings#docker-update';
        }

        function setGlobalUpdateUI(state) {
            const btn = document.getElementById('global-update-btn');
            const badge = document.getElementById('global-update-badge');
            if (!btn || !badge) return;

            if (state === 'disabled') {
                btn.classList.add('opacity-50');
                badge.classList.add('hidden');
                btn.title = '已禁用自动检查更新';
                return;
            }

            btn.classList.remove('opacity-50');

            if (state === 'available') {
                badge.classList.remove('hidden');
                btn.title = '检测到新版本，点击前往更新';
            } else if (state === 'checking') {
                badge.classList.add('hidden');
                btn.title = '正在检测更新';
            } else {
                badge.classList.add('hidden');
                btn.title = '已是最新版本';
            }
        }

        function showUpdateToast() {
            if (__updateToastShown) return;
            const container = document.getElementById('global-toast-container');
            if (!container) return;
            const toast = document.createElement('div');
            toast.className = "flex items-center gap-3 bg-white/95 dark:bg-apple-cardDark/95 backdrop-blur-2xl border border-amber-200/70 dark:border-amber-500/20 p-3 rounded-2xl shadow-xl transform translate-x-full opacity-0 transition-all duration-400 pointer-events-auto w-[280px] md:w-80 cursor-pointer";
            toast.onclick = handleUpdateClick;
            toast.innerHTML = `<div class="w-10 h-10 rounded-full bg-amber-500 flex items-center justify-center shrink-0 shadow-inner"><i class="fa-solid fa-arrow-up-right-dots text-white"></i></div><div class="flex-1 min-w-0"><div class="text-[10px] font-bold text-amber-600 dark:text-amber-400 mb-0.5">版本更新</div><div class="text-[13px] font-medium text-gray-800 dark:text-gray-200 truncate">检测到新镜像，点击更新</div></div>`;
            container.appendChild(toast);
            setTimeout(() => { toast.classList.remove('translate-x-full', 'opacity-0'); toast.classList.add('translate-x-0', 'opacity-100'); }, 50);
            setTimeout(() => { if (toast.parentElement) { toast.classList.remove('translate-x-0', 'opacity-100'); toast.classList.add('translate-x-full', 'opacity-0'); setTimeout(() => toast.remove(), 400); } }, 6000);
            __updateToastShown = true;
        }

        async function loadUpdateCheckSetting() {
            try {
                const res = await fetch('/api/settings');
                const json = await res.json();
                if (json.status === 'success') {
                    __updateCheckDisabled = !!(json.data && json.data.disable_update_check);
                } else {
                    __updateCheckDisabled = true;
                }
            } catch (e) {
                __updateCheckDisabled = true;
            }
            return __updateCheckDisabled;
        }

        window.setUpdateCheckDisabled = function(value) {
            __updateCheckDisabled = !!value;
            if (__updateCheckDisabled) {
                setGlobalUpdateUI('disabled');
            }
        };

        async function checkGlobalDockerUpdate(force = false) {
            if (__updateChecking) return;
            if (__updateCheckDisabled === null) await loadUpdateCheckSetting();
            if (__updateCheckDisabled && !force) {
                setGlobalUpdateUI('disabled');
                return;
            }

            const now = Date.now();
            if (!force && __updateLastCheckedAt && (now - __updateLastCheckedAt) < 60 * 60 * 1000) {
                return;
            }

            __updateChecking = true;
            setGlobalUpdateUI('checking');
            try {
                const res = await fetch('/api/system/docker_update/status');
                const json = await res.json();
                if (json.status === 'success') {
                    __updateHasUpdate = !!(json.data && json.data.available);
                    if (__updateHasUpdate) {
                        setGlobalUpdateUI('available');
                        showUpdateToast();
                    } else {
                        setGlobalUpdateUI('latest');
                    }
                } else {
                    setGlobalUpdateUI('latest');
                }
            } catch (e) {
                setGlobalUpdateUI('latest');
            } finally {
                __updateChecking = false;
                __updateLastCheckedAt = Date.now();
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            setTimeout(fetchNotifications, 1000);
            setInterval(fetchNotifications, 30000);
            setTimeout(fetchGlobalLive, 2000);
            setInterval(fetchGlobalLive, 10000);
            setTimeout(() => checkGlobalDockerUpdate(false), 3500);
            setInterval(() => checkGlobalDockerUpdate(false), 60 * 60 * 1000);
        });
        
        // ==========================================
        // 全局检索
        // ==========================================
        const searchModal = document.getElementById('ep-global-search'); const searchContainer = document.getElementById('ep-search-container'); const searchInput = document.getElementById('ep-search-input'); const searchResults = document.getElementById('ep-search-results'); const searchLoading = document.getElementById('ep-search-loading'); const searchEmpty = document.getElementById('ep-search-empty'); let searchTimeout = null;
        function openGlobalSearch() { searchModal.classList.remove('hidden'); void searchModal.offsetWidth; searchModal.classList.remove('opacity-0'); searchContainer.classList.remove('scale-95'); searchInput.value = ''; searchResults.innerHTML = ''; searchResults.appendChild(searchEmpty); searchEmpty.style.display = 'block'; setTimeout(() => searchInput.focus(), 100); }
        function closeGlobalSearch() { searchModal.classList.add('opacity-0'); searchContainer.classList.add('scale-95'); setTimeout(() => { searchModal.classList.add('hidden'); searchInput.blur(); }, 200); }
        document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openGlobalSearch(); } if (e.key === 'Escape' && !searchModal.classList.contains('hidden')) { closeGlobalSearch(); } });
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout); const query = e.target.value.trim(); if (!query) { searchResults.innerHTML = ''; searchResults.appendChild(searchEmpty); searchEmpty.style.display = 'block'; return; }
            searchLoading.classList.remove('hidden'); searchEmpty.style.display = 'none';
            searchTimeout = setTimeout(async () => { try { const res = await fetch(`/api/library/search?query=${encodeURIComponent(query)}`); const data = await res.json(); renderSearchResults(data.data || []); } catch (err) { searchResults.innerHTML = '<div class="text-center py-10 text-red-500 font-medium">搜索失败</div>'; } finally { searchLoading.classList.add('hidden'); } }, 500);
        });
        function renderSearchResults(items) {
            if (items.length === 0) { searchResults.innerHTML = '<div class="text-center py-12 text-gray-400 font-medium">未找到资源</div>'; return; } let html = '';
            items.forEach(item => { 
                let cleanOverview = item.overview ? item.overview.replace(/<[^>]*>?/gm, '') : '暂无剧集简介';
                let badgesHtml = ''; if (item.badges && item.badges.length > 0) { badgesHtml = item.badges.map(b => { const colorClass = b.color ? b.color : 'bg-gray-100 text-gray-600 dark:bg-[#3A3A3C] dark:text-gray-300 border border-gray-200/50 dark:border-white/5'; return `<span class="px-2 py-0.5 text-[10px] font-bold rounded-md ${colorClass} shadow-sm whitespace-nowrap">${b.text}</span>`; }).join(''); }
                const yearText = item.year ? `<span class="text-[12px] font-mono font-bold text-gray-400 ml-2 shrink-0">${item.year}</span>` : '';
                html += `<a href="${item.emby_url}" target="_blank" class="flex gap-4 p-3 mb-2 bg-transparent hover:bg-gray-50 dark:hover:bg-black/20 rounded-[16px] cursor-pointer transition-all group border border-transparent hover:border-gray-200/60 dark:hover:border-white/5 hover:shadow-sm"><img src="${item.poster}" onerror="if(!this.dataset.fallback){this.dataset.fallback='avif';this.src='${__logoAppAvif}';} else if(this.dataset.fallback==='avif'){this.dataset.fallback='webp';this.src='${__logoAppWebp}';} else if(this.dataset.fallback==='webp'){this.dataset.fallback='png';this.src='${__logoAppPng}';} else {this.onerror=null;}" class="w-16 h-24 object-cover rounded-[10px] bg-gray-100 dark:bg-[#2C2C2E] shrink-0 border border-gray-200/50 dark:border-white/5 shadow-sm group-hover:scale-105 transition-transform"><div class="flex-1 min-w-0 flex flex-col justify-center overflow-hidden"><div class="flex items-center mb-1.5 overflow-hidden"><h4 class="font-bold text-[15px] text-gray-900 dark:text-white truncate group-hover:text-brand-500 transition-colors">${item.name}</h4>${yearText}</div><div class="flex flex-wrap gap-1.5 mb-1.5">${badgesHtml}</div><p class="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2 leading-relaxed break-all overflow-wrap-anywhere">${cleanOverview}</p></div></a>`; 
            });
            searchResults.innerHTML = html;
        }

        const NAV_MAP = { 'requests_admin': { icon: 'fa-clapperboard', text: '工单', url: '/requests_admin' }, 'users': { icon: 'fa-users-gear', text: '用户', url: '/users_manage' }, 'details': { icon: 'fa-magnifying-glass-chart', text: '洞察', url: '/details' }, 'insight': { icon: 'fa-chart-pie', text: '全景', url: '/insight' }, 'gaps': { icon: 'fa-puzzle-piece', text: '缺集', url: '/gaps' }, 'dedupe': { icon: 'fa-clone', text: '去重', url: '/dedupe' }, 'content': { icon: 'fa-film', text: '排行', url: '/content' }, 'history': { icon: 'fa-clock-rotate-left', text: '历史', url: '/history' }, 'calendar': { icon: 'fa-calendar-days', text: '日历', url: '/calendar' }, 'bot': { icon: 'fa-robot', text: '机器', url: '/bot' }, 'clients': { icon: 'fa-desktop', text: '管控', url: '/clients' }, 'risk': { icon: 'fa-shield-halved', text: '风控', url: '/risk' }, 'report': { icon: 'fa-wand-magic-sparkles', text: '工坊', url: '/report' }, 'tasks': { icon: 'fa-bolt', text: '任务', url: '/tasks' }, 'settings': { icon: 'fa-gear', text: '设置', url: '/settings' }, 'about': { icon: 'fa-circle-info', text: '关于', url: '/about' } };
        function renderMobileDynamicNav() {
            if(window.innerWidth > 768) return; const savedNavs = JSON.parse(localStorage.getItem('ep_mobile_nav') || '["requests_admin", "users"]'); const container = document.getElementById('mobile-dynamic-nav-container'); if(!container) return;
            const activePath = window.location.pathname; let html = '';
            savedNavs.forEach(key => { const item = NAV_MAP[key] || NAV_MAP['requests_admin']; const isActive = activePath.includes(item.url) ? 'text-brand-500' : 'text-gray-500 dark:text-gray-400'; const badgeHtml = key === 'requests_admin' ? `<span id="notify-badge-mobile" class="hidden absolute top-1 right-1/4 h-2 w-2 bg-red-500 rounded-full border border-white dark:border-apple-cardDark animate-pulse"></span>` : ''; html += `<a href="${item.url}" class="flex flex-col items-center justify-center flex-1 py-1.5 transition-colors relative ${isActive}"><i class="fa-solid ${item.icon} text-[17px] mb-0.5"></i><span class="text-[10px] font-medium">${item.text}</span>${badgeHtml}</a>`; });
            container.innerHTML = html; if(savedNavs.includes('requests_admin') && typeof fetchNotifications === 'function') fetchNotifications();
        }

        document.addEventListener('DOMContentLoaded', () => {
            renderMobileDynamicNav(); 
            const sheetContent = document.querySelector('#bottom-sheet #sheet-scroll-content');
            if(sheetContent) { 
                const configBtnHtml = `<button onclick="openNavConfigModal()" class="w-full flex items-center justify-center gap-2 py-3 bg-white/60 dark:bg-apple-cardDark/60 backdrop-blur-md text-gray-600 dark:text-gray-300 rounded-2xl font-bold text-[13px] shadow-sm mt-4 mb-2 border border-white/50 dark:border-white/10 active:scale-95 transition-transform"><i class="fa-solid fa-pen-to-square"></i> 自定义底部导航</button>`; 
                const logoutBtn = document.getElementById('mobile-logout-btn'); 
                if(logoutBtn) logoutBtn.insertAdjacentHTML('beforebegin', configBtnHtml); else sheetContent.insertAdjacentHTML('beforeend', configBtnHtml); 
            }
            const modalHtml = `<div id="nav-config-modal" class="fixed inset-0 z-[100] hidden items-center justify-center bg-black/40 backdrop-blur-sm opacity-0 transition-opacity duration-300 px-4"><div class="bg-apple-bgLight dark:bg-apple-cardDark w-full max-w-sm rounded-[28px] shadow-2xl p-6 transform scale-95 transition-transform duration-300 border border-white/50 dark:border-white/5"><div class="text-center mb-6"><h3 class="text-lg font-bold text-gray-900 dark:text-white">自定义底部导航</h3><p class="text-xs text-gray-500 mt-1">请选择 2 个最常用的功能</p></div><div class="space-y-4"><div><label class="text-[11px] font-bold text-gray-500 ml-1">左侧快捷按键</label><select id="nav-select-1" class="w-full mt-1.5 bg-white dark:bg-[#2C2C2E] text-gray-800 dark:text-gray-200 p-3 rounded-xl border-none focus:ring-2 focus:ring-brand-500 text-[13px] font-medium outline-none shadow-sm"></select></div><div><label class="text-[11px] font-bold text-gray-500 ml-1">右侧快捷按键</label><select id="nav-select-2" class="w-full mt-1.5 bg-white dark:bg-[#2C2C2E] text-gray-800 dark:text-gray-200 p-3 rounded-xl border-none focus:ring-2 focus:ring-brand-500 text-[13px] font-medium outline-none shadow-sm"></select></div></div><div class="flex gap-3 mt-8"><button onclick="closeNavConfigModal()" class="flex-1 py-3 bg-white dark:bg-[#2C2C2E] text-gray-600 dark:text-gray-300 rounded-[14px] font-bold text-[13px] transition-colors shadow-sm">取消</button><button onclick="saveNavConfig()" class="flex-1 py-3 bg-brand-500 text-white rounded-[14px] font-bold text-[13px] shadow-md transition-colors active:scale-95">保存配置</button></div></div></div>`;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            
            const sidebarNav = document.getElementById('sidebar-nav');
            if (sidebarNav) {
                const activeLink = sidebarNav.querySelector('.text-brand-500');
                if (activeLink) {
                    const navRect = sidebarNav.getBoundingClientRect(); const linkRect = activeLink.getBoundingClientRect();
                    if (linkRect.top < navRect.top || linkRect.bottom > navRect.bottom) { activeLink.scrollIntoView({ behavior: 'instant', block: 'center' }); }
                }
            }
        });

        function openNavConfigModal() { toggleBottomSheet(); const modal = document.getElementById('nav-config-modal'); const s1 = document.getElementById('nav-select-1'); const s2 = document.getElementById('nav-select-2'); let options = ''; for(let key in NAV_MAP) { options += `<option value="${key}">${NAV_MAP[key].text}</option>`; } s1.innerHTML = options; s2.innerHTML = options; const savedNavs = JSON.parse(localStorage.getItem('ep_mobile_nav') || '["requests_admin", "users"]'); s1.value = savedNavs[0] || 'requests_admin'; s2.value = savedNavs[1] || 'users'; modal.classList.remove('hidden'); void modal.offsetWidth; modal.classList.remove('opacity-0'); modal.querySelector('div').classList.remove('scale-95'); }
        function closeNavConfigModal() { const modal = document.getElementById('nav-config-modal'); modal.classList.add('opacity-0'); modal.querySelector('div').classList.add('scale-95'); setTimeout(() => modal.classList.add('hidden'), 300); }
        function saveNavConfig() { localStorage.setItem('ep_mobile_nav', JSON.stringify([document.getElementById('nav-select-1').value, document.getElementById('nav-select-2').value])); renderMobileDynamicNav(); closeNavConfigModal(); }

        // ==========================================
        // 🔥 控制中心附属功能 (降噪、诊断、日志)
        // ==========================================
        
        // 1. 通知降噪管理
        async function openNotifyMuteModal() {
            const sheet = document.getElementById('bottom-sheet'); if (sheet && !sheet.classList.contains('translate-y-full')) { toggleBottomSheet(); }
            const modal = document.getElementById('notify-mute-modal'); const container = modal.querySelector('div:nth-child(2)');
            modal.classList.remove('hidden'); void modal.offsetWidth; modal.classList.remove('opacity-0'); container.classList.remove('scale-95');
            try {
                const [usersRes, mutesRes] = await Promise.all([ fetch('/api/notify_rules/users').then(r => r.json()), fetch('/api/notify_rules/mutes').then(r => r.json()) ]);
                if(usersRes.success && mutesRes.success) {
                    const users = usersRes.data; const mutedPlay = new Set(mutesRes.data.playback || []); const mutedLogin = new Set(mutesRes.data.login || []);
                    const buildHtml = (muteSet) => users.map(u => `<label class="flex items-center gap-2.5 p-2.5 rounded-lg border border-gray-200/50 dark:border-white/10 bg-white dark:bg-[#2C2C2E] cursor-pointer hover:bg-gray-50 dark:hover:bg-white/5 transition-colors"><input type="checkbox" value="${u.id}" class="ep-checkbox text-brand-500 shrink-0" ${muteSet.has(u.id) ? 'checked' : ''}><span class="text-[13px] font-bold text-gray-700 dark:text-gray-300 truncate flex-1">${u.name}</span></label>`).join('');
                    document.getElementById('mute-list-playback').innerHTML = buildHtml(mutedPlay); document.getElementById('mute-list-login').innerHTML = buildHtml(mutedLogin);
                }
            } catch(e) { document.getElementById('mute-list-playback').innerHTML = '<span class="text-red-500 text-xs">拉取数据失败，请确保后台 API 已生效</span>'; }
        }

        function closeNotifyMuteModal() {
            const modal = document.getElementById('notify-mute-modal'); const container = modal.querySelector('div:nth-child(2)');
            modal.classList.add('opacity-0'); container.classList.add('scale-95'); setTimeout(() => modal.classList.add('hidden'), 200);
        }

        async function saveNotifyMutes() {
            const getChecked = (containerId) => Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map(cb => cb.value);
            const payload = { playback: getChecked('mute-list-playback'), login: getChecked('mute-list-login') };
            try {
                const res = await fetch('/api/notify_rules/mutes', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); const data = await res.json();
                const toastContainer = document.getElementById('global-toast-container'); const toast = document.createElement('div');
                toast.className = `flex items-center gap-3 bg-white/95 dark:bg-apple-cardDark/95 backdrop-blur-2xl border border-gray-100 dark:border-white/5 p-3 rounded-2xl shadow-xl transform translate-x-full opacity-0 transition-all duration-400 pointer-events-auto w-[280px] md:w-80`;
                toast.innerHTML = `<div class="w-10 h-10 rounded-full ${data.success ? 'bg-brand-500' : 'bg-red-500'} flex items-center justify-center shrink-0 shadow-inner"><i class="fa-solid ${data.success ? 'fa-check' : 'fa-xmark'} text-white"></i></div><div class="flex-1 min-w-0"><div class="text-[10px] font-bold text-gray-500 dark:text-gray-400 mb-0.5">系统提示</div><div class="text-[13px] font-medium text-gray-800 dark:text-gray-200 truncate">${data.msg}</div></div>`;
                toastContainer.appendChild(toast); setTimeout(() => { toast.classList.remove('translate-x-full', 'opacity-0'); toast.classList.add('translate-x-0', 'opacity-100'); }, 50);
                setTimeout(() => { toast.classList.remove('translate-x-0', 'opacity-100'); toast.classList.add('translate-x-full', 'opacity-0'); setTimeout(() => toast.remove(), 400); }, 3000);
                if(data.success) closeNotifyMuteModal();
            } catch(e) { alert('保存失败！网络异常'); }
        }

        // 2. 网络探针
        function openNetworkCheckModal() {
            const modal = document.getElementById('network-check-modal'); const container = modal.querySelector('div:nth-child(2)');
            modal.classList.remove('hidden'); void modal.offsetWidth; modal.classList.remove('opacity-0'); container.classList.remove('scale-95');
            document.getElementById('network-results').classList.add('hidden');
        }
        function closeNetworkCheckModal() {
            const modal = document.getElementById('network-check-modal'); const container = modal.querySelector('div:nth-child(2)');
            modal.classList.add('opacity-0'); container.classList.add('scale-95'); setTimeout(() => modal.classList.add('hidden'), 200);
        }
        async function runNetworkCheck() {
            document.getElementById('network-results').classList.remove('hidden');
            const tg = document.getElementById('res-tg'); const tmdb = document.getElementById('res-tmdb'); const wh = document.getElementById('res-webhook');
            tg.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 探测中...'; tg.className = "text-gray-400 font-mono text-[12px] font-medium";
            tmdb.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 探测中...'; tmdb.className = "text-gray-400 font-mono text-[12px] font-medium";
            wh.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 校验中...'; wh.className = "text-gray-400 font-mono text-[12px] font-medium";
            
            try {
                const res = await fetch('/api/system/network_check');
                const json = await res.json();
                if(json.success) {
                    const d = json.data;
                    if(d.tg.ok) { tg.innerHTML = `🟢 连通正常 (${d.tg.ping}ms)`; tg.className = "text-green-500 font-mono text-[12px] font-bold"; }
                    else { tg.innerHTML = `🔴 无法连接 (超时)`; tg.className = "text-red-500 font-mono text-[12px] font-bold"; }
                    
                    if(d.tmdb.ok) { tmdb.innerHTML = `🟢 连通正常 (${d.tmdb.ping}ms)`; tmdb.className = "text-green-500 font-mono text-[12px] font-bold"; }
                    else { tmdb.innerHTML = `🔴 无法连接 (超时)`; tmdb.className = "text-red-500 font-mono text-[12px] font-bold"; }
                    
                    wh.innerHTML = `✅ 距上次接收: ${d.webhook.last_active}`; wh.className = "text-brand-500 font-mono text-[12px] font-bold";
                }
            } catch(e) {
                tg.innerHTML = `🔴 检测失败`; tg.className = "text-red-500 font-mono text-[12px] font-bold";
                tmdb.innerHTML = `🔴 检测失败`; tmdb.className = "text-red-500 font-mono text-[12px] font-bold";
                wh.innerHTML = `🔴 检测失败`; wh.className = "text-red-500 font-mono text-[12px] font-bold";
            }
        }

        // 3. 运行日志控制台 (从内存读取并自适应暗/亮色)
        let logInterval = null;
        function openLogModal() {
            const modal = document.getElementById('sys-log-modal'); const container = modal.querySelector('div:nth-child(2)');
            modal.classList.remove('hidden'); void modal.offsetWidth; modal.classList.remove('opacity-0'); container.classList.remove('scale-95');
            fetchLogs();
            logInterval = setInterval(fetchLogs, 3000);
        }
        function closeLogModal() {
            const modal = document.getElementById('sys-log-modal'); const container = modal.querySelector('div:nth-child(2)');
            modal.classList.add('opacity-0'); container.classList.add('scale-95'); setTimeout(() => modal.classList.add('hidden'), 200);
            if(logInterval) clearInterval(logInterval);
        }
        async function fetchLogs() {
            try {
                const res = await fetch('/api/system/logs');
                const json = await res.json();
                const term = document.getElementById('log-terminal');
                
                const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 50;
                
                if(json.success && json.data) {
                    term.innerHTML = json.data.replace(/</g, "&lt;").replace(/>/g, "&gt;") + "\n<span class='animate-pulse'>_</span>";
                }
                
                if(isScrolledToBottom) {
                    term.scrollTop = term.scrollHeight;
                }
            } catch(e) {}
        }
        async function toggleDebugMode() {
            const isDebug = document.getElementById('debug-toggle-cb').checked;
            const term = document.getElementById('log-terminal');
            try {
                await fetch('/api/system/debug', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({enable: isDebug})
                });
                if (isDebug) term.innerHTML += "\n[SYSTEM] ======= DEBUG 模式已动态开启 =======\n<span class='animate-pulse'>_</span>";
                else term.innerHTML += "\n[SYSTEM] ======= 恢复 INFO 日志级别 =======\n<span class='animate-pulse'>_</span>";
                term.scrollTop = term.scrollHeight;
                fetchLogs();
            } catch(e) {}
        }

    

        (() => {
            let csrfToken = null;
            const _fetch = window.fetch.bind(window);
            const isSameOrigin = (url) => {
                try {
                    if (url instanceof Request) url = url.url;
                    if (typeof url !== 'string') return true;
                    if (url.startsWith('/')) return true;
                    return url.startsWith(window.location.origin);
                } catch {
                    return true;
                }
            };
            const ensureCsrf = async () => {
                if (csrfToken) return csrfToken;
                try {
                    const res = await _fetch('/api/csrf');
                    const json = await res.json();
                    if (json.status === 'success' && json.token) csrfToken = json.token;
                } catch {}
                return csrfToken;
            };
            window.fetch = async (input, init = {}) => {
                const method = (init.method || (input instanceof Request ? input.method : 'GET') || 'GET').toUpperCase();
                if (['POST','PUT','PATCH','DELETE'].includes(method) && isSameOrigin(input)) {
                    await ensureCsrf();
                    if (csrfToken) {
                        const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined) || {});
                        if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', csrfToken);
                        init.headers = headers;
                    }
                }
                return _fetch(input, init);
            };
        })();
    
