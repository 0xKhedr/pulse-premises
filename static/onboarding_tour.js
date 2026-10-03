document.addEventListener("DOMContentLoaded", function () {
    // Only run tour if not on login page
    if (window.location.pathname.includes('login') || window.location.pathname === "/") {
        return;
    }

    // Check if tour was already completed
    if (localStorage.getItem('sb_tour_completed')) {
        return;
    }

    // Wait a moment for UI to settle
    setTimeout(() => {
        const intro = introJs();
        
        // Define standard steps assuming standard navigation exists
        // If these elements don't exist on the specific page, intro.js will gracefully skip or we can target general areas.
        intro.setOptions({
            steps: [
                {
                    intro: "👋 Welcome to the Smart Building Management System! Let's take a quick tour."
                },
                {
                    element: document.querySelector('nav') || document.body,
                    intro: "This is your main navigation. Use it to jump between dashboards, devices, and settings."
                },
                {
                    element: document.querySelector('.sb-help-fab') || document.body,
                    intro: "Stuck? Click this help icon at any time to open the Contextual Help Drawer."
                },
                {
                    intro: "Pro Tip: Press <strong>Cmd+K</strong> or <strong>Ctrl+K</strong> to instantly search for any page or setting!"
                }
            ],
            showProgress: true,
            showBullets: false,
            exitOnOverlayClick: false,
            exitOnEsc: false
        });
        
        intro.start();
        
        intro.oncomplete(function() {
            localStorage.setItem('sb_tour_completed', 'true');
        });
        
        intro.onexit(function() {
            localStorage.setItem('sb_tour_completed', 'true');
        });
        
    }, 1000);
});
