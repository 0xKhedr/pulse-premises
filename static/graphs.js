document.addEventListener("DOMContentLoaded", function () {
    // Inject styles for the graph modal
    const style = document.createElement('style');
    style.innerHTML = `
        .sb-graph-backdrop {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(15, 23, 42, 0.4);
            backdrop-filter: blur(4px);
            z-index: 13000;
            display: none;
            align-items: center;
            justify-content: center;
        }

        .sb-graph-modal {
            background: white;
            width: 800px;
            max-width: 95%;
            border-radius: 12px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.2);
            border: 1px solid #e2e8f0;
            display: flex;
            flex-direction: column;
        }

        .sb-graph-header {
            padding: 20px 24px;
            border-bottom: 1px solid #e2e8f0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .sb-graph-title {
            margin: 0;
            font-size: 20px;
            color: #0f172a;
            font-weight: 600;
        }

        .sb-graph-close {
            background: none;
            border: none;
            font-size: 24px;
            color: #94a3b8;
            cursor: pointer;
        }

        .sb-graph-close:hover {
            color: #1e293b;
        }

        .sb-graph-content {
            padding: 24px;
            height: 400px;
            position: relative;
        }
        
        .sb-graph-controls {
            padding: 10px 24px;
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            display: flex;
            gap: 10px;
        }
        
        .sb-graph-btn {
            background: white;
            border: 1px solid #cbd5e1;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
        }
        .sb-graph-btn.active {
            background: #10b981;
            color: white;
            border-color: #10b981;
        }
    `;
    document.head.appendChild(style);

    // Create DOM structure
    const backdrop = document.createElement('div');
    backdrop.className = 'sb-graph-backdrop';
    backdrop.innerHTML = `
        <div class="sb-graph-modal" onclick="event.stopPropagation()">
            <div class="sb-graph-header">
                <h3 class="sb-graph-title">Device Telemetry History</h3>
                <div>
                    <button class="sb-graph-btn" id="sb-csv-btn" style="margin-right: 12px; background: #3b82f6; color: white; border: none;">📥 Export CSV</button>
                    <button class="sb-graph-close">&times;</button>
                </div>
            </div>
            <div class="sb-graph-controls" id="sb-graph-controls">
                <!-- Buttons will be injected here based on available telemetry keys -->
            </div>
            <div class="sb-graph-content">
                <canvas id="sb-telemetry-chart"></canvas>
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);

    const closeBtn = backdrop.querySelector('.sb-graph-close');
    closeBtn.onclick = () => {
        backdrop.style.display = 'none';
        if (window.sbChart) {
            window.sbChart.destroy();
            window.sbChart = null;
        }
    };

    backdrop.onclick = () => closeBtn.onclick();

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && backdrop.style.display === 'flex') {
            closeBtn.onclick();
        }
    });

    // Global function to open graph modal
    window.openTelemetryGraph = async function(deviceId, deviceName) {
        backdrop.style.display = 'flex';
        backdrop.querySelector('.sb-graph-title').innerText = `${deviceName} - History`;
        const controlsDiv = document.getElementById('sb-graph-controls');
        controlsDiv.innerHTML = '<span style="color:#94a3b8; font-size:14px;">Loading data...</span>';
        
        if (window.sbChart) {
            window.sbChart.destroy();
            window.sbChart = null;
        }

        try {
            const response = await fetch(`/api/devices/${deviceId}/history`);
            const data = await response.json();
            
            if (data.status !== 'success' || !data.history || data.history.length === 0) {
                controlsDiv.innerHTML = '<span style="color:#ef4444; font-size:14px;">No historical data available yet. Wait for sensor uplinks.</span>';
                return;
            }

            // Extract all possible telemetry keys (excluding standard ones like payload)
            const keys = new Set();
            data.history.forEach(row => {
                if(row.telemetry) {
                    Object.keys(row.telemetry).forEach(k => {
                        if (k !== 'payload' && typeof row.telemetry[k] === 'number') {
                            keys.add(k);
                        }
                    });
                }
            });

            if (keys.size === 0) {
                controlsDiv.innerHTML = '<span style="color:#f59e0b; font-size:14px;">No numeric telemetry fields found to graph.</span>';
                return;
            }

            const availableMetrics = Array.from(keys);
            let activeMetric = availableMetrics[0];

            // Render buttons
            function renderControls() {
                controlsDiv.innerHTML = '';
                availableMetrics.forEach(metric => {
                    const btn = document.createElement('button');
            btn.className = `sb-graph-btn ${metric === activeMetric ? 'active' : ''}`;
                    btn.innerText = metric.toUpperCase();
                    btn.onclick = () => {
                        activeMetric = metric;
                        renderControls();
                        drawChart(data.history, activeMetric);
                    };
                    controlsDiv.appendChild(btn);
                });
            }

            // CSV Export logic
            const csvBtn = document.getElementById('sb-csv-btn');
            csvBtn.onclick = () => {
                if (!data.history || data.history.length === 0) return;
                
                // Get all unique keys across all history items
                const allKeys = new Set(['timestamp']);
                data.history.forEach(row => {
                    if (row.telemetry) {
                        Object.keys(row.telemetry).forEach(k => allKeys.add(k));
                    }
                });
                
                function csvEscape(val) {
                    const s = String(val ?? '');
                    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
                        return '"' + s.replace(/"/g, '""') + '"';
                    }
                    return s;
                }
                
                const headerRow = Array.from(allKeys).map(csvEscape).join(',');
                const csvRows = ['\uFEFF' + headerRow];
                
                data.history.forEach(row => {
                    const rowValues = Array.from(allKeys).map(key => {
                        if (key === 'timestamp') return csvEscape(row.timestamp);
                        return csvEscape((row.telemetry && row.telemetry[key] !== undefined) ? row.telemetry[key] : '');
                    });
                    csvRows.push(rowValues.join(','));
                });
                
                const csvString = csvRows.join('\n');
                const blob = new Blob([csvString], { type: 'text/csv' });
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
        a.download = `telemetry_${deviceId}_${new Date().toISOString().split('T')[0]}.csv`;
                a.click();
                window.URL.revokeObjectURL(url);
            };

            renderControls();
            drawChart(data.history, activeMetric);

        } catch (e) {
            controlsDiv.innerHTML = '<span style="color:#ef4444; font-size:14px;">Failed to fetch history.</span>';
        }
    };

    function drawChart(historyData, metricKey) {
        const ctx = document.getElementById('sb-telemetry-chart').getContext('2d');
        
        if (window.sbChart) {
            window.sbChart.destroy();
        }

        // Prepare data
        const labels = [];
        const values = [];

        historyData.forEach(row => {
            const d = new Date(row.timestamp);
            labels.push(d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}));
            
            if (row.telemetry && row.telemetry[metricKey] !== undefined) {
                values.push(row.telemetry[metricKey]);
            } else {
                values.push(null);
            }
        });

        window.sbChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: metricKey.toUpperCase(),
                    data: values,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    borderWidth: 2,
                    tension: 0.3,
                    fill: true,
                    pointBackgroundColor: '#059669',
                    pointRadius: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: false,
                        grid: { color: '#f1f5f9' }
                    },
                    x: {
                        grid: { display: false }
                    }
                }
            }
        });
    }

    // Expose openTelemetryGraph to window so standard HTML buttons can use it:
    // onclick="openTelemetryGraph('device-id-123', 'Room 101 Sensor')"
});
