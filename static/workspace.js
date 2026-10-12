document.addEventListener('DOMContentLoaded', () => {
    const menu = document.getElementById('workspaceMenu');
    const nav = document.getElementById('workspaceSidebar');
    const scrim = document.getElementById('workspaceScrim');
    const toggleMenu = open => {
        nav.classList.toggle('is-open', open); scrim.classList.toggle('is-visible', open);
        menu.setAttribute('aria-expanded', String(open)); document.body.classList.toggle('menu-open', open);
    };
    menu?.addEventListener('click', () => toggleMenu(!nav.classList.contains('is-open')));
    scrim?.addEventListener('click', () => toggleMenu(false));
    document.addEventListener('keydown', event => { if (event.key === 'Escape') toggleMenu(false); });
    let tabGroup = 0;
    function tabs(host, groups) {
        if (!host || groups.every(group => !group.nodes.length)) return;
        const bar = document.createElement('div'); bar.className = 'section-tabs';
        bar.setAttribute('role', 'tablist'); bar.setAttribute('aria-label', 'Page sections'); host.prepend(bar);
        const buttons = [], panels = [];
        const prefix = `section-${tabGroup++}`;
        const select = index => {
            buttons.forEach((button, i) => { button.setAttribute('aria-selected', String(i === index)); button.tabIndex = i === index ? 0 : -1; panels[i].hidden = i !== index; });
            window.dispatchEvent(new Event('resize'));
        };
        groups.filter(group => group.nodes.length).forEach((group, index) => {
            const button = document.createElement('button'); button.type = 'button'; button.textContent = group.label;
            button.id = `${prefix}-tab-${index}`; button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', `${prefix}-panel-${index}`);
            const panel = document.createElement('section'); panel.className = 'tab-panel'; panel.id = `${prefix}-panel-${index}`;
            panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id);
            const grid = document.createElement('div'); grid.className = 'grid';
            group.nodes.forEach(node => grid.append(node)); panel.append(grid); host.append(panel);
            buttons.push(button); panels.push(panel); bar.append(button);
            button.addEventListener('click', () => select(index));
            button.addEventListener('keydown', event => {
                let target;
                if (event.key === 'ArrowRight') target = (index + 1) % buttons.length;
                if (event.key === 'ArrowLeft') target = (index - 1 + buttons.length) % buttons.length;
                if (event.key === 'Home') target = 0;
                if (event.key === 'End') target = buttons.length - 1;
                if (target !== undefined) { event.preventDefault(); select(target); buttons[target].focus(); }
            });
        });
        select(0);
        host.addEventListener('click', event => {
            const action = event.target.closest('[onclick]')?.getAttribute('onclick') || '';
            const target = action.includes('editAccess(') ? 'accessUser' : action.includes('selectCredentialsUser(') ? 'credentialsUser' : null;
            if (target) { const index = panels.findIndex(panel => panel.contains(document.getElementById(target))); if (index >= 0) select(index); }
        });
    }
    const path = location.pathname;
    const container = document.querySelector('.workspace-content > .container');
    if (path === '/admin/setup') {
        const cards = [...container.querySelectorAll(':scope > .grid > .card')];
        const matching = names => cards.filter(card => names.includes(card.querySelector('h2')?.textContent.trim()));
        tabs(container, [
            {label: 'Organization', nodes: matching(['Clients', 'Sites'])},
            {label: 'Buildings & floors', nodes: matching(['Buildings', 'Floors'])},
            {label: 'Gateways', nodes: matching(['Gateways'])},
            {label: 'Devices', nodes: matching(['Device Assignment', 'Devices & Building Placement'])},
            {label: 'Structure & tools', nodes: matching(['Operational Links', 'Deployment Structure Summary'])}
        ]);
        // Separate creation, updates and map uploads inside each asset card.
        const formGroups = [
            [['clientName', 'Create client'], ['clientEditSelect', 'Edit client']],
            [['siteClientSelect', 'Create site'], ['siteEditSelect', 'Edit site'], ['siteMapSelect', 'Site map']],
            [['buildingSiteSelect', 'Create building'], ['buildingEditSelect', 'Edit building']],
            [['floorBuildingSelect', 'Create floor'], ['floorEditSelect', 'Edit floor'], ['floorImageSelect', 'Floor plan']]
        ];
        formGroups.forEach(groups => {
            const card = document.getElementById(groups[0][0])?.closest('.card');
            if (!card) return;
            const starts = groups.map(([id]) => document.getElementById(id).previousElementSibling);
            const status = card.querySelector('.status');
            const sections = groups.map(([id, label], index) => {
                const wrapper = document.createElement('div');
                let node = starts[index]; const end = starts[index + 1] || status;
                while (node && node !== end) { const next = node.nextElementSibling; wrapper.append(node); node = next; }
                return {label, nodes: [wrapper]};
            });
            const host = document.createElement('div'); card.insertBefore(host, status); tabs(host, sections);
        });
        const note = container.querySelector('.note-card');
        if (note) { const details = document.createElement('details'); details.className = 'setup-guide'; const summary = document.createElement('summary'); summary.textContent = 'Setup guide'; note.before(details); details.append(summary, note); details.style.marginBottom = '20px'; }
    }
    if (path === '/admin/client-access') {
        const cards = [...container.querySelectorAll(':scope > .grid > .card, :scope > .card')];
        const get = title => cards.filter(card => card.querySelector('h2')?.textContent.trim() === title);
        tabs(container, [
            {label: 'Users', nodes: get('Users')}, {label: 'Create user', nodes: get('Create Client User')},
            {label: 'Grant access', nodes: get('Give Access')}, {label: 'Credentials', nodes: get('Client Login Credentials')},
            {label: 'Access records', nodes: get('Access Records')}
        ]);
    }
    if (path === '/alarm-settings-page') {
        const host = document.getElementById('settingsArea');
        if (host) tabs(host, [...host.querySelectorAll('.card')].filter(card => card.querySelector('h2')).map(card => ({label: card.querySelector('h2').textContent.trim(), nodes: [card]})));
    }
    if (path === '/admin/provision-options') tabs(container, [...container.querySelectorAll(':scope > .card')].map(card => ({label: card.querySelector('h2').textContent.trim(), nodes: [card]})));
    if (path === '/client-portal') {
        const content = document.querySelector('.workspace-content');
        const activities = [...content.querySelectorAll(':scope > .activity-container')];
        const host = document.createElement('div'); container.before(host);
        tabs(host, [{label: 'Live view', nodes: [container]}, ...activities.map(node => ({label: node.querySelector('h2')?.textContent.trim() || 'Activity', nodes: [node]}))]);
    }
    document.querySelectorAll('.workspace-content table').forEach(table => {
        if (!table.parentElement.classList.contains('table-wrap')) { const wrap = document.createElement('div'); wrap.className = 'table-wrap'; table.before(wrap); wrap.append(table); }
    });
    document.querySelectorAll('.workspace-content input[id], .workspace-content select[id], .workspace-content textarea[id]').forEach(control => {
        const label = control.previousElementSibling;
        if (label?.tagName === 'LABEL' && !label.htmlFor) label.htmlFor = control.id;
    });
    document.querySelectorAll('.section-tabs[role=tablist]').forEach(bar => {
        const buttons = [...bar.querySelectorAll('[role=tab]')];
        if (buttons.some(button => button.id.startsWith('section-'))) return;
        bar.addEventListener('keydown', event => {
            const index = buttons.indexOf(event.target); let target;
            if (event.key === 'ArrowRight') target = (index + 1) % buttons.length;
            if (event.key === 'ArrowLeft') target = (index - 1 + buttons.length) % buttons.length;
            if (event.key === 'Home') target = 0;
            if (event.key === 'End') target = buttons.length - 1;
            if (target !== undefined) { event.preventDefault(); buttons[target].click(); buttons[target].focus(); }
        });
    });
});
