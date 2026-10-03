document.addEventListener("DOMContentLoaded", function () {
    const style = document.createElement('style');
    style.innerHTML = `
        .sb-bulk-backdrop {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(15, 23, 42, 0.4);
            backdrop-filter: blur(4px);
            z-index: 15000;
            display: none;
            align-items: center;
            justify-content: center;
        }
        .sb-bulk-modal {
            background: white;
            width: 450px;
            border-radius: 12px;
            padding: 24px;
            color: #1e293b;
        }
        .sb-bulk-modal h3 {
            margin-top: 0;
            margin-bottom: 10px;
        }
        .sb-bulk-modal input[type="file"] {
            margin-top: 20px;
            margin-bottom: 20px;
            display: block;
        }
        .sb-bulk-actions {
            display: flex;
            justify-content: flex-end;
            gap: 10px;
        }
        .sb-bulk-btn {
            padding: 8px 16px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
        }
        .sb-bulk-submit { background: #10b981; color: white; }
    `;
    document.head.appendChild(style);

    const backdrop = document.createElement('div');
    backdrop.className = 'sb-bulk-backdrop';
    backdrop.innerHTML = `
        <div class="sb-bulk-modal" onclick="event.stopPropagation()">
            <h3>Bulk Provision Devices</h3>
            <p style="font-size:14px; color:#64748b;">Upload a CSV with headers: <code>dev_eui, app_key, room_id, profile_id, label</code></p>
            <input type="file" id="sb-bulk-file" accept=".csv">
            <div id="sb-bulk-status" style="font-size:13px; color:#ef4444; margin-bottom:10px;"></div>
            <div class="sb-bulk-actions">
                <button class="sb-bulk-btn" onclick="closeBulkImport()">Cancel</button>
                <button class="sb-bulk-btn sb-bulk-submit" onclick="submitBulkImport()">Upload</button>
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);
    backdrop.addEventListener('click', () => closeBulkImport());

    window.openBulkImport = function() {
        backdrop.style.display = 'flex';
        document.getElementById('sb-bulk-status').innerText = '';
        document.getElementById('sb-bulk-file').value = '';
    };

    window.closeBulkImport = function() {
        backdrop.style.display = 'none';
    };

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && backdrop.style.display === 'flex') {
            closeBulkImport();
        }
    });

    window.submitBulkImport = async function() {
        const fileInput = document.getElementById('sb-bulk-file');
        const status = document.getElementById('sb-bulk-status');
        
        if (!fileInput.files.length) {
            status.innerText = "Please select a file.";
            return;
        }
        
        const formData = new FormData();
        formData.append("file", fileInput.files[0]);
        
        status.innerText = "Uploading...";
        status.style.color = "#3b82f6";
        
        try {
            const response = await fetch('/api/devices/bulk-import', {
                method: 'POST',
                body: formData
            });
            const result = await response.json();
            
            if (result.status === 'success') {
                status.style.color = "#10b981";
                status.innerText = \`Successfully imported \${result.imported} devices.\`;
                setTimeout(() => {
                    closeBulkImport();
                    location.reload();
                }, 2000);
            } else if (result.status === 'partial') {
                // Some rows landed, some did not - report both rather than
                // calling the whole upload a failure.
                status.style.color = "#f59e0b";
                status.innerText =
                    \`Imported \${result.imported} devices, \${result.errors.length} row(s) failed: \` +
                    result.errors.slice(0, 3).join("; ");
                setTimeout(() => {
                    closeBulkImport();
                    location.reload();
                }, 5000);
            } else {
                status.style.color = "#ef4444";
                status.innerText = (result.errors && result.errors.length)
                    ? \`Import failed: \${result.errors.slice(0, 3).join("; ")}\`
                    : (result.message || result.detail || "Upload failed.");
            }
        } catch (e) {
            status.style.color = "#ef4444";
            status.innerText = "Error uploading file.";
        }
    };
});
