document.addEventListener("DOMContentLoaded", function () {
    // Inject styles for the toast notification
    const style = document.createElement('style');
    style.innerHTML = `
        .sb-toast-container {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 11000;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .sb-toast {
            background: #ffffff;
            border-left: 5px solid #ef4444; /* Red for alarms */
            box-shadow: 0 10px 25px rgba(0,0,0,0.1);
            border-radius: 8px;
            padding: 16px 20px;
            width: 320px;
            transform: translateX(120%);
            transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            flex-direction: column;
            gap: 6px;
            cursor: pointer;
        }

        .sb-toast.show {
            transform: translateX(0);
        }

        .sb-toast-title {
            color: #b91c1c;
            font-weight: bold;
            font-size: 16px;
            margin: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .sb-toast-message {
            color: #475569;
            font-size: 14px;
            margin: 0;
        }
        
        .sb-toast-close {
            color: #94a3b8;
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
        }
        
        .sb-toast-close:hover {
            color: #0f172a;
        }
    `;
    document.head.appendChild(style);

    const toastContainer = document.createElement('div');
    toastContainer.className = 'sb-toast-container';
    document.body.appendChild(toastContainer);

    function showToast(title, deviceName, ruleName) {
        const toast = document.createElement('div');
        toast.className = 'sb-toast';
        
        const titleDiv = document.createElement('div');
        titleDiv.className = 'sb-toast-title';
        const titleSpan = document.createElement('span');
        titleSpan.textContent = '⚠️ ' + title;
        const closeButton = document.createElement('button');
        closeButton.className = 'sb-toast-close';
        closeButton.innerHTML = '&times;';
        titleDiv.appendChild(titleSpan);
        titleDiv.appendChild(closeButton);
        
        const msgDiv = document.createElement('div');
        msgDiv.className = 'sb-toast-message';
        const strong = document.createElement('strong');
        strong.textContent = deviceName || 'Unknown Device';
        const em = document.createElement('em');
        em.textContent = ruleName || 'unknown rule';
        msgDiv.appendChild(strong);
        msgDiv.appendChild(document.createTextNode(' triggered rule '));
        msgDiv.appendChild(em);
        msgDiv.appendChild(document.createTextNode('.'));
        
        toast.appendChild(titleDiv);
        toast.appendChild(msgDiv);
        
        toastContainer.appendChild(toast);
        void toast.offsetWidth;
        toast.classList.add('show');
        
        closeButton.onclick = (e) => {
            e.stopPropagation();
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 400);
        };
        
        setTimeout(() => {
            if(toast.parentElement) {
                toast.classList.remove('show');
                setTimeout(() => {
                    if(toast.parentElement) toast.remove();
                }, 400);
            }
        }, 6000);
    }

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/alarms`;
    
    let ws;
    let reconnectAttempts = 0;
    
    function connectWebSocket() {
        ws = new WebSocket(wsUrl);
        
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === "ALARM") {
                    showToast('Alarm Triggered', data.device_name, data.rule);
                    
                    // Optional: If the user is on the floor live view, reload it
                    if (window.location.pathname.includes('/client-floor-live-view')) {
                        // A more elegant approach would be injecting the data, but for now
                        // we can do a soft refresh or trigger a local event
                        const event = new CustomEvent('LiveAlarmUpdate', { detail: data });
                        document.dispatchEvent(event);
                    }
                }
            } catch (e) {
                console.error("Error parsing WS message", e);
            }
        };
        
        ws.onclose = () => {
            reconnectAttempts++;
            const delay = Math.min(5000 * Math.pow(2, reconnectAttempts - 1), 60000);
            console.log(`Real-time Alarms disconnected. Reconnecting in ${delay/1000}s... (attempt ${reconnectAttempts})`);
            setTimeout(connectWebSocket, delay);
        };
        
        ws.onopen = () => {
            console.log("Real-time Alarms connected.");
            reconnectAttempts = 0;
        };
    }
    
    // Only connect if the user is not on a login page
    if (!window.location.pathname.includes('login')) {
        connectWebSocket();
    }
});
