from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login_page.html", {})


@router.get("/admin-login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    # This is where auth_middleware sends unauthenticated admin traffic, so it
    # has to actually offer a credential form. It previously served a stale
    # copy of the gateway monitor, which left no way to sign in at all.
    from app.main import render_role_login_page

    return render_role_login_page(
        request,
        login_type="admin",
        title="Admin Login",
        subtitle="Sign in to manage clients, devices, gateways and alarms.",
        badge="ADMINISTRATOR",
        accent="#2563eb",
    )


@router.get("/gateway-monitor", response_class=HTMLResponse)
def gateway_monitor(request: Request):
    return templates.TemplateResponse(request, "gateway_monitor.html", {})


@router.get("/admin/gateway-placement", response_class=HTMLResponse)
def gateway_placement_editor(request: Request):
    return templates.TemplateResponse(request, "gateway_placement_editor.html", {})


@router.get("/admin/client-access", response_class=HTMLResponse)
def admin_client_access_page(request: Request):
    return templates.TemplateResponse(request, "admin_client_access_page.html", {})


@router.get("/sensor-catalog-manager", response_class=HTMLResponse)
def sensor_catalog_manager_page(request: Request):
    return templates.TemplateResponse(request, "sensor_catalog_manager_page.html", {})


@router.get("/firmware-module-manager", response_class=HTMLResponse)
def firmware_module_manager_page(request: Request):
    return templates.TemplateResponse(request, "firmware_module_manager_page.html", {})


@router.get("/sensor-profile-manager", response_class=HTMLResponse)
def sensor_profile_manager_page(request: Request):
    return templates.TemplateResponse(request, "sensor_profile_manager_page.html", {})


@router.get("/sensor-profile-editor", response_class=HTMLResponse)
def simple_sensor_profile_editor_page(request: Request):
    return templates.TemplateResponse(request, "simple_sensor_profile_editor_page.html", {})


@router.get("/sensor-profile-editor/advanced", response_class=HTMLResponse)
def sensor_profile_advanced_editor_page(request: Request):
    return templates.TemplateResponse(request, "sensor_profile_advanced_editor_page.html", {})


@router.get("/admin/provision-options", response_class=HTMLResponse)
def admin_provision_options_page(request: Request):
    return templates.TemplateResponse(request, "admin_provision_options_page.html", {})


@router.get("/admin/alarms", response_class=HTMLResponse)
def admin_alarms_page(request: Request):
    return templates.TemplateResponse(request, "admin_alarms_page.html", {})


@router.get("/floorplan-editor", response_class=HTMLResponse)
def floorplan_editor(request: Request):
    return templates.TemplateResponse(request, "floorplan_editor.html", {})


@router.get("/site-map-editor", response_class=HTMLResponse)
def site_map_editor(request: Request):
    return templates.TemplateResponse(request, "site_map_editor.html", {})


@router.get("/admin/audit-log", response_class=HTMLResponse)
def admin_audit_log_page(request: Request):
    return templates.TemplateResponse(request, "admin_audit_log_page.html", {})


@router.get("/admin", response_class=HTMLResponse)
def admin_home_page(request: Request):
    return templates.TemplateResponse(request, "admin_home_page.html", {})


@router.get("/node-placement-editor", response_class=HTMLResponse)
def node_placement_editor(request: Request):
    return templates.TemplateResponse(request, "node_placement_editor.html", {})


@router.get("/client-login", response_class=HTMLResponse)
def client_login_page(request: Request):
    # client_login_page.html is the portal itself, not a login form; it is
    # served from /client-portal once the session cookie exists.
    from app.main import render_role_login_page

    return render_role_login_page(
        request,
        login_type="client",
        title="Client Login",
        subtitle="Sign in to view your buildings, devices and alarms.",
        badge="CLIENT",
        accent="#0d9488",
    )


@router.get("/floor-live-view", response_class=HTMLResponse)
def floor_live_view(request: Request):
    return templates.TemplateResponse(request, "floor_live_view.html", {})


@router.get("/admin/setup", response_class=HTMLResponse)
def admin_setup_asset_management_page(request: Request):
    return templates.TemplateResponse(request, "admin_setup_asset_management_page.html", {})


@router.get("/alarm-settings-page", response_class=HTMLResponse)
def alarm_settings_page(request: Request):
    return templates.TemplateResponse(request, "alarm_settings_page.html", {})


@router.get("/client-portal", response_class=HTMLResponse)
def client_portal_page(request: Request):
    return templates.TemplateResponse(request, "client_portal_page.html", {})


@router.get("/building-overview", response_class=HTMLResponse)
def building_overview(request: Request):
    return templates.TemplateResponse(request, "building_overview.html", {})


@router.get("/floor-editor", response_class=HTMLResponse)
def floor_editor(request: Request):
    return templates.TemplateResponse(request, "floor_editor.html", {})


@router.get("/device-capabilities-manager", response_class=HTMLResponse)
def device_capabilities_manager_page(request: Request):
    return templates.TemplateResponse(request, "device_capabilities_manager_page.html", {})
