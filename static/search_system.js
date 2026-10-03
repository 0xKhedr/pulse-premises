document.addEventListener("DOMContentLoaded", function () {
    const style = document.createElement('style');
    style.innerHTML = `
        .sb-search-backdrop {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(15, 23, 42, 0.4);
            backdrop-filter: blur(4px);
            z-index: 12000;
            display: none;
            align-items: flex-start;
            justify-content: center;
            padding-top: 10vh;
        }

        .sb-search-modal {
            background: white;
            width: 500px;
            max-width: 90%;
            border-radius: 12px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.2);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            border: 1px solid #e2e8f0;
        }

        .sb-search-input-wrapper {
            display: flex;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid #e2e8f0;
        }
        
        .sb-search-input-wrapper span {
            color: #94a3b8;
            font-size: 20px;
            margin-right: 12px;
        }

        .sb-search-input {
            border: none;
            font-size: 18px;
            width: 100%;
            outline: none;
            color: #1e293b;
        }

        .sb-search-input::placeholder {
            color: #cbd5e1;
        }

        .sb-search-results {
            max-height: 400px;
            overflow-y: auto;
            padding: 10px;
        }

        .sb-search-item {
            display: flex;
            flex-direction: column;
            padding: 12px 16px;
            border-radius: 8px;
            text-decoration: none;
            color: #1e293b;
            cursor: pointer;
        }

        .sb-search-item:hover, .sb-search-item.active {
            background: #f1f5f9;
        }

        .sb-search-item-title {
            font-weight: 600;
            font-size: 15px;
            color: #0f172a;
        }

        .sb-search-item-desc {
            font-size: 13px;
            color: #64748b;
            margin-top: 4px;
        }
        
        .sb-search-footer {
            padding: 10px 16px;
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
            font-size: 12px;
            color: #94a3b8;
            text-align: right;
        }
    `;
    document.head.appendChild(style);

    const backdrop = document.createElement('div');
    backdrop.className = 'sb-search-backdrop';
    
    backdrop.innerHTML = `
        <div class="sb-search-modal" onclick="event.stopPropagation()">
            <div class="sb-search-input-wrapper">
                <span>🔍</span>
                <input type="text" class="sb-search-input" placeholder="Search pages, alarms, sensors..." />
            </div>
            <div class="sb-search-results"></div>
            <div class="sb-search-footer">
                Press <strong>Esc</strong> to close
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);

    const searchIndex = [
        { title: "Admin Dashboard", desc: "Main control panel", url: "/admin" },
        { title: "Gateway Monitor", desc: "View and manage gateways", url: "/gateway-monitor" },
        { title: "Sensor Profiles", desc: "Create or edit sensor payload decoders", url: "/sensor-catalog-manager" },
        { title: "Alarm Settings", desc: "Manage alarm templates and thresholds", url: "/admin-alarms" },
        { title: "Asset Setup", desc: "Hierarchy, floors, and building maps", url: "/admin-setup-asset-management" },
        { title: "Client Management", desc: "Manage users and roles", url: "/admin-client-management" },
        { title: "Network Status", desc: "TTN Webhook configuration", url: "/network-settings" }
    ];

    const input = backdrop.querySelector('.sb-search-input');
    const resultsContainer = backdrop.querySelector('.sb-search-results');

    function renderResults(query) {
        resultsContainer.innerHTML = '';
        activeIndex = -1;
        const q = query.toLowerCase();
        
        const filtered = searchIndex.filter(item => 
            item.title.toLowerCase().includes(q) || 
            item.desc.toLowerCase().includes(q)
        );

        if (filtered.length === 0) {
            resultsContainer.innerHTML = '<div style="padding:20px; text-align:center; color:#94a3b8;">No results found.</div>';
            return;
        }

        filtered.forEach(item => {
            const a = document.createElement('a');
            a.className = 'sb-search-item';
            a.href = item.url;
            a.innerHTML = `
                <div class="sb-search-item-title">${item.title}</div>
                <div class="sb-search-item-desc">${item.desc}</div>
            `;
            resultsContainer.appendChild(a);
        });
    }

    function toggleSearch() {
        if (backdrop.style.display === 'flex') {
            backdrop.style.display = 'none';
        } else {
            backdrop.style.display = 'flex';
            input.value = '';
            renderResults('');
            setTimeout(() => input.focus(), 50);
        }
    }

    backdrop.onclick = () => {
        backdrop.style.display = 'none';
    };

    input.addEventListener('input', (e) => {
        renderResults(e.target.value);
    });

    let activeIndex = -1;
    
    document.addEventListener('keydown', (e) => {
        // Cmd+K or Ctrl+K (case-insensitive)
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            toggleSearch();
        }
        
        if (backdrop.style.display !== 'flex') return;
        
        // Escape to close
        if (e.key === 'Escape') {
            backdrop.style.display = 'none';
            activeIndex = -1;
            return;
        }
        
        const items = resultsContainer.querySelectorAll('.sb-search-item');
        if (items.length === 0) return;
        
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            activeIndex = Math.min(activeIndex + 1, items.length - 1);
            items.forEach((el, i) => el.classList.toggle('active', i === activeIndex));
            items[activeIndex]?.scrollIntoView({ block: 'nearest' });
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            activeIndex = Math.max(activeIndex - 1, 0);
            items.forEach((el, i) => el.classList.toggle('active', i === activeIndex));
            items[activeIndex]?.scrollIntoView({ block: 'nearest' });
        } else if (e.key === 'Enter' && activeIndex >= 0 && activeIndex < items.length) {
            e.preventDefault();
            items[activeIndex].click();
        }
    });
});
