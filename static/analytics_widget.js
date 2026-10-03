document.addEventListener("DOMContentLoaded", async function () {
    const summaryGrid = document.querySelector('.summary-grid');
    if (!summaryGrid) return;
    
    // Prevent duplicate injection
    if (summaryGrid.querySelector('[data-analytics-widget]')) return;
    
    try {
        const res = await fetch('/api/analytics');
        if (!res.ok) return;
        const data = await res.json();
        
        const widgets = [
            { title: "🟢 Active Devices", value: (data.active_devices !== undefined ? data.active_devices : (data.online_devices ?? 0)), color: "#10b981" },
            { title: "📡 Gateways", value: data.total_gateways ?? 0, color: "#3b82f6" },
            { title: "🔴 Offline Devices", value: data.offline_devices ?? 0, color: (data.offline_devices ?? 0) > 0 ? "#ef4444" : "#94a3b8" },
            { title: "⚠️ Alarms Today", value: data.alarms_today ?? 0, color: (data.alarms_today ?? 0) > 0 ? "#f59e0b" : "#94a3b8" }
        ];
        
        widgets.forEach(w => {
            const card = document.createElement('div');
            card.className = 'summary-card';
            card.setAttribute('data-analytics-widget', 'true');
            card.style.borderLeft = `4px solid ${w.color}`;
            card.innerHTML = `
                <div class="summary-title" style="color: ${w.color}; font-weight:bold;">${w.title}</div>
                <div class="summary-value">${w.value}</div>
            `;
            summaryGrid.appendChild(card);
        });
        
    } catch (e) {
        console.error("Failed to load analytics widgets", e);
    }
});
