document.addEventListener("DOMContentLoaded", function () {
    const style = document.createElement('style');
    style.innerHTML = `
        .sb-fw-backdrop {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(15, 23, 42, 0.4);
            backdrop-filter: blur(4px);
            z-index: 14000;
            display: none;
            align-items: center;
            justify-content: center;
        }

        .sb-fw-modal {
            background: white;
            width: 450px;
            max-width: 90%;
            border-radius: 12px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.2);
            border: 1px solid #e2e8f0;
            padding: 24px;
        }

        .sb-fw-title {
            margin: 0 0 16px 0;
            font-size: 20px;
            color: #0f172a;
            font-weight: 600;
        }
        
        .sb-fw-desc {
            color: #64748b;
            font-size: 14px;
            margin-bottom: 20px;
        }

        .sb-fw-input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            font-size: 16px;
            margin-bottom: 20px;
            outline: none;
        }
        
        .sb-fw-input:focus {
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
        }

        .sb-fw-actions {
            display: flex;
            justify-content: flex-end;
            gap: 12px;
        }

        .sb-fw-btn {
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 500;
            border: none;
            font-size: 14px;
        }
        
        .sb-fw-cancel {
            background: #f1f5f9;
            color: #475569;
        }
        
        .sb-fw-cancel:hover {
            background: #e2e8f0;
        }

        .sb-fw-submit {
            background: #10b981;
            color: white;
        }
        
        .sb-fw-submit:hover {
            background: #059669;
        }
    `;
    document.head.appendChild(style);

    const backdrop = document.createElement('div');
    backdrop.className = 'sb-fw-backdrop';
    backdrop.innerHTML = `
        <div class="sb-fw-modal" onclick="event.stopPropagation()">
            <h3 class="sb-fw-title">Generate C++ Firmware</h3>
            <div class="sb-fw-desc">
                Enter a short, alphanumeric name for your sensor (e.g. <strong>CO2Sensor</strong>). We will generate the C++ driver, LoRaWAN encoder, and instructions for you!
            </div>
            <input type="text" class="sb-fw-input" id="sb-fw-input" placeholder="e.g. LaserDistance">
            <div class="sb-fw-actions">
                <button class="sb-fw-btn sb-fw-cancel">Cancel</button>
                <button class="sb-fw-btn sb-fw-submit">Download ZIP</button>
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);

    const input = backdrop.querySelector('#sb-fw-input');
    const cancelBtn = backdrop.querySelector('.sb-fw-cancel');
    const submitBtn = backdrop.querySelector('.sb-fw-submit');

    window.openFirmwareGenerator = function() {
        backdrop.style.display = 'flex';
        input.value = '';
        setTimeout(() => input.focus(), 50);
    };

    function close() {
        backdrop.style.display = 'none';
    }

    cancelBtn.onclick = close;
    backdrop.onclick = close;

    submitBtn.onclick = () => {
        const name = input.value.trim();
        if (!name || !/^[a-zA-Z0-9]+$/.test(name)) {
            alert("Please enter a valid alphanumeric name without spaces.");
            return;
        }
        
        // Trigger download
        window.location.href = `/api/firmware/generate-sensor-template?name=${name}`;
        close();
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submitBtn.onclick();
        if (e.key === 'Escape') close();
    });
});
