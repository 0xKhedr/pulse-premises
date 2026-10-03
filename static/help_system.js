document.addEventListener("DOMContentLoaded", function () {
    // 1. Inject Styles
    const style = document.createElement('style');
    style.innerHTML = `
        .sb-help-fab {
            position: fixed;
            bottom: 30px;
            right: 30px;
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, #10b981, #34d399); /* Emerald Gradient */
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 28px;
            font-weight: bold;
            box-shadow: 0 10px 25px rgba(59, 130, 246, 0.5);
            cursor: pointer;
            z-index: 9999;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: 2px solid rgba(255,255,255,0.2);
        }
        .sb-help-fab:hover {
            transform: scale(1.1) rotate(10deg);
            box-shadow: 0 15px 35px rgba(139, 92, 246, 0.6);
        }
        
        .sb-help-drawer {
            position: fixed;
            top: 0;
            right: -450px;
            width: 400px;
            height: 100vh;
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-left: 1px solid rgba(0, 0, 0, 0.1);
            z-index: 10000;
            transition: right 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: -10px 0 30px rgba(0,0,0,0.1);
            display: flex;
            flex-direction: column;
            color: #1e293b;
            font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        .sb-help-drawer.open {
            right: 0;
        }

        .sb-help-header {
            padding: 25px;
            background: rgba(248, 250, 252, 1);
            border-bottom: 1px solid rgba(0, 0, 0, 0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .sb-help-header h2 {
            margin: 0;
            font-size: 22px;
            background: linear-gradient(to right, #059669, #10b981);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .sb-help-close {
            background: transparent;
            border: none;
            color: #94a3b8;
            font-size: 24px;
            cursor: pointer;
            transition: color 0.2s;
        }

        .sb-help-close:hover {
            color: #0f172a;
        }

        .sb-help-content {
            padding: 25px;
            overflow-y: auto;
            flex-grow: 1;
        }

        .sb-help-item {
            margin-bottom: 24px;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            padding: 18px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.02);
        }

        .sb-help-item h3 {
            margin: 0 0 10px 0;
            font-size: 16px;
            color: #0f172a;
        }

        .sb-help-item p {
            margin: 0;
            color: #94a3b8;
            font-size: 14px;
            line-height: 1.6;
        }
    `;
    document.head.appendChild(style);

    // 2. Help Content Dictionary based on Paths
    const helpData = {
        "/admin": [
            { title: "Admin Portal", desc: "Welcome to the central hub. From here you can manage all users, clients, and devices across the entire system." },
            { title: "Navigation", desc: "Use the grid cards below to access the Gateway Monitor, Floor Editors, User Management, and Alarm Settings." }
        ],
        "/gateway-monitor": [
            { title: "What is a Gateway?", desc: "Gateways receive LoRaWAN signals from your sensors and forward them to The Things Network." },
            { title: "Adding Gateways", desc: "Click 'Create Gateway' to register a new gateway. Ensure the Gateway EUI exactly matches the hardware." },
            { title: "Gateway Placement", desc: "Click 'Placement' on any active gateway to visually place it on your building's floor map." }
        ],
        "/provision-options": [
            { title: "Device Provisioning", desc: "Provisioning a device links its physical hardware (via DevEUI) to this system through TTN." },
            { title: "Assigning Profiles", desc: "When provisioning, you must select a Sensor Profile. This profile tells the system how to decode the sensor's raw data." }
        ],
        "/admin-alarms": [
            { title: "Alarm Settings", desc: "Alarms automatically trigger when a sensor's telemetry crosses a predefined threshold (e.g. Temperature > 30)." },
            { title: "Recipients", desc: "Add email addresses to the recipients list to ensure your team is instantly notified when an alarm occurs." }
        ],
        "/admin-setup-asset-management": [
            { title: "Hierarchy Setup", desc: "Assets are organized as Sites > Buildings > Floors > Rooms. You must build this hierarchy before placing sensors." },
            { title: "Visual Mapping", desc: "Upload a floorplan image to any Floor. Later, you can drag and drop devices onto this map for real-time visualization." }
        ],
        "/sensor-catalog-manager": [
            { title: "Sensor Profiles", desc: "A profile maps raw Hex payload bytes to human-readable variables (like Temperature, Humidity, Occupancy)." },
            { title: "Creating Profiles", desc: "You can create Simple profiles for standard telemetry, or Advanced profiles with custom Javascript decoders." }
        ],
        "/client": [
            { title: "Client Portal", desc: "Clients can only view the buildings and floors explicitly assigned to them by an Administrator." },
            { title: "Live View", desc: "Click on any Floor to view the live interactive map showing current sensor statuses and active alarms." }
        ],
        "default": [
            { title: "Need Help?", desc: "You are currently on a page with standard functionality. Use the top navigation bar to return to your main dashboard." }
        ]
    };

    // 3. Find relevant content
    const currentPath = window.location.pathname;
    let pageContent = helpData["default"];
    
    const sortedPaths = Object.keys(helpData)
        .filter(p => p !== 'default')
        .sort((a, b) => b.length - a.length);
    
    for (const path of sortedPaths) {
        if (currentPath.startsWith(path) && path !== "/") {
            pageContent = helpData[path];
            break;
        }
    }

    // 4. Create UI Elements
    const fab = document.createElement('div');
    fab.className = 'sb-help-fab';
    fab.innerHTML = '?';
    document.body.appendChild(fab);

    const drawer = document.createElement('div');
    drawer.className = 'sb-help-drawer';
    
    let itemsHtml = pageContent.map(item => `
        <div class="sb-help-item">
            <h3>${item.title}</h3>
            <p>${item.desc}</p>
        </div>
    `).join('');

    drawer.innerHTML = `
        <div class="sb-help-header">
            <h2>Page Guide</h2>
            <button class="sb-help-close">&times;</button>
        </div>
        <div class="sb-help-content">
            ${itemsHtml}
            <div class="sb-help-item" style="margin-top: 40px; background: #ecfdf5; border-color: #a7f3d0;">
                <h3 style="color: #059669;">Contact Support</h3>
                <p style="color: #064e3b;">If you need further assistance, please contact your system administrator.</p>
            </div>
        </div>
    `;
    document.body.appendChild(drawer);

    // 5. Interactions
    fab.addEventListener('click', () => {
        drawer.classList.add('open');
    });

    const closeBtn = drawer.querySelector('.sb-help-close');
    closeBtn.addEventListener('click', () => {
        drawer.classList.remove('open');
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && drawer.classList.contains('open')) {
            drawer.classList.remove('open');
        }
    });
});
